from pathlib import Path

from slide_agent.powerpoint import inspect_powerpoint_render


def test_powerpoint_render_can_be_disabled(tmp_path: Path):
    report = inspect_powerpoint_render(
        tmp_path / "missing.pptx",
        tmp_path / "render",
        expected_slide_count=1,
    )
    assert report["status"] == "skipped"
    assert report["renderer"] == "powerpoint_com"
