"""Optional editorial review of each final, office-rendered slide image.

Vision findings are suggestions. They never certify visual quality and never
change the deterministic QA score. An unavailable model is reported as such,
not as an empty successful review.
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from pathlib import Path
from typing import Any

from .llm import InferenceClient, InferenceError

METHOD = "rendered_slide_vision_model_v1"
MAX_SLIDES = 15
MAX_ISSUES_PER_SLIDE = 3
REQUEST_TIMEOUT_SECONDS = 25
REQUEST_MAX_TOKENS = 1100
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
CODES = {
    "clipped_text",
    "small_text",
    "low_contrast",
    "overlap",
    "chart_legibility",
    "visual_irrelevant",
    "crowded",
    "empty_space",
    "reading_order",
    "image_quality",
}

SYSTEM = """You review one final presentation slide, shown as a rendered PNG.
Identify only clear, visible problems that would matter to a reader: clipped
text, unreadable labels, low contrast, overlap, confusing hierarchy or reading
order, a clearly unrelated illustration, excessive crowding or near-empty
content area, or degraded image quality. Inspect charts and tables at normal
viewing size. Do not infer whether statements are true or supported by source
materials. Do not suggest a problem unless you can point to visible evidence.
Return JSON with an issues array. Use an empty array if no clear problem is
visible. At most three distinct findings per slide. The evidence should name
visible text or a specific visual location, not a generic assessment."""

ISSUE_SCHEMA = {
    "type": "object",
    "properties": {
        "code": {"enum": sorted(CODES)},
        "evidence": {"type": "string", "minLength": 3, "maxLength": 140},
        "reason": {"type": "string", "minLength": 6, "maxLength": 220},
    },
    "required": ["code", "evidence", "reason"],
    "additionalProperties": False,
}
SCHEMA = {
    "type": "object",
    "properties": {
        "issues": {
            "type": "array",
            "items": ISSUE_SCHEMA,
            "maxItems": MAX_ISSUES_PER_SLIDE,
        }
    },
    "required": ["issues"],
    "additionalProperties": False,
}


def _report(
    status: str,
    expected: list[int],
    reviewed: list[int],
    issues: list[dict[str, Any]],
    reason: str = "",
) -> dict[str, Any]:
    result = {
        "status": status,
        "method": METHOD,
        "reviewed_slides": sorted(reviewed),
        "unreviewed_slides": sorted(set(expected) - set(reviewed)),
        "issues": sorted(issues, key=lambda item: (item["slide"], item["code"])),
        "limitations": (
            "Model findings are visual suggestions for a person to confirm; "
            "the model cannot verify source claims or guarantee that no issue exists"
        ),
    }
    if reason:
        result["reason"] = reason
    return result


def _bounded_client(client: InferenceClient) -> InferenceClient:
    """Use one attempt and a short request timeout for this optional stage."""
    if not isinstance(client, InferenceClient):
        return client
    settings = replace(
        client.settings,
        timeout_seconds=max(1, min(client.settings.timeout_seconds, REQUEST_TIMEOUT_SECONDS)),
        max_retries=1,
        max_output_tokens=(
            min(client.settings.max_output_tokens, REQUEST_MAX_TOKENS)
            if client.settings.max_output_tokens > 0
            else REQUEST_MAX_TOKENS
        ),
    )
    return InferenceClient(settings)


def _valid_png(path: Path) -> bool:
    try:
        with path.open("rb") as image:
            return image.read(len(PNG_SIGNATURE)) == PNG_SIGNATURE
    except OSError:
        return False


def _review_one(
    number: int, path: Path, client: InferenceClient
) -> list[dict[str, Any]]:
    response = client.chat_json(
        system=SYSTEM,
        user=f"Review the rendered presentation slide {number}. Return only JSON.",
        image_paths=[str(path)],
        schema=SCHEMA,
        max_tokens=REQUEST_MAX_TOKENS,
        temperature=0,
    )
    raw = response.get("issues")
    if not isinstance(raw, list) or len(raw) > MAX_ISSUES_PER_SLIDE:
        raise ValueError("Invalid vision response")
    accepted: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in raw:
        if not isinstance(item, dict):
            raise TypeError("Invalid vision issue")
        code, evidence, reason = (
            item.get("code"),
            item.get("evidence"),
            item.get("reason"),
        )
        if (
            code not in CODES
            or not isinstance(evidence, str)
            or not isinstance(reason, str)
            or not 3 <= len(evidence.strip()) <= 140
            or not 6 <= len(reason.strip()) <= 220
        ):
            raise ValueError("Invalid vision issue")
        key = (code, evidence.strip().casefold())
        if key in seen:
            continue
        seen.add(key)
        accepted.append(
            {
                "slide": number,
                "code": code,
                "evidence": " ".join(evidence.split()),
                "reason": " ".join(reason.split()),
            }
        )
    return accepted


def review_rendered_slides(
    exports: dict[str, Any],
    client: InferenceClient | None,
    *,
    expected_slide_count: int,
) -> dict[str, Any]:
    """Review each final PNG only when the actual office export succeeded.

    A first-image probe prevents a text-only model from receiving every slide.
    The remaining requests use the server's configured parallelism, capped at
    three; each has one attempt and a 25-second timeout.
    """
    expected = list(range(1, max(0, expected_slide_count) + 1))
    if os.getenv("INFERENCE_RENDER_AUDIT", "0").strip().lower() not in {
        "1", "true", "on", "yes"
    }:
        return _report("not_run", expected, [], [], "Rendered vision audit is disabled")
    if client is None or not getattr(client.settings, "vision_enabled", False):
        return _report("not_run", expected, [], [], "A vision-capable inference model is not configured")
    if exports.get("status") != "passed" or exports.get("renderer") != "libreoffice":
        return _report("not_run", expected, [], [], "A final office-rendered export is unavailable")

    paths: dict[int, Path] = {}
    for preview in exports.get("previews") or []:
        if not isinstance(preview, dict):
            continue
        number, raw_path = preview.get("slide"), preview.get("path")
        if (
            type(number) is int
            and number in expected
            and isinstance(raw_path, str)
            and Path(raw_path).suffix.lower() == ".png"
            and _valid_png(Path(raw_path))
        ):
            paths[number] = Path(raw_path)
    candidates = sorted(paths)[:MAX_SLIDES]
    if not candidates:
        return _report("not_run", expected, [], [], "No final rendered PNG previews are available")

    bounded = _bounded_client(client)
    reviewed: list[int] = []
    findings: list[dict[str, Any]] = []
    first = candidates.pop(0)
    try:
        findings.extend(_review_one(first, paths[first], bounded))
    except (InferenceError, OSError, TypeError, ValueError, KeyError):
        return _report("unavailable", expected, [], [], "The vision model did not review the rendered image")
    reviewed.append(first)

    if candidates:
        workers = min(3, max(1, int(getattr(client.settings, "parallel_requests", 1))))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            pending = {
                pool.submit(_review_one, number, paths[number], bounded): number
                for number in candidates
            }
            for future in as_completed(pending):
                number = pending[future]
                try:
                    findings.extend(future.result())
                except (InferenceError, OSError, TypeError, ValueError, KeyError):
                    continue
                reviewed.append(number)
    status = "reviewed" if len(reviewed) == len(expected) else "partial"
    reason = (
        "Some slides could not be reviewed or exceeded the 15-slide audit limit"
        if status == "partial"
        else ""
    )
    return _report(status, expected, reviewed, findings, reason)
