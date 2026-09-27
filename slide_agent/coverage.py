"""Conservative lexical coverage: unmatched paraphrases require human review."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from pptx import Presentation


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
            if key not in {"asset_id", "match", "match_score", "origin", "score"}
            for text in _strings(item)
        ]
    if isinstance(value, list):
        return [text for item in value for text in _strings(item)]
    return []


def visible_texts(slide: dict) -> list[str]:
    """Texts a slide shows; speaker notes are not visible."""
    return [
        str(text)
        for text in (
            slide.get("title", ""),
            slide.get("subtitle", ""),
            slide.get("body", ""),
            *slide.get("bullets", []),
            *_strings(slide.get("visual") or {}),
        )
    ]


_BUDGET = re.compile(r"\b(?:бюджет\w*|финансирован\w*|budget|funding)\b", re.IGNORECASE)
_BUDGET_APPROVAL = re.compile(
    r"\b(?:утверд\w*|одобр\w*|соглас\w*|выдел\w*|approve\w*|allocat\w*)\b",
    re.IGNORECASE,
)
_BUDGET_UNSPECIFIED = re.compile(
    r"\b(?:бюджет\w*|budget|funding)\b.{0,45}"
    r"(?:не указан\w*|не определ\w*|предстоит уточн\w*|not specified|unknown)",
    re.IGNORECASE,
)
_SECURITY = re.compile(
    r"\b(?:утеч\w*|риск\w*|безопасност\w*|защит\w*|leak\w*|risk\w*|security)\b",
    re.IGNORECASE,
)
_ABSOLUTE = re.compile(
    r"\b(?:исключ\w*|невозможн\w*|нулев\w*|полност\w*|гарантир\w*|"
    r"абсолют\w*|zero|eliminat\w*|guarantee\w*|impossible|never)\b|100\s*%",
    re.IGNORECASE,
)
_SELF_SERVICE = re.compile(
    r"\b(?:дол\w*|процент\w*|числ\w*|количеств\w*|share|rate|number)\b"
    r".{0,100}?\b(?:обращени\w*|вопрос\w*|запрос\w*|tickets?|requests?)\b"
    r".{0,100}?\b(?:без\s+(?:участи\w*\s+|привлечени\w*\s+)?специалист\w*|"
    r"without\s+(?:a\s+)?(?:specialist|agent|human))\b",
    re.IGNORECASE,
)
_DIRECTION = re.compile(
    r"\b(?P<down>сниж\w*|сократ\w*|уменьш\w*|падени\w*|"
    r"decreas\w*|reduc\w*|lower\w*)\b|"
    r"\b(?P<up>рост\w*|увелич\w*|повыш\w*|increas\w*|rais\w*)\b|"
    r"\b(?P<neutral>измер\w*|отслеж\w*|контрол\w*|оцен\w*|measure\w*|track\w*)\b",
    re.IGNORECASE,
)


def _decreases_self_service(text: str) -> bool:
    for metric in _SELF_SERVICE.finditer(text):
        prefix = text[max(0, metric.start() - 45) : metric.start()]
        directions = list(_DIRECTION.finditer(prefix))
        if not directions or directions[-1].lastgroup != "down":
            continue
        before = prefix[: directions[-1].start()]
        if re.search(r"(?:\bне\s+|\bnot\s+)$", before, re.IGNORECASE):
            continue
        return True
    return False


def grounding_findings(source: str, plan: dict) -> list[dict]:
    """High-confidence, exact-quote findings for risky brief extrapolations.

    These rules are deliberately narrow. They catch unsupported budget scope,
    absolute security promises and reversal of a self-service success metric;
    they do not certify the other claims in a presentation.
    """
    source_budget = bool(_BUDGET.search(source))
    source_budget_approval = any(
        _BUDGET.search(part) and _BUDGET_APPROVAL.search(part)
        for part in re.split(r"[.!?\n]", source)
    )
    source_decreases_self_service = _decreases_self_service(source)
    findings = []
    seen = set()
    for number, slide in enumerate(plan.get("slides", []), 1):
        for text in visible_texts(slide):
            if not text.strip() or _normalized(text) in _normalized(source):
                continue
            reasons = []
            if _BUDGET.search(text) and not _BUDGET_UNSPECIFIED.search(text):
                if not source_budget:
                    reasons.append("В брифе нет бюджета: не добавляйте его в решение или запрос на одобрение.")
                elif _BUDGET_APPROVAL.search(text) and not source_budget_approval:
                    reasons.append("Бриф не запрашивает утверждение бюджета.")
            if _SECURITY.search(text) and _ABSOLUTE.search(text):
                reasons.append("Абсолютная гарантия безопасности не следует из брифа; используйте его точную формулировку.")
            if _decreases_self_service(text) and not source_decreases_self_service:
                reasons.append("Уменьшение доли обращений без специалиста меняет смысл метрики успеха.")
            for reason in reasons:
                key = (number, text, reason)
                if key in seen:
                    continue
                seen.add(key)
                findings.append(
                    {"slide": number, "code": "unsupported_claim", "quote": text[:160], "reason": reason}
                )
    return findings


def unsupported_numbers(source: str, plan: dict) -> list[dict]:
    """Visible numbers of the plan that the source never states.

    Integers below 10 are skipped: they usually count the slide's own items.
    Speaker notes are not visible and are not checked.
    """
    known = _numbers(source)
    result = []
    for number, slide in enumerate(plan.get("slides", []), 1):
        texts = visible_texts(slide)
        missing = sorted(
            value
            for value in _numbers("\n".join(texts)) - known
            if "." in value or float(value) >= 10
        )
        if missing:
            result.append({"slide": number, "numbers": missing})
    return result


def coverage_report(source: str, plan: dict, output: Path | None = None) -> dict:
    # Imported here because brief planning also uses grounding_findings.
    from .planner import _sections, _sentences

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
        "grounding_findings": grounding_findings(source, plan),
        "note": "Unmatched paraphrases are not proof of omission; speaker notes do not count as slide content.",
    }
