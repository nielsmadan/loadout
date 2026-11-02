"""A source init would write that render refuses is reported with render's own message.

Init validates its plan by rendering the serialized source in isolation. When render refuses,
the refusal is what the user must act on, such as two servers to rename, so it is the issue's
message under its own code, `source-refused`.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import loadout
from loadout.init_workflow import SKILL_ADVICE

pytestmark = pytest.mark.migration_integration

OPENCODE_OVERLAP = {"permission": {"bash": {"*": "ask"}, "a_*": "allow", "a_b_x": "deny"}}
OPENCODE_REFUSAL = (
    "opencode: MCP rule 'a/*' is 'a_*', which also matches tools of server 'a_b'; "
    "rename 'a' or 'a_b'"
)
PI_SERVERS = {
    "mcpServers": {
        "my-svc": {"command": "srv"},
        "my_svc": {"command": "srv"},
    }
}
PI_REFUSAL = (
    "pi: MCP servers 'my-svc' and 'my_svc' are both 'my_svc' to Pi, which rejects the "
    "second; rename one"
)


def _git(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    return root


def _write(path: Path, document: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document))


def _refused(argv: list[str], capsys, source: Path, message: str) -> None:
    assert loadout.main([*argv, "--dry-run", "--json"]) == 2
    preview = json.loads(capsys.readouterr().out)
    assert [(i["code"], i["paths"], i["message"]) for i in preview["issues"]] == [
        ("source-refused", [str(source)], message)
    ]
    assert preview["recommendation"] == SKILL_ADVICE

    assert loadout.main([*argv, "--dry-run"]) == 2
    captured = capsys.readouterr()
    assert f"source-refused: {message}" in captured.out + captured.err


@pytest.mark.parametrize(
    ("harness", "native", "message"),
    [
        ("opencode", ("opencode.json", OPENCODE_OVERLAP), OPENCODE_REFUSAL),
        ("pi", (".pi/mcp.json", PI_SERVERS), PI_REFUSAL),
    ],
)
def test_project_init_reports_the_refusal_render_gives(
    tmp_path: Path, capsys, harness: str, native: tuple[str, dict[str, object]], message: str
) -> None:
    root = _git(tmp_path / "p")
    relative, document = native
    _write(root / relative, document)
    argv = ["init", "project", "--root", str(root), "--harness", harness]
    _refused(argv, capsys, root / "loadout", message)


def test_global_init_reports_the_refusal_render_gives(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    _write(fake_home / ".config/opencode/opencode.json", OPENCODE_OVERLAP)
    source = _git(tmp_path / "source")
    argv = ["init", "global", str(source), "--harness", "opencode"]
    _refused(argv, capsys, source / "loadout", OPENCODE_REFUSAL)
