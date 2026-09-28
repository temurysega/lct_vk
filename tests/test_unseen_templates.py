"""Public templates the service has not seen: type scale, service pages,
closing slides, sample text left in exemplar slots and long titles."""

import io

from PIL import Image
from pptx import Presentation
from pptx.util import Inches, Pt

from slide_agent.analyzer import build_design_system, build_pattern_catalog
from slide_agent.composer import (
    _clear_unused_sample_text,
    _grow_title_frame,
    _set_text_frame,
)
from slide_agent.qa import inspect_presentation


def _element(text: str, top: float = 1.5, *, size: float | None = None) -> dict:
    font = {"size_pt": size, "name": "Arial"} if size else {}
    return {
        "left": 0.8,
        "top": top,
        "width": 6.0,
        "height": 0.6,
        "paragraphs": [{"text": text, "font": font}],
    }


def _slide(index: int, *texts: str, shapes: int = 3) -> dict:
    return {
        "index": index,
        "layout_index": 0,
        "text_elements": [
            _element(text, 0.6 + 0.8 * position) for position, text in enumerate(texts)
        ],
        "shapes": [{"type": "AUTO_SHAPE"}] * shapes,
        "images": [],
    }


def _context(slides: list[dict]) -> dict:
    return {
        "presentation": {"slide_width_inches": 10.0, "slide_height_inches": 5.625},
        "slide_masters": [
            {"index": 0, "placeholders": [{"type": "TITLE ", "font": {"size_pt": 31.0}}]}
        ],
        "slide_layouts": [
            {
                "index": 0,
                "name": "TITLE_AND_BODY",
                "master_index": 0,
                "placeholders": [
                    {"type": "TITLE ", "left": 0.8, "top": 0.6, "width": 8.4, "height": 0.6},
                    {
                        "type": "BODY ",
                        "left": 0.8,
                        "top": 1.4,
                        "width": 8.0,
                        "height": 3.6,
                        "font": {"size_pt": 14.0},
                    },
                ],
            }
        ],
        "slides": slides,
    }


def test_type_scale_includes_sizes_inherited_from_layouts_and_master() -> None:
    slide = _slide(1, "Cover")
    slide["text_elements"][0]["paragraphs"][0]["font"] = {"size_pt": 12.0, "name": "Barlow"}
    design = build_design_system(_context([slide]))

    # 31 pt comes from the master title placeholder, 14 pt from the layout body.
    assert {31.0, 14.0, 12.0} <= set(design["typography"]["observed_sizes_pt"])


def test_service_pages_are_not_exemplars_and_thanks_slide_closes() -> None:
    slides = [
        _slide(1, "Technology Consulting", "Here is where your presentation begins"),
        _slide(2, "Contents of this template", "A thanks slide, which you must keep"),
        _slide(3, "Understanding the problem", "Mercury", "Venus", "Mars"),
        _slide(4, "Instructions for use", "You can delete this slide"),
        _slide(5, "Resources", "Close-up of woman using a laptop"),
        _slide(6, "Thanks!", "Do you have any questions?", "youremail@freepik.com"),
        _slide(7, "Educational icons", shapes=900),
        _slide(8),  # a text-free end card with the template author's logo
    ]
    patterns = {
        item["id"]: item
        for item in build_pattern_catalog(_context(slides))["patterns"]
        if item.get("source_kind") == "slide_exemplar"
    }

    assert set(patterns) == {"slide-1", "slide-3", "slide-6", "slide-8"}
    assert "closing" in patterns["slide-6"]["roles"]
    # Only the thank-you slide closes: not the page that mentions one, nor
    # the logo card that merely comes last.
    assert "closing" not in patterns["slide-8"]["roles"]
    assert "closing" not in patterns["slide-3"]["roles"]


def _textbox(slide, name: str, text: str, top: float = 1.0):
    shape = slide.shapes.add_textbox(Inches(1), Inches(top), Inches(4), Inches(0.6))
    shape.name = name
    shape.text_frame.text = text
    return shape


def test_unfilled_exemplar_slots_lose_their_sample_text() -> None:
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    filled = _textbox(slide, "BrandDeck Text 5", "Спасибо")
    heading = _textbox(slide, "Google Shape;3808", "Mission", 2.0)
    body = _textbox(slide, "Google Shape;3807", "Venus has a beautiful name", 3.0)
    brand = _textbox(slide, "Footer text", "brand.com", 4.0)
    had_text = {shape.element for shape in slide.shapes}

    _clear_unused_sample_text(slide, had_text, {"brand.com"})

    assert filled.text_frame.text == "Спасибо"
    assert heading.text_frame.text == "" and body.text_frame.text == ""
    assert brand.text_frame.text == "brand.com"  # repeated across the template


