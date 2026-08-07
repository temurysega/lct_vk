from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.util import Inches, Pt

from slide_agent.composer import _set_text_frame


def test_replacing_exemplar_text_preserves_explicit_style():
    presentation = Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[6])
    shape = slide.shapes.add_textbox(Inches(1), Inches(1), Inches(8), Inches(1))
    shape.text = "Исходный заголовок"
    run = shape.text_frame.paragraphs[0].runs[0]
    run.font.name = "VK Sans Display"
    run.font.size = Pt(32)
    run.font.bold = True
    run.font.color.rgb = RGBColor(0, 211, 227)

    _set_text_frame(shape, ["Новый заголовок"])

    output_run = shape.text_frame.paragraphs[0].runs[0]
    assert output_run.font.name == "VK Sans Display"
    assert output_run.font.size.pt == 32
    assert output_run.font.bold is True
    assert output_run.font.color.rgb == RGBColor(0, 211, 227)
