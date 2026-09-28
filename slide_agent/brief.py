"""Deck structure and content from a short brief and its purpose.

Two model stages, both with grammar-constrained JSON (``response_format`` with a
JSON schema, supported by llama.cpp and vLLM):

1. outline — the exact number of slides, their order, role, goal and visual
   type, following the narrative beats of the purpose in
   ``assets/deck_purposes.json`` (feature, product, project, initiative);
2. slide content — every slide is written separately against the fixed
   outline, in parallel when the inference server has several slots.

The model may only use facts from the brief; numbers on slides are checked
deterministically against the brief after planning (``coverage.py``).
"""

from __future__ import annotations

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from pathlib import Path
from typing import Any

from .coverage import (
    grounding_findings,
    source_backed_baseline_fallback,
    source_backed_kpi_fallback,
    source_backed_pilot_status_fallback,
    unsupported_numbers,
)
from .diagrams import ITEM_LIMITS
from .images import stems
from .llm import InferenceClient, InferenceError
from .prompt_config import load_prompt

PURPOSES_PATH = Path(__file__).with_name("assets") / "deck_purposes.json"
DEFAULT_SLIDE_COUNT = 12
MAX_DIAGRAM_ITEMS = 5
# Word limits of the slide prompt, checked after generation: a longer answer
# is sent back once with the list of problems.
LIMITS = {"bullet": 15, "label": 5, "detail": 16, "cell": 8}
MODES = ("auto", "source", "brief")
DIAGRAM_VISUALS = (
    "process",
    "icon_grid",
    "cycle",
    "funnel",
    "pyramid",
    "hierarchy",
    "matrix",
)
_PURPOSE_HINTS = {
    "feature": ("фича", "фичу", "функци", "feature"),
    "product": ("продукт", "product"),
    "project": ("проект", "project"),
    "initiative": ("инициатив", "initiative", "предлага", "proposal"),
}


@lru_cache(maxsize=1)
def purpose_config() -> dict[str, Any]:
    return json.loads(PURPOSES_PATH.read_text(encoding="utf-8"))


def purposes() -> tuple[str, ...]:
    return tuple(purpose_config()["purposes"])


def detect_purpose(brief: str) -> str:
    """Purpose named in the brief ("Назначение: фича"), else keyword counts."""
    lowered = brief.casefold()
    stated = re.search(r"(?:назначение|purpose)\s*[:—-]\s*(\w+)", lowered)
    if stated:
        for purpose, hints in _PURPOSE_HINTS.items():
            if stated.group(1).startswith(hints):
                return purpose
    counts = {
        purpose: sum(lowered.count(hint) for hint in hints)
        for purpose, hints in _PURPOSE_HINTS.items()
    }
    best = max(counts, key=counts.get)
    return best if counts[best] else "project"


def use_brief_mode(content: str, mode: str, client: InferenceClient | None) -> bool:
    if mode not in MODES:
        raise ValueError(
            f"Unknown planning mode: {mode}; use one of {', '.join(MODES)}"
        )
    if mode == "source":
        return False
    if mode == "brief":
        if client is None:
            raise ValueError(
                "Generation from a brief needs a model: set INFERENCE_BASE_URL and "
                "INFERENCE_MODEL, or provide full source material"
            )
        return True
    if client is None:
        return False
    headings = len(re.findall(r"^#{1,6}\s", content, re.MULTILINE))
    limit = int(purpose_config().get("auto_brief_max_chars", 1500))
    return len(content.strip()) <= limit and headings < 3


def _brief_visuals(
    brief: str, image_catalog: list[dict[str, str]] | None = None
) -> list[str]:
    # Only visuals the diagram engine sizes and contrast-checks itself. The
    # legacy timeline and metric cards follow the layout zone and came out
    # tiny or on top of template decoration in the 3-template run, so dated
    # stages go to process and key numbers to icon_grid labels.
    return ["none", *DIAGRAM_VISUALS, "table", *(["image"] if image_catalog else [])]