def _title_slide():
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[1])  # title and content
    title, body = slide.placeholders[0], slide.placeholders[1]
    title.left, title.top, title.width, title.height = (
        Inches(0.5), Inches(0.3), Inches(9), Inches(0.6)
    )
    body.left, body.top, body.width, body.height = (
        Inches(0.5), Inches(1.0), Inches(9), Inches(5.5)
    )
    return slide, title, body


DESIGN = {"canvas": {"width_inches": 10.0, "height_inches": 7.5}, "brand": {}}
LONG_TITLE = (
    "Сотруднику нужен источник ответа со ссылкой на регламент, "
    "а специалисту поддержки — контекст вопроса"
)


def test_long_title_grows_and_moves_the_empty_body_slot_down() -> None:
    slide, title, body = _title_slide()

    _grow_title_frame(slide, title, LONG_TITLE, DESIGN)

    title_bottom = (title.top + title.height) / 914400
    assert title_bottom > 0.9 + 0.2
    assert body.top / 914400 >= title_bottom + 0.09
    assert body.height / 914400 < 5.5


def test_title_does_not_grow_over_text_below_it() -> None:
    slide, title, body = _title_slide()
    body.text_frame.text = "Готовый текст слайда"

    _grow_title_frame(slide, title, LONG_TITLE, DESIGN)

    assert (title.top + title.height) / 914400 <= 1.0 - 0.07
    assert body.top == Inches(1.0)


def test_narrow_title_column_never_breaks_a_word(tmp_path) -> None:
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    column = _textbox(slide, "Title", "", 1.0)
    column.width, column.height = Inches(1.3), Inches(4.0)
    _set_text_frame(column, ["Сотруднику нужен источник ответа, специалисту"], font_size=32)
    size = column.text_frame.paragraphs[0].runs[0].font.size.pt
    # "специалисту" fits the 1.3-inch column: 11 glyphs × 0.6 em at this size.
    assert 11 * size * 0.6 / 72 <= 1.3

    broken = _textbox(slide, "BrandDeck Title", "Сотруднику нужен", 5.0)
    broken.width = Inches(1.0)
    for run in broken.text_frame.paragraphs[0].runs:
        run.font.size = Pt(32)
    path = tmp_path / "narrow.pptx"
    prs.save(path)
    issues = inspect_presentation(path)["issues"]
    assert any(
        issue["code"] == "word_split_risk" and issue["shape"] == "BrandDeck Title"
        for issue in issues
    )


def test_qa_flags_sample_text_and_text_under_a_picture(tmp_path) -> None:
    template = Presentation()
    for index in range(3):
        slide = template.slides.add_slide(template.slide_layouts[6])
        _textbox(slide, "Brand", "brand.com", 6.5)
        if index == 0:
            _textbox(slide, "Heading", "Venus has a beautiful name")
    template_path = tmp_path / "template.pptx"
    template.save(template_path)

    output = Presentation()
    slide = output.slides.add_slide(output.slide_layouts[6])
    _textbox(slide, "Heading", "Venus has a beautiful name")
    _textbox(slide, "Brand", "brand.com", 6.5)
    text = _textbox(slide, "BrandDeck Body", "Новый текст слайда", 3.0)
    text.width = Inches(8)
    for paragraph in text.text_frame.paragraphs:
        for run in paragraph.runs:
            run.font.size = Pt(18)
    photo = io.BytesIO()
    Image.new("RGB", (400, 300), "#345678").save(photo, format="PNG")
    picture = slide.shapes.add_picture(
        io.BytesIO(photo.getvalue()), Inches(5), Inches(2.8), Inches(3), Inches(2.25)
    )
    picture.name = "BrandDeck Image"
    output_path = tmp_path / "output.pptx"
    output.save(output_path)

    issues = inspect_presentation(output_path, template_path=template_path)["issues"]
    codes = [(issue["code"], issue.get("shape")) for issue in issues]

    assert ("template_sample_text", "Heading") in codes
    assert not any(code == "template_sample_text" and shape == "Brand" for code, shape in codes)
    assert ("image_text_overlap", "BrandDeck Body / BrandDeck Image") in codes
