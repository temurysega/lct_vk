"""Choose and validate diagram, pictogram and image visuals for a plan.

Model output is untrusted: diagram types are allow-listed, item counts and
text lengths are bounded, and images may only reference known asset ids. The
offline planner uses transparent rules from ``assets/visual_rules.json`` so a
deck gets native diagrams without any model.
"""

from __future__ import annotations

import json
import math
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from .diagrams import DIAGRAM_TYPES, ITEM_LIMITS
from .pictograms import assign_icons, words

RULES_PATH = Path(__file__).with_name("assets") / "visual_rules.json"
LABEL_LIMIT = 80
DETAIL_LIMIT = 220


@lru_cache(maxsize=1)
def visual_rules() -> dict[str, Any]:
    return json.loads(RULES_PATH.read_text(encoding="utf-8"))


def _clip(value: Any, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _clean_item(item: Any) -> Any:
    if isinstance(item, dict):
        cleaned = {
            "label": _clip(item.get("label") or item.get("title"), LABEL_LIMIT),
            "detail": _clip(
                item.get("detail") or item.get("text") or item.get("description"),
                DETAIL_LIMIT,
            ),
        }
        if item.get("value") not in (None, ""):
            cleaned["value"] = _clip(item.get("value"), 24)
        if isinstance(item.get("icon"), str):
            cleaned["icon"] = _clip(item["icon"], 40)
        return cleaned if cleaned["label"] or cleaned["detail"] else None
    text = _clip(item, DETAIL_LIMIT)
    return text or None


def _independent_metric_items(items: list[Any]) -> bool:
    """A pyramid or cycle misrepresents unrelated quantities as a relationship.

    The quantity may be the card's label or detail. Numbered stages and items
    with more than one quantity remain untouched: their relationship cannot be
    inferred from a single metric.
    """
    if len(items) < 2:
        return False
    quantities: list[int] = []
    units: list[str] = []
    for item in items:
        if not isinstance(item, dict) or item.get("value"):
            return False
        matches = [
            match
            for field in ("label", "detail")
            if (
                match := re.match(
                    r"^\s*(\d+)\s+([^\W\d_]+)",
                    str(item.get(field) or ""),
                    re.UNICODE,
                )
            )
        ]
        if len(matches) != 1:
            return False
        match = matches[0]
        quantities.append(int(match.group(1)))
        units.append(match.group(2).casefold()[:5])
    return len(set(units)) == len(units) and set(quantities) != set(
        range(1, len(items) + 1)
    )


def sanitize_diagram(value: dict[str, Any]) -> dict[str, Any] | None:
    kind = value.get("type")
    if kind not in DIAGRAM_TYPES:
        return None
    raw = value.get("items") if isinstance(value.get("items"), list) else []
    items = [item for item in (_clean_item(entry) for entry in raw) if item]
    if kind in {"pyramid", "cycle"} and _independent_metric_items(items):
        kind = "icon_grid"
    minimum, maximum = ITEM_LIMITS[kind]
    if kind == "matrix" and len(items) != 4:
        kind, minimum, maximum = "icon_grid", *ITEM_LIMITS["icon_grid"]
    if len(items) < minimum:
        return None
    result: dict[str, Any] = {"type": kind, "items": items[:maximum]}
    if kind == "hierarchy" and value.get("root"):
        result["root"] = _clip(value.get("root"), LABEL_LIMIT)
    if kind == "matrix" and isinstance(value.get("axes"), dict):
        axes = {
            k: _clip(v, 60) for k, v in value["axes"].items() if k in {"x", "y"} and v
        }
        if axes:
            result["axes"] = axes
    if value.get("origin") == "offline_rules":
        result["origin"] = "offline_rules"
    return result


def sanitize_image(
    value: dict[str, Any], asset_ids: set[str] | frozenset[str]
) -> dict[str, Any] | None:
    asset_id = str(value.get("asset_id") or "").strip()
    result: dict[str, Any] = {"type": "image"}
    if asset_id:
        if asset_id not in asset_ids:
            return None
        result["asset_id"] = asset_id
    else:
        request = _clip(
            value.get("request") or value.get("prompt") or value.get("description"), 300
        )
        if not request:
            return None
        result["request"] = request
    if value.get("caption"):
        result["caption"] = _clip(value.get("caption"), 120)
    return result


def _heading_matches(heading: str, keywords: list[str]) -> bool:
    tokens = words(heading)
    for keyword in keywords:
        parts = words(keyword)
        for start in range(len(tokens) - len(parts) + 1):
            if all(tokens[start + i].startswith(parts[i]) for i in range(len(parts))):
                return True
    return False


def _sequence_share(items: list[str]) -> float:
    markers = visual_rules()["sequence_markers"]
    hits = 0
    for item in items:
        head = words(item)[:4]
        if any(token.startswith(marker) for token in head for marker in markers):
            hits += 1
    return hits / max(1, len(items))


def infer_diagram(heading: str, items: list[str]) -> dict[str, Any] | None:
    """Structural diagram for one slide, or ``None`` when rules do not apply."""
    rules = visual_rules()
    limit = int(rules.get("max_words_per_diagram_item", 24))
    if not items or any(len(str(item).split()) > limit for item in items):
        return None
    count = len(items)
    keywords = rules["heading_keywords"]
    ordered = ("matrix", "funnel", "pyramid", "cycle", "hierarchy", "process")
    for kind in ordered:
        minimum, maximum = ITEM_LIMITS[kind]
        if not minimum <= count <= maximum:
            continue
        if _heading_matches(heading, keywords.get(kind, [])):
            visual: dict[str, Any] = {
                "type": kind,
                "items": list(items),
                "origin": "offline_rules",
            }
            if kind == "hierarchy":
                visual["root"] = _clip(heading, LABEL_LIMIT)
            return visual
    minimum, maximum = ITEM_LIMITS["process"]
    if minimum + 1 <= count <= maximum and _sequence_share(items) >= float(
        rules.get("sequence_share", 0.6)
    ):
        return {"type": "process", "items": list(items), "origin": "offline_rules"}
    return None


def icon_grid_confidence(heading: str, items: list[str]) -> float:
    rules = visual_rules()["icon_grid"]
    minimum, maximum = ITEM_LIMITS["icon_grid"]
    if not minimum <= len(items) <= maximum:
        return 0.0
    if any(
        len(str(item).split()) > int(rules.get("max_words_per_item", 22))
        for item in items
    ):
        return 0.0
    icons = assign_icons([str(item) for item in items], context=heading)
    share = sum(bool(icon) for icon in icons) / len(items)
    return share if share >= float(rules.get("min_icon_share", 0.6)) else 0.0


def add_offline_visuals(slides: list[dict[str, Any]]) -> None:
    """Attach rule-based diagrams, then pictogram grids for a share of slides."""
    content = [
        slide
        for slide in slides
        if slide.get("role") == "content"
        and not slide.get("visual")
        and slide.get("bullets")
    ]
    for slide in content:
        visual = infer_diagram(str(slide.get("title", "")), list(slide["bullets"]))
        if visual:
            slide["visual"] = visual
            slide["bullets"] = []
    share = float(visual_rules()["icon_grid"].get("max_share_of_content_slides", 0.4))
    budget = max(1, math.floor(len(content) * share)) if content else 0
    ranked = sorted(
        (
            (
                icon_grid_confidence(
                    str(slide.get("title", "")), list(slide["bullets"])
                ),
                index,
            )
            for index, slide in enumerate(content)
            if not slide.get("visual")
        ),
        key=lambda item: (-item[0], item[1]),
    )
    chosen: list[int] = []
    for confidence, index in ranked:
        if confidence <= 0 or len(chosen) >= budget:
            continue
        # Avoid two pictogram grids in a row: variety reads better.
        if any(abs(index - other) == 1 for other in chosen):
            continue
        chosen.append(index)
    for index in chosen:
        slide = content[index]
        slide["visual"] = {
            "type": "icon_grid",
            "items": list(slide["bullets"]),
            "origin": "offline_rules",
        }
        slide["bullets"] = []


def visual_text(visual: dict[str, Any] | None) -> list[str]:
    """Plain text carried by a diagram visual (for capacity and coverage)."""
    if not isinstance(visual, dict) or visual.get("type") not in DIAGRAM_TYPES:
        return []
    texts = []
    if visual.get("root"):
        texts.append(str(visual["root"]))
    for item in visual.get("items", []):
        if isinstance(item, dict):
            texts.extend(
                str(item.get(key))
                for key in ("label", "detail", "value")
                if item.get(key)
            )
        elif item:
            texts.append(str(item))
    return texts
