from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Any

from pptx import Presentation

from .llm import InferenceClient, InferenceError

PLANNER_SYSTEM = """You are an expert presentation content architect.
Turn the supplied source into a concise, evidence-faithful slide deck.
Return one JSON object with: language, title, slides.
Each slide must have: title, role, subtitle, body, bullets, visual, speaker_notes.
Allowed roles: cover, section, content, two_column, comparison, data, image, closing.
visual is null or an object. Supported visual types:
- metric_cards: {type, items:[{label,value}]}
- bar_chart: {type, categories:[...], series:[{name,values:[numbers]}]}
- pie_chart: {type, categories:[...], values:[numbers]}
- table: {type, headers:[...], rows:[[...]]}
- timeline: {type, items:[{label,detail}]}
Use only facts and numbers present in the source. Do not invent evidence.
Keep titles under 80 characters, at most 5 bullets per slide, and at most 110 characters per bullet.
The first slide must be cover and the final slide closing. Return JSON only."""


def load_content(value: str | Path) -> tuple[str, str]:
    """Return ``(content, source_label)`` from a path or inline text."""
    candidate = Path(str(value)).expanduser()
    try:
        exists = candidate.is_file()
    except OSError:
        exists = False
    if not exists:
        return str(value).strip(), "inline"

    suffix = candidate.suffix.lower()
    if suffix in {".md", ".txt", ".html"}:
        return candidate.read_text(encoding="utf-8"), candidate.name
    if suffix == ".json":
        data = json.loads(candidate.read_text(encoding="utf-8"))
        return json.dumps(data, ensure_ascii=False, indent=2), candidate.name
    if suffix == ".csv":
        with candidate.open("r", encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.reader(stream))
        return "\n".join(" | ".join(row) for row in rows), candidate.name
    if suffix == ".pptx":
        prs = Presentation(candidate)
        blocks = []
        for index, slide in enumerate(prs.slides, 1):
            texts = [
                shape.text.strip()
                for shape in slide.shapes
                if hasattr(shape, "text") and shape.text.strip()
            ]
            blocks.append(f"## Slide {index}\n" + "\n".join(texts))
        return "\n\n".join(blocks), candidate.name

    try:
        from markitdown import MarkItDown

        result = MarkItDown().convert(str(candidate))
        return result.text_content, candidate.name
    except ImportError as exc:
        raise ValueError(
            f"{suffix or 'This'} document type requires the optional 'documents' dependencies"
        ) from exc


def _detect_russian(text: str) -> bool:
    cyrillic = len(re.findall(r"[А-Яа-яЁё]", text))
    latin = len(re.findall(r"[A-Za-z]", text))
    return cyrillic > latin


def _sentences(text: str) -> list[str]:
    cleaned = re.sub(r"\s+", " ", text).strip()
    if not cleaned:
        return []
    parts = re.split(r"(?<=[.!?])\s+|\s*[;•]\s*", cleaned)
    return [part.strip(" -\t") for part in parts if part.strip(" -\t")]


def _sections(text: str) -> list[tuple[str, list[str]]]:
    sections: list[tuple[str, list[str]]] = []
    title = ""
    content: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        heading = re.match(r"^#{1,6}\s+(.+)$", line)
        if heading:
            if title or content:
                sections.append((title, content))
            title, content = heading.group(1).strip(), []
            continue
        if line:
            content.append(re.sub(r"^[-*+]\s+", "", line))
    if title or content:
        sections.append((title, content))
    return sections


