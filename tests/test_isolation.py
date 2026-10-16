"""The benchmark helpers isolate the same variables the test suite does (review 2, 2.9):
a list missing XDG_STATE_HOME let a benchmark's init delete the developer's own backup."""

from __future__ import annotations

from pathlib import Path

import pytest

import conftest
import init_evidence


@pytest.mark.parametrize("variable", ["XDG_STATE_HOME", "FACTORY_HOME_OVERRIDE"])
def test_the_benchmark_environment_drops_every_redirect_the_suite_clears(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, variable: str
) -> None:
    monkeypatch.setenv(variable, "/real/location")

    environment = init_evidence.isolated_environment(tmp_path / "home")

    assert variable not in environment
    assert environment["HOME"] == str(tmp_path / "home")


def test_the_suite_and_the_benchmarks_share_one_list() -> None:
    assert init_evidence.HARNESS_VARIABLES is conftest.HARNESS_VARIABLES
