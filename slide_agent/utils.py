from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_WORKSPACE = PROJECT_ROOT / "slide-workspace"

_CYRILLIC_TO_LATIN = str.maketrans(
    {
        "а": "a",
        "б": "b",
        "в": "v",
        "г": "g",
        "д": "d",
        "е": "e",
        "ё": "e",
        "ж": "zh",
        "з": "z",
        "и": "i",
        "й": "y",
        "к": "k",
        "л": "l",
        "м": "m",
        "н": "n",
        "о": "o",
        "п": "p",
        "р": "r",
        "с": "s",
        "т": "t",
        "у": "u",
        "ф": "f",
        "х": "h",
        "ц": "ts",
        "ч": "ch",
        "ш": "sh",
        "щ": "sch",
        "ъ": "",
        "ы": "y",
        "ь": "",
        "э": "e",
        "ю": "yu",
        "я": "ya",
    }
)


def slugify(value: str, fallback: str = "deck") -> str:
    value = value.strip().lower().translate(_CYRILLIC_TO_LATIN)
    value = re.sub(r"[^a-z0-9]+", "-", value, flags=re.IGNORECASE)
    return value.strip("-") or fallback


def timestamp() -> str:
    return datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")


def unique_dir(parent: Path, stem: str) -> Path:
    parent.mkdir(parents=True, exist_ok=True)
    base = parent / f"{slugify(stem)}-{timestamp()}"
    candidate = base
    suffix = 2
    while candidate.exists():
        candidate = Path(f"{base}-{suffix}")
        suffix += 1
    candidate.mkdir(parents=True)
    return candidate


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, ensure_ascii=False, indent=2)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.write("\n")
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def copy_file(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def resolve_workspace(value: str | Path | None = None) -> Path:
    configured = value or os.getenv("BRANDDECK_WORKSPACE")
    return Path(configured).expanduser().resolve() if configured else DEFAULT_WORKSPACE


def strip_code_fence(value: str) -> str:
    value = value.strip()
    match = re.fullmatch(
        r"```(?:json)?\s*(.*?)\s*```", value, re.DOTALL | re.IGNORECASE
    )
    return match.group(1).strip() if match else value


def find_latest(directory: Path) -> Path | None:
    candidates = (
        [item for item in directory.iterdir() if item.is_dir()]
        if directory.exists()
        else []
    )
    return (
        max(candidates, key=lambda item: item.stat().st_mtime) if candidates else None
    )