def _assign_outline_images(
    slides: list[dict[str, Any]], image_catalog: list[dict[str, str]]
) -> list[dict[str, Any]]:
    """Reserve relevant interior slides before the model writes their text.

    A picture replaces an outline visual only when its label or surrounding
    document text overlaps the slide's message. The slide writer then produces
    bullets for that picture instead of diagram items that would be lost.
    """
    pairs: list[tuple[float, str, int]] = []
    for asset in image_catalog:
        asset_id = str(asset.get("id", ""))
        terms = stems(f"{asset.get('label', '')} {asset.get('context', '')}")
        if not asset_id or not terms:
            continue
        for index, slide in enumerate(slides[1:-1], 1):
            title = stems(slide["title"])
            goal = stems(slide["goal"])
            overlap = terms & (title | goal)
            if not overlap:
                continue
            score = 2 * len(terms & title) + len(terms & (goal - title))
            # Prefer the message over a generic mention in a long goal.
            pairs.append((float(score), asset_id, index))
    used_assets: set[str] = set()
    used_slides: set[int] = set()
    for _score, asset_id, index in sorted(
        pairs, key=lambda pair: (-pair[0], pair[1], pair[2])
    ):
        if asset_id in used_assets or index in used_slides:
            continue
        slides[index]["visual"] = "image"
        slides[index]["asset_id"] = asset_id
        used_assets.add(asset_id)
        used_slides.add(index)
    # An unmatched image choice has no asset and must remain a text slide.
    for slide in slides:
        if slide["visual"] == "image" and not slide.get("asset_id"):
            slide["visual"] = "none"
    return slides


def _text(limit: int) -> dict[str, Any]:
    # A safety cap, not the target length: a grammar cut ends text mid-word,
    # so the prompt asks for shorter text and the audit checks density.
    return {"type": "string", "maxLength": limit}


