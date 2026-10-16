"""The staged check writes out only what a render can read, not the whole repository
(review 2.8)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from loadout import git_runner, staged
from loadout.git_tree import Entry, render_inputs, tree_entries
from loadout.staged_render import Snapshot
from registration import register

pytestmark = pytest.mark.integration


def _repository(root: Path, files: dict[str, str]) -> Path:
    for name, content in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(content)
    subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    return root


def test_a_project_reads_its_loadout_folder_and_root_files(tmp_path: Path) -> None:
    root = _repository(
        tmp_path,
        {
            "loadout/config.toml": 'harnesses = ["claude"]\n',
            "loadout/skills/s/SKILL.md": "x\n",
            "CLAUDE.md": "@AGENTS.md\n",
            "src/app/big.bin": "payload\n",
            "docs/guide.md": "guide\n",
        },
    )

    inputs = render_inputs(root, Path("."), tree_entries(root))

    assert sorted(inputs) == ["CLAUDE.md", "loadout/config.toml", "loadout/skills/s/SKILL.md"]


def test_a_global_source_reads_its_folder_and_named_sources_outside_it(tmp_path: Path) -> None:
    root = _repository(
        tmp_path,
        {
            "config/loadout.toml": '[[source]]\nname = "s"\npath = "../shared"\n',
            "config/skills/a/SKILL.md": "a\n",
            "shared/permissions.toml": "[shell]\n",
            "unrelated/notes.md": "notes\n",
        },
    )

    inputs = render_inputs(root, Path("config"), tree_entries(root))

    # `config/skills` is no source's, so no render reads it.
    assert sorted(inputs) == ["config/loadout.toml", "shared/permissions.toml"]


def test_a_root_level_global_source_reads_only_what_its_sources_use(tmp_path: Path) -> None:
    """Review 3, batch 3.2: a source at the repository root wrote the whole repository out,
    and 64k files took 33 s. Its profiles' sources count too, since a profile may name its own."""
    root = _repository(
        tmp_path,
        {
            "loadout.toml": '[[source]]\nname = "s"\npath = "."\nuse = ["instructions", "permissions"]\n',
            "work.toml": '[[source]]\nname = "e"\npath = "extra"\n',
            "permissions.toml": "[shell]\n",
            "instructions/x.md": "x\n",
            "skills/s/SKILL.md": "s\n",
            "src/app.py": "pass\n",
            "extra/mcp.toml": "",
            "extra/skills/a/SKILL.md": "a\n",
            "extra/deep/notes.md": "notes\n",
        },
    )

    inputs = render_inputs(root, Path("."), tree_entries(root))

    assert sorted(inputs) == [
        "extra/mcp.toml",
        "extra/skills/a/SKILL.md",
        "instructions/x.md",
        "loadout.toml",
        "permissions.toml",
        "work.toml",
    ]


