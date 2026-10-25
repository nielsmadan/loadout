"""A heading two instruction fragments both bring into one document.

dreamhack/app1 rendered two `## React Native project tooling` sections, one from the template
and one hand-copied into the project's own instructions. `check` and `sync` name the heading
and the fragments it came from, as a note, never a failure.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import loadout
from loadout.notices import repeated_headings

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
