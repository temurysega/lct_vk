from __future__ import annotations

from pathlib import Path

import pytest

from slide_agent.analyzer import analyze_template
from slide_agent.pdf_importer import _clean_font_name
from slide_agent.utils import read_json


def test_pdf_font_names_are_normalized_for_powerpoint():
    assert _clean_font_name("ABCDEF+Helvetica-Bold") == "Arial"
    assert _clean_font_name("ArialMT") == "Arial"
    assert _clean_font_name("BrandSans-Regular") == "BrandSans"


def test_pdf_is_accepted_as_a_template(tmp_path: Path):
    pymupdf = pytest.importorskip("pymupdf")
    source = tmp_path / "reference.pdf"
    document = pymupdf.open()
    for page_index in range(2):
        page = document.new_page(width=960, height=540)
        page.draw_rect(page.rect, color=None, fill=(0.03, 0.08, 0.15))
        for index in range(12):
            x = 50 + index * 60
            page.draw_rect(
                pymupdf.Rect(x, 190, x + 42, 250),
                color=None,
                fill=(0.0, 0.45 + page_index * 0.1, 1.0),
            )
        page.insert_text((50, 90), f"Reference page {page_index + 1}", fontsize=28)
    document.save(source)
    document.close()

    analyzed = analyze_template(source, workspace=tmp_path / "workspace")
    manifest = read_json(analyzed / "manifest.json")
    design = read_json(analyzed / "design_system.json")

    assert manifest["imported_from_pdf"] is True
    assert manifest["slide_count"] == 2
    assert manifest["pdf_import"]["vectors"] >= 20
    assert (analyzed / "source.pdf").is_file()
    assert (analyzed / "original.pptx").is_file()
    assert design["source_model"]["composition_mode"] == "native_grid"
