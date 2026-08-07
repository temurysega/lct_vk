from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import re
import statistics
from collections import Counter, defaultdict
from collections.abc import Iterable
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any

from .llm import InferenceClient, InferenceError
from .renderer import render_context_previews
from .utils import (
    PROJECT_ROOT,
    copy_file,
    read_json,
    resolve_workspace,
    slugify,
    write_json,
)

ANALYSIS_SCHEMA_VERSION = "1.0"


def _load_extractor():
    script = PROJECT_ROOT / "shared" / "scripts" / "extract_template.py"
    if not script.exists():
        raise FileNotFoundError(f"Template extractor is missing: {script}")
    spec = importlib.util.spec_from_file_location(
        "branddeck_template_extractor", script
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load extractor: {script}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.extract_template_context


def _walk(value: Any) -> Iterable[tuple[str, Any]]:
    if isinstance(value, dict):
        for key, child in value.items():
            yield key, child
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _normal_color(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    color = value.strip().lstrip("#").upper()
    if len(color) == 8:
        color = color[-6:]
    if len(color) == 6 and all(char in "0123456789ABCDEF" for char in color):
        return color
    return None


def _observed_colors(context: dict[str, Any]) -> list[dict[str, Any]]:
    counts: Counter[str] = Counter()
    for key, value in _walk(context):
        if key not in {"color", "background_color", "fill", "line_color"}:
            continue
        if isinstance(value, dict):
            value = value.get("color")
        color = _normal_color(value)
        if color:
            counts[color] += 1
    for value in context.get("theme", {}).get("color_scheme", {}).values():
        color = _normal_color(value)
        if color:
            counts[color] += 2
    return [
        {"hex": color, "usage_count": count} for color, count in counts.most_common(16)
    ]


def _font_samples(context: dict[str, Any]) -> list[tuple[str, float, str, bool]]:
    samples: list[tuple[str, float, str, bool]] = []
    for slide in context.get("slides", []):
        for element in slide.get("text_elements", []):
            role = str(element.get("placeholder_type", "body")).lower()
            for paragraph in element.get("paragraphs", []):
                font = paragraph.get("font", {})
                name = font.get("name")
                size = font.get("size_pt")
                if name and isinstance(size, (int, float)):
                    samples.append((name, float(size), role, bool(font.get("bold"))))
    return samples


def _text_color_samples(context: dict[str, Any]) -> list[tuple[str, float]]:
    samples: list[tuple[str, float]] = []
    for slide in context.get("slides", []):
        for element in slide.get("text_elements", []):
            for paragraph in element.get("paragraphs", []):
                font = paragraph.get("font", {})
                color = _normal_color(font.get("color"))
                size = font.get("size_pt")
                if color and isinstance(size, (int, float)):
                    samples.append((color, float(size)))
    return samples


def _mode(values: Iterable[str], fallback: str) -> str:
    counts = Counter(value for value in values if value)
    return counts.most_common(1)[0][0] if counts else fallback


def _source_model(context: dict[str, Any]) -> dict[str, Any]:
    shapes = [
        shape
        for slide in context.get("slides", [])
        for shape in slide.get("shapes", [])
    ]
    pdf_shapes = [
        shape
        for shape in shapes
        if str(shape.get("name", "")).lower().startswith("pdf ")
    ]
    ratio = len(pdf_shapes) / max(1, len(shapes))
    fragmented = len(pdf_shapes) >= 10 and ratio >= 0.25
    return {
        "fragmented": fragmented,
        "pdf_shape_ratio": round(ratio, 3),
        "composition_mode": "native_grid" if fragmented else "template_layout",
    }


def build_design_system(context: dict[str, Any]) -> dict[str, Any]:
    presentation = context.get("presentation", {})
    theme = context.get("theme", {})
    fonts_summary = context.get("fonts_summary", {})
    samples = _font_samples(context)
    color_samples = _text_color_samples(context)

    title_sizes = [size for _, size, role, _ in samples if "title" in role]
    body_sizes = [size for _, size, role, _ in samples if "title" not in role]
    all_sizes = [size for _, size, _, _ in samples]
    font_families = fonts_summary.get("families_used", [])
    if not font_families:
        font_families = sorted({name for name, _, _, _ in samples})
    theme_fonts = theme.get("font_scheme", {})
    font_counts = Counter(name for name, _, _, _ in samples)
    primary_font = (
        (font_counts.most_common(1)[0][0] if font_counts else None)
        or (font_families[0] if font_families else None)
        or theme_fonts.get("minorFont")
        or theme_fonts.get("majorFont")
        or "Arial"
    )
    heading_font_counts = Counter(
        name
        for name, size, role, bold in samples
        if size >= 28 or "title" in role or bold
    )
    heading_font = (
        heading_font_counts.most_common(1)[0][0]
        if heading_font_counts
        else primary_font
    )

    def median(values: list[float], fallback: float) -> float:
        return round(statistics.median(values), 1) if values else fallback

    palette = [
        {"role": role, "hex": color}
        for role, raw in theme.get("color_scheme", {}).items()
        if (color := _normal_color(raw))
    ]
    observed = _observed_colors(context)
    if not palette:
        palette = [
            {"role": f"observed_{idx + 1}", "hex": item["hex"]}
            for idx, item in enumerate(observed[:8])
        ]

    width = float(presentation.get("slide_width_inches", 13.333))
    height = float(presentation.get("slide_height_inches", 7.5))
    placeholders = [
        ph
        for layout in context.get("slide_layouts", [])
        for ph in layout.get("placeholders", [])
        if isinstance(ph.get("left"), (int, float))
    ]
    lefts = [float(ph["left"]) for ph in placeholders if ph.get("left", 0) > 0]
    rights = [
        width - float(ph.get("left", 0)) - float(ph.get("width", 0))
        for ph in placeholders
    ]
    positive_rights = [value for value in rights if value > 0]
    background = _mode(
        (
            _normal_color(slide.get("effective_background", {}).get("color")) or ""
            for slide in context.get("slides", [])
        ),
        "FFFFFF",
    )
    content_title_sizes = [size for size in all_sizes if 28 <= size <= 56]
    readable_body_sizes = [size for size in body_sizes if 14 <= size <= 24]
    content_title_size = min(40.0, max(30.0, median(content_title_sizes, 34.0)))
    body_size = min(20.0, max(16.0, median(readable_body_sizes, 18.0)))
    heading_color = _mode(
        (color for color, size in color_samples if size >= 28),
        "FFFFFF" if background != "FFFFFF" else "111827",
    )
    body_color = _mode(
        (color for color, size in color_samples if 12 <= size < 28),
        heading_color,
    )
    excluded = {background, heading_color, body_color, "000000", "FFFFFF"}
    accent_color = (
        body_color
        if body_color not in {background, heading_color}
        else next(
            (item["hex"] for item in observed if item["hex"] not in excluded),
            body_color,
        )
    )
    source_model = _source_model(context)

    return {
        "schema_version": ANALYSIS_SCHEMA_VERSION,
        "canvas": {
            "width_inches": width,
            "height_inches": height,
            "aspect_ratio": round(width / height, 4) if height else None,
        },
        "typography": {
            "primary_font": primary_font,
            "heading_font": heading_font,
            "families": font_families,
            "theme_fonts": theme_fonts,
            "title_size_pt": median(title_sizes, max(all_sizes, default=32.0)),
            "content_title_size_pt": content_title_size,
            "cover_title_size_pt": min(52.0, max(40.0, content_title_size * 1.35)),
            "body_size_pt": body_size,
            "observed_sizes_pt": sorted(
                {size for _, size, _, _ in samples}, reverse=True
            ),
        },
        "colors": {
            "theme": palette,
            "observed": observed,
        },
        "brand": {
            "background": background,
            "heading": heading_color,
            "body": body_color,
            "accent": accent_color,
        },
        "source_model": source_model,
        "spacing": {
            "typical_left_margin_inches": 0.65
            if source_model["fragmented"]
            else (round(statistics.median(lefts), 2) if lefts else 0.6),
            "typical_right_margin_inches": 0.65
            if source_model["fragmented"]
            else (
                round(statistics.median(positive_rights), 2) if positive_rights else 0.6
            ),
            "minimum_gap_inches": 0.12,
        },
        "imagery": {
            "asset_count": len(context.get("images_manifest", [])),
            "assets": context.get("images_manifest", []),
            "master_assets": [
                item
                for item in context.get("images_manifest", [])
                if item.get("source") == "master"
            ],
            "layout_assets": [
                item
                for item in context.get("images_manifest", [])
                if item.get("source") == "layout"
            ],
        },
    }


def _placeholder_kind(value: Any) -> str:
    return str(value or "").lower().replace(" ", "_")


def _pattern_roles(layout: dict[str, Any], examples: list[dict[str, Any]]) -> list[str]:
    name = str(layout.get("name", "")).lower()
    placeholders = layout.get("placeholders", [])
    types = [_placeholder_kind(ph.get("type")) for ph in placeholders]
    body = [
        ph
        for ph in placeholders
        if any(
            token in _placeholder_kind(ph.get("type"))
            for token in ("body", "object", "content", "text")
        )
    ]
    picture = [
        ph for ph in placeholders if "picture" in _placeholder_kind(ph.get("type"))
    ]
    example_shapes = [shape for slide in examples for shape in slide.get("shapes", [])]
    has_data = any(
        str(shape.get("type", "")).upper() in {"TABLE", "CHART"}
        for shape in example_shapes
    )
    has_images = bool(picture) or any(slide.get("images") for slide in examples)

    closing_words = (
        "closing",
        "thank",
        "contacts",
        "questions",
        "спасибо",
        "контакт",
        "вопрос",
    )
    cover_words = ("title slide", "cover", "титуль", "облож")
    section_words = ("section", "divider", "раздел")
    blank_words = ("blank", "пуст")

    roles: list[str] = []
    if any(word in name for word in closing_words):
        roles.append("closing")
    is_section_name = any(word in name for word in section_words)
    if any(word in name for word in cover_words) or (
        not is_section_name
        and any("title" in item or "ctr_title" in item for item in types)
        and any("subtitle" in item for item in types)
        and len(body) <= 1
    ):
        roles.append("cover")
    if is_section_name:
        roles.append("section")
    if len(body) >= 2:
        x_positions = sorted(float(item.get("left", 0)) for item in body)
        if x_positions and x_positions[-1] - x_positions[0] > 1.0:
            roles.extend(["two_column", "comparison"])
    if has_data:
        roles.append("data")
    if has_images:
        roles.append("image")
    if any(word in name for word in blank_words) or not placeholders:
        roles.append("blank")
    if not roles and not body and any("title" in item for item in types):
        roles.append("section")
    if not roles or (set(roles) <= {"data", "image"}):
        roles.append("content")
    return list(dict.fromkeys(roles))


def _text_size(element: dict[str, Any]) -> float:
    return max(
        (
            float(paragraph.get("font", {}).get("size_pt", 0) or 0)
            for paragraph in element.get("paragraphs", [])
        ),
        default=0.0,
    )


def _element_text(element: dict[str, Any]) -> str:
    return " ".join(
        str(paragraph.get("text", "")).strip()
        for paragraph in element.get("paragraphs", [])
        if str(paragraph.get("text", "")).strip()
    )


def _zone_capacity(zone: dict[str, Any], default_size: float = 18.0) -> int:
    width = max(0.1, float(zone.get("w", 0) or 0))
    height = max(0.1, float(zone.get("h", 0) or 0))
    font_size = float(zone.get("font", {}).get("size_pt", default_size) or default_size)
    chars_per_line = max(5, width * 72 / max(6.0, font_size) / 0.52)
    lines = max(1, height * 72 / max(7.0, font_size * 1.25))
    return max(12, round(chars_per_line * lines))


def _rhetorical_pattern(
    roles: list[str], zones: list[dict[str, Any]], bullet_count: int
) -> str:
    body_zones = [zone for zone in zones if zone.get("type") != "title"]
    if "cover" in roles:
        return "hero"
    if "closing" in roles:
        return "closing"
    if "comparison" in roles:
        return "comparison"
    if "two_column" in roles:
        return "two_column"
    if "data" in roles:
        return "data"
    if "image" in roles and len(body_zones) <= 2:
        return "image_text"
    if bullet_count >= 2:
        return "bullet_list"
    if len(body_zones) >= 4:
        return "multi_card"
    if "section" in roles:
        return "section"
    return "content"


def _capacity_profile(
    *,
    zones: list[dict[str, Any]],
    roles: list[str],
    text_elements: list[dict[str, Any]],
    images: list[dict[str, Any]],
    shapes: list[dict[str, Any]],
    canvas_width: float,
    canvas_height: float,
) -> dict[str, Any]:
    body_zones = [zone for zone in zones if zone.get("type") != "title"]
    source_texts = [_element_text(element) for element in text_elements]
    bullet_count = sum(
        text.lstrip().startswith(("•", "-", "–", "—")) for text in source_texts
    )
    body_capacity = sum(_zone_capacity(zone) for zone in body_zones)
    canvas_area = max(0.1, canvas_width * canvas_height)
    image_ratios = [
        (
            float(image.get("width", 0) or 0)
            * float(image.get("height", 0) or 0)
            / canvas_area
        )
        for image in images
    ]
    image_area = sum(image_ratios)
    content_image_ratios = [ratio for ratio in image_ratios if 0.03 <= ratio <= 0.6]
    shape_count = len(shapes)
    complexity = min(1.0, (shape_count + len(images) * 3 + len(zones)) / 80)
    return {
        "title_chars": max(
            (_zone_capacity(zone) for zone in zones if zone.get("type") == "title"),
            default=80,
        ),
        "body_chars": max(80, body_capacity),
        "body_zones": max(1, len(body_zones)),
        "bullets_per_zone": min(8, max(2, bullet_count or len(body_zones) + 1)),
        "source_bullet_count": bullet_count,
        "supports_image": bool(content_image_ratios) or "image" in roles,
        "supports_data": "data" in roles
        or any(
            str(shape.get("type", "")).upper() in {"TABLE", "CHART"} for shape in shapes
        ),
        "image_area_ratio": round(min(1.0, image_area), 3),
        "content_image_count": len(content_image_ratios),
        "content_image_area_ratio": round(min(1.0, sum(content_image_ratios)), 3),
        "shape_count": shape_count,
        "complexity": round(complexity, 3),
        "rhetorical_pattern": _rhetorical_pattern(roles, zones, bullet_count),
    }


def _slide_roles(
    slide: dict[str, Any],
    *,
    slide_number: int,
    slide_count: int,
    canvas_width: float,
    canvas_height: float,
) -> list[str]:
    text_elements = slide.get("text_elements", [])
    text = " ".join(
        paragraph.get("text", "")
        for element in text_elements
        for paragraph in element.get("paragraphs", [])
    ).lower()
    shapes = slide.get("shapes", [])
    images = slide.get("images", [])
    has_structured_data = any(
        str(shape.get("type", "")).upper() in {"TABLE", "CHART"} for shape in shapes
    )
    number_count = len(re.findall(r"\d+(?:[.,]\d+)?%?", text))
    image_area = sum(
        float(item.get("width", 0)) * float(item.get("height", 0)) for item in images
    )

    roles: list[str] = []
    if slide_number == 1:
        roles.append("cover")
    if slide_number == slide_count or any(
        token in text for token in ("спасибо", "thank you", "контакты", "contacts")
    ):
        roles.append("closing")
    if len(text_elements) <= 2 and not has_structured_data and "cover" not in roles:
        roles.append("section")

    positioned = sorted(
        text_elements,
        key=lambda item: (float(item.get("left", 0)), float(item.get("top", 0))),
    )
    if len(positioned) >= 3:
        lefts = [float(item.get("left", 0)) for item in positioned]
        if max(lefts, default=0) - min(lefts, default=0) > canvas_width * 0.3:
            roles.append("two_column")
            if any(token in text for token in ("сравнен", "versus", " vs ", "вариант")):
                roles.append("comparison")
    if has_structured_data or number_count >= 4:
        roles.append("data")
    if image_area >= canvas_width * canvas_height * 0.18:
        roles.append("image")
    if not roles or len(text_elements) >= 3:
        roles.append("content")
    return list(dict.fromkeys(roles))


def _slide_exemplar_patterns(context: dict[str, Any]) -> list[dict[str, Any]]:
    presentation = context.get("presentation", {})
    width = float(presentation.get("slide_width_inches", 13.333))
    height = float(presentation.get("slide_height_inches", 7.5))
    slides = context.get("slides", [])
    layouts = context.get("slide_layouts", [])
    patterns: list[dict[str, Any]] = []
    for position, slide in enumerate(slides, 1):
        layout_index = int(slide.get("layout_index", 0))
        layout = layouts[layout_index] if 0 <= layout_index < len(layouts) else {}
        elements = slide.get("text_elements", [])
        upper_elements = [
            element
            for element in elements
            if float(element.get("top", 0) or 0) < height * 0.28
        ]
        title_pool = upper_elements or elements
        title_element = (
            max(
                title_pool,
                key=lambda item: (
                    _text_size(item),
                    float(item.get("width", 0) or 0),
                    -float(item.get("top", 0) or 0),
                ),
            )
            if title_pool
            else None
        )
        zones = []
        for element in slide.get("text_elements", []):
            zones.append(
                {
                    "idx": None,
                    "type": "title" if element is title_element else "body",
                    "x": element.get("left"),
                    "y": element.get("top"),
                    "w": element.get("width"),
                    "h": element.get("height"),
                    "font": next(
                        (
                            paragraph.get("font", {})
                            for paragraph in element.get("paragraphs", [])
                            if paragraph.get("font")
                        ),
                        {},
                    ),
                    "alignment": next(
                        (
                            paragraph.get("alignment")
                            for paragraph in element.get("paragraphs", [])
                            if paragraph.get("alignment")
                        ),
                        None,
                    ),
                }
            )
        body_count = max(1, sum(zone["type"] == "body" for zone in zones))
        roles = _slide_roles(
            slide,
            slide_number=position,
            slide_count=len(slides),
            canvas_width=width,
            canvas_height=height,
        )
        capacity = _capacity_profile(
            zones=zones,
            roles=roles,
            text_elements=slide.get("text_elements", []),
            images=slide.get("images", []),
            shapes=slide.get("shapes", []),
            canvas_width=width,
            canvas_height=height,
        )
        capacity["body_zones"] = body_count
        patterns.append(
            {
                "id": f"slide-{int(slide.get('index', position))}",
                "name": f"Exemplar slide {int(slide.get('index', position))}",
                "source_kind": "slide_exemplar",
                "layout_index": layout_index,
                "master_index": int(layout.get("master_index", 0)),
                "roles": roles,
                "placeholders": zones,
                "capacity": capacity,
                "example_slide_indices": [int(slide.get("index", position))],
                "layout_assets": [
                    image.get("name") for image in layout.get("images", [])
                ],
                "slide_assets": [
                    image.get("name") for image in slide.get("images", [])
                ],
                "background": slide.get(
                    "effective_background", slide.get("background", {})
                ),
            }
        )
    return patterns


def build_pattern_catalog(context: dict[str, Any]) -> dict[str, Any]:
    presentation = context.get("presentation", {})
    canvas_width = float(presentation.get("slide_width_inches", 13.333))
    canvas_height = float(presentation.get("slide_height_inches", 7.5))
    slides_by_layout: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for slide in context.get("slides", []):
        if isinstance(slide.get("layout_index"), int):
            slides_by_layout[slide["layout_index"]].append(slide)

    patterns: list[dict[str, Any]] = []
    for layout in context.get("slide_layouts", []):
        index = int(layout.get("index", len(patterns)))
        examples = slides_by_layout.get(index, [])
        placeholders = []
        for ph in layout.get("placeholders", []):
            placeholders.append(
                {
                    "idx": ph.get("idx"),
                    "type": _placeholder_kind(ph.get("type")),
                    "x": ph.get("left"),
                    "y": ph.get("top"),
                    "w": ph.get("width"),
                    "h": ph.get("height"),
                    "font": ph.get("font", {}),
                    "alignment": ph.get("alignment"),
                }
            )
        body_zones = [
            ph
            for ph in placeholders
            if any(
                token in ph["type"] for token in ("body", "object", "content", "text")
            )
        ]
        roles = _pattern_roles(layout, examples)
        capacity = _capacity_profile(
            zones=placeholders,
            roles=roles,
            text_elements=[
                element
                for slide in examples[:3]
                for element in slide.get("text_elements", [])
            ],
            images=[
                image for slide in examples[:3] for image in slide.get("images", [])
            ],
            shapes=[
                shape for slide in examples[:3] for shape in slide.get("shapes", [])
            ],
            canvas_width=canvas_width,
            canvas_height=canvas_height,
        )
        capacity["body_zones"] = max(1, len(body_zones))
        capacity["supports_image"] = capacity["supports_image"] or any(
            "picture" in ph["type"] for ph in placeholders
        )
        patterns.append(
            {
                "id": f"layout-{index}",
                "name": layout.get("name") or f"Layout {index}",
                "layout_index": index,
                "master_index": int(layout.get("master_index", 0)),
                "roles": roles,
                "placeholders": placeholders,
                "capacity": capacity,
                "example_slide_indices": [slide.get("index") for slide in examples],
                "layout_assets": [
                    image.get("name") for image in layout.get("images", [])
                ],
                "background": layout.get("background", {"type": "inherit"}),
            }
        )

    patterns.extend(_slide_exemplar_patterns(context))

    if _source_model(context)["fragmented"]:
        native_patterns = [
            ("native-cover", ["cover"], "hero", 120, 0, 0),
            (
                "native-cards",
                ["content", "comparison", "data"],
                "bullet_list",
                900,
                4,
                4,
            ),
            (
                "native-list",
                ["content", "section"],
                "bullet_list",
                900,
                4,
                4,
            ),
            (
                "native-split",
                ["content", "comparison", "data", "image"],
                "two_column",
                750,
                3,
                4,
            ),
            ("native-closing", ["closing"], "closing", 120, 0, 0),
        ]
        for (
            pattern_id,
            roles,
            rhetorical,
            body_chars,
            body_zones,
            bullets,
        ) in native_patterns:
            patterns.append(
                {
                    "id": pattern_id,
                    "name": pattern_id.replace("native-", "Native ").title(),
                    "layout_index": 6,
                    "master_index": 0,
                    "roles": roles,
                    "placeholders": [],
                    "capacity": {
                        "title_chars": 120,
                        "body_chars": body_chars,
                        "body_zones": max(1, body_zones),
                        "bullets_per_zone": max(2, bullets),
                        "source_bullet_count": 0,
                        "supports_image": pattern_id == "native-split",
                        "supports_data": pattern_id in {"native-cards", "native-split"},
                        "image_area_ratio": 0,
                        "content_image_count": 0,
                        "content_image_area_ratio": 0,
                        "shape_count": 0,
                        "complexity": 0.1,
                        "rhetorical_pattern": rhetorical,
                    },
                    "example_slide_indices": [],
                    "layout_assets": [],
                    "background": {"type": "brand"},
                    "source_kind": "native_grid",
                }
            )

    if patterns and not any("cover" in item["roles"] for item in patterns):
        patterns[0]["roles"].insert(0, "cover")
    if patterns and not any("closing" in item["roles"] for item in patterns):
        patterns[-1]["roles"].insert(0, "closing")

    return {
        "schema_version": ANALYSIS_SCHEMA_VERSION,
        "pattern_count": len(patterns),
        "patterns": patterns,
    }


def _guideline_markdown(
    source_name: str,
    design: dict[str, Any],
    catalog: dict[str, Any],
    llm_notes: dict[str, Any] | None,
) -> str:
    typography = design["typography"]
    lines = [
        f"# Design guideline: {source_name}",
        "",
        "## Typography",
        "",
        f"- Primary font: `{typography['primary_font']}`",
        f"- Heading font: `{typography['heading_font']}`",
        f"- Title size: approximately {typography['title_size_pt']} pt",
        f"- Body size: approximately {typography['body_size_pt']} pt",
        f"- Observed families: {', '.join(typography['families']) or 'not explicitly encoded'}",
        "",
        "## Source model",
        "",
        f"- Composition mode: `{design['source_model']['composition_mode']}`",
        f"- PDF shape ratio: `{design['source_model']['pdf_shape_ratio']}`",
        "",
        "## Color palette",
        "",
    ]
    for item in design["colors"]["theme"]:
        lines.append(f"- `{item['role']}`: `#{item['hex']}`")
    lines.extend(["", "## Layout patterns", ""])
    for pattern in catalog["patterns"]:
        lines.append(
            f"- `{pattern['id']}` — {pattern['name']}: {', '.join(pattern['roles'])}; "
            f"{len(pattern['placeholders'])} placeholders, examples {pattern['example_slide_indices'] or 'none'}"
        )
    lines.extend(
        [
            "",
            "## Composition rules",
            "",
            "- Reuse the original slide master and selected layout; never redraw global brand decorations.",
            "- Keep content inside the extracted placeholder zones and preserve their alignment.",
            "- Prefer a single key message per slide and no more than five bullets per content zone.",
            "- Use theme colors for generated charts, tables, cards and emphasis.",
            "- Preserve master and layout imagery automatically through the source PPTX package.",
        ]
    )
    if llm_notes:
        observations = llm_notes.get("brand_observations", [])
        if observations:
            lines.extend(["", "## AI brand observations", ""])
            lines.extend(f"- {item}" for item in observations if isinstance(item, str))
    return "\n".join(lines).rstrip() + "\n"


def _llm_analyze(
    client: InferenceClient,
    design: dict[str, Any],
    catalog: dict[str, Any],
    preview_paths: list[str],
) -> dict[str, Any] | None:
    system = (
        "You are a presentation design-system analyst. Infer only reusable visual rules; "
        "do not invent colors, fonts or layout geometry. Return JSON with keys "
        "brand_observations (array of concise strings) and pattern_recommendations "
        "(object mapping pattern id to a short usage recommendation). When raster previews "
        "are attached, use them to assess hierarchy, density, rhythm and imagery style, "
        "while exact values remain grounded in the JSON."
    )
    compact = {
        "design_system": design,
        "patterns": catalog["patterns"],
    }
    try:
        images = preview_paths if client.settings.vision_enabled else []
        result = client.chat_json(
            system=system,
            user=json.dumps(compact, ensure_ascii=False),
            max_tokens=1800,
            image_paths=images,
        )
        result["vision_inputs"] = len(images)
        return result
    except InferenceError:
        if preview_paths:
            try:
                result = client.chat_json(
                    system=system,
                    user=json.dumps(compact, ensure_ascii=False),
                    max_tokens=1800,
                )
                result["vision_inputs"] = 0
                return result
            except InferenceError:
                pass
        return None


def analyze_template(
    template_path: str | Path,
    *,
    workspace: str | Path | None = None,
    name: str | None = None,
    client: InferenceClient | None = None,
    force: bool = False,
) -> Path:
    source = Path(template_path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Template not found: {source}")
    if source.suffix.lower() != ".pptx":
        raise ValueError(f"Only .pptx templates are supported: {source}")

    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    workspace_path = resolve_workspace(workspace)
    template_name = f"{slugify(name or source.stem, 'template')}-{digest[:8]}"
    output_dir = workspace_path / "templates" / template_name
    manifest_path = output_dir / "manifest.json"
    if not force and manifest_path.exists():
        manifest = read_json(manifest_path)
        if manifest.get("source_sha256") == digest:
            return output_dir

    output_dir.mkdir(parents=True, exist_ok=True)
    original = output_dir / "original.pptx"
    copy_file(source, original)
    extract_template_context = _load_extractor()
    extraction_log = io.StringIO()
    try:
        with redirect_stdout(extraction_log), redirect_stderr(extraction_log):
            extract_template_context(str(original), str(output_dir))
    finally:
        (output_dir / "extraction.log").write_text(
            extraction_log.getvalue(), encoding="utf-8"
        )

    context = read_json(output_dir / "context.json")
    design = build_design_system(context)
    catalog = build_pattern_catalog(context)
    vision_manifest = render_context_previews(context, output_dir / "previews")
    preview_paths = [item["path"] for item in vision_manifest.get("previews", [])[:4]]
    llm_notes = _llm_analyze(client, design, catalog, preview_paths) if client else None
    if llm_notes:
        for pattern in catalog["patterns"]:
            recommendation = llm_notes.get("pattern_recommendations", {}).get(
                pattern["id"]
            )
            if isinstance(recommendation, str):
                pattern["usage_recommendation"] = recommendation

    write_json(output_dir / "design_system.json", design)
    write_json(output_dir / "pattern_catalog.json", catalog)
    if llm_notes:
        write_json(output_dir / "ai_observations.json", llm_notes)
    (output_dir / "guideline.md").write_text(
        _guideline_markdown(source.name, design, catalog, llm_notes), encoding="utf-8"
    )
    write_json(
        manifest_path,
        {
            "schema_version": ANALYSIS_SCHEMA_VERSION,
            "template_id": template_name,
            "source_file": source.name,
            "source_sha256": digest,
            "slide_count": context.get("presentation", {}).get("slide_count", 0),
            "master_count": len(context.get("slide_masters", [])),
            "layout_count": len(context.get("slide_layouts", [])),
            "pattern_count": catalog["pattern_count"],
            "ai_enhanced": bool(llm_notes),
            "vision_preview_count": len(vision_manifest.get("previews", [])),
            "vision_assisted": bool(llm_notes and llm_notes.get("vision_inputs")),
        },
    )
    return output_dir
