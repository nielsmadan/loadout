"""What a render reads of a git tree, selected from literal entries: the link rules need
no repository (review 5, batch 3.1)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from loadout.git_tree import Entry, select_inputs

FILE, LINK = "100644", "120000"


def _entries(files: list[str], links: dict[str, str]) -> tuple[dict[str, Entry], dict[str, bytes]]:
    entries = {name: Entry(FILE, f"f-{name}") for name in files}
    entries.update({name: Entry(LINK, f"l-{name}") for name in links})
    contents = {f"l-{name}": target.encode() for name, target in links.items()}
    return entries, contents


def test_a_linked_source_folder_brings_its_files_and_its_artifact_folders() -> None:
    entries, contents = _entries(
        ["real/src/permissions.toml", "real/src/instructions/x.md", "real/src/skills/s/SKILL.md"],
        {"lib": "real"},
    )
    entries["config/loadout.toml"] = Entry(FILE, "m")
    contents["m"] = b'[[source]]\nname = "s"\npath = "../lib/src"\nuse = ["instructions"]\n'

    assert sorted(select_inputs(Path("config"), entries, contents)) == [
        "config/loadout.toml",
        "lib",
        "real/src/instructions/x.md",
        "real/src/permissions.toml",
    ]


MANY_LINKS = """
from pathlib import Path
from loadout.git_tree import Entry, select_inputs
entries = {f"assets/{i}/f{j}.txt": Entry("100644", f"x{i}-{j}") for i in range(400) for j in range(50)}
entries.update({f"skills/s{i}": Entry("120000", f"l{i}") for i in range(500)})
entries.update({f"shared/s{i}/SKILL.md": Entry("100644", f"s{i}") for i in range(500)})
entries["loadout.toml"] = Entry("100644", "m")
contents = {f"l{i}": f"../shared/s{i}".encode() for i in range(500)}
contents["m"] = b'[[source]]\\nname = "s"\\npath = "."\\n'
print(len(select_inputs(Path("."), entries, contents)))
"""


@pytest.mark.integration
def test_many_links_among_many_entries_are_followed_in_linear_time() -> None:
    """Review 5, batch 3.1: every link was asked about every path, and every entry about
    every target: 200 links took the pre-commit hook from 2 s to 15 s."""
    try:
        result = subprocess.run(
            [sys.executable, "-c", MANY_LINKS],
            capture_output=True,
            check=True,
            text=True,
            timeout=20,
        )
    except subprocess.TimeoutExpired:
        pytest.fail("following 500 links took longer than 20 s")

    # The manifest, the links and the skills they name; never the assets.
    assert result.stdout == "1001\n"


SELECT = """
import json, sys
from pathlib import Path
from loadout.git_tree import Entry, select_inputs
request = json.load(sys.stdin)
entries = {name: Entry(*entry) for name, entry in request["entries"].items()}
contents = {oid: text.encode() for oid, text in request["contents"].items()}
print(json.dumps(sorted(select_inputs(Path(request["relative"]), entries, contents))))
"""


def _selected_in_a_child(relative: str, links: dict[str, str], manifest: str) -> list[str]:
    """`select_inputs` over links alone, run in a child given 20 s: a cycle the selection
    stops following loops forever when it regresses, so it must fail, never hang."""
    entries = {name: [LINK, f"l-{name}"] for name in links}
    entries[f"{relative}/loadout.toml".removeprefix("./")] = [FILE, "m"]
    contents = {f"l-{name}": target for name, target in links.items()} | {"m": manifest}
    request = {"relative": relative, "entries": entries, "contents": contents}
    try:
        result = subprocess.run(
            [sys.executable, "-c", SELECT],
            input=json.dumps(request),
            capture_output=True,
            check=True,
            text=True,
            timeout=20,
        )
    except subprocess.TimeoutExpired:
        pytest.fail("following the links did not end within 20 s")
    return list(json.loads(result.stdout))


@pytest.mark.integration
def test_a_link_that_resolves_through_itself_is_left_unresolved() -> None:
    """Review 5, batch 2.5: `lp -> lp/b` mapped `lp/src` to `lp/b/src`, then `lp/b/b/src`,
    without end. The file system gives up on it, and so does the selection."""
    manifest = '[[source]]\nname = "s"\npath = "lp/src"\n'

    assert _selected_in_a_child("loadout", {"loadout/lp": "lp/b"}, manifest) == [
        "loadout/loadout.toml",
        "loadout/lp",
    ]


@pytest.mark.integration
@pytest.mark.parametrize(
    "links",
    [{"a": "b", "b": "a"}, {"a": "b/x", "b": "a/y"}],
    ids=["naming each other", "growing"],
)
def test_a_cycle_of_links_is_followed_to_its_end(links: dict[str, str]) -> None:
    """Review 5, batch 5.2: with nothing to stop it, following a cycle looped forever."""
    manifest = '[[source]]\nname = "s"\npath = "a"\n'

    assert _selected_in_a_child(".", links, manifest) == ["a", "b", "loadout.toml"]


def test_a_linked_artifact_folder_brings_what_is_nested_in_it() -> None:
    """Review 5, batch 5.5: an artifact folder is read whole, through a link too."""
    entries, contents = _entries(
        ["shared/skills/a/SKILL.md", "shared/skills/a/scripts/run.sh", "shared/other.md"],
        {"skills": "shared/skills"},
    )
    entries["loadout.toml"] = Entry(FILE, "m")
    contents["m"] = b'[[source]]\nname = "s"\npath = "."\nuse = ["skills"]\n'

    assert sorted(select_inputs(Path("."), entries, contents)) == [
        "loadout.toml",
        "shared/skills/a/SKILL.md",
        "shared/skills/a/scripts/run.sh",
        "skills",
    ]


@pytest.mark.parametrize(
    ("links", "target"),
    [({"bases/b.json": "../real/base.json"}, "real/base.json"), ({"bases": "real"}, "real/b.json")],
    ids=["the file", "a folder above it"],
)
def test_a_linked_permission_base_brings_its_target(links: dict[str, str], target: str) -> None:
    """Review 5, batch 5.6: a base file is read through a link at it or above it."""
    entries, contents = _entries([target], links)
    entries["config/loadout.toml"] = Entry(FILE, "m")
    contents["m"] = (
        b'[[source]]\nname = "s"\npath = "."\n\n'
        b'[permissions.p]\noutput = "out.json"\nrender = "opencode"\nbase = "../bases/b.json"\n'
    )

    assert sorted(select_inputs(Path("config"), entries, contents)) == sorted(
        [*links, "config/loadout.toml", target]
    )
