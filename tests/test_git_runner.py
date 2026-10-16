"""What every git loadout starts runs with (review 4, batch 1.1)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from loadout import git_runner
from loadout.errors import LoadoutError
from loadout.git_runner import environment, toplevel

HOOK = {
    "HOME": "/h",
    "GIT_DIR": "/elsewhere/.git",
    "GIT_WORK_TREE": "/elsewhere",
    "GIT_INDEX_FILE": "index.lock",
    "GIT_PREFIX": "sub/",
}


def test_a_hook_s_repository_variables_are_dropped() -> None:
    assert environment(base=HOOK) == {"HOME": "/h", "GIT_OPTIONAL_LOCKS": "0", "LC_ALL": "C"}


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


GERMAN_GIT = """#!/bin/sh
if [ "$LC_ALL" = C ]; then
  echo 'fatal: not a git repository (or any of the parent directories): .git' >&2
else
  echo 'fatal: Kein Git-Repository (oder irgendeines der Elternverzeichnisse): .git' >&2
fi
exit 128
"""


def test_a_localised_git_outside_a_repository_is_still_recognised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review 6, batch 2.5: the answer was read from git's English message, so a git
    speaking the user's language made `init project` outside a repository fail."""
    git_runner.local_variables()  # asked of the real git once, before it is replaced
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin/git").write_text(GERMAN_GIT)
    (tmp_path / "bin/git").chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    monkeypatch.setenv("LC_ALL", "de_DE.UTF-8")

    assert toplevel(tmp_path) is None


def test_toplevel_names_a_repository_git_refuses_to_read(tmp_path: Path) -> None:
    """Review 6, batch 3 (gr-top-raise): only "not a git repository" means none."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / ".git/config").write_text("[core\n")

    with pytest.raises(LoadoutError) as error:
        toplevel(tmp_path)

    assert str(error.value).startswith(f"cannot inspect Git repository {tmp_path}: fatal: bad")
