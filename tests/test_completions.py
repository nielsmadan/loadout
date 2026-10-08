from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

import loadout
from loadout import completions
from loadout.cli import build_parser
from loadout.completions import (
    MARKER,
    CompletionState,
    classify,
    completion_path,
    detect_shell,
    render,
)

HOME = Path("/home/someone")


@pytest.mark.parametrize(
    ("parent", "login", "expected"),
    [
        ("-zsh", "/bin/bash", "zsh"),
        ("/bin/bash", None, "bash"),
        ("uv", "/opt/homebrew/bin/fish", "fish"),
        (None, "/bin/zsh", "zsh"),
    ],
)
def test_the_invoking_shell_wins_and_the_login_shell_is_the_fallback(
    parent: str | None, login: str | None, expected: str
) -> None:
    environ = {} if login is None else {"SHELL": login}
    assert detect_shell(parent, environ) == expected


def test_a_shell_that_is_neither_running_nor_the_login_shell_is_undetected() -> None:
    assert detect_shell("python3", {"SHELL": "/bin/tcsh"}) is None


def test_each_shell_installs_where_it_loads_completions_from() -> None:
    assert completion_path("bash", {}, HOME) == HOME / (
        ".local/share/bash-completion/completions/loadout"
    )
    assert completion_path("zsh", {}, HOME) == HOME / ".local/share/zsh/site-functions/_loadout"
    assert completion_path("fish", {}, HOME) == HOME / ".config/fish/completions/loadout.fish"


def test_the_install_location_follows_the_shell_and_xdg_variables() -> None:
    environ = {
        "XDG_DATA_HOME": "/data",
        "XDG_CONFIG_HOME": "/config",
        "BASH_COMPLETION_USER_DIR": "/bash",
    }
    assert completion_path("bash", environ, HOME) == Path("/bash/completions/loadout")
    assert completion_path("zsh", environ, HOME) == Path("/data/zsh/site-functions/_loadout")
    assert completion_path("fish", environ, HOME) == Path("/config/fish/completions/loadout.fish")


@pytest.mark.parametrize("shell", completions.SHELLS)
def test_every_script_carries_the_ownership_marker(shell: str) -> None:
    assert MARKER in render(shell, build_parser()).splitlines()


def test_zsh_keeps_compdef_on_the_first_line() -> None:
    """compinit only autoloads a function whose first line is `#compdef`."""
    lines = render("zsh", build_parser()).splitlines()
    assert lines[:2] == ["#compdef loadout", MARKER]


def test_classify_tells_ours_from_someone_elses() -> None:
    expected = render("bash", build_parser())
    assert classify(None, expected) is CompletionState.ABSENT
    assert classify(expected, expected) is CompletionState.CURRENT
    assert classify(expected + "# edited\n", expected) is CompletionState.STALE
    assert classify("complete -W x loadout\n", expected) is CompletionState.FOREIGN


@pytest.mark.integration
class TestCli:
    @pytest.fixture(autouse=True)
    def _isolated(self, fake_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("XDG_DATA_HOME", str(fake_home / "data"))
        monkeypatch.delenv("BASH_COMPLETION_USER_DIR", raising=False)
        self.directory = fake_home / "data" / "bash-completion" / "completions"
        self.script = self.directory / "loadout"

    def _run(self, *verb: str) -> int:
        return loadout.main(["integrate", "completions", *verb, "--shell", "bash"])

    def test_install_status_uninstall_leave_an_unrelated_completion_alone(self, capsys) -> None:
        self.directory.mkdir(parents=True)
        unrelated = self.directory / "othertool"
        unrelated.write_text("complete -W 'a b' othertool\n")

        assert self._run("install") == 0
        assert MARKER in self.script.read_text()
        assert self._run("status") == 0
        assert "installed and current" in capsys.readouterr().out
        assert self._run("uninstall") == 0

        assert not self.script.exists()
        assert unrelated.read_text() == "complete -W 'a b' othertool\n"

    def test_status_reports_a_script_an_older_loadout_wrote(self, capsys) -> None:
        assert self._run("install") == 0
        self.script.write_text(self.script.read_text() + "# from an older release\n")
        capsys.readouterr()

        assert self._run("status") == 0
        assert "out of date" in capsys.readouterr().out
        assert self._run("install") == 0
        assert self.script.read_text() == render("bash", build_parser())

    def test_a_completion_loadout_did_not_write_is_never_replaced_or_removed(self, capsys) -> None:
        self.directory.mkdir(parents=True)
        self.script.write_text("complete -W 'mine' loadout\n")

        assert self._run("install") == 3
        assert self._run("uninstall") == 0

        assert "not written by loadout" in capsys.readouterr().out
        assert self.script.read_text() == "complete -W 'mine' loadout\n"

    def test_an_undetectable_shell_asks_for_one(self, monkeypatch, capsys) -> None:
        monkeypatch.setattr(completions, "_parent_command", lambda: "python3")
        monkeypatch.setenv("SHELL", "/bin/tcsh")

        assert loadout.main(["integrate", "completions", "install"]) == 2

        assert "pass --shell {bash,zsh,fish}" in capsys.readouterr().err
        assert not self.script.exists()

    @pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
    def test_the_bash_script_sources_and_completes_the_command_set(self) -> None:
        assert self._run("install") == 0
        probe = (
            'source "$1" || exit 1\n'
            'COMP_WORDS=(loadout ""); COMP_CWORD=1; COMP_LINE="loadout "; COMP_POINT=8\n'
            "_shtab_loadout\n"
            'echo "${COMPREPLY[*]}"\n'
        )
        result = subprocess.run(
            ["bash", "-c", probe, "probe", str(self.script)],
            capture_output=True,
            text=True,
            check=True,
        )
        typed = {"sync", "check", "init", "template", "integrate"}
        assert set(result.stdout.split()) == typed
