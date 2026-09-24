"""Exemplar leftovers: the composer removes them and the audit reports them."""

from pathlib import Path

import pytest
from PIL import Image
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
from pptx.util import Inches, Pt

from slide_agent.composer import _remove_exemplar_leftovers
from slide_agent.exporter import export_presentation, find_libreoffice
from slide_agent.qa import inspect_presentation
from slide_agent.render_audit import inspect_rendered_fill

DESIGN = {"canvas": {"width_inches": 13.333, "height_inches": 7.5}}


def _rect(slide, name, box, kind=MSO_SHAPE.ROUNDED_RECTANGLE, text=""):
    shape = slide.shapes.add_shape(kind, *(Inches(value) for value in box))
    shape.name = name
    shape.fill.solid()
    shape.fill.fore_color.rgb = RGBColor(0x20, 0x20, 0x20)
    if text:
        shape.text_frame.text = text
    return shape


def _text(slide, name, box, text):
    shape = slide.shapes.add_textbox(*(Inches(value) for value in box))
    shape.name = name
    shape.text_frame.text = text
    return shape


def _deck() -> Presentation:
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    return prs


def test_cleanup_removes_scaffolding_and_keeps_design():
    prs = _deck()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _rect(slide, "Band", (0, 5.8, 13.333, 1.7))
    label = _rect(slide, "Sample label", (1.2, 1.5, 2.1, 0.5), text="Текст")
    icon = slide.shapes.add_group_shape()
    icon.name = "Icon"
    icon.shapes.add_shape(MSO_SHAPE.OVAL, Inches(0.5), Inches(1.5), Inches(0.5), Inches(0.5))
    card = _rect(slide, "Card", (6.0, 1.0, 6.4, 1.9), text="Заголовок")
    inside = _rect(slide, "Card icon", (6.1, 1.1, 0.5, 0.5), kind=MSO_SHAPE.OVAL)
    under = _rect(slide, "Under text", (0.5, 3.4, 2.8, 1.4))
    host = _rect(slide, "Host panel", (6.0, 3.2, 6.4, 2.3))
    avatar = _rect(slide, "Avatar", (0.5, 6.3, 0.9, 0.9), kind=MSO_SHAPE.OVAL)
    column = _text(slide, "Column", (0.5, 0.1, 5.0, 1.0), "Образец")
    _rect(slide, "Highlight", (0.6, 0.4, 4.5, 0.3))  # a bar in the text flow
    arrow = slide.shapes.add_connector(
        MSO_CONNECTOR.STRAIGHT, Inches(0.7), Inches(3.5), Inches(3.0), Inches(4.5)
    )
    arrow.name = "Arrow"
    divider = slide.shapes.add_connector(
        MSO_CONNECTOR.STRAIGHT, Inches(5.2), Inches(3.0), Inches(5.2), Inches(5.0)
    )
    divider.name = "Divider"
    original = {shape.element for shape in slide.shapes}
    had_text = {label.element, card.element, column.element}

    # The composer erased the sample label, filled the card and added text.
    label.text_frame.text = ""
    card.text_frame.text = "Пилот охватывает два подразделения"
    column.text_frame.text = "Новый заголовок блока"
    body = _text(slide, "BrandDeck Body", (0.5, 3.0, 4.0, 2.0), "Новый текст слайда")
    _text(slide, "BrandDeck Text 9", (6.2, 3.4, 6.0, 1.8), "Текст на панели")
    caption = _text(slide, "BrandDeck Caption", (1.6, 6.3, 3.0, 0.9), "Вопросы?")
    original |= {caption.element}  # a template caption the composer filled

    removed = {
        item["shape"]: item["reason"]
        for item in _remove_exemplar_leftovers(slide, DESIGN, original, had_text)
    }
    assert removed == {
        "Sample label": "erased_sample",
        "Icon": "attached_to_erased",
        "Under text": "under_content",
        "Avatar": "empty_photo_frame",
        "Highlight": "under_content",
        "Arrow": "under_content",
    }
    kept = {shape.name for shape in slide.shapes}
    assert {"Band", "Card", "Card icon", "Host panel", "Divider", body.name} <= kept
    assert inside.name in kept and host.name in kept and under.name not in kept
    assert icon.name not in kept and avatar.name not in kept


