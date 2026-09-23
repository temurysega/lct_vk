"""Conservative lexical coverage: unmatched paraphrases require human review."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from pptx import Presentation

from .planner import _sections, _sentences


def _normalized(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("•", " ")).strip().casefold()


def _contains(text: str, unit: str) -> bool:
    return (
        re.search(r"(?<!\w)" + re.escape(_normalized(unit)) + r"(?!\w)", text)
        is not None
    )


_NUMBER = re.compile(
    r"(?<![\w.,])\d{1,3}(?:[ \u00a0\u202f]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?"
)


def _numbers(text: str) -> set[str]:
    found = set()
    for match in _NUMBER.finditer(text):
        value = re.sub(r"[ \u00a0\u202f]", "", match.group()).replace(",", ".")
        found.add(value.rstrip("0").rstrip(".") if "." in value else value)
    return found


def _strings(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return [str(value)]
    if isinstance(value, dict):
        return [
            text
            for key, item in value.items()
            if key != "asset_id"
            for text in _strings(item)
        ]
    if isinstance(value, list):
        return [text for item in value for text in _strings(item)]
    return []


def unsupported_numbers(source: str, plan: dict) -> list[dict]:
    """Visible numbers of the plan that the source never states.

    Integers below 10 are skipped: they usually count the slide's own items.
    Speaker notes are not visible and are not checked.
    """
    known = _numbers(source)
    result = []
    for number, slide in enumerate(plan.get("slides", []), 1):
        texts = [
            slide.get("title", ""),
            slide.get("subtitle", ""),
            slide.get("body", ""),
            *slide.get("bullets", []),
            *_strings(slide.get("visual") or {}),
        ]
        missing = sorted(
            value
            for value in _numbers("\n".join(texts)) - known
            if "." in value or float(value) >= 10
        )
        if missing:
            result.append({"slide": number, "numbers": missing})
    return result


def coverage_report(source: str, plan: dict, output: Path | None = None) -> dict:
    units = []
    for heading, lines in _sections(source):
        if heading:
            units.append(heading)
        for line in lines:
            units.extend(_sentences(line))
    slides = plan.get("slides", [])
    planned = _normalized(
        "\n".join(
            "\n".join(
                [
                    slide.get("title", ""),
                    slide.get("subtitle", ""),
                    slide.get("body", ""),
                    *slide.get("bullets", []),
                    json.dumps(slide.get("visual") or {}, ensure_ascii=False),
                ]
            )
            for slide in slides
        )
    )
    rendered = None
    if output is not None:
        texts = []

        def visit(shapes):
            for shape in shapes:
                if hasattr(shape, "shapes"):
                    visit(shape.shapes)
                if shape.has_text_frame:
                    texts.append(shape.text)
                if shape.has_table:
                    texts.extend(
                        cell.text for row in shape.table.rows for cell in row.cells
                    )
                if shape.has_chart:
                    for series in shape.chart.series:
                        texts.extend([series.name, *map(str, series.values)])
                    texts.extend(
                        str(category.label)
                        for category in shape.chart.plots[0].categories
                    )

        for slide in Presentation(output).slides:
            visit(slide.shapes)
        rendered = _normalized("\n".join(texts))
    rows = [
        {
            "source_id": f"s{index:04}",
            "text": text,
            "in_plan": _contains(planned, text),
            "in_pptx": _contains(rendered, text) if rendered is not None else None,
        }
        for index, text in enumerate(units, 1)
    ]
    missing_plan = [r["source_id"] for r in rows if not r["in_plan"]]
    missing_pptx = [r["source_id"] for r in rows if r["in_pptx"] is False]
    return {
        "method": "literal_source_units_v1",
        "semantic_verification": "not_performed",
        "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
        "status": "needs_review"
        if missing_plan or missing_pptx
        else "literal_coverage_complete",
        "units": rows,
        "missing_from_plan": missing_plan,
        "missing_from_pptx": missing_pptx,
        "unsupported_numbers": unsupported_numbers(source, plan),
        "note": "Unmatched paraphrases are not proof of omission; speaker notes do not count as slide content.",
    }
