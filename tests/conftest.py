from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def disable_powerpoint_com_for_unit_tests(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BRANDDECK_POWERPOINT_QA", "0")
