from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

import loadout

pytestmark = pytest.mark.migration_integration

HAND_WRITTEN = (
    'prefix_rule(pattern=["git", "status"], decision="allow")\n'
    'prefix_rule(pattern=["rm"], decision="forbidden")\n'
)


def _init(fake_home: Path, tmp_path: Path, rules: str) -> tuple[int, Path]:
    (fake_home / ".codex" / "rules").mkdir(parents=True)
    (fake_home / ".codex" / "rules" / "permissions.rules").write_text(rules)
    source = tmp_path / "source"
    source.mkdir()
    code = loadout.main(["init", "global", str(source), "--harness", "codex", "--yes"])
    return code, source / "loadout"


def test_a_hand_written_forbidden_rule_survives_adoption_as_a_deny(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    code, source = _init(fake_home, tmp_path, HAND_WRITTEN)

    assert code == 0
    shell = tomllib.loads((source / "permissions.toml").read_text())["shell"]
    assert (shell["allow"], shell["deny"]) == (["git status"], ["rm"])
    rendered = (fake_home / ".codex" / "rules" / "permissions.rules").read_text()
    assert 'prefix_rule(pattern = ["rm"], decision = "forbidden")' in rendered
    assert 'prefix_rule(pattern = ["git", "status"], decision = "allow")' in rendered
    capsys.readouterr()
    assert loadout.main(["sync", "--global"]) == 0
    assert "Up to date." in capsys.readouterr().out


def test_a_rule_adoption_cannot_read_refuses_and_leaves_the_file(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    rules = 'prefix_rule(pattern = ["rm"], decision = "forbidden", when = "always")\n'

    code, source = _init(fake_home, tmp_path, rules)

    assert code != 0
    assert "when" in capsys.readouterr().err
    assert (fake_home / ".codex" / "rules" / "permissions.rules").read_text() == rules
    assert not (source / "permissions.toml").exists()


def test_init_names_the_parameters_it_drops_before_writing(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    rules = 'prefix_rule(pattern = ["rm"], decision = "forbidden", justification = "Use trash.")\n'

    code, source = _init(fake_home, tmp_path, rules)

    assert code == 0
    assert "justification of the rule for 'rm' will be dropped" in capsys.readouterr().out
    assert tomllib.loads((source / "permissions.toml").read_text())["shell"]["deny"] == ["rm"]


APPROVAL = 'prefix_rule(pattern=["npm", "test"], decision="allow")\n'


def test_codex_approvals_are_adopted_and_their_file_deleted(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    approvals = fake_home / ".codex" / "rules" / "default.rules"
    approvals.parent.mkdir(parents=True)
    approvals.write_text(APPROVAL)
    source = tmp_path / "source"
    source.mkdir()

    assert loadout.main(["init", "global", str(source), "--harness", "codex", "--yes"]) == 0

    shell = tomllib.loads((source / "loadout" / "permissions.toml").read_text())["shell"]
    assert shell["allow"] == ["npm test"]
    rendered = (fake_home / ".codex" / "rules" / "permissions.rules").read_text()
    assert 'prefix_rule(pattern = ["npm", "test"], decision = "allow")' in rendered
    assert sorted(p.name for p in approvals.parent.iterdir()) == ["permissions.rules"]
    capsys.readouterr()
    assert loadout.main(["check", "--global"]) == 0


def test_a_project_rules_file_beside_the_rendered_one_is_adopted_and_deleted(
    tmp_path: Path, capsys
) -> None:
    root = tmp_path / "project"
    extra = root / ".codex" / "rules" / "extra.rules"
    extra.parent.mkdir(parents=True)
    extra.write_text(APPROVAL)

    argv = ["init", "project", "--root", str(root), "--harness", "codex", "--yes"]
    assert loadout.main(argv) == 0

    shell = tomllib.loads((root / "loadout" / "permissions.toml").read_text())["shell"]
    assert shell["allow"] == ["npm test"]
    assert sorted(p.name for p in extra.parent.iterdir()) == ["permissions.rules"]
    capsys.readouterr()
    assert loadout.main(["check", "--root", str(root)]) == 0