def _object(properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def outline_schema(count: int, visuals: list[str]) -> dict[str, Any]:
    slide = _object(
        {
            "title": _text(120),
            "role": {"enum": ["cover", "content", "comparison", "data", "closing"]},
            "goal": _text(300),
            "visual": {"enum": visuals},
        }
    )
    return _object(
        {
            "language": {"enum": ["ru", "en"]},
            "title": _text(120),
            "subtitle": _text(200),
            "slides": {
                "type": "array",
                "items": slide,
                "minItems": count,
                "maxItems": count,
            },
        }
    )


def _visual_schema(kind: str) -> dict[str, Any] | None:
    if kind in DIAGRAM_VISUALS:
        minimum, maximum = ITEM_LIMITS.get(kind, (2, 6))
        # Five items keep a generated diagram readable in any template zone.
        maximum = min(maximum, MAX_DIAGRAM_ITEMS)
        item = _object({"label": _text(60), "detail": _text(200)})
        properties: dict[str, Any] = {
            "type": {"enum": [kind]},
            "items": {
                "type": "array",
                "items": item,
                "minItems": minimum,
                "maxItems": maximum,
            },
        }
        if kind == "hierarchy":
            properties["root"] = _text(60)
        if kind == "matrix":
            properties["axes"] = _object({"x": _text(60), "y": _text(60)})
        return _object(properties)
    if kind == "table":
        cells = {"type": "array", "items": _text(80), "minItems": 2, "maxItems": 5}
        return _object(
            {
                "type": {"enum": [kind]},
                "headers": cells,
                "rows": {"type": "array", "items": cells, "minItems": 2, "maxItems": 5},
            }
        )
    return None


def slide_schema(role: str, visual: str) -> dict[str, Any]:
    visual_schema = None if role in {"cover", "closing"} else _visual_schema(visual)
    if role == "cover":
        bullets = (0, 0)
    elif role == "closing":
        bullets = (0, 3)
    elif visual_schema is not None:
        bullets = (0, 0)
    else:
        bullets = (2, 4)
    properties: dict[str, Any] = {
        "subtitle": _text(200),
        "bullets": {
            "type": "array",
            "items": _text(200),
            "minItems": bullets[0],
            "maxItems": bullets[1],
        },
        "speaker_notes": _text(400),
    }
    if visual_schema is not None:
        properties["visual"] = visual_schema
    return _object(properties)


def _normalized_outline(outline: dict[str, Any], count: int) -> list[dict[str, Any]]:
    raw = [item for item in outline.get("slides", []) if isinstance(item, dict)]
    if len(raw) < count:
        raise InferenceError(
            f"Outline has {len(raw)} slides instead of the requested {count}"
        )
    if len(raw) > count:
        # A server without schema support may overshoot: keep cover and closing.
        raw = raw[: count - 1] + raw[-1:]
    slides = []
    for index, item in enumerate(raw):
        role = str(item.get("role") or "content")
        if index == 0:
            role = "cover"
        elif index == len(raw) - 1:
            role = "closing"
        elif role not in {"content", "comparison", "data"}:
            role = "content"
        visual = str(item.get("visual") or "none")
        slides.append(
            {
                "title": str(item.get("title") or "").strip(),
                "role": role,
                "goal": str(item.get("goal") or "").strip(),
                "visual": "none" if role in {"cover", "closing"} else visual,
            }
        )
    return slides


def _outline_problems(slides: list[dict[str, Any]], brief: str = "") -> list[str]:
    problems = []
    seen: dict[str, int] = {}
    for number, slide in enumerate(slides, 1):
        key = re.sub(r"\W+", " ", slide["title"]).strip().casefold()
        if not key:
            problems.append(f"slide {number} has no title")
        elif key in seen:
            problems.append(
                f"slides {seen[key]} and {number} share the title {slide['title']!r}"
            )
        else:
            seen[key] = number
    goals = [slide["goal"].casefold() for slide in slides[1:-1]]
    if goals and len(set(goals)) < len(goals):
        problems.append("goals repeat: every slide needs its own message")
    if brief:
        outline_plan = {"slides": [
            {"title": slide["title"], "subtitle": slide["goal"]}
            for slide in slides
        ]}
        problems.extend(
            f"slide {issue['slide']} is not grounded in the brief: "
            f"{issue['quote']!r} ({issue['reason']})"
            for issue in grounding_findings(brief, outline_plan)
        )
        problems.extend(
            f"slide {issue['slide']} uses numbers absent from the brief: "
            f"{', '.join(issue['numbers'])}"
            for issue in unsupported_numbers(brief, outline_plan)
        )
    return problems


def _filled(item: Any) -> bool:
    return isinstance(item, dict) and any(
        str(item.get(key) or "").strip() for key in ("label", "detail", "value")
    )


def _usable_visual(visual: Any, kind: str) -> dict[str, Any] | None:
    """The visual without empty items, or None when too little is left to draw."""
    if not isinstance(visual, dict):
        return None
    if kind == "table":
        headers = [str(cell).strip() for cell in visual.get("headers") or []]
        rows = [
            [str(cell).strip() for cell in row]
            for row in visual.get("rows") or []
            if isinstance(row, list) and any(str(cell).strip() for cell in row)
        ]
        if sum(bool(cell) for cell in headers) < 2 or not rows:
            return None
        return {**visual, "headers": headers, "rows": rows}
    items = [item for item in visual.get("items") or [] if _filled(item)]
    minimum = ITEM_LIMITS.get(kind, (2, 6))[0]
    return {**visual, "items": items} if len(items) >= minimum else None


def _visual_bullets(visual: Any) -> list[str]:
    if not isinstance(visual, dict):
        return []
    lines = []
    for item in visual.get("items") or []:
        if _filled(item):
            parts = [str(item.get(key) or "").strip() for key in ("label", "detail")]
            lines.append(" — ".join(part for part in parts if part))
    return lines


def _content_problems(
    content: dict[str, Any], slide: dict[str, Any], brief: str = ""
) -> list[str]:
    def long(text: Any, limit: int) -> bool:
        return len(str(text or "").split()) > limit

    problems = []
    framed = slide["role"] in {"cover", "closing"}
    expects_visual = not framed and _visual_schema(slide["visual"]) is not None
    if (
        expects_visual
        and _usable_visual(content.get("visual"), slide["visual"]) is None
    ):
        problems.append(
            f"visual {slide['visual']} has empty or too few filled items; fill every item"
        )
    bullets = [str(b).strip() for b in content.get("bullets") or [] if str(b).strip()]
    if (
        slide["role"] not in {"cover", "closing"}
        and not expects_visual
        and len(bullets) < 2
    ):
        problems.append("the slide needs at least 2 non-empty bullets")
    problems += [
        f"bullet longer than {LIMITS['bullet']} words: {text!r}"
        for text in content.get("bullets") or []
        if long(text, LIMITS["bullet"])
    ]
    visual = content.get("visual") if isinstance(content.get("visual"), dict) else {}
    for item in visual.get("items") or []:
        if not isinstance(item, dict):
            continue
        if long(item.get("label"), LIMITS["label"]):
            problems.append(
                f"label longer than {LIMITS['label']} words: {item['label']!r}"
            )
        if long(item.get("detail"), LIMITS["detail"]):
            problems.append(
                f"detail longer than {LIMITS['detail']} words: {item['detail']!r}"
            )
    cells = [*(visual.get("headers") or [])]
    for row in visual.get("rows") or []:
        cells.extend(row if isinstance(row, list) else [])
    problems.extend(
        f"table cell longer than {LIMITS['cell']} words: {cell!r}"
        for cell in cells
        if long(cell, LIMITS["cell"])
    )
    if brief:
        payload = _slide_payload(content, slide)
        problems.extend(
            f"unsupported claim {issue['quote']!r}: {issue['reason']}"
            for issue in grounding_findings(brief, {"slides": [payload]})
        )
        problems.extend(
            "numbers absent from the brief: " + ", ".join(issue["numbers"])
            for issue in unsupported_numbers(
                brief, {"slides": [payload]}, include_notes=True
            )
        )
    return problems


def _slide_payload(
    content: dict[str, Any], outline_slide: dict[str, Any]
) -> dict[str, Any]:
    raw = content.get("bullets") if isinstance(content.get("bullets"), list) else []
    bullets = [str(item).strip() for item in raw if str(item).strip()]
    visual = (
        {"type": "image", "asset_id": outline_slide["asset_id"]}
        if outline_slide.get("asset_id")
        else _usable_visual(content.get("visual"), outline_slide["visual"])
    )
    if outline_slide["role"] not in {"cover", "closing"} and not visual and not bullets:
        # Never leave a content slide with its title only.
        bullets = _visual_bullets(content.get("visual"))[:5] or [outline_slide["goal"]]
    return {
        "title": outline_slide["title"],
        "role": outline_slide["role"],
        "subtitle": str(content.get("subtitle") or "").strip(),
        "body": "",
        "bullets": bullets,
        "visual": visual,
        "speaker_notes": str(content.get("speaker_notes") or "").strip(),
    }


EDITORIAL_CODES = {"unsupported_claim", "repeated_message"}


def _ensure_grounded(brief: str, plan: dict[str, Any]) -> None:
    """Do not compose a brief deck with known unsupported claims."""
    risks = grounding_findings(brief, plan)
    if risks:
        first = risks[0]
        raise InferenceError(
            f"Brief deck has {len(risks)} unresolved grounding issue(s); "
            f"slide {first['slide']}: {first['quote']!r}. {first['reason']}"
        )
    missing_numbers = unsupported_numbers(brief, plan, include_notes=True)
    if missing_numbers:
        first = missing_numbers[0]
        raise InferenceError(
            f"Brief deck has numbers absent from source on slide {first['slide']}: "
            + ", ".join(first["numbers"])
        )


def revise_flagged_slides(
    plan: dict[str, Any],
    *,
    brief: str,
    client: InferenceClient,
    audit: dict[str, Any],
) -> list[int]:
    """Rewrite flagged slides, retrying once if a grounded rule still fails.

    Each attempt gets the previous text and exact-quote findings, including
    notes. A title finding opens the title for correction. Image placements
    stay. An unresolved deterministic claim blocks the deck before composition.
    """
    outline = (plan.get("brief") or {}).get("outline") or []
    if audit.get("status") != "reviewed" or len(outline) != len(plan.get("slides", [])):
        _ensure_grounded(brief, plan)
        return []
    findings: dict[int, list[dict[str, Any]]] = {}
    for issue in audit.get("issues") or []:
        if issue.get("code") in EDITORIAL_CODES:
            findings.setdefault(int(issue["slide"]), []).append(issue)
    if not findings:
        _ensure_grounded(brief, plan)
        return []
    system = load_prompt("brief-slide")
    titles = [str(slide.get("title", "")) for slide in plan["slides"]]

    def rewrite(number: int) -> tuple[int, dict[str, Any] | None, list[str]]:
        original = plan["slides"][number - 1]
        current = original
        issues = findings[number]
        for _ in range(2):
            outline_slide = {**outline[number - 1], "title": current.get("title", "")}
            schema = slide_schema(outline_slide["role"], outline_slide["visual"])
            if any(issue["quote"] in str(current.get("title", "")) for issue in issues):
                schema["properties"]["title"] = _text(120)
                schema["required"].append("title")
            request = {
                "brief": brief,
                "purpose": plan["brief"].get("purpose"),
                "deck_title": plan.get("title"),
                "outline": titles,
                "slide_number": number,
                "slide": outline_slide,
                "previous_answer": {
                    key: current.get(key)
                    for key in ("title", "subtitle", "bullets", "visual", "speaker_notes")
                },
                "fix_previous_answer": [
                    f"«{issue['quote']}»: {issue['reason']}" for issue in issues
                ],
            }
            try:
                content = client.chat_json(
                    system=system,
                    user=json.dumps(request, ensure_ascii=False),
                    schema=schema,
                    max_tokens=1200,
                    temperature=0.3,
                )
            except InferenceError:
                continue
            payload = _slide_payload(content, outline_slide)
            title = str(content.get("title") or "").strip()
            if title:
                payload["title"] = title
            if (original.get("visual") or {}).get("type") == "image":
                payload["visual"] = original["visual"]
            current = {**current, **payload}
            issues = grounding_findings(brief, {"slides": [current]})
            issues.extend(
                {
                    "quote": ", ".join(item["numbers"]),
                    "reason": "Чисел нет в брифе",
                }
                for item in unsupported_numbers(
                    brief, {"slides": [current]}, include_notes=True
                )
            )
            if not issues:
                return number, payload, []
        fallback = current
        used_fallbacks = []
        for name, repair in (
            ("kpi", source_backed_kpi_fallback),
            ("baseline", source_backed_baseline_fallback),
            ("pilot_status", source_backed_pilot_status_fallback),
        ):
            revised = repair(brief, fallback)
            if revised is not None:
                fallback = revised
                used_fallbacks.append(name)
        if (
            used_fallbacks
            and not grounding_findings(brief, {"slides": [fallback]})
            and not unsupported_numbers(
                brief, {"slides": [fallback]}, include_notes=True
            )
        ):
            return number, fallback, used_fallbacks
        return number, None, []

    workers = max(1, client.settings.parallel_requests)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(rewrite, sorted(findings)))
    revised = []
    fallbacks: dict[str, list[int]] = {"kpi": [], "baseline": [], "pilot_status": []}
    for number, payload, used_fallbacks in results:
        if payload is None:
            continue
        old_title = str(plan["slides"][number - 1].get("title") or "")
        plan["slides"][number - 1] = {**plan["slides"][number - 1], **payload}
        revised.append(number)
        if plan["slides"][number - 1].get("title") != old_title:
            outline[number - 1]["title"] = plan["slides"][number - 1]["title"]
            if number == 1 and plan.get("title") == old_title:
                plan["title"] = plan["slides"][number - 1]["title"]
        for name in used_fallbacks:
            fallbacks[name].append(number)
    if fallbacks["kpi"]:
        plan["brief"]["source_backed_kpi_fallbacks"] = fallbacks["kpi"]
    if fallbacks["baseline"]:
        plan["brief"]["source_backed_baseline_fallbacks"] = fallbacks["baseline"]
    if fallbacks["pilot_status"]:
        plan["brief"]["source_backed_pilot_status_fallbacks"] = fallbacks["pilot_status"]
    _ensure_grounded(brief, plan)
    return revised


