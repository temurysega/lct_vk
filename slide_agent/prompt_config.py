from __future__ import annotations

import hashlib
from pathlib import Path

PROMPT_VERSION = "1.2.0"
PROMPT_ROOT = Path(__file__).with_name("prompts")


def load_prompt(name: str) -> str:
    if name not in {"planner", "planner-cpu", "analyzer", "layout"}:
        raise ValueError(f"Unknown prompt: {name}")
    return (PROMPT_ROOT / f"{name}-v1.txt").read_text(encoding="utf-8").strip()


def prompt_manifest() -> dict:
    return {
        "version": PROMPT_VERSION,
        "sha256": {
            name: hashlib.sha256(load_prompt(name).encode("utf-8")).hexdigest()
            for name in ("planner", "planner-cpu", "analyzer", "layout")
        },
    }
