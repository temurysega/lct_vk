from __future__ import annotations

import math
import zipfile
from pathlib import Path
from typing import Any

from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE

from .utils import write_json


def _inches(value: int) -> float:
    return value / 914400


def _shape_font_size(shape: Any, default: float) -> float:
    sizes: list[float] = []
    if not getattr(shape, "has_text_frame", False):
        return default
    for paragraph in shape.text_frame.paragraphs:
        for run in paragraph.runs:
            if run.font.size:
                sizes.append(run.font.size.pt)
    return min(sizes) if sizes else default


def _overflow_ratio(shape: Any, default_size: float) -> float:
    text = getattr(shape, "text", "").strip()
    if not text:
        return 0.0
    width = max(0.1, _inches(shape.width))
    height = max(0.1, _inches(shape.height))
    font_size = max(6.0, _shape_font_size(shape, default_size))
    chars_per_line = max(7, int(width * 72 / (font_size * 0.52)))
    lines = 0
    for logical_line in text.splitlines() or [text]:
        lines += max(1, math.ceil(len(logical_line) / chars_per_line))
    required_height = lines * font_size * 1.22 / 72 + 0.08
    return required_height / height


def inspect_presentation(
    output_path: str | Path,
    *,
    design_system: dict[str, Any] | None = None,
    expected_slide_count: int | None = None,
    report_path: str | Path | None = None,
) -> dict[str, Any]:
    path = Path(output_path).resolve()
    issues: list[dict[str, Any]] = []
    if not path.exists():
        report = {
            "status": "failed",
            "score": 0,
            "output": str(path),
            "issues": [
                {
                    "severity": "error",
                    "code": "missing_file",
                    "message": "PPTX file does not exist",
                }
            ],
        }
        if report_path:
            write_json(Path(report_path), report)
        return report

    try:
        with zipfile.ZipFile(path) as archive:
            bad_member = archive.testzip()
            if bad_member:
                issues.append(
                    {
                        "severity": "error",
                        "code": "corrupt_zip",
                        "message": f"Corrupt package member: {bad_member}",
                    }
                )
    except zipfile.BadZipFile:
        issues.append(
            {
                "severity": "error",
                "code": "invalid_pptx",
                "message": "File is not a valid ZIP/OOXML package",
            }
        )

    try:
        prs = Presentation(path)
    except Exception as exc:  # noqa: BLE001 - QA must report every malformed-package failure
        issues.append({"severity": "error", "code": "open_failed", "message": str(exc)})
        report = {"status": "failed", "score": 0, "output": str(path), "issues": issues}
        if report_path:
            write_json(Path(report_path), report)
        return report

    slide_width = _inches(prs.slide_width)
    slide_height = _inches(prs.slide_height)
    default_body = float(
        (design_system or {}).get("typography", {}).get("body_size_pt", 18)
    )
    explicit_fonts: set[str] = set()
    slide_reports: list[dict[str, Any]] = []
    expects_native_grid = (design_system or {}).get("source_model", {}).get(
        "composition_mode"
    ) == "native_grid"
    for slide_index, slide in enumerate(prs.slides, 1):
        slide_issues: list[dict[str, Any]] = []
        text_count = 0
        text_shapes: list[Any] = []
        for shape in slide.shapes:
            if expects_native_grid and str(shape.name).lower().startswith("pdf "):
                slide_issues.append(
                    {
                        "severity": "error",
                        "code": "source_fragment_leak",
                        "shape": shape.name,
                        "message": "A PDF-conversion fragment leaked into a native slide",
                    }
                )
            x, y, w, h = map(
                _inches, (shape.left, shape.top, shape.width, shape.height)
            )
            selectable_background = (
                shape.shape_type == MSO_SHAPE_TYPE.PICTURE
                and w >= slide_width * 0.8
                and h >= slide_height * 0.8
            )
            if expects_native_grid and (
                str(shape.name) == "BrandDeck Background Asset" or selectable_background
            ):
                slide_issues.append(
                    {
                        "severity": "error",
                        "code": "selectable_background",
                        "shape": shape.name,
                        "message": "A full-slide raster must be stored as a slide background",
                    }
                )
            if (
                x < -0.02
                or y < -0.02
                or x + w > slide_width + 0.02
                or y + h > slide_height + 0.02
            ):
                generated_shape = str(shape.name).startswith("BrandDeck")
                brand_bleed = shape.name in {
                    "BrandDeck Background Asset",
                    "BrandDeck Brand Asset",
                }
                slide_issues.append(
                    {
                        "severity": (
                            "info" if brand_bleed or not generated_shape else "warning"
                        ),
                        "code": (
                            "brand_asset_bleed"
                            if brand_bleed
                            else "out_of_bounds"
                            if generated_shape
                            else "template_bleed"
                        ),
                        "shape": shape.name,
                        "message": f"Shape bounds ({x:.2f}, {y:.2f}, {w:.2f}, {h:.2f}) exceed canvas",
                    }
                )
            if getattr(shape, "has_text_frame", False) and shape.text.strip():
                text_count += 1
                text_shapes.append(shape)
                ratio = _overflow_ratio(shape, default_body)
                if ratio > 1.45:
                    slide_issues.append(
                        {
                            "severity": "error" if ratio > 2.0 else "warning",
                            "code": "text_overflow_risk",
                            "shape": shape.name,
                            "ratio": round(ratio, 2),
                            "message": "Estimated text height exceeds the available box",
                        }
                    )
                for paragraph in shape.text_frame.paragraphs:
                    for run in paragraph.runs:
                        if run.font.name:
                            explicit_fonts.add(run.font.name)
                        if (
                            expects_native_grid
                            and str(shape.name).startswith("BrandDeck Native")
                            and not any(
                                token in str(shape.name)
                                for token in (
                                    "Footer",
                                    "Badge",
                                    "Number",
                                    "Navigation Label",
                                )
                            )
                            and run.font.size
                            and run.font.size.pt < 14
                        ):
                            slide_issues.append(
                                {
                                    "severity": "warning",
                                    "code": "native_text_too_small",
                                    "shape": shape.name,
                                    "message": "Native slide text is smaller than 14 pt",
                                }
                            )
        for first_index, first in enumerate(text_shapes):
            for second in text_shapes[first_index + 1 :]:
                if not (
                    str(first.name).startswith("BrandDeck")
                    or str(second.name).startswith("BrandDeck")
                ):
                    continue
                left = max(first.left, second.left)
                top = max(first.top, second.top)
                right = min(first.left + first.width, second.left + second.width)
                bottom = min(first.top + first.height, second.top + second.height)
                if right <= left or bottom <= top:
                    continue
                intersection = (right - left) * (bottom - top)
                smaller_area = max(
                    1, min(first.width * first.height, second.width * second.height)
                )
                ratio = intersection / smaller_area
                if ratio > 0.15:
                    slide_issues.append(
                        {
                            "severity": "error" if ratio > 0.4 else "warning",
                            "code": "text_overlap",
                            "shape": f"{first.name} / {second.name}",
                            "ratio": round(ratio, 2),
                            "message": "Visible text boxes overlap",
                        }
                    )
        if text_count == 0:
            slide_issues.append(
                {
                    "severity": "warning",
                    "code": "empty_slide",
                    "message": "No visible text detected",
                }
            )
        if expects_native_grid and not any(
            str(shape.name).startswith("BrandDeck Native") for shape in slide.shapes
        ):
            slide_issues.append(
                {
                    "severity": "error",
                    "code": "native_grid_missing",
                    "message": "Fragmented source was not rebuilt with native objects",
                }
            )
        for item in slide_issues:
            item["slide"] = slide_index
        issues.extend(slide_issues)
        slide_reports.append(
            {
                "slide": slide_index,
                "shape_count": len(slide.shapes),
                "text_shape_count": text_count,
            }
        )

    if expected_slide_count is not None and len(prs.slides) != expected_slide_count:
        issues.append(
            {
                "severity": "error",
                "code": "slide_count_mismatch",
                "message": f"Expected {expected_slide_count} slides, found {len(prs.slides)}",
            }
        )

    allowed_fonts = set((design_system or {}).get("typography", {}).get("families", []))
    primary = (design_system or {}).get("typography", {}).get("primary_font")
    if primary:
        allowed_fonts.add(primary)
    foreign_fonts = sorted(explicit_fonts - allowed_fonts) if allowed_fonts else []
    if foreign_fonts:
        issues.append(
            {
                "severity": "warning",
                "code": "foreign_fonts",
                "message": "Explicit fonts outside the extracted design system",
                "fonts": foreign_fonts,
            }
        )

    errors = sum(item["severity"] == "error" for item in issues)
    warnings = sum(item["severity"] == "warning" for item in issues)
    score = max(0, 100 - errors * 18 - warnings * 4)
    status = "failed" if errors else ("warning" if warnings else "passed")
    report = {
        "status": status,
        "score": score,
        "output": str(path),
        "file_size_bytes": path.stat().st_size,
        "slide_count": len(prs.slides),
        "canvas": {"width_inches": slide_width, "height_inches": slide_height},
        "explicit_fonts": sorted(explicit_fonts),
        "issues": issues,
        "slides": slide_reports,
    }
    if report_path:
        write_json(Path(report_path), report)
    return report


def compact_plan(plan: dict[str, Any]) -> dict[str, Any]:
    """Deterministically reduce density before a QA retry."""

    def truncate(value: Any, limit: int) -> str:
        text = str(value)
        if len(text) <= limit:
            return text
        shortened = text[: max(1, limit - 1)].rsplit(" ", 1)[0].rstrip(" ,;:-")
        return f"{shortened or text[: max(1, limit - 1)]}…"

    compacted = {**plan, "slides": []}
    for slide in plan.get("slides", []):
        new_slide = dict(slide)
        new_slide["body"] = truncate(new_slide.get("body", ""), 420)
        new_slide["bullets"] = [
            truncate(item, 88) for item in new_slide.get("bullets", [])[:4]
        ]
        visual = new_slide.get("visual")
        if isinstance(visual, dict):
            visual = dict(visual)
            if isinstance(visual.get("items"), list):
                visual["items"] = visual["items"][:4]
            if isinstance(visual.get("categories"), list):
                visual["categories"] = visual["categories"][:8]
            if isinstance(visual.get("rows"), list):
                visual["rows"] = visual["rows"][:7]
            new_slide["visual"] = visual
        compacted["slides"].append(new_slide)
    return compacted
