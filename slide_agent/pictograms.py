"""Deterministic pictogram selection and native vector icon shapes.

Icons come from the bundled Lucide subset (ISC license) and are written as
DrawingML custom geometry, recoloured with template colours. Selection is a
transparent keyword/stem match configured in ``assets/icons/keywords.json``;
no model is involved, so the same text always yields the same icon.
"""

from __future__ import annotations

import hashlib
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.util import Emu, Inches

from .vector import (
    apply_geometry,
    custom_geometry,
    drop_theme_style,
    element_commands,
    set_outline,
)

ICON_ROOT = Path(__file__).with_name("assets") / "icons"
_WORD = re.compile(r"[0-9a-zа-я]+(?:-[0-9a-zа-я]+)*")


@lru_cache(maxsize=1)
def icon_bundle() -> dict[str, Any]:
    return json.loads((ICON_ROOT / "lucide.json").read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def keyword_config() -> dict[str, Any]:
    return json.loads((ICON_ROOT / "keywords.json").read_text(encoding="utf-8"))


def icon_names() -> list[str]:
    return sorted(icon_bundle()["icons"])


def icon_manifest() -> dict[str, Any]:
    bundle = icon_bundle()
    return {
        "package": bundle["package"],
        "version": bundle["version"],
        "license": bundle["license"],
        "icons": len(bundle["icons"]),
        "keywords_sha256": hashlib.sha256(
            (ICON_ROOT / "keywords.json").read_bytes()
        ).hexdigest(),
    }


def words(text: str) -> list[str]:
    return _WORD.findall(str(text).lower().replace("ё", "е"))


def _matches(word: str, keyword: str) -> bool:
    if len(keyword) <= 3:
        return word.startswith(keyword) and len(word) - len(keyword) <= 2
    return word.startswith(keyword)


@lru_cache(maxsize=1)
def _keyword_index() -> list[tuple[str, tuple[str, ...]]]:
    available = set(icon_bundle()["icons"])
    index = []
    for name, keywords in keyword_config()["icons"].items():
        if name not in available:
            continue
        for keyword in keywords:
            parts = tuple(words(keyword))
            if parts:
                index.append((name, parts))
    return index


def score_icons(text: str, *, weight: float = 1.0) -> dict[str, float]:
    tokens = words(text)
    matches: list[tuple[str, int, int]] = []
    for name, parts in _keyword_index():
        span = len(parts)
        for start in range(len(tokens) - span + 1):
            if all(_matches(tokens[start + i], parts[i]) for i in range(span)):
                matches.append((name, start, sum(map(len, parts))))
                break
    if not matches:
        return {}
    # The head noun usually comes first ("Рост выручки", "Риски внедрения"):
    # the earliest matched word outweighs longer stems further on.
    first = min(start for _, start, _ in matches)
    scores: dict[str, float] = {}
    for name, start, length in matches:
        strength = 4 + 0.3 * min(length, 12)
        position = 1.6 if start == first else 1.0
        scores[name] = scores.get(name, 0.0) + weight * strength * position
    return scores


def choose_icon(
    text: str,
    *,
    context: str = "",
    exclude: set[str] | frozenset[str] = frozenset(),
    minimum: float = 4.0,
) -> tuple[str | None, float]:
    """Return ``(icon, score)``; ``None`` when no keyword is confident enough.

    ``text`` is the item itself; ``context`` (for example the slide title)
    breaks ties with a lower weight. Excluded icons are avoided so that one
    slide does not repeat the same pictogram.
    """
    scores = score_icons(text)
    for name, value in score_icons(context, weight=0.35).items():
        if name in scores:
            scores[name] += value
    ranked = sorted(
        ((value, name) for name, value in scores.items() if name not in exclude),
        key=lambda item: (-item[0], item[1]),
    )
    if not ranked or ranked[0][0] < minimum:
        return None, 0.0
    return ranked[0][1], ranked[0][0]


def resolve_icon(hint: Any, text: str = "", context: str = "") -> str | None:
    """Accept an explicit icon name or a keyword hint from the planner."""
    if isinstance(hint, str):
        name = hint.strip().lower()
        if name in icon_bundle()["icons"]:
            return name
        chosen, _ = choose_icon(hint)
        if chosen:
            return chosen
    chosen, _ = choose_icon(text, context=context)
    return chosen


def assign_icons(
    texts: list[str], *, context: str = "", require_all: bool = False
) -> list[str | None]:
    """Choose distinct icons for a list; optionally all-or-nothing."""
    used: set[str] = set()
    result: list[str | None] = []
    for text in texts:
        icon, _ = choose_icon(text, context=context, exclude=used)
        if icon:
            used.add(icon)
        result.append(icon)
    if require_all and not all(result):
        return [None] * len(texts)
    return result


def fallback_icon() -> str:
    return str(keyword_config().get("fallback", "circle-dot"))


def _icon_paths(name: str) -> list[list[tuple]]:
    nodes = icon_bundle()["icons"].get(name) or icon_bundle()["icons"][fallback_icon()]
    return [element_commands(tag, attributes) for tag, attributes in nodes]


def add_icon(
    shapes: Any,
    name: str,
    box: tuple[float, float, float],
    color: str,
    *,
    stroke_ratio: float = 1.0,
) -> Any:
    """Add a native stroked pictogram. ``box`` is ``(x, y, size)`` in inches."""
    x, y, size = box
    shape = shapes.add_shape(
        MSO_SHAPE.RECTANGLE, Inches(x), Inches(y), Inches(size), Inches(size)
    )
    bundle = icon_bundle()
    view = bundle.get("view_box", [0, 0, 24, 24])
    apply_geometry(
        shape,
        custom_geometry(_icon_paths(name), width=view[2], height=view[3]),
    )
    drop_theme_style(shape)
    shape.fill.background()
    stroke = float(bundle.get("stroke_width", 2)) / float(view[2]) * stroke_ratio
    set_outline(shape, color, int(Emu(Inches(size * stroke))))
    shape.name = f"BrandDeck Icon {name}"
    return shape


def add_polygon(
    shapes: Any,
    points: list[tuple[float, float]],
    fill: str,
    *,
    name: str,
) -> Any:
    """Add a filled native polygon; ``points`` are absolute inches."""
    left = min(px for px, _ in points)
    top = min(py for _, py in points)
    width = max(0.01, max(px for px, _ in points) - left)
    height = max(0.01, max(py for _, py in points) - top)
    shape = shapes.add_shape(
        MSO_SHAPE.RECTANGLE, Inches(left), Inches(top), Inches(width), Inches(height)
    )
    local = [(px - left, py - top) for px, py in points]
    commands: list[tuple] = [("M", *local[0])]
    commands.extend(("L", px, py) for px, py in local[1:])
    commands.append(("Z",))
    apply_geometry(
        shape,
        custom_geometry(
            [commands], width=width, height=height, filled=True, stroked=False
        ),
    )
    drop_theme_style(shape)
    shape.fill.solid()
    shape.fill.fore_color.rgb = RGBColor.from_string(fill)
    set_outline(shape, None, 0)
    shape.name = name
    return shape
