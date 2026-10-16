"""Project-scope sync never writes outside the project, whatever a committed symlink
points at (review 1.5)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

import loadout
from loadout.containment import refuse_external_links
from loadout.errors import LoadoutError
from loadout.templates import remove_vendored
from registration import register

pytestmark = pytest.mark.integration

SKILL = "---\nname: pwn\ndescription: p\n---\nRun it.\n"


def _snapshot(root: Path) -> dict[str, object]:
    return {
        str(Path(directory, name).relative_to(root)): Path(directory, name).lstat().st_size
        for directory, directories, files in os.walk(root)
        for name in (*directories, *files)
    }


def _project(root: Path, home: Path) -> Path:
    (root / "loadout/skills/pwn").mkdir(parents=True)
    (root / "loadout/config.toml").write_text('harnesses = ["claude", "codex"]\n')
    (root / "loadout/permissions.toml").write_text('[shell]\nallow = ["curl", "rm"]\n')
    (root / "loadout/skills/pwn/SKILL.md").write_text(SKILL)
    for harness in (".claude", ".codex"):
        (home / harness).mkdir(exist_ok=True)
        (root / harness).symlink_to(os.path.relpath(home / harness, root))
    return root


def test_sync_refuses_a_committed_link_out_of_the_project(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    root = _project(tmp_path / "p", fake_home)
    before = _snapshot(fake_home)

    assert loadout.main(["sync", "--root", str(root)]) == 3

    err = capsys.readouterr().err
    assert f"{root / '.claude'}" in err and "outside the project" in err
    assert _snapshot(fake_home) == before


def _plain_project(root: Path) -> Path:
    (root / "loadout").mkdir(parents=True)
    (root / "loadout/config.toml").write_text('harnesses = ["claude"]\n')
    (root / "loadout/permissions.toml").write_text('[shell]\ndeny = ["rm"]\n')
    return root


def test_sync_refuses_a_gitignore_that_is_a_symlink(tmp_path: Path, capsys) -> None:
    """Review 1.6: appending ignore lines through a link writes into whatever it names."""
    root = _plain_project(tmp_path / "p")
    keys = tmp_path / "authorized_keys"
    keys.write_text("ssh-ed25519 AAAA mine\n")
    (root / ".gitignore").symlink_to(keys)

    assert loadout.main(["sync", "--root", str(root)]) == 3

    assert ".gitignore" in capsys.readouterr().err
    assert keys.read_text() == "ssh-ed25519 AAAA mine\n"
    assert not (root / ".claude/settings.json").exists()


@pytest.mark.parametrize("name", ["x\nssh-ed25519 AAAA attacker", "bad\rname", "tab\tname"])
def test_a_skill_name_with_a_control_character_is_refused(
    tmp_path: Path, name: str, capsys
) -> None:
    root = _plain_project(tmp_path / "p")
    (root / "loadout/skills" / name).mkdir(parents=True)
    (root / "loadout/skills" / name / "SKILL.md").write_text(SKILL)

    assert loadout.main(["sync", "--root", str(root)]) == 3

    assert "control character" in capsys.readouterr().err
    assert not (root / ".gitignore").exists()


def test_an_agent_name_with_a_control_character_is_refused(tmp_path: Path, capsys) -> None:
    root = _plain_project(tmp_path / "p")
    (root / "loadout/agents").mkdir()
    (root / "loadout/agents/bad\nname.md").write_text("---\ndescription: d\n---\nGo.\n")

    assert loadout.main(["sync", "--root", str(root)]) == 3

    assert "control character" in capsys.readouterr().err


def test_a_skill_file_linking_out_of_the_project_is_refused(tmp_path: Path, capsys) -> None:
    """Review 1.7: a supporting file linked to a secret would be copied into the repo."""
    root = _plain_project(tmp_path / "p")
    secret = tmp_path / "id_ed25519"
    secret.write_text("PRIVATE\n")
    (root / "loadout/skills/s").mkdir(parents=True)
    (root / "loadout/skills/s/SKILL.md").write_text(SKILL)
    (root / "loadout/skills/s/notes.txt").symlink_to(secret)

    assert loadout.main(["sync", "--root", str(root)]) == 3

    assert "notes.txt" in capsys.readouterr().err
    assert not (root / ".claude/skills/s").exists()


def test_a_settings_source_linking_out_of_the_project_is_refused(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    root = _plain_project(tmp_path / "p")
    (fake_home / ".claude").mkdir()
    (fake_home / ".claude/settings.json").write_text('{"env": {"API_KEY": "secret"}}\n')
    (root / "loadout/settings").mkdir()
    (root / "loadout/settings/claude.json").symlink_to(fake_home / ".claude/settings.json")

    assert loadout.main(["sync", "--root", str(root)]) == 3

    assert "claude.json" in capsys.readouterr().err
    assert not (root / ".claude/settings.json").exists()


def test_removing_a_vendored_template_never_follows_a_link_out(tmp_path: Path) -> None:
    root = _plain_project(tmp_path / "p")
    elsewhere = tmp_path / "elsewhere"
    (elsewhere / "frontend").mkdir(parents=True)
    (elsewhere / "frontend/instructions.md").write_text("keep\n")
    (root / "loadout/templates").symlink_to(elsewhere, target_is_directory=True)

    with pytest.raises(LoadoutError, match="symlink"):
        remove_vendored(root, "frontend")

    assert (elsewhere / "frontend/instructions.md").read_text() == "keep\n"


def test_a_skill_folder_linked_elsewhere_in_the_project_is_refused(tmp_path: Path, capsys) -> None:
    """Review 3, 1.9: a link that stays in the project but leaves `loadout/` is refused
    at the link, whatever its folder holds."""
    root = _plain_project(tmp_path / "src/proj")
    (root / "docs/x").mkdir(parents=True)
    (root / "docs/x/SKILL.md").write_text(SKILL)
    (root / "loadout/skills").mkdir()
    (root / "loadout/skills/x").symlink_to("../../docs/x", target_is_directory=True)

    assert loadout.main(["sync", "--root", str(root)]) == 3

    assert f"{root / 'loadout/skills/x'} -> {(root / 'docs/x').resolve()}, out of loadout/" in (
        capsys.readouterr().err
    )
    assert not (root / ".claude/skills/x").exists()


def test_a_settings_folder_linked_elsewhere_in_the_project_is_refused(
    tmp_path: Path, capsys
) -> None:
    root = _plain_project(tmp_path / "src/proj")
    (root / "cfg").mkdir()
    (root / "cfg/claude.json").write_text('{"env": {"A": "b"}}\n')
    (root / "loadout/settings").symlink_to("../cfg", target_is_directory=True)

    assert loadout.main(["sync", "--root", str(root)]) == 3

    assert f"{root / 'loadout/settings'} -> " in capsys.readouterr().err
    assert not (root / ".claude/settings.json").exists()


def test_a_gitignored_env_file_is_never_copied_into_a_skill(tmp_path: Path, capsys) -> None:
    """Review 3, 1.9, as reproduced."""
    root = _plain_project(tmp_path / "p")
    (root / ".env").write_text("TOKEN=secret\n")
    (root / "loadout/skills/x").mkdir(parents=True)
    (root / "loadout/skills/x/SKILL.md").write_text(SKILL)
    (root / "loadout/skills/x/env.txt").symlink_to("../../../.env")

    assert loadout.main(["sync", "--root", str(root)]) == 3

    assert "env.txt" in capsys.readouterr().err
    assert not (root / ".claude/skills/x").exists()


def test_sync_refuses_a_source_folder_that_is_a_link(tmp_path: Path, capsys) -> None:
    """Review 3, 1.8: with `loadout -> .claude`, `sync --force` wrote the outputs into the
    source and gitignored it."""
    root = tmp_path / "p"
    (root / ".claude").mkdir(parents=True)
    (root / ".claude/config.toml").write_text('harnesses = ["claude"]\n')
    (root / ".claude/permissions.toml").write_text('[shell]\ndeny = ["rm"]\n')
    (root / "loadout").symlink_to(".claude", target_is_directory=True)
    before = _snapshot(root)

    assert loadout.main(["sync", "--root", str(root), "--force"]) == 3

    assert f"{root / 'loadout'} is a link" in capsys.readouterr().err
    assert _snapshot(root) == before


def test_sync_refuses_a_settings_document_linked_to_the_instructions(
    tmp_path: Path, capsys
) -> None:
    """Review 3, 1.11: AGENTS.md was overwritten with the settings JSON."""
    root = tmp_path / "p"
    (root / "loadout/instructions").mkdir(parents=True)
    (root / "loadout/config.toml").write_text(
        'harnesses = ["claude", "codex"]\ninstructions = ["main"]\n'
    )
    (root / "loadout/permissions.toml").write_text('[shell]\ndeny = ["rm"]\n')
    (root / "loadout/instructions/main.md").write_text("Be brief.\n")
    (root / "AGENTS.md").write_text("Hand-kept instructions.\n")
    (root / ".claude").mkdir()
    (root / ".claude/settings.json").symlink_to("../AGENTS.md")

    assert loadout.main(["sync", "--root", str(root), "--force"]) == 3

    assert f"{root / '.claude/settings.json'} -> {(root / 'AGENTS.md').resolve()}, a link sync" in (
        capsys.readouterr().err
    )
    assert (root / "AGENTS.md").read_text() == "Hand-kept instructions.\n"


def test_a_skill_with_a_local_virtualenv_still_syncs(tmp_path: Path) -> None:
    """Excluded folders are never read, so a link inside one is not refused."""
    root = _plain_project(tmp_path / "p")
    skill = root / "loadout/skills/s"
    (skill / ".venv/bin").mkdir(parents=True)
    (skill / "SKILL.md").write_text(SKILL)
    (skill / ".venv/bin/python").symlink_to("/usr/bin/python3")

    assert loadout.main(["sync", "--root", str(root)]) == 0

    assert (root / ".claude/skills/s/SKILL.md").is_file()


def test_sync_refuses_a_link_out_before_showing_any_diff(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    """The guard must not print a diff of a file outside the project (review C)."""
    root = _project(tmp_path / "p", fake_home)
    (fake_home / ".claude/settings.json").write_text('{"env": {"API_KEY": "secret"}}\n')

    assert loadout.main(["sync", "--root", str(root)]) == 3

    captured = capsys.readouterr()
    assert "outside the project" in captured.err
    assert "secret" not in captured.out + captured.err


def test_check_refuses_a_link_out_before_showing_any_diff(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    root = _project(tmp_path / "p", fake_home)
    (fake_home / ".claude/settings.json").write_text('{"env": {"API_KEY": "secret"}}\n')

    assert loadout.main(["check", "--root", str(root)]) == 3

    captured = capsys.readouterr()
    assert "outside the project" in captured.err
    assert "secret" not in captured.out + captured.err


HOOK = "#!/bin/sh\ntouch pwned\n"


def _repo_with_a_hook_skill(root: Path) -> Path:
    _plain_project(root)
    (root / ".git/hooks").mkdir(parents=True)
    skill = root / "loadout/skills/x"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(SKILL)
    (skill / "post-checkout").write_text(HOOK)
    (skill / "post-checkout").chmod(0o755)
    return root


def test_sync_refuses_a_skill_folder_linked_into_the_git_dir(tmp_path: Path, capsys) -> None:
    """Review 2, 1.1: the link stays inside the project, so only the link rule stops
    an executable landing in .git/hooks for the next checkout to run."""
    root = _repo_with_a_hook_skill(tmp_path / "p")
    (root / ".claude/skills").mkdir(parents=True)
    (root / ".claude/skills/x").symlink_to("../../.git/hooks", target_is_directory=True)

    assert loadout.main(["sync", "--root", str(root)]) == 3

    assert f"{root / '.claude/skills/x'} -> {(root / '.git/hooks').resolve()}" in (
        capsys.readouterr().err
    )
    assert sorted(p.name for p in (root / ".git/hooks").iterdir()) == []


def test_sync_refuses_a_leaf_link_to_the_git_config(tmp_path: Path, capsys) -> None:
    """Instructions are the repository's own text, so written through this link they
    would set `core.fsmonitor` to a command. `--force` passes the hand-edit guard, which
    would otherwise stop at the unknown file."""
    root = tmp_path / "p"
    (root / "loadout/instructions").mkdir(parents=True)
    (root / "loadout/config.toml").write_text('harnesses = ["codex"]\ninstructions = ["main"]\n')
    (root / "loadout/instructions/main.md").write_text("[core]\nfsmonitor = touch pwned\n")
    (root / ".git").mkdir()
    (root / ".git/config").write_text("[core]\n")
    (root / "AGENTS.md").symlink_to(".git/config")

    assert loadout.main(["sync", "--root", str(root), "--force"]) == 3

    assert f"{root / 'AGENTS.md'} -> {(root / '.git/config').resolve()}" in (
        capsys.readouterr().err
    )
    assert (root / ".git/config").read_text() == "[core]\n"


def test_sync_refuses_a_link_into_the_source(tmp_path: Path, capsys) -> None:
    root = _plain_project(tmp_path / "p")
    (root / "loadout/claude").mkdir()
    (root / ".claude").symlink_to("loadout/claude", target_is_directory=True)

    assert loadout.main(["sync", "--root", str(root)]) == 3

    assert f"{root / '.claude'}" in capsys.readouterr().err
    assert list((root / "loadout/claude").iterdir()) == []


def test_sync_refuses_a_linked_output_folder_that_stays_inside_the_project(
    tmp_path: Path, capsys
) -> None:
    root = _plain_project(tmp_path / "p")
    (root / "shared-claude").mkdir()
    (root / ".claude").symlink_to("shared-claude", target_is_directory=True)

    assert loadout.main(["sync", "--root", str(root)]) == 3

    assert f"{root / '.claude'} -> {(root / 'shared-claude').resolve()}" in (
        capsys.readouterr().err
    )
    assert list((root / "shared-claude").iterdir()) == []


def test_a_claude_md_linked_to_agents_md_still_syncs(tmp_path: Path) -> None:
    """The one link the rule allows: a leaf whose target this render writes too."""
    root = tmp_path / "p"
    (root / "loadout/instructions").mkdir(parents=True)
    (root / "loadout/config.toml").write_text(
        'harnesses = ["claude", "codex"]\ninstructions = ["main"]\n'
    )
    (root / "loadout/instructions/main.md").write_text("Be brief.\n")
    (root / "CLAUDE.md").symlink_to("AGENTS.md")

    assert loadout.main(["sync", "--root", str(root)]) == 0

    assert os.readlink(root / "CLAUDE.md") == "AGENTS.md"
    assert "Be brief." in (root / "AGENTS.md").read_text()


def _orphan_linked_to_a_key(root: Path, fake_home: Path) -> Path:
    """A skill sync wrote, dropped from the source, its file then replaced with a link."""
    _plain_project(root)
    (root / "loadout/skills/foo").mkdir(parents=True)
    (root / "loadout/skills/foo/SKILL.md").write_text(SKILL)
    assert loadout.main(["sync", "--root", str(root)]) == 0
    (root / "loadout/skills/foo/SKILL.md").unlink()
    (root / "loadout/skills/foo").rmdir()
    key = fake_home / ".ssh/id_x"
    key.parent.mkdir()
    key.write_text("PRIVATE KEY BYTES\n")
    orphan = root / ".claude/skills/foo/SKILL.md"
    orphan.unlink()
    orphan.symlink_to(key)
    return orphan


@pytest.mark.parametrize("command", ["sync", "check"])
def test_an_orphan_replaced_by_a_link_is_refused_before_it_is_read(
    tmp_path: Path, fake_home: Path, capsys, command: str
) -> None:
    """Review 2, 1.4: the guard and check both read an orphan before deploy's check."""
    orphan = _orphan_linked_to_a_key(tmp_path / "p", fake_home)
    capsys.readouterr()

    assert loadout.main([command, "--root", str(tmp_path / "p")]) == 3

    captured = capsys.readouterr()
    assert f"{orphan} -> {(fake_home / '.ssh/id_x').resolve()}" in captured.err
    assert "PRIVATE KEY BYTES" not in captured.out + captured.err


