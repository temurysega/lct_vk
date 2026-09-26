"""Conservative text-model review of source grounding and narrative repetition.

The result is a suggestion for editorial review, not a proof that a claim is
true. The renderer and geometric QA remain deterministic and independent.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

from .llm import InferenceClient, InferenceError
from .prompt_config import load_prompt

MAX_SOURCE_CHARS = 12000
MAX_ISSUES = 20
CODES = {"unsupported_claim", "repeated_message", "title_not_claim"}


def _slide_text(slide: dict[str, Any]) -> str:
    lines = [str(slide.get(key) or "") for key in ("title", "subtitle", "body")]
    lines.extend(str(item) for item in slide.get("bullets") or [])
    visual = slide.get("visual") or {}
    if isinstance(visual, dict):
        for item in visual.get("items") or []:
            if isinstance(item, dict):
                lines.extend(str(item.get(key) or "") for key in ("label", "detail", "value"))
            else:
                lines.append(str(item))
        lines.extend(str(item) for item in visual.get("headers") or [])
        for row in visual.get("rows") or []:
            lines.extend(str(item) for item in row)
    return "\n".join(line for line in lines if line.strip())


def _normalized(text: str) -> str:
    return re.sub(r"\s+", " ", text.casefold()).strip()


def review_content(
    source: str, plan: dict[str, Any], client: InferenceClient | None
) -> dict[str, Any]:
    """Review one shared content plan before its three layout variants."""
    if client is None or os.getenv("INFERENCE_CONTEXT_AUDIT", "0") != "1":
        return {"status": "not_run", "reason": "No configured model or audit disabled"}
    if len(source) > MAX_SOURCE_CHARS:
        return {
            "status": "not_run",
            "reason": "Source exceeds the text audit context limit; manual review required",
        }
    slides = plan.get("slides") or []
    texts = [_slide_text(slide) for slide in slides]
    payload = {
        "source": source,
        "slides": [
            {"number": number, "role": slide.get("role"), "text": text}
            for number, (slide, text) in enumerate(zip(slides, texts, strict=True), 1)
        ],
    }
    issue = {
        "type": "object",
        "properties": {
            "slide": {"type": "integer", "minimum": 1, "maximum": len(slides)},
            "code": {"enum": sorted(CODES)},
            "quote": {"type": "string", "maxLength": 160},
            "reason": {"type": "string", "maxLength": 200},
        },
        "required": ["slide", "code", "quote", "reason"],
        "additionalProperties": False,
    }
    schema = {
        "type": "object",
        "properties": {
            "issues": {"type": "array", "items": issue, "maxItems": MAX_ISSUES}
        },
        "required": ["issues"],
        "additionalProperties": False,
    }
    try:
        result = client.chat_json(
            system=load_prompt("context-audit"),
            user=json.dumps(payload, ensure_ascii=False),
            schema=schema,
            max_tokens=1800,
            temperature=0.1,
        )
    except (InferenceError, ValueError) as exc:
        return {"status": "not_run", "reason": str(exc)[:240]}
    accepted: list[dict[str, Any]] = []
    seen: set[tuple[int, str, str]] = set()
    for item in result.get("issues") or []:
        if not isinstance(item, dict):
            continue
        number, code = item.get("slide"), item.get("code")
        quote = str(item.get("quote") or "").strip()
        reason = str(item.get("reason") or "").strip()
        if (
            not isinstance(number, int)
            or not 1 <= number <= len(texts)
            or code not in CODES
            or (code == "title_not_claim" and slides[number - 1].get("role") in {"cover", "closing", "section"})
            or not quote
            or _normalized(quote) not in _normalized(texts[number - 1])
        ):
            continue
        key = (number, code, _normalized(quote))
        if key in seen:
            continue
        seen.add(key)
        accepted.append(
            {"slide": number, "code": code, "quote": quote, "reason": reason}
        )
    return {
        "status": "reviewed",
        "method": "text_model_suggestions_v1",
        "issues": accepted,
        "limitations": "No rendered slide image was examined; a person must confirm every suggestion and visual relevance",
    }