def plan_from_brief(
    brief: str,
    *,
    client: InferenceClient,
    slide_count: int | None = None,
    purpose: str | None = None,
    image_catalog: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    config = purpose_config()["purposes"]
    purpose = purpose or detect_purpose(brief)
    if purpose not in config:
        raise ValueError(f"Unknown purpose: {purpose}; use one of {', '.join(config)}")
    count = slide_count or DEFAULT_SLIDE_COUNT
    visuals = _brief_visuals(brief, image_catalog)

    started = time.perf_counter()
    request: dict[str, Any] = {
        "brief": brief,
        "purpose": purpose,
        "purpose_beats": config[purpose]["beats"],
        "requested_slide_count": count,
        "visual_types": visuals,
    }
    if image_catalog:
        request["available_images"] = image_catalog[:20]
    problems: list[str] = []
    for _ in range(3):
        outline_raw = client.chat_json(
            system=load_prompt("brief-outline"),
            user=json.dumps(request, ensure_ascii=False),
            schema=outline_schema(count, visuals),
            max_tokens=3000,
            temperature=0.3,
        )
        outline = _normalized_outline(outline_raw, count)
        problems = _outline_problems(outline, brief)
        if not problems:
            break
        request["fix_previous_outline"] = problems
    outline_baseline_fallbacks = []
    if problems:
        for number, slide in enumerate(outline, 1):
            repaired = source_backed_baseline_fallback(brief, slide)
            if repaired is not None:
                outline[number - 1] = repaired
                outline_baseline_fallbacks.append(number)
        problems = _outline_problems(outline, brief)
    if problems:
        raise InferenceError(
            "Outline remains invalid after three attempts: "
            + "; ".join(problems[:3])
        )
    if image_catalog:
        outline = _assign_outline_images(outline, image_catalog)
    outline_seconds = time.perf_counter() - started
    deck_title = str(outline_raw.get("title") or outline[0]["title"]).strip()
    if source_backed_baseline_fallback(brief, {"title": deck_title}) is not None:
        deck_title = outline[0]["title"]
    titles = [slide["title"] for slide in outline]
    system = load_prompt("brief-slide")

    def write(index: int) -> tuple[dict[str, Any], str | None]:
        slide = outline[index]
        request = {
            "brief": brief,
            "purpose": purpose,
            "deck_title": deck_title,
            "outline": titles,
            "slide_number": index + 1,
            "slide": slide,
        }
        error = None
        best: dict[str, Any] | None = None
        for _ in range(2):
            try:
                content = client.chat_json(
                    system=system,
                    user=json.dumps(request, ensure_ascii=False),
                    schema=slide_schema(slide["role"], slide["visual"]),
                    max_tokens=1200,
                    temperature=0.3,
                )
            except InferenceError as exc:
                error = str(exc)[:300]
                continue
            best, error = content, None
            problems = _content_problems(content, slide, brief)
            if not problems:
                break
            request["fix_previous_answer"] = problems
        if best is not None:
            # Text still too long after the retry stays for the audit to flag.
            return _slide_payload(best, slide), None
        # The outline goal still carries the message of the slide.
        fallback = {"bullets": [slide["goal"]] if slide["role"] != "cover" else []}
        return _slide_payload(fallback, slide), error

    started_slides = time.perf_counter()
    workers = max(1, client.settings.parallel_requests)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        written = list(pool.map(write, range(len(outline))))
    slides = [slide for slide, _ in written]
    failures = [
        {"slide": index + 1, "error": error}
        for index, (_, error) in enumerate(written)
        if error
    ]
    if slides and not slides[0]["subtitle"]:
        slides[0]["subtitle"] = str(outline_raw.get("subtitle") or "").strip()
    return {
        "language": str(outline_raw.get("language") or "ru"),
        "title": deck_title,
        "slides": slides,
        "brief": {
            "purpose": purpose,
            "purpose_label": config[purpose]["label"],
            "slide_count": count,
            "outline": outline,
            "outline_problems": problems,
            "outline_source_backed_baseline_fallbacks": outline_baseline_fallbacks,
            "visual_types": visuals,
            "slide_failures": failures,
            "parallel_requests": workers,
            "timings_seconds": {
                "outline": round(outline_seconds, 2),
                "slides": round(time.perf_counter() - started_slides, 2),
            },
        },
    }