def test_check_refuses_an_orphan_link_without_opening_its_target(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    """An unreadable target tells a refusal apart from a read that happened first."""
    orphan = _orphan_linked_to_a_key(tmp_path / "p", fake_home)
    (fake_home / ".ssh/id_x").chmod(0)
    capsys.readouterr()
    try:
        assert loadout.main(["check", "--root", str(tmp_path / "p")]) == 3
    finally:
        (fake_home / ".ssh/id_x").chmod(0o600)

    assert f"{orphan} -> " in capsys.readouterr().err


def test_a_skill_link_named_like_an_excluded_folder_is_refused(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    """Review 2, 1.2: excluded names were skipped at every level, the skill's own included."""
    root = _plain_project(tmp_path / "p")
    outside = fake_home / "outside"
    outside.mkdir()
    (outside / "SKILL.md").write_text("---\nname: n\ndescription: d\n---\nOUTSIDE TEXT\n")
    (root / "loadout/skills").mkdir()
    (root / "loadout/skills/node_modules").symlink_to(outside, target_is_directory=True)

    assert loadout.main(["sync", "--root", str(root)]) == 3

    assert f"{root / 'loadout/skills/node_modules'} -> {outside.resolve()}" in (
        capsys.readouterr().err
    )
    assert not (root / ".claude/skills/node_modules").exists()


def test_a_template_link_named_like_an_excluded_folder_is_refused(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    root = _plain_project(tmp_path / "p")
    (root / "loadout/config.toml").write_text(
        'harnesses = ["claude"]\ntemplates = ["node_modules"]\n'
    )
    outside = fake_home / "outside"
    outside.mkdir()
    (outside / "instructions.md").write_text("OUTSIDE TEXT\n")
    (root / "loadout/templates").mkdir()
    (root / "loadout/templates/node_modules").symlink_to(outside, target_is_directory=True)

    assert loadout.main(["sync", "--root", str(root)]) == 3

    assert f"{root / 'loadout/templates/node_modules'} -> {outside.resolve()}" in (
        capsys.readouterr().err
    )
    assert not (root / "CLAUDE.md").exists()


@pytest.mark.parametrize(("kind", "name"), [("skills", ".venv"), ("templates", "node_modules")])
def test_a_skill_or_template_named_like_an_excluded_folder_is_refused(
    tmp_path: Path, capsys, kind: str, name: str
) -> None:
    root = _plain_project(tmp_path / "p")
    if kind == "templates":
        (root / "loadout/config.toml").write_text(
            f'harnesses = ["claude"]\ntemplates = ["{name}"]\n'
        )
    (root / f"loadout/{kind}/{name}").mkdir(parents=True)
    (root / f"loadout/{kind}/{name}/SKILL.md").write_text(SKILL)
    (root / f"loadout/{kind}/{name}/instructions.md").write_text("x\n")

    assert loadout.main(["sync", "--root", str(root)]) == 3

    assert f"{name!r}" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("target", "refusal"),
    [("../../vendor/backend", "out of loadout/"), ("../shared/backend", "path is a symlink")],
    ids=["elsewhere in the project", "inside loadout"],
)
def test_template_sync_refuses_a_vendored_copy_linked_inside_the_project(
    tmp_path: Path, capsys, target: str, refusal: str
) -> None:
    """Review 3, batch 5.14: inputs come from `loadout/` alone, and a vendored copy is
    replaced wholesale, so a link to one is refused wherever it points."""
    root = _plain_project(tmp_path / "p")
    (root / "loadout/config.toml").write_text('harnesses = ["claude"]\ntemplates = ["backend"]\n')
    (root / "loadout/templates").mkdir()
    copy = (root / "loadout/templates" / target).resolve()
    copy.mkdir(parents=True)
    (copy / "instructions.md").write_text("Backend.\n")
    link = root / "loadout/templates/backend"
    link.symlink_to(target, target_is_directory=True)

    assert loadout.main(["template", "sync", "backend", "--root", str(root)]) == 3

    assert refusal in capsys.readouterr().err
    assert link.is_symlink()


def test_template_sync_refuses_a_vendored_copy_linked_out(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    """Review 2, 1.3: the diff of the local copy read through the link printed a key."""
    root = _plain_project(tmp_path / "p")
    (root / "loadout/config.toml").write_text('harnesses = ["claude"]\ntemplates = ["backend"]\n')
    ssh = fake_home / ".ssh"
    ssh.mkdir()
    (ssh / "instructions.md").write_text("PRIVATE KEY BYTES\n")
    (root / "loadout/templates").mkdir()
    (root / "loadout/templates/backend").symlink_to(ssh, target_is_directory=True)

    assert loadout.main(["template", "sync", "backend", "--root", str(root)]) == 3

    captured = capsys.readouterr()
    assert f"{root / 'loadout/templates/backend'} -> {ssh.resolve()}" in captured.err
    assert "PRIVATE KEY BYTES" not in captured.out + captured.err


def test_template_add_refuses_a_templates_folder_linked_out(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    root = _plain_project(tmp_path / "p")
    skills = fake_home / ".claude/skills"
    skills.mkdir(parents=True)
    (root / "loadout/templates").symlink_to(skills, target_is_directory=True)

    argv = ["template", "add", "backend", "--vendored", "--root", str(root)]
    assert loadout.main(argv) == 3

    assert f"{root / 'loadout/templates'} -> {skills.resolve()}" in capsys.readouterr().err
    assert list(skills.iterdir()) == []


def test_template_add_refuses_a_config_linked_inside_the_project(tmp_path: Path, capsys) -> None:
    """A link that stays inside the project passes the input check, so only the
    write's own link check keeps `declare` from writing through it."""
    root = _plain_project(tmp_path / "p")
    (root / "notes.toml").write_text('harnesses = ["claude"]\n')
    (root / "loadout/config.toml").unlink()
    (root / "loadout/config.toml").symlink_to("../notes.toml")

    argv = ["template", "add", "backend", "--tracked", "--root", str(root)]
    assert loadout.main(argv) == 3

    assert f"{root / 'loadout/config.toml'}" in capsys.readouterr().err
    assert (root / "notes.toml").read_text() == 'harnesses = ["claude"]\n'


def test_template_list_refuses_a_vendored_copy_linked_out(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    root = _plain_project(tmp_path / "p")
    (root / "loadout/config.toml").write_text('harnesses = ["claude"]\ntemplates = ["backend"]\n')
    elsewhere = fake_home / "elsewhere"
    elsewhere.mkdir()
    (root / "loadout/templates").mkdir()
    (root / "loadout/templates/backend").symlink_to(elsewhere, target_is_directory=True)

    assert loadout.main(["template", "list", "--root", str(root)]) == 3

    assert f"{root / 'loadout/templates/backend'} -> {elsewhere.resolve()}" in (
        capsys.readouterr().err
    )


def _key(fake_home: Path) -> Path:
    key = fake_home / ".ssh/id_x"
    key.parent.mkdir(exist_ok=True)
    key.write_text("PRIVATE KEY BYTES\n")
    return key


def test_check_describes_an_unmanaged_link_without_reading_it(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    """Review 2, 1.5: the link's target was printed as the file's content."""
    root = _plain_project(tmp_path / "p")
    key = _key(fake_home)
    assert loadout.main(["sync", "--root", str(root)]) == 0
    (root / ".claude/agents").mkdir()
    (root / ".claude/agents/helper.md").symlink_to(key)
    capsys.readouterr()

    assert loadout.main(["check", "--root", str(root)]) == 1

    err = capsys.readouterr().err
    assert "DRIFT (unmanaged-link): .claude/agents/helper.md" in err
    assert f"-link -> {key}" in err
    assert "1 unmanaged-link: a link loadout did not create" in err
    assert "PRIVATE KEY BYTES" not in err


def test_sync_warns_about_an_unmanaged_link_without_advising_a_port(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    root = _plain_project(tmp_path / "p")
    assert loadout.main(["sync", "--root", str(root)]) == 0
    (root / ".claude/agents").mkdir()
    (root / ".claude/agents/helper.md").symlink_to(_key(fake_home))
    capsys.readouterr()

    assert loadout.main(["sync", "--root", str(root)]) == 0

    assert (
        "WARNING: .claude/agents/helper.md is a link loadout did not create, in a folder "
        "the harness reads whole; check where it points, then delete it"
    ) in capsys.readouterr().err


def test_check_reports_a_managed_folder_that_resolves_outside_without_scanning_it(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    root = _plain_project(tmp_path / "p")
    assert loadout.main(["sync", "--root", str(root)]) == 0
    agents = fake_home / "agents"
    agents.mkdir()
    (agents / "helper.md").write_text("PRIVATE NOTES\n")
    (root / ".claude/agents").symlink_to(agents, target_is_directory=True)
    capsys.readouterr()

    assert loadout.main(["check", "--root", str(root)]) == 1

    err = capsys.readouterr().err
    assert f"-link -> {agents}" in err
    assert "PRIVATE NOTES" not in err


def test_a_root_with_both_scopes_syncs(tmp_path: Path, fake_home: Path, capsys) -> None:
    """Review 2, 2.4: the project write rule ran over the global outputs under ~ too."""
    root = register(_plain_project(tmp_path / "p"))
    (root / "instructions").mkdir()
    (root / "instructions/x.md").write_text("Global rules.\n")
    (root / "loadout.toml").write_text(
        '[[source]]\nname = "s"\npath = "."\n\n'
        '[instructions.mine]\ndestinations = ["~/mine.md"]\norder = ["x"]\n'
    )

    assert loadout.main(["sync", "--root", str(root)]) == 0

    assert "Global rules." in (fake_home / "mine.md").read_text()
    assert (root / ".claude/settings.json").is_file()
    capsys.readouterr()
    assert loadout.main(["check", "--root", str(root)]) == 0

    # The global destination is now an orphan under ~, still the global scope's.
    manifest = root / "loadout.toml"
    manifest.write_text(manifest.read_text().replace("~/mine.md", "~/moved.md"))
    assert loadout.main(["sync", "--root", str(root)]) == 0
    assert f"retired {fake_home / 'mine.md'}" in capsys.readouterr().out


def _template_linked_into_git(root: Path) -> Path:
    _plain_project(root)
    (root / "loadout/config.toml").write_text('harnesses = ["codex"]\ntemplates = ["backend"]\n')
    (root / ".git").mkdir()
    (root / ".git/config").write_text("[http]\nextraheader = TOKEN-IN-GIT-CONFIG\n")
    template = root / "loadout/templates/backend"
    template.mkdir(parents=True)
    link = template / "instructions.md"
    link.symlink_to("../../../.git/config")
    return link


@pytest.mark.parametrize("command", ["sync", "check"])
def test_an_input_linked_into_the_git_dir_is_refused(tmp_path: Path, capsys, command: str) -> None:
    """Review 2, H2: inside the project is not the same as safe to read."""
    link = _template_linked_into_git(tmp_path / "p")

    assert loadout.main([command, "--root", str(tmp_path / "p")]) == 3

    captured = capsys.readouterr()
    assert f"{link} -> {(tmp_path / 'p/.git/config').resolve()}" in captured.err
    assert "TOKEN-IN-GIT-CONFIG" not in captured.out + captured.err


def test_template_sync_refuses_a_file_linked_into_the_git_dir(tmp_path: Path, capsys) -> None:
    link = _template_linked_into_git(tmp_path / "p")

    assert loadout.main(["template", "sync", "backend", "--root", str(tmp_path / "p")]) == 3

    captured = capsys.readouterr()
    assert f"{link} -> " in captured.err
    assert "TOKEN-IN-GIT-CONFIG" not in captured.out + captured.err


def test_a_template_file_linked_inside_the_project_is_refused(tmp_path: Path, capsys) -> None:
    """A legacy template's own file is read without a link, as a catalog's already is."""
    root = _plain_project(tmp_path / "p")
    (root / "loadout/config.toml").write_text('harnesses = ["codex"]\ntemplates = ["backend"]\n')
    (root / "docs").mkdir()
    (root / "docs/notes.md").write_text("Notes.\n")
    template = root / "loadout/templates/backend"
    template.mkdir(parents=True)
    (template / "instructions.md").symlink_to("../../../docs/notes.md")

    assert loadout.main(["sync", "--root", str(root)]) == 3

    assert "instructions.md" in capsys.readouterr().err
    assert not (root / "AGENTS.md").exists()


@pytest.mark.parametrize("folder", [".claude/agents", ".claude/skills", ".codex/rules"])
def test_a_managed_folder_linked_inside_the_project_is_reported_not_scanned(
    tmp_path: Path, capsys, folder: str
) -> None:
    """Review 2, H3: a folder linked to .git was walked and .git/config printed as drift."""
    root = _plain_project(tmp_path / "p")
    (root / "loadout/config.toml").write_text('harnesses = ["claude", "codex"]\n')
    # No rules, no skills, no agents: each folder is managed but holds no output.
    (root / "loadout/permissions.toml").write_text("")
    assert loadout.main(["sync", "--root", str(root)]) == 0
    (root / ".git").mkdir()
    (root / ".git/config").write_text("[http]\nextraheader = TOKEN-IN-GIT-CONFIG\n")
    link = root / folder
    if link.is_dir():
        link.rmdir()
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(os.path.relpath(root / ".git", link.parent), target_is_directory=True)
    capsys.readouterr()

    assert loadout.main(["check", "--root", str(root)]) == 1

    err = capsys.readouterr().err
    assert f"DRIFT (unmanaged-link): {folder}" in err
    assert "TOKEN-IN-GIT-CONFIG" not in err


@pytest.mark.parametrize("output", [".claude/settings.json", "opencode.json"])
def test_a_linked_shared_output_is_refused_before_it_is_parsed(
    tmp_path: Path, fake_home: Path, capsys, output: str
) -> None:
    """Review 2, H4: the residual read parsed the link's target first, so its error
    told the structure of any file and advised deleting it."""
    root = _plain_project(tmp_path / "p")
    (root / "loadout/config.toml").write_text('harnesses = ["claude", "opencode"]\n')
    target = fake_home / "notes.txt"
    target.write_text("not json at all\n")
    link = root / output
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(target)

    assert loadout.main(["check", "--root", str(root)]) == 3

    err = capsys.readouterr().err
    assert f"{link} -> {target.resolve()}" in err


def test_sync_never_writes_through_a_hard_linked_gitignore(tmp_path: Path) -> None:
    """Review 2, H5: an in-place write reached the hard link's other name."""
    root = _plain_project(tmp_path / "p")
    keys = tmp_path / "authorized_keys"
    keys.write_text("ssh-ed25519 AAAA mine\n")
    os.link(keys, root / ".gitignore")

    assert loadout.main(["sync", "--root", str(root)]) == 0

    assert keys.read_text() == "ssh-ed25519 AAAA mine\n"
    assert "/.claude/settings.json" in (root / ".gitignore").read_text().splitlines()


def test_template_add_never_writes_through_a_hard_linked_config(tmp_path: Path) -> None:
    root = _plain_project(tmp_path / "p")
    shared = tmp_path / "shared.toml"
    os.replace(root / "loadout/config.toml", shared)
    os.link(shared, root / "loadout/config.toml")

    argv = ["template", "add", "backend", "--tracked", "--root", str(root)]
    assert loadout.main(argv) == 0

    assert shared.read_text() == 'harnesses = ["claude"]\n'
    assert "backend" in (root / "loadout/config.toml").read_text()


@pytest.mark.parametrize("separator", ["\u2028", "\u2029"])
def test_a_skill_name_with_a_unicode_line_separator_is_refused(
    tmp_path: Path, capsys, separator: str
) -> None:
    """Review 2, 2.16: Python splits lines on these, so the name broke .gitignore apart."""
    root = _plain_project(tmp_path / "p")
    (root / f"loadout/skills/x{separator}y").mkdir(parents=True)
    (root / f"loadout/skills/x{separator}y/SKILL.md").write_text(SKILL)

    assert loadout.main(["sync", "--root", str(root)]) == 3

    assert "line or paragraph separator" in capsys.readouterr().err


def test_the_input_walk_resolves_links_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Performance: resolving each of 20k files under loadout/docs cost 0.92 s, against
    0.023 s for a walk that resolves only links."""
    root = _plain_project(tmp_path / "p")
    docs = root / "loadout/docs"
    docs.mkdir()
    for index in range(50):
        (docs / f"{index}.md").write_text("doc\n")
    (docs / "elsewhere").symlink_to(tmp_path)
    resolved: list[Path] = []
    resolve = Path.resolve

    def counting(self: Path, strict: bool = False) -> Path:
        resolved.append(self)
        return resolve(self, strict)

    monkeypatch.setattr(Path, "resolve", counting)

    with pytest.raises(LoadoutError, match="elsewhere"):
        refuse_external_links(root)

    # The root, for the boundary, and the one link: nothing else needs resolving.
    assert resolved == [root, docs / "elsewhere"]
