from __future__ import annotations

import hashlib
from pathlib import Path

PROMPT_VERSION = "1.4.0"
PROMPT_ROOT = Path(__file__).with_name("prompts")
# Older versions stay in the repository so earlier manifests remain reproducible.
PROMPT_FILES = {
    "planner": "planner-v2.txt",
    "planner-cpu": "planner-cpu-v2.txt",
    "analyzer": "analyzer-v1.txt",
    "layout": "layout-v1.txt",
    "image": "image-v1.txt",
    "brief-outline": "brief-outline-v1.txt",
    "brief-slide": "brief-slide-v1.txt",
}


def load_prompt(name: str) -> str:
    if name not in PROMPT_FILES:
        raise ValueError(f"Unknown prompt: {name}")
    return (PROMPT_ROOT / PROMPT_FILES[name]).read_text(encoding="utf-8").strip()


def prompt_manifest() -> dict:
    from .pictograms import icon_manifest

    return {
        "version": PROMPT_VERSION,
        "files": dict(PROMPT_FILES),
        "sha256": {
            name: hashlib.sha256(load_prompt(name).encode("utf-8")).hexdigest()
            for name in PROMPT_FILES
        },
        "pictograms": icon_manifest(),
    }
