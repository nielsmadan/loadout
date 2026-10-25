"""A heading two instruction fragments both bring into one document.

dreamhack/app1 rendered two `## React Native project tooling` sections, one from the template
and one hand-copied into the project's own instructions. `check` and `sync` name the heading
and the fragments it came from, as a note, never a failure, in project and global documents.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import loadout
from loadout.notices import repeated_headings
from registration import register

TOOLING = "## React Native project tooling"


def test_a_heading_from_two_fragments_is_reported_with_both() -> None:
    fragments = (
        ("main.md", f"# App\n\n{TOOLING}\n\n- Use bundle exec pod install.\n"),
        ("react-native.md", f"{TOOLING}\n\n- Use bundle exec pod install.\n\n## Upgrades\n"),
        ("mobile.md", "## Devices\n\n```bash\n# App\n```\n"),
    )

    assert repeated_headings(fragments) == {TOOLING: ("main.md", "react-native.md")}


def test_a_heading_repeated_inside_one_fragment_is_its_own_structure() -> None:
    fragments = (("main.md", "## Android\n\n### Testing\n\n## iOS\n\n### Testing\n"),)

    assert repeated_headings(fragments) == {}


def _project(root: Path) -> Path:
    files = {
        "loadout/config.toml": 'harnesses = ["codex"]\ninstructions = ["main"]\ntemplates = ["rn"]\n',
        "loadout/instructions/main.md": f"# App\n\n{TOOLING}\n\n- Mine.\n",
        "loadout/templates/rn/instructions.md": f"{TOOLING}\n\n- Theirs.\n",
        "loadout/templates/rn/permissions.toml": "[shell]\nallow = []\n",
    }
    for relative, content in files.items():
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
        (root / relative).write_text(content)
    return root


def _instruction_notes(notes: list[str]) -> list[str]:
    return [note for note in notes if note.startswith("project.instructions:")]


@pytest.mark.integration
def test_check_and_sync_note_the_repeated_heading_and_its_fragments(tmp_path: Path, capsys) -> None:
    root = _project(tmp_path / "project")

    assert loadout.main(["sync", "--root", str(root)]) == 0
    synced = capsys.readouterr().out.splitlines()
    assert loadout.main(["check", "--root", str(root), "--json"]) == 0

    notes = _instruction_notes(json.loads(capsys.readouterr().out)["notes"])
    assert notes == [
        (
            f'project.instructions: the instruction document repeats the heading "{TOOLING}", '
            "from loadout/instructions/main.md and loadout/templates/rn/instructions.md; keep "
            "one section, with /loadout"
        )
    ]
    assert f"note: {notes[0]}" in synced


@pytest.mark.integration
def test_check_notes_a_heading_two_global_fragments_both_bring(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    source = tmp_path / "source"
    files = {
        "loadout.toml": '[[source]]\nname = "s"\npath = "."\n\n[claude]\ninstructions = ["a", "b"]\n',
        "instructions/a.md": f"{TOOLING}\n\n- One.\n",
        "instructions/b.md": f"# Rules\n\n{TOOLING}\n\n- Two.\n",
    }
    for relative, content in files.items():
        (source / relative).parent.mkdir(parents=True, exist_ok=True)
        (source / relative).write_text(content)
    register(source)
    assert loadout.main(["sync", "--root", str(source)]) == 0
    capsys.readouterr()

    assert loadout.main(["check", "--global", "--json"]) == 0

    notes = json.loads(capsys.readouterr().out)["notes"]
    assert [note for note in notes if "repeats the heading" in note] == [
        (
            f'claude.instructions: the instruction document repeats the heading "{TOOLING}", '
            "from instructions/a.md and instructions/b.md; keep one section, with /loadout"
        )
    ]


@pytest.mark.integration
def test_only_the_selected_profile_s_documents_are_scanned(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    source = tmp_path / "source"
    files = {
        "loadout.toml": (
            '[[source]]\nname = "s"\npath = "."\n\n[instructions.notes]\n'
            'output = "out/notes.md"\nprofile = "variant"\norder = ["a", "b"]\n'
        ),
        "instructions/a.md": f"{TOOLING}\n",
        "instructions/b.md": f"{TOOLING}\n",
    }
    for relative, content in files.items():
        (source / relative).parent.mkdir(parents=True, exist_ok=True)
        (source / relative).write_text(content)
    register(source)

    found = {}
    for profile in ("default", "variant"):
        capsys.readouterr()
        loadout.main(["check", "--global", "--profile", profile, "--json"])
        notes = json.loads(capsys.readouterr().out)["notes"]
        found[profile] = [note.split(":")[0] for note in notes if "repeats the heading" in note]

    assert found == {"default": [], "variant": ["notes.instructions"]}
