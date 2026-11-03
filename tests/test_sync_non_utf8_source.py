"""A source file that is not UTF-8 is refused by name, not with a decoder traceback."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import loadout

pytestmark = pytest.mark.integration

AGENT = "---\nname: a\ndescription: d\n---\nx\n"


@pytest.fixture
def project(tmp_path: Path, capsys) -> Path:
    root = tmp_path / "p"
    (root / ".claude/agents").mkdir(parents=True)
    (root / "CLAUDE.md").write_text("# P\n")
    (root / ".claude/agents/a.md").write_text(AGENT)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    assert (
        loadout.main(["init", "project", "--root", str(root), "--harness", "claude", "--yes"]) == 0
    )
    (root / "loadout/mcp.toml").write_text('[docs]\ntransport = "stdio"\ncommand = "d"\n')
    assert loadout.main(["sync", "--root", str(root)]) == 0
    capsys.readouterr()
    return root


@pytest.mark.parametrize(
    "relative", ["instructions/main.md", "permissions.toml", "mcp.toml", "agents/a.md"]
)
@pytest.mark.parametrize("command", ["sync", "check"])
def test_a_source_file_that_is_not_utf8_is_named(
    project: Path, capsys, relative: str, command: str
) -> None:
    source = project / "loadout" / relative
    source.write_bytes(source.read_bytes() + b"# \xff\n")

    assert loadout.main([command, "--root", str(project)]) == 3
    assert capsys.readouterr().err == (
        f"loadout: {source}: not UTF-8: invalid start byte; re-encode it as UTF-8\n"
    )
