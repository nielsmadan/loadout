"""A secret found during adoption stays in the source and is warned about (ADR 0030): detection
decides nothing about where content goes, so tracked project content stays shared, untracked
co-owned settings stay private, and every source file holding a secret is named."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import loadout

pytestmark = pytest.mark.migration_integration

TOKEN_ARGS = ["--token", "s3cr3tvalue"]


def _git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(root), "-c", "user.email=t@e.com", "-c", "user.name=t", *args],
        check=check,
        capture_output=True,
        text=True,
    )


def _project(root: Path, files: dict[str, str]) -> Path:
    for relative, content in files.items():
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
        (root / relative).write_text(content)
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "base")
    return root


def _global_with_a_token(fake_home: Path, tmp_path: Path) -> Path:
    (fake_home / ".claude.json").write_text(
        json.dumps({"mcpServers": {"docs": {"command": "docs-mcp", "args": TOKEN_ARGS}}})
    )
    source = tmp_path / "source"
    source.mkdir()
    return source


def test_a_global_mcp_token_lands_in_the_source_and_is_named(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    source = _global_with_a_token(fake_home, tmp_path)

    argv = ["init", "global", str(source), "--harness", "claude", "--yes"]
    assert loadout.main(argv) == 0

    out = capsys.readouterr().out
    mcp = source / "loadout/mcp.toml"
    assert 'args = ["--token", "s3cr3tvalue"]' in mcp.read_text()
    assert _git(source, "check-ignore", str(mcp), check=False).returncode == 1
    assert f"Contains a secret: {mcp}: the value after --token" in out
    assert "the source now contains secrets, so keep its remote private" in out


def test_the_json_preview_and_result_list_each_secret(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    source = _global_with_a_token(fake_home, tmp_path)
    secret = {"path": str(source / "loadout/mcp.toml"), "reason": "the value after --token"}
    argv = ["init", "global", str(source), "--harness", "claude", "--json"]

    assert loadout.main([*argv, "--dry-run"]) == 0
    assert json.loads(capsys.readouterr().out)["secrets"] == [secret]

    assert loadout.main([*argv, "--yes"]) == 0
    assert json.loads(capsys.readouterr().out)["secrets"] == [secret]


def test_a_global_skill_with_a_token_goes_into_its_normal_source(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    skill = fake_home / ".claude/skills/deploy/SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("---\nname: deploy\ndescription: d\n---\n-----BEGIN PRIVATE KEY-----\n")
    source = tmp_path / "source"
    source.mkdir()

    argv = ["init", "global", str(source), "--harness", "claude", "--yes"]
    assert loadout.main(argv) == 0

    adopted = source / "loadout/skills/deploy/SKILL.md"
    assert adopted.read_text() == skill.read_text()
    assert _git(source, "check-ignore", str(adopted), check=False).returncode == 1
    assert f"Contains a secret: {adopted}: a private key" in capsys.readouterr().out


def test_a_tracked_project_skill_with_a_token_stays_shared(tmp_path: Path, capsys) -> None:
    root = _project(
        tmp_path / "p",
        {
            ".claude/skills/deploy/SKILL.md": (
                "---\nname: deploy\ndescription: d\n---\nPush with ghp_abcdefghijklmnopqrstuvwx1234.\n"
            )
        },
    )

    argv = ["init", "project", "--root", str(root), "--harness", "claude", "--yes"]
    assert loadout.main(argv) == 0

    skill = root / "loadout/skills/deploy/SKILL.md"
    assert _git(root, "check-ignore", str(skill), check=False).returncode == 1
    assert f"Contains a secret: {skill}: a token in a provider's format" in capsys.readouterr().out


def test_a_tracked_project_settings_token_is_committed_source(tmp_path: Path, capsys) -> None:
    settings = {"permissions": {"deny": ["Bash(rm:*)"]}, "env": {"DEPLOY_KEY": "abc123secret"}}
    root = _project(tmp_path / "p", {".claude/settings.json": json.dumps(settings)})

    argv = ["init", "project", "--root", str(root), "--harness", "claude", "--yes"]
    assert loadout.main(argv) == 0

    residual = root / "loadout/settings/claude.json"
    assert json.loads(residual.read_text()) == {"env": {"DEPLOY_KEY": "abc123secret"}}
    assert _git(root, "check-ignore", str(residual), check=False).returncode == 1
    assert f"Contains a secret: {residual}: env.DEPLOY_KEY" in capsys.readouterr().out


def test_an_untracked_project_settings_file_stays_private_without_a_secret(
    tmp_path: Path, capsys
) -> None:
    root = _project(tmp_path / "p", {"README.md": "x\n"})
    (root / ".claude").mkdir()
    (root / ".claude/settings.json").write_text(
        json.dumps({"permissions": {"deny": ["Bash(rm:*)"]}, "model": "opus"})
    )

    argv = ["init", "project", "--root", str(root), "--harness", "claude", "--yes"]
    assert loadout.main(argv) == 0

    residual = root / "loadout/settings/claude.json"
    assert json.loads(residual.read_text()) == {"model": "opus"}
    assert _git(root, "check-ignore", str(residual)).stdout.strip() == str(residual)
    assert (
        f"Private (stays on this machine): {root / '.claude/settings.json'}: Git does not track it"
        in capsys.readouterr().out
    )
