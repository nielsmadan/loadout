"""What every git loadout starts runs with (review 4, batch 1.1)."""

from __future__ import annotations

from pathlib import Path

from loadout.git_runner import environment

HOOK = {
    "HOME": "/h",
    "GIT_DIR": "/elsewhere/.git",
    "GIT_WORK_TREE": "/elsewhere",
    "GIT_INDEX_FILE": "index.lock",
    "GIT_PREFIX": "sub/",
}


def test_a_hook_s_repository_variables_are_dropped() -> None:
    assert environment(base=HOOK) == {"HOME": "/h", "GIT_OPTIONAL_LOCKS": "0"}


def test_the_given_index_stays_when_it_is_the_one_inspected() -> None:
    assert environment(keep_index=True, base=HOOK)["GIT_INDEX_FILE"] == str(
        Path("index.lock").absolute()
    )


def test_a_named_index_is_the_one_used() -> None:
    assert environment(index=Path("/tmp/scratch-index"), base=HOOK)["GIT_INDEX_FILE"] == (
        "/tmp/scratch-index"
    )
