from __future__ import annotations

import os
import subprocess
import sys
import time
from collections.abc import Sequence
from pathlib import Path

import pytest

from loadout import exclude, overlay_record
from loadout.errors import LoadoutError
from loadout.exclude import (
    BLOCK_BEGIN,
    BLOCK_END,
    GLOBAL_BEGIN,
    GLOBAL_END,
    GlobalIgnoreState,
    anchored,
    block_lines,
    global_pattern,
    offer_text,
    read_global_state,
    user_covered,
    with_block,
    write_global_block,
    write_global_state,
    write_repo_block,
)
from loadout.overlay_git import scratch_repo

APP_BEGIN = BLOCK_BEGIN.format(name="app")
APP_END = BLOCK_END.format(name="app")
DOCS_BEGIN = BLOCK_BEGIN.format(name="app-docs")
DOCS_END = BLOCK_END.format(name="app-docs")


def _app(text: str, *lines: str) -> str:
    return with_block(text, APP_BEGIN, APP_END, lines)


def test_block_into_an_empty_file() -> None:
    assert _app("", "/a") == f"{APP_BEGIN}\n/a\n{APP_END}\n"


def test_block_into_a_file_without_a_final_newline_keeps_the_user_line() -> None:
    assert _app("mine", "/a") == f"mine\n\n{APP_BEGIN}\n/a\n{APP_END}\n"


def test_block_into_a_crlf_file_keeps_the_crlf_lines() -> None:
    result = _app("one\r\ntwo\r\n", "/a")
    assert result == f"one\r\ntwo\r\n\n{APP_BEGIN}\n/a\n{APP_END}\n"
    assert _app(result) == "one\r\ntwo\r\n"


def test_block_into_a_non_utf8_file_round_trips_the_bytes() -> None:
    raw = b"caf\xe9\n"
    text = raw.decode("utf-8", "surrogateescape")
    result = _app(text, "/a").encode("utf-8", "surrogateescape")
    assert result == raw + b"\n" + f"{APP_BEGIN}\n/a\n{APP_END}\n".encode()


def test_replacing_a_block_changes_only_its_lines() -> None:
    text = f"before\n\n{APP_BEGIN}\n/old\n{APP_END}\nafter\n"
    assert _app(text, "/new", "/more") == f"before\n\n{APP_BEGIN}\n/new\n/more\n{APP_END}\nafter\n"


def test_removing_a_block_keeps_the_surrounding_lines() -> None:
    text = f"before\n{APP_BEGIN}\n/old\n{APP_END}\nafter\n"
    assert _app(text) == "before\nafter\n"


def test_add_then_remove_returns_the_original_bytes() -> None:
    original = "mine\n# note\n"
    assert _app(_app(original, "/a"), *()) == original


def test_removing_an_absent_block_changes_nothing() -> None:
    assert _app("mine\n") == "mine\n"


def test_updating_one_overlay_leaves_a_prefixed_name_intact() -> None:
    both = with_block(_app("", "/a"), DOCS_BEGIN, DOCS_END, ["/docs"])
    updated = _app(both, "/b")
    assert block_lines(updated, APP_BEGIN, APP_END) == ("/b",)
    assert block_lines(updated, DOCS_BEGIN, DOCS_END) == ("/docs",)
    assert block_lines(both, DOCS_BEGIN, DOCS_END) == ("/docs",)


def test_a_begin_without_an_end_raises() -> None:
    with pytest.raises(LoadoutError, match="closing line"):
        block_lines(f"{APP_BEGIN}\n/a\n", APP_BEGIN, APP_END)


def test_a_duplicate_block_raises() -> None:
    block = f"{APP_BEGIN}\n/a\n{APP_END}\n"
    with pytest.raises(LoadoutError, match="more than once"):
        with_block(block + block, APP_BEGIN, APP_END, ["/b"])


def test_a_begin_with_an_end_that_is_a_prefix_of_another_overlays_end_raises() -> None:
    text = f"{APP_BEGIN}\n/a\n{DOCS_BEGIN}\n/d\n{DOCS_END}\n"
    with pytest.raises(LoadoutError, match="closing line"):
        _app(text, "/b")
    assert block_lines(text, DOCS_BEGIN, DOCS_END) == ("/d",)


def test_emptying_blocks_that_are_not_last_does_not_grow_the_blank_lines() -> None:
    text = "mine\n"
    for _ in range(3):
        text = _app(text, "/a")
        text = with_block(text, DOCS_BEGIN, DOCS_END, ["/d"])
        text = _app(text)
        text = with_block(text, DOCS_BEGIN, DOCS_END, [])
    assert text == "mine\n"


@pytest.mark.integration
def test_an_unclosed_block_error_names_the_file(tmp_path: Path) -> None:
    broken = tmp_path / "ignore"
    broken.write_text(f"{GLOBAL_BEGIN}\n**/x\n")
    with pytest.raises(LoadoutError, match=str(broken)):
        write_global_block(broken, lambda _: ["**/y"])
    with scratch_repo() as scratch, pytest.raises(LoadoutError, match=str(broken)):
        user_covered(scratch, broken, ("x",))


