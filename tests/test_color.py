from __future__ import annotations

import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

import loadout
from loadout.color import color_enabled, paint, print_styled
from loadout.diffs import DIFF_LIMIT, colored, diff_lines, report_diff
from loadout.files import Drift
from loadout.stealth_check import StealthCheck, report_check
from registration import register


class Terminal(io.StringIO):
    def isatty(self) -> bool:
        return True


def _bold(line: str) -> str:
    return f"\x1b[1m{line.removesuffix(chr(10))}\x1b[0m\n"


def _red(line: str) -> str:
    return f"\x1b[31m{line.removesuffix(chr(10))}\x1b[0m\n"


def _green(line: str) -> str:
    return f"\x1b[32m{line.removesuffix(chr(10))}\x1b[0m\n"


def _cyan(line: str) -> str:
    return f"\x1b[36m{line.removesuffix(chr(10))}\x1b[0m\n"


def test_each_part_of_a_diff_takes_its_color() -> None:
    lines = diff_lines("doc", "a\nold\n", "a\nnew", context=1)
    assert colored(lines) == [
        _bold("--- doc (on disk)\n"),
        _bold("+++ doc (expected)\n"),
        _cyan("@@ -1,2 +1,2 @@\n"),
        " a\n",
        _red("-old\n"),
        _green("+new\n"),
        "\\ No newline at end of file\n",
    ]


def test_a_removed_line_that_looks_like_a_header_is_red() -> None:
    lines = diff_lines("q.sql", "-- note\n++ x\n", "++ y\n", context=0)
    assert colored(lines)[3:] == [_red("--- note\n"), _red("-++ x\n"), _green("+++ y\n")]


def test_lines_that_name_a_file_stay_plain_between_diffs() -> None:
    first = diff_lines("a", "x\n", "y\n", context=0)
    note = ["-kit/+odd: differs (binary)\n"]
    second = diff_lines("b", "p\n", "q\n", context=0)
    painted = colored([*first, *note, *second])
    assert painted[5] == note[0]
    assert painted[6:9] == [
        _bold("--- b (on disk)\n"),
        _bold("+++ b (expected)\n"),
        _cyan("@@ -1 +1 @@\n"),
    ]
    assert painted[9:] == [_red("-p\n"), _green("+q\n")]


def test_the_reset_comes_before_the_newline() -> None:
    assert paint("text\n", "1;33") == "\x1b[1;33mtext\x1b[0m\n"
    assert paint("text", "32") == "\x1b[32mtext\x1b[0m"


@pytest.mark.parametrize(
    ("stream", "environ", "enabled"),
    [
        (Terminal(), {}, True),
        (io.StringIO(), {}, False),
        (Terminal(), {"NO_COLOR": "1"}, False),
        (Terminal(), {"NO_COLOR": ""}, True),
        (io.StringIO(), {"FORCE_COLOR": "1"}, True),
        (io.StringIO(), {"FORCE_COLOR": ""}, False),
        (io.StringIO(), {"FORCE_COLOR": "0"}, False),
        (io.StringIO(), {"FORCE_COLOR": "false"}, False),
        (Terminal(), {"FORCE_COLOR": "0"}, True),
        (io.StringIO(), {"FORCE_COLOR": "1", "NO_COLOR": "1"}, False),
        (Terminal(), {"TERM": "dumb"}, False),
        (io.StringIO(), {"FORCE_COLOR": "1", "TERM": "dumb"}, True),
        (Terminal(), {"TERM": "xterm-256color"}, True),
    ],
)
def test_color_follows_the_stream_and_the_environment(
    stream: io.StringIO, environ: dict[str, str], enabled: bool
) -> None:
    assert color_enabled(stream, environ) is enabled


def test_a_diff_on_a_terminal_is_colored_and_its_overflow_note_is_not() -> None:
    stream = Terminal()
    lines = diff_lines("doc", "".join(f"{n}\n" for n in range(DIFF_LIMIT)), "", context=0)
    assert report_diff(lines, stream, {}) is True
    shown = stream.getvalue().splitlines(keepends=True)
    assert shown[3] == _red("-0\n")
    assert shown[-1] == f"    ... {len(lines) - DIFF_LIMIT} more diff line(s)\n"


def test_a_piped_diff_is_plain() -> None:
    stream = io.StringIO()
    lines = diff_lines("doc", "old\n", "new\n", context=0)
    report_diff(lines, stream, {})
    assert stream.getvalue() == "".join(lines)


def test_a_styled_line_is_painted_only_for_a_terminal() -> None:
    terminal, pipe = Terminal(), io.StringIO()
    print_styled("DRIFT", "1;33", terminal, {})
    print_styled("DRIFT", "1;33", pipe, {})
    assert terminal.getvalue() == "\x1b[1;33mDRIFT\x1b[0m\n"
    assert pipe.getvalue() == "DRIFT\n"


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "-c",
            "user.email=test@example.com",
            "-c",
            "user.name=test",
            "-c",
            "commit.gpgsign=false",
            "-c",
            "init.defaultBranch=main",
            *args,
        ],
        check=True,
        capture_output=True,
    )


