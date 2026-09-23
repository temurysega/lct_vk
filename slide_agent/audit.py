"""Deterministic audit annotations and the contract for selective layout repairs."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from pptx import Presentation

from .qa import _inspected_shapes

REPAIRABLE = {
    "text_overflow_risk",
    "text_overlap",
    "out_of_bounds",
    "native_text_too_small",
    "placeholder_text",
}


def enrich_audit(
    path: Path, report: dict[str, Any], plan: dict[str, Any]
) -> dict[str, Any]:
    prs = Presentation(path)
    issues = report["issues"]
    seen: dict[str, int] = {}
    for number, slide in enumerate(prs.slides, 1):
        texts = []
        for shape, _ in _inspected_shapes(slide.shapes):
            if getattr(shape, "has_text_frame", False):
                text = shape.text.strip()
                if text:
                    texts.append(text)
                if re.search(
                    r"lorem ipsum|\bTODO\b|\bXXX\b|вставьте текст", text, re.IGNORECASE
                ):
                    issues.append(
                        {
                            "severity": "warning",
                            "code": "placeholder_text",
                            "slide": number,
                            "shape": shape.name,
                            "message": "На слайде остался текст-заглушка",
                        }
                    )
            if getattr(shape, "has_table", False) and (
                len(shape.table.rows) > 7 or len(shape.table.columns) > 5
            ):
                issues.append(
                    {
                        "severity": "warning",
                        "code": "table_density",
                        "slide": number,
                        "shape": shape.name,
                        "message": "Таблица превышает 7 строк или 5 столбцов",
                    }
                )
            if getattr(shape, "has_chart", False) and len(shape.chart.series) > 5:
                issues.append(
                    {
                        "severity": "warning",
                        "code": "chart_density",
                        "slide": number,
                        "shape": shape.name,
                        "message": "В диаграмме больше 5 рядов",
                    }
                )
        fingerprint = "\n".join(texts)
        if fingerprint and fingerprint in seen:
            issues.append(
                {
                    "severity": "warning",
                    "code": "duplicate_slide",
                    "slide": number,
                    "message": f"Текст повторяет слайд {seen[fingerprint]}",
                }
            )
        seen[fingerprint] = number
        spec = plan["slides"][number - 1]
        bullets = spec.get("bullets", [])
        if len(bullets) > 6 or any(len(b.split()) > 15 for b in bullets):
            issues.append(
                {
                    "severity": "warning",
                    "code": "bullet_density",
                    "slide": number,
                    "message": "Больше 6 тезисов или тезис длиннее 15 слов; требуется редактура",
                }
            )
    for index, issue in enumerate(issues):
        number = issue.get("slide")
        bounds = []
        if isinstance(number, int) and 1 <= number <= len(prs.slides):
            names = str(issue.get("shape", "")).split(" / ")
            for shape, _ in _inspected_shapes(prs.slides[number - 1].shapes):
                if shape.name in names:
                    bounds.append(
                        {
                            key: round(value / 914400, 4)
                            for key, value in zip(
                                ("x", "y", "w", "h"),
                                (shape.left, shape.top, shape.width, shape.height),
                            )
                        }
                    )
        identity = json.dumps(
            [number, issue.get("code"), issue.get("shape"), index], sort_keys=True
        )
        issue["id"] = hashlib.sha256(identity.encode()).hexdigest()[:16]
        issue["check_type"] = "deterministic"
        issue["bounds"] = bounds
        issue["repairable"] = bool(
            isinstance(number, int) and issue["code"] in REPAIRABLE
        )
    errors = sum(i["severity"] == "error" for i in issues)
    warnings = sum(i["severity"] == "warning" for i in issues)
    report["status"] = "failed" if errors else "warning" if warnings else "passed"
    report["score"] = min(report["score"], max(0, 100 - 18 * errors - 4 * warnings))
    report["schema_version"] = "2.0"
    return report
