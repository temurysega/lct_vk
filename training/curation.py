"""Extract training content without page labels, footers or ornamental glyphs."""

import re
from collections import Counter


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def element_text(element: dict) -> str:
    return clean_text(
        " ".join(p.get("text", "") for p in element.get("paragraphs", []))
    )


def font_size(element: dict) -> float:
    return max(
        (
            float(p.get("font", {}).get("size_pt", 0) or 0)
            for p in element.get("paragraphs", [])
        ),
        default=0,
    )


def is_page_marker(text: str) -> bool:
    text = clean_text(text)
    return bool(
        re.fullmatch(
            r"(?:(?:p(?:age)?|стр(?:аница)?|слайд)\s*[.·:—-]?\s*)?\d{1,4}"
            r"(?:\s*(?:/|из|of)\s*\d{1,4})?\.?|[‹<]?#[›>]?",
            text,
            re.IGNORECASE,
        )
    )


def repeated_marginal_text(context: dict) -> set[str]:
    height = context["presentation"]["slide_height_inches"]
    counts = Counter()
    for slide in context["slides"]:
        counts.update(
            {
                element_text(e).casefold()
                for e in slide.get("text_elements", [])
                if e.get("top", 0) > height * 0.84
                or (e.get("top", 0) < height * 0.12 and font_size(e) < 20)
            }
        )
    return {
        text
        for text, count in counts.items()
        if count >= max(3, len(context["slides"]) * 0.35)
    }


def select_content(
    slide: dict, height: float, repeated: set[str]
) -> tuple[str, list[str]]:
    elements = []
    for element in slide.get("text_elements", []):
        text = element_text(element)
        placeholder = element.get("placeholder_type", "").upper()
        if (
            not re.search(r"[^\W\d_]", text, re.UNICODE)
            or is_page_marker(text)
            or text.casefold() in repeated
            or any(kind in placeholder for kind in ("SLIDE_NUMBER", "FOOTER", "DATE"))
            or text.casefold()
            in {
                "фото",
                "photo",
                "click to add title",
                "нажмите, чтобы добавить заголовок",
            }
        ):
            continue
        elements.append(element)
    if not elements:
        return "", []
    upper = [e for e in elements if e.get("top", 0) < height * 0.7]
    pool = upper or elements

    def score(element):
        text = element_text(element)
        placeholder = element.get("placeholder_type", "").upper()
        explicit_title = "TITLE" in placeholder and "SUBTITLE" not in placeholder
        size = font_size(element)
        # Prominent text wins over small labels even when shapes are out of order.
        return (
            int(explicit_title),
            size - max(0, len(text) - 180) / 45,
            -element.get("top", 0),
            -element.get("left", 0),
        )

    title = max(pool, key=score)
    bullets = []
    for element in sorted(
        elements, key=lambda e: (round(e.get("top", 0), 1), e.get("left", 0))
    ):
        if element is title:
            continue
        for paragraph in element.get("paragraphs", []):
            text = clean_text(paragraph.get("text", ""))
            if (
                text
                and not is_page_marker(text)
                and re.search(r"\w", text)
                and text not in bullets
            ):
                bullets.append(text)
    return element_text(title), bullets
