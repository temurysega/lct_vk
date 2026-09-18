"""Bound template evidence sent to a model; keep full geometry on the CPU side."""

from __future__ import annotations

from typing import Any


def compact_patterns(catalog: dict[str, Any], limit: int = 48) -> list[dict[str, Any]]:
    patterns = catalog.get("patterns", [])
    if len(patterns) > limit:
        # Sample throughout the catalogue, including layout and exemplar sections.
        patterns = [
            patterns[round(i * (len(patterns) - 1) / (limit - 1))] for i in range(limit)
        ]
    keys = (
        "title_chars",
        "body_chars",
        "usable_body_zones",
        "supports_data",
        "supports_image",
        "rhetorical_pattern",
    )
    return [
        {
            "id": p["id"],
            "roles": p["roles"],
            "capacity": {k: p.get("capacity", {}).get(k) for k in keys},
        }
        for p in patterns
    ]


def compact_design(design: dict[str, Any]) -> dict[str, Any]:
    typography = design.get("typography", {})
    colors = design.get("colors", {})
    return {
        "canvas": design.get("canvas", {}),
        "typography": {
            k: typography.get(k)
            for k in (
                "primary_font",
                "heading_font",
                "content_title_size_pt",
                "body_size_pt",
            )
        },
        "theme": colors.get("theme", []),
        "spacing": {
            k: v
            for k, v in design.get("spacing", {}).items()
            if isinstance(v, (str, int, float))
        },
    }
