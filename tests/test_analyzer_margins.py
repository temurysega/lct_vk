"""Content margins must not be inferred from page-number placeholders."""

from slide_agent.analyzer import build_design_system


def _context(placeholders: list[dict]) -> dict:
    return {
        "presentation": {"slide_width_inches": 13.33, "slide_height_inches": 7.5},
        "slide_layouts": [{"placeholders": placeholders}],
        "slides": [
            {"text_elements": []},
            {
                "text_elements": [
                    {"left": 0.48, "top": 0.91, "width": 6.77, "height": 0.58},
                    {"left": 12.38, "top": 7.18, "width": 0.45, "height": 0.19},
                ]
            },
        ],
    }


def test_footer_only_layout_uses_exemplar_content_margin() -> None:
    context = _context(
        [
            {"type": "DATE ", "left": 0.5, "top": 6.95, "width": 2.33},
            {"type": "FOOTER ", "left": 3.417, "top": 6.95, "width": 3.167},
            {"type": "SLIDE_NUMBER ", "left": 7.167, "top": 6.95, "width": 2.33},
        ]
    )

    spacing = build_design_system(context)["spacing"]

    assert spacing["typical_left_margin_inches"] == 0.48
    assert spacing["typical_right_margin_inches"] == 0.48


def test_real_content_placeholder_takes_precedence_over_exemplar() -> None:
    context = _context(
        [
            {"type": "BODY", "left": 0.8, "top": 1.7, "width": 11.3},
            {"type": "FOOTER", "left": 3.417, "top": 0.2, "width": 3.167},
        ]
    )

    spacing = build_design_system(context)["spacing"]

    assert spacing["typical_left_margin_inches"] == 0.8
    assert spacing["typical_right_margin_inches"] == 1.23
