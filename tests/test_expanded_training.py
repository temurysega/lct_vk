import copy
import json

import pytest
from pptx import Presentation
from pptx.util import Inches

from slide_agent.planner import _slide_requirements
from training.data_utils import check_split
from training.prepare_expanded import expanded_notebook, split_families
from training.template_families import cluster_sources, fingerprint


def row(family, group, template=None):
    template = template or family
    patterns = [
        {
            "id": f"slide-{i}",
            "layout_index": i,
            "master_index": 0,
            "roles": ["content"],
            "capacity": {
                "body_chars": 200 + 50 * i,
                "title_chars": 80,
                "usable_body_zones": 1,
                "rhetorical_pattern": "content",
            },
        }
        for i in range(3)
    ]
    return {
        "slide": {"title": group, "role": "content", "bullets": ["fact"], "body": ""},
        "requirements": _slide_requirements(
            {"title": group, "role": "content", "bullets": ["fact"], "body": ""}
        ),
        "positive": patterns[0],
        "alternatives": patterns[1:],
        "template": template,
        "template_sha256": template,
        "source_slide": 1,
        "group": group,
        "design_family": family,
    }


def test_variants_and_augmentations_remain_in_one_split():
    rows = [
        row("brand", "first", "brand-blue"),
        row("brand", "second", "brand-red"),
        row("valid", "third"),
        row("test", "fourth"),
        row("brand", "shared"),
        row("test", "shared"),
        row("brand", "first"),
    ]
    parts, dropped = split_families(
        rows, {"brand": "train", "valid": "validation", "test": "test"}
    )
    assert [len(parts[n]) for n in ("train", "validation", "test")] == [6, 1, 1]
    assert dropped == {
        "cross_split_shared_content": 2,
        "within_family_repeated_content": 1,
    }
    assert {r["provenance"]["template"] for r in parts["train"]} == {
        "brand-blue",
        "brand-red",
    }
    check_split(parts["train"], parts["validation"], parts["test"])
    bad = copy.deepcopy(parts["test"])
    bad[0]["provenance"]["design_family"] = "brand"
    with pytest.raises(ValueError, match="design_family"):
        check_split(parts["train"], parts["validation"], bad)
    del bad[0]["provenance"]["design_family"]
    with pytest.raises(ValueError, match="Missing design_family"):
        check_split(parts["train"], parts["validation"], bad)


def test_structural_duplicate_ignores_text_colour_and_filenames(tmp_path):
    sources = []
    for name, text in [("blue", "Original content"), ("red", "Different content")]:
        deck = Presentation()
        slide = deck.slides.add_slide(deck.slide_layouts[6])
        slide.shapes.add_textbox(Inches(1), Inches(1), Inches(6), Inches(1)).text = text
        path = tmp_path / (name + ".pptx")
        deck.save(path)
        sources.append(
            {"name": path.name, "design_family": name, "fingerprint": fingerprint(path)}
        )
    assert sources[0]["fingerprint"]["sha256"] != sources[1]["fingerprint"]["sha256"]
    edges = cluster_sources(sources)
    assert len({s["design_family"] for s in sources}) == 1
    assert edges[0]["reason"] == "same_geometry_ignoring_text_and_colour"


def test_expanded_notebook_preserves_original_and_is_valid_python(tmp_path):
    import ast

    from training.build_cpu_notebook import notebook

    original = tmp_path / "original.ipynb"
    original.write_text(json.dumps(notebook()))
    before = original.read_bytes()
    result = expanded_notebook(original, "lct_cpu_expanded")
    assert original.read_bytes() == before
    assert "check_split" in str(result)
    assert "MyDrive/lct_cpu_expanded" in str(result)
    for cell in result["cells"]:
        if cell["cell_type"] == "code":
            ast.parse("".join(cell["source"]))
            assert cell["outputs"] == [] and cell["execution_count"] is None


def test_cpu_report_macro_averages_families_not_rows(monkeypatch):
    from training import check_gguf
    from training.prepare_drive import make_record

    rows = [
        make_record(row("small", "a"), 0),
        make_record(row("large", "b"), 0),
        make_record(row("large", "c"), 0),
    ]
    answers = iter(
        [
            rows[0]["messages"][-1]["content"],
            '{"choice":"invalid"}',
            '{"choice":"invalid"}',
        ]
    )
    monkeypatch.setattr(
        check_gguf,
        "post_json",
        lambda *a, **kw: {"choices": [{"message": {"content": next(answers)}}]},
    )
    report = check_gguf.evaluate("unused", rows, None)
    assert report["weak_label_accuracy"] == pytest.approx(1 / 3)
    assert report["macro_family_accuracy"] == 0.5
    assert report["by_design_family"]["large"]["examples"] == 2
