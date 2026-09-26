"""Compare exported slide pixels, rather than only template layout IDs."""

from __future__ import annotations

import math
from itertools import combinations
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops

PIXEL_DELTA = 24
SLIDE_CHANGED_FRACTION = 0.02
MIN_CHANGED_SLIDE_SHARE = 0.20
COMPARE_SIZE = (320, 180)


def _changed_fraction(first: Path, second: Path) -> float:
    with Image.open(first) as source:
        left = source.convert("RGB").resize(COMPARE_SIZE, Image.Resampling.LANCZOS)
    with Image.open(second) as source:
        right = source.convert("RGB").resize(COMPARE_SIZE, Image.Resampling.LANCZOS)
    channels = ImageChops.difference(left, right).split()
    maximum = ImageChops.lighter(
        ImageChops.lighter(channels[0], channels[1]), channels[2]
    )
    return sum(maximum.histogram()[PIXEL_DELTA:]) / (COMPARE_SIZE[0] * COMPARE_SIZE[1])


def inspect_visual_diversity(
    variants: list[dict[str, Any]], roles: list[str]
) -> dict[str, Any]:
    """Require each variant pair to visibly differ on multiple content slides.

    Covers and closings may deliberately use the same brand frame. A slide
    counts only when at least 2% of its rendered pixels change by 24/255 or
    more. These thresholds discard antialiasing and tiny decoration shifts.
    """
    eligible = [
        index
        for index, role in enumerate(roles, 1)
        if role not in {"cover", "closing", "section"}
    ]
    required = min(
        len(eligible), max(2, math.ceil(len(eligible) * MIN_CHANGED_SLIDE_SHARE))
    )
    pairs: list[dict[str, Any]] = []
    for left, right in combinations(variants, 2):
        left_id = str(left.get("variant", {}).get("id", "unknown"))
        right_id = str(right.get("variant", {}).get("id", "unknown"))
        left_paths = {
            item.get("slide"): Path(item["path"])
            for item in left.get("exports", {}).get("previews", [])
            if item.get("path")
        }
        right_paths = {
            item.get("slide"): Path(item["path"])
            for item in right.get("exports", {}).get("previews", [])
            if item.get("path")
        }
        slides = []
        missing = []
        for index in eligible:
            first, second = left_paths.get(index), right_paths.get(index)
            if not first or not second or not first.is_file() or not second.is_file():
                missing.append(index)
                continue
            fraction = _changed_fraction(first, second)
            slides.append({"slide": index, "changed_fraction": round(fraction, 4)})
        changed = [
            item["slide"]
            for item in slides
            if item["changed_fraction"] >= SLIDE_CHANGED_FRACTION
        ]
        pairs.append(
            {
                "variants": [left_id, right_id],
                "status": "passed"
                if not missing and len(changed) >= required
                else "needs_review",
                "changed_slides": changed,
                "missing_previews": missing,
                "slides": slides,
            }
        )
    return {
        "status": "passed"
        if pairs and eligible and all(p["status"] == "passed" for p in pairs)
        else "needs_review",
        "method": "rendered_pixel_difference_v1",
        "pixel_delta": PIXEL_DELTA,
        "slide_changed_fraction": SLIDE_CHANGED_FRACTION,
        "eligible_slides": eligible,
        "required_changed_slides_per_pair": required,
        "pairs": pairs,
    }