def _adopted(root: Path) -> None:
    register(root)
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "source")
    assert loadout.main(["sync", "--root", str(root), "--force"]) == 0


@pytest.mark.integration
def test_check_on_a_terminal_colors_the_drift_and_its_diff(
    root: Path, capsys, monkeypatch: pytest.MonkeyPatch
) -> None:
    _adopted(root)
    output = root / "out" / "shared.md"
    first = output.read_text(encoding="utf-8").splitlines()[0]
    output.write_text("clobbered\n", encoding="utf-8")
    capsys.readouterr()
    monkeypatch.setenv("FORCE_COLOR", "1")

    assert loadout.main(["check", "--root", str(root)]) == 1
    err = capsys.readouterr().err
    assert "\x1b[1;33mDRIFT (hand-edited): out/shared.md\x1b[0m\n" in err
    assert "\x1b[31m-clobbered\x1b[0m\n" in err
    assert f"\x1b[32m+{first}\x1b[0m\n" in err


@pytest.mark.integration
def test_an_up_to_date_check_on_a_terminal_is_green(
    root: Path, capsys, monkeypatch: pytest.MonkeyPatch
) -> None:
    _adopted(root)
    capsys.readouterr()
    monkeypatch.setenv("FORCE_COLOR", "1")

    assert loadout.main(["check", "--root", str(root)]) == 0
    assert "\x1b[32mgenerated files are up to date\x1b[0m\n" in capsys.readouterr().out


@pytest.mark.integration
def test_an_abort_on_a_terminal_keeps_its_complete_diff_plain(
    root: Path, capsys, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _adopted(root)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    bulk = {f"key-{index}": index for index in range(60)}
    (root / "perm" / "opencode.json").write_text(
        json.dumps(bulk, indent=2) + "\n", encoding="utf-8"
    )
    capsys.readouterr()
    monkeypatch.setenv("FORCE_COLOR", "1")

    assert loadout.main(["sync", "--root", str(root)]) == 1
    err = capsys.readouterr().err
    assert '\x1b[31m-  "key-0": 0,\x1b[0m\n' in err
    kept = (tmp_path / "state" / "loadout" / "sync-abort.diff").read_text(encoding="utf-8")
    assert kept.startswith("--- perm/opencode.json (on disk)\n")
    assert '\n-  "key-0": 0,\n' in kept


def test_a_stealth_check_on_a_terminal_colors_its_drift_and_diff(
    fake_home: Path, capsys, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FORCE_COLOR", "1")
    drift = Drift(
        path=fake_home / "work" / "CLAUDE.local.md",
        actual="old\n",
        expected="new\n",
        kind="hand-edited",
    )
    report_check(StealthCheck(name="work", drift=(drift,), notes=(), errors=(), code=1))
    err = capsys.readouterr().err
    assert "\x1b[1;33mstealth work: DRIFT (hand-edited): ~/work/CLAUDE.local.md\x1b[0m\n" in err
    assert _red("-old\n") in err
    assert _green("+new\n") in err


def test_an_up_to_date_stealth_check_on_a_terminal_is_green(
    capsys, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FORCE_COLOR", "1")
    report_check(StealthCheck(name="work", drift=(), notes=(), errors=(), code=0))
    assert capsys.readouterr().out == "\x1b[32mstealth work: up to date\x1b[0m\n"


@pytest.mark.integration
def test_check_colors_only_the_stream_that_is_a_terminal(
    root: Path, capsys, monkeypatch: pytest.MonkeyPatch
) -> None:
    _adopted(root)
    (root / "out" / "shared.md").write_text("clobbered\n", encoding="utf-8")
    capsys.readouterr()
    terminal = Terminal()
    monkeypatch.setattr(sys, "stderr", terminal)
    monkeypatch.setenv("TERM", "xterm-256color")

    assert loadout.main(["check", "--root", str(root)]) == 1
    assert "\x1b[1;33mDRIFT (hand-edited): out/shared.md\x1b[0m\n" in terminal.getvalue()
    assert "\x1b[31m-clobbered\x1b[0m\n" in terminal.getvalue()


@pytest.mark.integration
def test_check_json_stays_plain_when_color_is_forced(
    root: Path, capsys, monkeypatch: pytest.MonkeyPatch
) -> None:
    _adopted(root)
    (root / "out" / "shared.md").write_text("clobbered\n", encoding="utf-8")
    capsys.readouterr()
    monkeypatch.setenv("FORCE_COLOR", "1")

    assert loadout.main(["check", "--json", "--root", str(root)]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "drift"