def test_the_head_snapshot_holds_only_what_ownership_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Performance, should-fix 1: a global source at a repository's root wrote the whole
    of HEAD to temp, though the HEAD pass reads only the manifests and project config."""
    register(tmp_path)
    root = _repository(
        tmp_path,
        {
            "loadout.toml": '[[source]]\nname = "own"\npath = "."\nuse = ["skills"]\n\n[claude]\n',
            "claude.toml": "",
            "work.toml": 'extends = "default"\n',
            "skills/a/SKILL.md": "---\nname: a\ndescription: A.\n---\nBody.\n",
            "assets/big.bin": "payload\n",
        },
    )
    subprocess.run(
        ["git", "-C", str(root), "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "x"],
        check=True,
    )
    written: dict[str, list[str]] = {}
    snapshot = staged.write_snapshot

    def recording(
        repository: Path, destination: Path, entries: dict[str, Entry], **kwargs: bool
    ) -> None:
        written[destination.name] = sorted(entries)
        snapshot(repository, destination, entries, **kwargs)

    monkeypatch.setattr(staged, "write_snapshot", recording)

    assert staged.check_staged(root, "work") == 0

    assert written["head"] == ["claude.toml", "loadout.toml", "work.toml"]
    assert written["index"] == ["claude.toml", "loadout.toml", "skills/a/SKILL.md", "work.toml"]


def test_head_ownership_includes_the_agents_folder(tmp_path: Path) -> None:
    """Review 3, batch 4.6: the HEAD pass named skills and module config but not agents,
    so an agent output inside the repository lost its protection once retired."""
    tree = tmp_path / "head"
    tree.mkdir()
    (tree / "loadout.toml").write_text('[[source]]\nname = "s"\npath = "."\n\n[codex]\n')
    snapshot = Snapshot(
        tree,
        tmp_path / "repo",
        Path("."),
        # A Codex home inside the repository, which the snapshot maps into its tree.
        {"HOME": str(tmp_path / "home"), "CODEX_HOME": str(tmp_path / "repo/codex")},
        (),
        registered=False,
    )

    assert tree / "codex/agents" in snapshot.global_ownership("default", render=False)


def _link(root: Path, name: str, target: str) -> None:
    (root / name).parent.mkdir(parents=True, exist_ok=True)
    (root / name).symlink_to(target)


def test_a_link_above_a_source_folder_brings_only_what_it_names(tmp_path: Path) -> None:
    """Review 4, batch 2.3: no link was followed, so a linked source read nothing."""
    for name, content in {
        "config/loadout.toml": '[[source]]\nname = "s"\npath = "../lib/src"\nuse = ["instructions"]\n',
        "real/src/permissions.toml": "[shell]\n",
        "real/src/instructions/x.md": "x\n",
        "real/src/skills/s/SKILL.md": "s\n",
        "real/other/y.md": "y\n",
    }.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(content)
    _link(tmp_path, "lib", "real")
    root = _repository(tmp_path, {})

    inputs = render_inputs(root, Path("config"), tree_entries(root))

    assert sorted(inputs) == [
        "config/loadout.toml",
        "lib",
        "real/src/instructions/x.md",
        "real/src/permissions.toml",
    ]


def test_links_are_followed_through_the_folders_they_reach(tmp_path: Path) -> None:
    for name, content in {
        "config/loadout.toml": '[[source]]\nname = "s"\npath = "../src"\nuse = ["instructions"]\n',
        "third/vendor/instr/x.md": "x\n",
        "shared/y.md": "y\n",
        "shared/z.md": "z\n",
    }.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(content)
    _link(tmp_path, "src/instructions", "../vendor/instr")
    _link(tmp_path, "vendor", "third/vendor")
    _link(tmp_path, "third/vendor/instr/y.md", "../../../shared/y.md")
    root = _repository(tmp_path, {})

    inputs = render_inputs(root, Path("config"), tree_entries(root))

    assert sorted(inputs) == [
        "config/loadout.toml",
        "shared/y.md",
        "src/instructions",
        "third/vendor/instr/x.md",
        "third/vendor/instr/y.md",
        "vendor",
    ]


def test_every_link_target_is_read_in_one_git_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review 5, batch 3.1: one `cat-file blob` per link was most of 15 s for 200 links."""
    for index in range(20):
        (tmp_path / f"shared/s{index}").mkdir(parents=True)
        (tmp_path / f"shared/s{index}/SKILL.md").write_text("s\n")
        (tmp_path / "skills").mkdir(exist_ok=True)
        (tmp_path / f"skills/s{index}").symlink_to(f"../shared/s{index}")
    (tmp_path / "loadout.toml").write_text('[[source]]\nname = "s"\npath = "."\n')
    root = _repository(tmp_path, {})
    entries = tree_entries(root)
    started: list[tuple[str, ...]] = []
    ran: list[tuple[str, ...]] = []
    real_start, real_run = git_runner.start, git_runner.run

    def start(path: Path, *arguments: str, **kwargs: bool) -> object:
        started.append(arguments)
        return real_start(path, *arguments, **kwargs)

    def run(path: Path, *arguments: str, **kwargs: object) -> object:
        ran.append(arguments)
        return real_run(path, *arguments, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(git_runner, "start", start)
    monkeypatch.setattr(git_runner, "run", run)

    inputs = render_inputs(root, Path("."), entries)

    assert (started, ran) == ([("cat-file", "--batch")], [])
    assert sum(1 for name in inputs if name.endswith("SKILL.md")) == 20