def _fallback_plan(text: str, slide_count: int | None) -> dict[str, Any]:
    russian = _detect_russian(text)
    sections = _sections(text)
    if not sections:
        sections = [("", _sentences(text))]

    first_title = next((title for title, _ in sections if title), "")
    if not first_title:
        words = re.sub(r"\s+", " ", text).strip().split()
        first_title = " ".join(words[:8]) or (
            "Презентация" if russian else "Presentation"
        )

    desired = max(3, slide_count or min(10, max(5, len(sections) + 2)))
    content_slots = desired - 2
    material: list[tuple[str, list[str]]] = []
    for index, (heading, lines) in enumerate(sections):
        if index == 0 and heading == first_title and len(sections) > 1:
            continue
        bullets = []
        for line in lines:
            bullets.extend(_sentences(line) or [line])
        material.append((heading, bullets))
    if not material:
        material = [("", _sentences(text))]

    while len(material) < content_slots:
        split_at = max(range(len(material)), key=lambda idx: len(material[idx][1]))
        heading, bullets = material[split_at]
        if len(bullets) < 2:
            break
        midpoint = max(1, len(bullets) // 2)
        material[split_at : split_at + 1] = [
            (heading, bullets[:midpoint]),
            (heading, bullets[midpoint:]),
        ]

    slides: list[dict[str, Any]] = [
        {
            "title": first_title[:80],
            "role": "cover",
            "subtitle": "Автоматически создано по исходным материалам"
            if russian
            else "Generated from the supplied source",
            "body": "",
            "bullets": [],
            "visual": None,
            "speaker_notes": "",
        }
    ]
    for index, (heading, bullets) in enumerate(material[:content_slots], 1):
        default_title = f"Раздел {index}" if russian else f"Section {index}"
        slide_bullets = [item[:110] for item in bullets[:5]]
        slides.append(
            {
                "title": (heading or default_title)[:80],
                "role": "content",
                "subtitle": "",
                "body": "",
                "bullets": slide_bullets,
                "visual": None,
                "speaker_notes": "",
            }
        )
    while len(slides) < desired - 1:
        slides.append(
            {
                "title": "Ключевые выводы" if russian else "Key takeaways",
                "role": "content",
                "subtitle": "",
                "body": "",
                "bullets": [],
                "visual": None,
                "speaker_notes": "",
            }
        )
    slides.append(
        {
            "title": "Спасибо" if russian else "Thank you",
            "role": "closing",
            "subtitle": "Вопросы?" if russian else "Questions?",
            "body": "",
            "bullets": [],
            "visual": None,
            "speaker_notes": "",
        }
    )
    return {
        "language": "ru" if russian else "en",
        "title": first_title,
        "slides": slides,
    }


def _sanitize_visual(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    supported = {"metric_cards", "bar_chart", "pie_chart", "table", "timeline"}
    if value.get("type") not in supported:
        return None
    return value


def normalize_plan(
    plan: dict[str, Any], *, source_text: str, slide_count: int | None
) -> dict[str, Any]:
    raw_slides = plan.get("slides")
    if not isinstance(raw_slides, list) or not raw_slides:
        return _fallback_plan(source_text, slide_count)

    allowed_roles = {
        "cover",
        "section",
        "content",
        "two_column",
        "comparison",
        "data",
        "image",
        "closing",
    }
    slides: list[dict[str, Any]] = []
    for raw in raw_slides:
        if not isinstance(raw, dict):
            continue
        title = str(raw.get("title") or "Untitled").strip()[:80]
        bullets = raw.get("bullets") if isinstance(raw.get("bullets"), list) else []
        bullets = [str(item).strip()[:110] for item in bullets if str(item).strip()][:5]
        role = str(raw.get("role", "content")).lower()
        slides.append(
            {
                "title": title,
                "role": role if role in allowed_roles else "content",
                "subtitle": str(raw.get("subtitle") or "").strip()[:180],
                "body": str(raw.get("body") or "").strip()[:700],
                "bullets": bullets,
                "visual": _sanitize_visual(raw.get("visual")),
                "speaker_notes": str(raw.get("speaker_notes") or "").strip()[:1000],
            }
        )

    if len(slides) < 2:
        return _fallback_plan(source_text, slide_count)
    slides[0]["role"] = "cover"
    slides[-1]["role"] = "closing"
    if slide_count:
        if len(slides) > slide_count:
            slides = slides[: max(1, slide_count - 1)] + [slides[-1]]
        while len(slides) < slide_count:
            slides.insert(
                -1,
                {
                    "title": "Key takeaways",
                    "role": "content",
                    "subtitle": "",
                    "body": "",
                    "bullets": [],
                    "visual": None,
                    "speaker_notes": "",
                },
            )
    return {
        "language": str(
            plan.get("language") or ("ru" if _detect_russian(source_text) else "en")
        ),
        "title": str(plan.get("title") or slides[0]["title"]),
        "slides": slides,
    }


def plan_deck(
    content: str,
    *,
    pattern_catalog: dict[str, Any],
    design_system: dict[str, Any],
    slide_count: int | None = None,
    client: InferenceClient | None = None,
) -> dict[str, Any]:
    if client is None:
        plan = _fallback_plan(content, slide_count)
        planner_mode = "offline"
    else:
        available = [
            {
                "id": item["id"],
                "name": item["name"],
                "roles": item["roles"],
                "capacity": item["capacity"],
            }
            for item in pattern_catalog.get("patterns", [])
        ]
        user = json.dumps(
            {
                "requested_slide_count": slide_count,
                "available_template_patterns": available,
                "design_tone": {
                    "font": design_system.get("typography", {}).get("primary_font"),
                    "palette": design_system.get("colors", {}).get("theme", []),
                },
                "source": content,
            },
            ensure_ascii=False,
        )
        try:
            plan = client.chat_json(system=PLANNER_SYSTEM, user=user, max_tokens=6000)
            planner_mode = "inference"
        except InferenceError:
            plan = _fallback_plan(content, slide_count)
            planner_mode = "fallback"
    if planner_mode == "inference":
        raw_slides = plan.get("slides") if isinstance(plan, dict) else None
        if (
            not isinstance(raw_slides, list)
            or sum(isinstance(item, dict) for item in raw_slides) < 2
        ):
            planner_mode = "fallback"
    normalized = normalize_plan(plan, source_text=content, slide_count=slide_count)
    normalized["planner"] = {"mode": planner_mode}
    return normalized


def _slide_requirements(slide: dict[str, Any]) -> dict[str, Any]:
    visual = slide.get("visual") or {}
    body_chars = len(str(slide.get("body", ""))) + sum(
        len(str(item)) for item in slide.get("bullets", [])
    )
    role = str(slide.get("role", "content"))
    if role in {"comparison", "two_column"}:
        rhetorical_pattern = "two_column"
    elif role == "data" or visual.get("type") in {
        "bar_chart",
        "pie_chart",
        "table",
        "metric_cards",
    }:
        rhetorical_pattern = "data"
    elif len(slide.get("bullets", [])) >= 2:
        rhetorical_pattern = "bullet_list"
    elif role in {"cover", "closing", "section"}:
        rhetorical_pattern = "hero" if role == "cover" else role
    else:
        rhetorical_pattern = "content"
    return {
        "role": role,
        "title_chars": len(str(slide.get("title", ""))),
        "body_chars": body_chars,
        "bullet_count": len(slide.get("bullets", [])),
        "needs_data": rhetorical_pattern == "data",
        "needs_image": role == "image",
        "rhetorical_pattern": rhetorical_pattern,
        "has_visual": bool(visual),
    }


def _score_pattern(
    pattern: dict[str, Any],
    requirements: dict[str, Any],
    *,
    reuse_count: int,
    avoided: bool,
) -> tuple[float, list[str], list[str]]:
    roles = set(pattern.get("roles", []))
    capacity = pattern.get("capacity", {})
    role = requirements["role"]
    reasons: list[str] = []
    risks: list[str] = []
    score = 0.0

    if role in roles:
        score += 10.0
        reasons.append(f"роль {role} поддерживается")
    if role == "content" and roles.intersection({"cover", "closing", "section"}):
        score -= 5.0
    if role not in {"cover", "closing", "section"} and "closing" in roles:
        score -= 9.0
    if role != "cover" and "cover" in roles:
        score -= 6.0

    requested_pattern = requirements["rhetorical_pattern"]
    available_pattern = capacity.get("rhetorical_pattern", "content")
    compatible_patterns = {
        ("content", "bullet_list"),
        ("bullet_list", "content"),
        ("data", "multi_card"),
        ("two_column", "comparison"),
    }
    if requested_pattern == available_pattern:
        score += 5.0
        reasons.append(f"риторический паттерн {available_pattern}")
    elif (requested_pattern, available_pattern) in compatible_patterns:
        score += 2.0
    elif requested_pattern in {"data", "two_column"}:
        score -= 3.0
        risks.append(f"нужен {requested_pattern}, доступен {available_pattern}")

    title_capacity = max(1, int(capacity.get("title_chars", 80) or 80))
    if requirements["title_chars"] <= title_capacity:
        score += 1.0
    else:
        score -= min(5.0, (requirements["title_chars"] / title_capacity - 1) * 4)
        risks.append("заголовок длиннее визуальной ёмкости")

    body_capacity = max(1, int(capacity.get("body_chars", 500) or 500))
    if requirements["body_chars"]:
        ratio = requirements["body_chars"] / body_capacity
        if ratio <= 1:
            score += 3.0
            reasons.append("текст помещается по геометрической ёмкости")
        else:
            score -= min(10.0, (ratio - 1) * 7)
            risks.append(f"текст/ёмкость: {ratio:.1f}×")

    bullet_capacity = max(
        1,
        int(capacity.get("body_zones", 1) or 1)
        * int(capacity.get("bullets_per_zone", 4) or 4),
    )
    if requirements["bullet_count"]:
        if requirements["bullet_count"] <= bullet_capacity:
            score += 2.0
        else:
            score -= (requirements["bullet_count"] - bullet_capacity) * 1.5
            risks.append("слишком много тезисов для числа слотов")

    if requirements["needs_data"]:
        if capacity.get("supports_data"):
            score += 4.0
            reasons.append("есть структура для данных")
        else:
            score -= 4.0
            risks.append("нет нативной структуры для данных")
    if requirements["needs_image"]:
        score += 3.0 if capacity.get("supports_image") else -5.0
    elif not requirements["has_visual"]:
        content_image_area = float(capacity.get("content_image_area_ratio", 0) or 0)
        score -= content_image_area * 3.0
        if content_image_area >= 0.35:
            risks.append("крупное исходное изображение не связано с новым текстом")
    if requirements["has_visual"] and capacity.get("body_zones", 0) >= 1:
        score += 1.0

    complexity = float(capacity.get("complexity", 0) or 0)
    if (
        requested_pattern in {"content", "bullet_list"}
        and not requirements["has_visual"]
    ):
        score -= complexity * 4.0
        if complexity >= 0.75:
            risks.append("сложный декоративный exemplar для простого текста")

    if pattern.get("source_kind") == "slide_exemplar":
        score += 1.5
        reasons.append("проверенный exemplar-слайд")
    elif pattern.get("source_kind") == "native_grid":
        score += 4.0
        reasons.append("чистая нативная сетка без PDF-фрагментов")
    score += min(len(pattern.get("example_slide_indices", [])), 2) * 0.2
    reuse_penalty = 2.0 if pattern.get("source_kind") == "native_grid" else 0.02
    score -= reuse_count * reuse_penalty
    if avoided:
        score -= 50.0
        risks.append("паттерн исключён предыдущим QA")
    return score, reasons, risks


def assign_patterns(
    plan: dict[str, Any],
    pattern_catalog: dict[str, Any],
    *,
    avoid_by_slide: dict[int, set[str]] | None = None,
) -> dict[str, Any]:
    patterns = pattern_catalog.get("patterns", [])
    if not patterns:
        raise ValueError("Template has no usable slide layouts")
    usage: dict[str, int] = {}
    for slide_index, slide in enumerate(plan.get("slides", []), 1):
        requirements = _slide_requirements(slide)
        scored: list[tuple[float, dict[str, Any], list[str], list[str]]] = []
        for pattern in patterns:
            score, reasons, risks = _score_pattern(
                pattern,
                requirements,
                reuse_count=usage.get(pattern["id"], 0),
                avoided=pattern["id"] in (avoid_by_slide or {}).get(slide_index, set()),
            )
            scored.append((score, pattern, reasons, risks))
        scored.sort(key=lambda item: (-item[0], item[1]["id"]))
        selected_score, selected, reasons, risks = scored[0]
        slide["pattern_id"] = selected["id"]
        slide["layout_index"] = selected["layout_index"]
        slide["master_index"] = selected["master_index"]
        slide["pattern_selection"] = {
            "score": round(selected_score, 2),
            "layout_pattern": selected.get("capacity", {}).get(
                "rhetorical_pattern", "content"
            ),
            "why_fit": "; ".join(reasons[:4]) or "лучший доступный компромисс",
            "risk": "; ".join(risks[:3]) or "низкий",
            "alternatives": [
                {"pattern_id": item[1]["id"], "score": round(item[0], 2)}
                for item in scored[1:4]
            ],
        }
        usage[selected["id"]] = usage.get(selected["id"], 0) + 1
    return plan