def test_a_non_regular_ignore_file_is_refused(tmp_path: Path) -> None:
    folder = tmp_path / "ignore"
    folder.mkdir()
    with pytest.raises(LoadoutError, match=f"{folder} must be a regular file"):
        write_global_block(folder, lambda _: ["**/y"])


def test_the_callback_sees_the_users_lines_at_write_time(tmp_path: Path) -> None:
    file = tmp_path / "ignore"
    file.write_text("first\n")
    seen: list[tuple[str, ...]] = []

    def patterns(lines: Sequence[str]) -> list[str]:
        seen.append(tuple(lines))
        return ["**/a"]

    assert write_global_block(file, patterns) is True
    file.write_text(file.read_text() + "second\n")
    assert write_global_block(file, patterns) is False
    assert seen == [("first",), ("first", "", "second")]


def test_anchored_escapes_and_marks_folders() -> None:
    assert anchored("a b.md", folder=False) == "/a\\ b.md"
    assert anchored(".claude/skills/x", folder=True) == "/.claude/skills/x/"
    assert global_pattern("CLAUDE.local.md") == "**/CLAUDE.local.md"


def test_global_state_round_trips(tmp_path: Path) -> None:
    assert read_global_state() == GlobalIgnoreState("unasked", None)
    state = GlobalIgnoreState("accepted", tmp_path / "ignore")
    write_global_state(state)
    assert read_global_state() == state


def test_global_state_beside_a_real_overlay_record_leaves_the_records_readable(
    tmp_path: Path, fake_home: Path
) -> None:
    folder = tmp_path / "app"
    folder.mkdir()
    overlay_record.write_overlay_record(overlay_record.empty_record(folder, "app", fake_home))
    write_global_state(GlobalIgnoreState("declined", None))
    scan = overlay_record.scan_overlay_records()
    assert [found.name for found in scan.records] == ["app"]
    assert scan.problems == ()


def test_global_state_beside_overlay_records_is_unreadable_when_malformed() -> None:
    exclude.global_state_path().parent.mkdir(parents=True, exist_ok=True)
    exclude.global_state_path().write_text('{"answer": "maybe", "file": null}')
    with pytest.raises(LoadoutError, match="unexpected contents"):
        read_global_state()


