"""Versions of the service and of its workflow stay consistent and documented."""

import re
from pathlib import Path

from slide_agent import __version__
from slide_agent.prompt_config import PROMPT_FILES, PROMPT_ROOT, PROMPT_VERSION

ROOT = Path(__file__).resolve().parents[1]


def test_service_version_is_declared_once_and_documented():
    project = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert re.search(r'^version = "([^"]+)"', project, re.MULTILINE).group(1) == __version__
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    latest = re.search(r"^## (\d+\.\d+\.\d+) .*\(workflow (\d+\.\d+\.\d+)\)", changelog, re.MULTILINE)
    assert latest is not None
    assert latest.groups() == (__version__, PROMPT_VERSION)


def test_every_active_prompt_is_a_versioned_file_listed_in_the_changelog():
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert f"| {PROMPT_VERSION} |" in changelog
    for name, filename in PROMPT_FILES.items():
        assert re.fullmatch(rf"{re.escape(name)}-v\d+\.txt", filename)
        assert (PROMPT_ROOT / filename).is_file()
        assert filename.removesuffix(".txt") in changelog
