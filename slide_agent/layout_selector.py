"""Optional LoRA layout selector; exact geometry and QA stay deterministic."""

from __future__ import annotations

import hashlib
import json
import os
import random
from dataclasses import replace
from typing import Any

from .llm import InferenceClient, InferenceError
from .prompt_config import load_prompt

CAPACITY_KEYS = (
    "title_chars",
    "body_chars",
    "usable_body_zones",
    "body_area_ratio",
    "supports_data",
    "supports_image",
    "image_slot_area_ratio",
    "rhetorical_pattern",
    "has_title_zone",
    "title_top_ratio",
)


def candidate_features(pattern: dict[str, Any]) -> dict[str, Any]:
    return {
        "roles": pattern.get("roles", []),
        "capacity": {k: pattern.get("capacity", {}).get(k) for k in CAPACITY_KEYS},
    }


def selection_payload(
    slide: dict[str, Any], requirements: dict[str, Any], patterns: list[dict[str, Any]]
) -> tuple[dict[str, Any], dict[str, str]]:
    """Use the same bounded request during training and inference."""
    mapping = {f"candidate-{i}": p["id"] for i, p in enumerate(patterns, 1)}
    content = {
        "title": str(slide.get("title", ""))[:160],
        "body": str(slide.get("body", ""))[:500],
        "bullets": [str(b)[:160] for b in slide.get("bullets", [])[:5]],
    }
    return {
        "content": content,
        "requirements": requirements,
        "candidates": [
            {"label": label, **candidate_features(p)}
            for label, p in zip(mapping, patterns, strict=True)
        ],
    }, mapping


def select_with_adapter(
    slide: dict[str, Any],
    requirements: dict[str, Any],
    scored: list[tuple],
    client: InferenceClient | None,
) -> tuple[str | None, dict[str, Any]]:
    model = os.getenv("INFERENCE_LAYOUT_MODEL", "").strip()
    if client is None or not model:
        return None, {"mode": "heuristic"}
    if client.settings.backend == "llamacpp" and client.settings.lora_id is None:
        return None, {
            "mode": "fallback",
            "reason": "INFERENCE_LORA_ID is not configured",
        }
    # Only consider candidates close to the deterministic winner. QA repair does
    # not call this model, and low-ranking candidates stay outside this window.
    pool = [item[1] for item in scored[:6] if item[0] >= scored[0][0] - 6]
    if len(pool) < 2:
        return None, {"mode": "heuristic", "reason": "one competitive candidate"}
    seed = int(
        hashlib.sha256(json.dumps(slide, sort_keys=True).encode()).hexdigest()[:8], 16
    )
    random.Random(seed).shuffle(pool)
    payload, mapping = selection_payload(slide, requirements, pool)
    selector = InferenceClient(
        replace(
            client.settings,
            model=client.settings.model
            if client.settings.backend == "llamacpp"
            else model,
            lora_scale=1.0,
            max_retries=1,
            timeout_seconds=min(
                client.settings.timeout_seconds,
                120 if client.settings.backend == "llamacpp" else 15,
            ),
        )
    )
    try:
        result = selector.chat_json(
            system=load_prompt("layout"),
            user=json.dumps(payload, ensure_ascii=False),
            max_tokens=48,
        )
        choice = result.get("choice")
        if not isinstance(choice, str) or choice not in mapping:
            raise InferenceError("Layout model returned an unknown candidate")
        return mapping[choice], {"mode": "adapter", "model": model, "choice": choice}
    except InferenceError as exc:
        return None, {"mode": "fallback", "model": model, "reason": str(exc)[:300]}