@pytest.fixture
def git_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "githome"
    home.mkdir()
    (home / "gitconfig").write_text("")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / "xdg"))
    monkeypatch.setenv("XDG_STATE_HOME", str(home / "state"))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(home / "gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    return home


def _git(root: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, text=True
    )
    return done.stdout


def _repo(path: Path) -> Path:
    path.mkdir(parents=True)
    _git(path, "init", "-q", "-b", "main")
    return path.resolve()


@pytest.mark.integration
def test_repo_block_ignores_a_new_file_and_keeps_the_hand_written_line(
    tmp_path: Path, git_home: Path
) -> None:
    repo = _repo(tmp_path / "repo")
    info = repo / ".git" / "info" / "exclude"
    info.write_text("/.claude/skills/review-react\n")

    changed = write_repo_block(repo / ".git", "app", [anchored(".codex/config.toml", folder=False)])

    (repo / ".codex").mkdir()
    (repo / ".codex" / "config.toml").write_text("x\n")
    assert changed is True
    assert info.read_text() == (
        f"/.claude/skills/review-react\n\n{APP_BEGIN}\n/.codex/config.toml\n"
        f".loadout-tmp-*\n{APP_END}\n"
    )
    assert _git(repo, "status", "--porcelain") == ""
    assert write_repo_block(repo / ".git", "app", ["/.codex/config.toml"]) is False


@pytest.mark.integration
def test_an_empty_pattern_list_removes_the_block(tmp_path: Path, git_home: Path) -> None:
    repo = _repo(tmp_path / "repo")
    info = repo / ".git" / "info" / "exclude"
    before = info.read_bytes()
    write_repo_block(repo / ".git", "app", ["/a"])
    assert write_repo_block(repo / ".git", "app", []) is True
    assert info.read_bytes() == before


@pytest.mark.integration
def test_a_caller_supplied_temp_pattern_is_not_duplicated(tmp_path: Path, git_home: Path) -> None:
    repo = _repo(tmp_path / "repo")
    info = repo / ".git" / "info" / "exclude"
    before = info.read_bytes()
    write_repo_block(repo / ".git", "app", ["/a", exclude.TEMP_PATTERN])
    assert block_lines(info.read_text(), APP_BEGIN, APP_END) == ("/a", exclude.TEMP_PATTERN)
    assert write_repo_block(repo / ".git", "app", [exclude.TEMP_PATTERN]) is True
    assert info.read_bytes() == before


def test_a_missing_git_directory_is_refused_and_not_created(tmp_path: Path) -> None:
    gone = tmp_path / "gone" / ".git"
    with pytest.raises(LoadoutError, match=str(gone)):
        write_repo_block(gone, "app", ["/a"])
    assert not (tmp_path / "gone").exists()


@pytest.mark.integration
def test_a_fifo_exclude_file_is_refused_without_blocking(tmp_path: Path, git_home: Path) -> None:
    repo = _repo(tmp_path / "repo")
    info = repo / ".git" / "info" / "exclude"
    info.unlink()
    os.mkfifo(info)
    with pytest.raises(LoadoutError, match=f"{info} must be a regular file"):
        write_repo_block(repo / ".git", "app", ["/a"])


@pytest.mark.integration
def test_a_symlinked_exclude_file_is_refused(tmp_path: Path, git_home: Path) -> None:
    repo = _repo(tmp_path / "repo")
    info = repo / ".git" / "info" / "exclude"
    target = tmp_path / "elsewhere"
    target.write_text("x\n")
    info.unlink()
    info.symlink_to(target)
    with pytest.raises(LoadoutError, match="symlink"):
        write_repo_block(repo / ".git", "app", ["/a"])
    assert target.read_text() == "x\n"


WRITER = (
    "import sys, time\n"
    "from pathlib import Path\n"
    "from loadout.exclude import BLOCK_BEGIN, exclude_path, write_repo_block\n"
    "git_dir, mine, other = Path(sys.argv[1]), sys.argv[2], sys.argv[3]\n"
    "while time.time() < float(sys.argv[4]):\n"
    "    pass\n"
    "import loadout.exclude as exclude\n"
    "read = exclude._read_text\n"
    "def slow(path):\n"
    "    result = read(path)\n"
    "    time.sleep(0.005)\n"
    "    return result\n"
    "exclude._read_text = slow\n"
    "seen = False\n"
    "for i in range(40):\n"
    "    write_repo_block(git_dir, mine, [f'/{mine}-{i}'])\n"
    "    present = BLOCK_BEGIN.format(name=other) in exclude_path(git_dir).read_text()\n"
    "    if seen and not present:\n"
    "        sys.exit(f'{other} block vanished after {mine} wrote {i}')\n"
    "    seen = seen or present\n"
)


@pytest.mark.integration
def test_concurrent_writers_of_different_overlays_keep_both_blocks(
    tmp_path: Path, git_home: Path
) -> None:
    repo = _repo(tmp_path / "repo")
    git_dir = str(repo / ".git")
    start = str(time.time() + 2)
    children = [
        subprocess.Popen(
            [sys.executable, "-c", WRITER, git_dir, mine, other, start],
            stderr=subprocess.PIPE,
            text=True,
        )
        for mine, other in (("one", "two"), ("two", "one"))
    ]
    try:
        errors = [child.communicate(timeout=120)[1] for child in children]
    finally:
        for child in children:
            child.kill()
    assert errors == ["", ""]
    assert [child.returncode for child in children] == [0, 0]
    text = (repo / ".git" / "info" / "exclude").read_text()
    assert block_lines(text, BLOCK_BEGIN.format(name="one"), BLOCK_END.format(name="one")) == (
        "/one-39",
        ".loadout-tmp-*",
    )
    assert block_lines(text, BLOCK_BEGIN.format(name="two"), BLOCK_END.format(name="two")) == (
        "/two-39",
        ".loadout-tmp-*",
    )


@pytest.mark.integration
def test_user_covered_sees_the_users_pattern_but_not_loadouts_own_block(
    tmp_path: Path, git_home: Path
) -> None:
    mine = tmp_path / "mine"
    mine.write_text("**/CLAUDE.local.md\n")
    ours = tmp_path / "ours"
    write_global_block(ours, lambda _: [global_pattern("CLAUDE.local.md")])
    units = ("CLAUDE.local.md",)
    with scratch_repo() as scratch:
        assert user_covered(scratch, mine, units) == frozenset(units)
        assert user_covered(scratch, ours, units) == frozenset()


@pytest.mark.integration
def test_write_global_block_through_a_symlink_keeps_the_link(
    tmp_path: Path, git_home: Path
) -> None:
    target = tmp_path / "dotfiles" / "ignore"
    target.parent.mkdir()
    target.write_text("mine\n")
    link = tmp_path / "link"
    link.symlink_to(target)

    assert write_global_block(link, lambda _: ["**/CLAUDE.local.md"]) is True

    assert link.is_symlink()
    assert target.read_text() == f"mine\n\n{GLOBAL_BEGIN}\n**/CLAUDE.local.md\n{GLOBAL_END}\n"
    assert os.path.realpath(link) == str(target.resolve())


@pytest.mark.integration
def test_offer_text_names_the_repository_holding_the_file(tmp_path: Path, git_home: Path) -> None:
    repo = _repo(tmp_path / "dotfiles")
    file = repo / "git" / "ignore"
    text = offer_text(["CLAUDE.local.md", ".claude/settings.local.json"], file)
    assert f"{file}" in text
    assert f"it is in the git repository at {repo}; the block will show there as a change" in text
    assert "CLAUDE.local.md, .claude/settings.local.json" in text
    assert "Other overlay paths stay ignored per repository." in text


@pytest.mark.integration
def test_offer_text_outside_a_repository_has_no_repository_clause(
    tmp_path: Path, git_home: Path
) -> None:
    text = offer_text(["CLAUDE.local.md"], tmp_path / "plain" / "ignore")
    assert str(tmp_path / "plain" / "ignore") in text
    assert "git repository" not in text
