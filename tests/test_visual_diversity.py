from pathlib import Path

from PIL import Image, ImageDraw

from slide_agent.service import _diversity_hybrid_plan
from slide_agent.utils import write_json
from slide_agent.visual_diversity import inspect_visual_diversity


def _variant(root: Path, name: str, *, shift: int = 0, tiny: bool = False) -> dict:
    previews = []
    for number in range(1, 6):
        image = Image.new("RGB", (320, 180), "white")
        if number in (2, 3, 4):
            draw = ImageDraw.Draw(image)
            x = 20 + (shift if number in (2, 3) else 0)
            draw.rectangle((x, 40, x + 60, 110), fill="#124EA0")
            if tiny:
                draw.point((10, 10), fill="#111111")
        path = root / f"{name}-{number}.png"
        image.save(path)
        previews.append({"slide": number, "path": str(path)})
    return {"variant": {"id": name}, "exports": {"previews": previews}}


def test_diversity_requires_visible_changes_on_multiple_content_slides(tmp_path: Path):
    roles = ["cover", "content", "content", "content", "closing"]
    variants = [
        _variant(tmp_path, "balanced"),
        _variant(tmp_path, "columns", tiny=True),
        _variant(tmp_path, "focus", shift=80),
    ]
    report = inspect_visual_diversity(variants, roles)
    assert report["status"] == "needs_review"
    assert report["pairs"][0]["changed_slides"] == []
    assert report["pairs"][1]["changed_slides"] == [2, 3]
    assert report["required_changed_slides_per_pair"] == 2


def test_diversity_passes_when_each_pair_changes_two_slides(tmp_path: Path):
    roles = ["cover", "content", "content", "content", "closing"]
    variants = [
        _variant(tmp_path, "balanced"),
        _variant(tmp_path, "columns", shift=40),
        _variant(tmp_path, "focus", shift=80),
    ]
    assert inspect_visual_diversity(variants, roles)["status"] == "passed"


def test_hybrid_fallback_uses_donor_layouts_on_only_two_slides(tmp_path: Path):
    results = []
    for variant in ("balanced", "columns", "focus"):
        directory = tmp_path / variant
        directory.mkdir()
        write_json(
            directory / "deck_plan.final.json",
            {
                "slides": [
                    {"title": f"Слайд {number}", "pattern_id": f"{variant}-{number}", "layout_index": number}
                    for number in range(1, 9)
                ]
            },
        )
        results.append({"variant": {"id": variant}, "presentation_dir": str(directory)})
    visual = {
        "status": "needs_review",
        "required_changed_slides_per_pair": 2,
        "pairs": [
            {"variants": ["balanced", "columns"], "status": "needs_review", "changed_slides": []},
            {"variants": ["balanced", "focus"], "status": "passed", "changed_slides": [2, 3, 6, 7]},
            {"variants": ["columns", "focus"], "status": "passed", "changed_slides": [2, 3, 6, 7]},
        ],
    }
    target, plan, record = _diversity_hybrid_plan(results, visual)
    assert target == 1 and record["slides"] == [2, 3]
    assert [slide["pattern_id"] for slide in plan["slides"]] == [
        "columns-1", "focus-2", "focus-3", "columns-4",
        "columns-5", "columns-6", "columns-7", "columns-8",
    ]