def _template(path: Path) -> None:
    prs = _deck()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _rect(slide, "Sample card", (0.6, 1.4, 3.0, 1.6), text="Текст")
    _rect(slide, "Accent", (5.0, 2.0, 2.0, 1.5))
    _rect(slide, "Avatar", (0.6, 5.2, 0.9, 0.9), kind=MSO_SHAPE.OVAL)
    _text(slide, "Speaker", (1.7, 5.2, 3.0, 0.9), "Имя спикера")
    _rect(slide, "Highlight", (1.8, 5.5, 2.5, 0.2))
    _rect(slide, "Card", (8.0, 4.0, 3.0, 1.5), text="Заголовок")
    _rect(slide, "Card icon", (8.1, 4.1, 0.4, 0.4), kind=MSO_SHAPE.OVAL)
    prs.save(path)


def test_audit_reports_leftovers_against_the_template(tmp_path: Path):
    template = tmp_path / "template.pptx"
    _template(template)
    prs = Presentation(template)
    slide = prs.slides[0]
    for shape in slide.shapes:
        if shape.name == "Sample card":
            shape.text_frame.text = ""
        if shape.name in {"Speaker", "Card"}:
            shape.text_frame.text = "Вопросы?"
            shape.name = f"BrandDeck Text {shape.shape_id}"
    _text(slide, "BrandDeck Body", (4.4, 1.5, 3.2, 1.2), "Текст поверх акцента")
    output = tmp_path / "output.pptx"
    prs.save(output)

    report = inspect_presentation(output, template_path=template)
    found = {
        (issue["code"], issue["shape"].split(" / ")[0]) for issue in report["issues"]
    }
    assert found == {
        ("emptied_template_block", "Sample card"),
        ("template_overlap", "Accent"),
        ("template_overlap", "Highlight"),
        ("empty_photo_frame", "Avatar"),
    }
    assert report["status"] == "warning" and report["score"] < 100


def test_audit_accepts_clean_slide(tmp_path: Path):
    template = tmp_path / "template.pptx"
    _template(template)
    prs = Presentation(template)
    slide = prs.slides[0]
    for shape in list(slide.shapes):
        if shape.name in {"Accent", "Avatar", "Highlight"}:
            shape.element.getparent().remove(shape.element)
        elif shape.has_text_frame:
            shape.text_frame.text = "Новый текст"
            shape.name = f"BrandDeck Text {shape.shape_id}"
    output = tmp_path / "output.pptx"
    prs.save(output)
    codes = {issue["code"] for issue in inspect_presentation(output, template_path=template)["issues"]}
    assert not codes & {"template_overlap", "emptied_template_block", "empty_photo_frame"}


@pytest.mark.skipif(not find_libreoffice(), reason="LibreOffice is unavailable")
def test_rendered_fill_flags_sparse_content_slides(tmp_path: Path):
    prs = _deck()
    for _ in range(3):
        prs.slides.add_slide(prs.slide_layouts[6])
    cover, sparse, full = prs.slides
    _text(cover, "BrandDeck Title", (1, 3, 8, 1), "Обложка")
    line = _text(sparse, "BrandDeck Body", (0.8, 1.0, 11.0, 5.5), "Один короткий тезис.")
    line.text_frame.paragraphs[0].runs[0].font.size = Pt(20)
    picture = tmp_path / "picture.png"
    Image.new("RGB", (800, 600), (40, 90, 160)).save(picture)
    _text(full, "BrandDeck Body", (0.8, 1.0, 4.5, 1.0), "Текст рядом с изображением.")
    image = full.shapes.add_picture(str(picture), Inches(5.8), Inches(0.9), Inches(6.8), Inches(5.1))
    image.name = "BrandDeck Image"
    output = tmp_path / "output.pptx"
    prs.save(output)

    assert export_presentation(output, tmp_path / "exports", expected_slide_count=3)["status"] == "passed"
    report = inspect_rendered_fill(
        tmp_path / "exports" / "output.pdf", output, ["cover", "content", "content"]
    )
    assert [item["slide"] for item in report["slides"]] == [2, 3]
    assert [(i["slide"], i["code"]) for i in report["issues"]] == [(2, "slide_underfilled")]
    assert report["issues"][0]["repairable"] is True
