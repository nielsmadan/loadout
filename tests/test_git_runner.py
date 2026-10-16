"""What every git loadout starts runs with (review 4, batch 1.1)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from loadout import git_runner
from loadout.git_runner import environment, toplevel

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
    assert environment(index="given", base=HOOK)["GIT_INDEX_FILE"] == str(
        Path("index.lock").absolute()
    )


def test_a_named_index_is_the_one_used() -> None:
    assert environment(index=Path("/tmp/scratch-index"), base=HOOK)["GIT_INDEX_FILE"] == (
        "/tmp/scratch-index"
    )


def test_toplevel_answers_inside_a_repository_and_none_outside(tmp_path: Path) -> None:
    """Review 5, batch 3.3: five probes each failed their own way."""
    repo = tmp_path / "repo"
    (repo / "sub").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    outside = tmp_path / "outside"
    outside.mkdir()

    assert (toplevel(repo / "sub"), toplevel(outside)) == (repo.resolve(), None)


def test_toplevel_is_none_without_git_to_ask(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))

    assert toplevel(tmp_path) is None


def test_a_streamed_git_starts_without_a_hook_s_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review 5, batch 5.8: `start` was not checked to pass `environment()`."""
    monkeypatch.setenv("GIT_DIR", "/elsewhere/.git")
    started: dict[str, object] = {}

    def popen(command: list[str], **kwargs: object) -> object:
        started.update(kwargs)
        return object()

    git_runner.local_variables()  # asked of git once, before Popen is replaced
    monkeypatch.setattr(git_runner.subprocess, "Popen", popen)

    git_runner.start(tmp_path, "cat-file", "--batch")

    environ = started["env"]
    assert isinstance(environ, dict)
    assert (environ.get("GIT_DIR"), environ["GIT_OPTIONAL_LOCKS"]) == (None, "0")
