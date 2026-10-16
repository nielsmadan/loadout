"""What a render reads of a git tree, selected from literal entries: the link rules need
no repository (review 5, batch 3.1)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from loadout.git_tree import Entry, _through_links, select_inputs

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
from loadout.git_tree import Entry, _through_links, select_inputs
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
from loadout.git_tree import Entry, _through_links, select_inputs
request = json.load(sys.stdin)
entries = {name: Entry(*entry) for name, entry in request["entries"].items()}
contents = {oid: text.encode() for oid, text in request["contents"].items()}
print(json.dumps(sorted(select_inputs(Path(request["relative"]), entries, contents))))
"""


def _selected_in_a_child(
    relative: str, links: dict[str, str], manifest: str | None, files: tuple[str, ...] = ()
) -> list[str]:
    """`select_inputs` over links and empty `files`, run in a child given 20 s: a cycle
    the selection stops following loops forever when it regresses, so it must fail,
    never hang."""
    entries = {name: [LINK, f"l-{name}"] for name in links}
    entries.update({name: [FILE, "empty"] for name in files})
    contents = {f"l-{name}": target for name, target in links.items()} | {"empty": ""}
    if manifest is not None:
        entries[f"{relative}/loadout.toml".removeprefix("./")] = [FILE, "m"]
        contents["m"] = manifest
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


@pytest.mark.integration
def test_links_that_grow_a_path_each_hop_are_followed_quickly() -> None:
    """Review 6, batch 1.3: `pN -> qN/a/…` beside `qN -> pN` grew the path by 1 KB a
    hop, and six such pairs took a commit through the hook to a minute."""
    deep = "/".join(["a"] * 512)
    links = {f"p{i}": f"q{i}/{deep}" for i in range(6)} | {f"q{i}": f"p{i}" for i in range(6)}
    manifest = '[[source]]\nname = "s"\npath = "."\n'

    assert _selected_in_a_child(".", links, manifest) == sorted([*links, "loadout.toml"])


def _path(length: int, last: str = "e") -> str:
    """A folder path `length` characters long, ending in a component of `last`."""
    head = "/".join(["d" * 99] * 10)
    return f"{head}/{last * (length - len(head) - 1)}"


@pytest.mark.parametrize(
    ("target", "followed"),
    [
        (_path(1024), True),
        (_path(1025), False),
        (_path(1023, "é"), False),
        ("/".join(["d"] * 128), True),
        ("/".join(["d"] * 129), False),
    ],
    ids=["1024 bytes", "1025 bytes", "over 1024 bytes in fewer characters", "128 deep", "129 deep"],
)
def test_a_link_to_a_path_past_the_file_system_s_bounds_is_left_unresolved(
    target: str, followed: bool
) -> None:
    """Review 6, batch 1.3: a path longer than PATH_MAX or deeper than a fixed number of
    components cannot be resolved, as ENAMETOOLONG leaves it."""
    entries, contents = _entries([f"{target}/f.md"], {"src": target})
    entries["loadout.toml"] = Entry(FILE, "m")
    contents["m"] = b'[[source]]\nname = "s"\npath = "src"\n'

    assert sorted(select_inputs(Path("."), entries, contents)) == (
        [*([f"{target}/f.md"] if followed else []), "loadout.toml", "src"]
    )


def test_a_link_walk_stops_when_its_work_budget_is_spent() -> None:
    """Review 6, batch 1.3: what is left of the walk stays unresolved."""
    entries, _ = _entries(["c/f.md"], {"a": "b", "b": "c"})
    targets = {"a": "b", "b": "c"}
    selected = {"a": entries["a"]}

    assert sorted(_through_links(entries, selected, [("a", True)], targets, budget=0)) == ["a"]
    assert sorted(_through_links(entries, selected, [("a", True)], targets)) == [
        "a",
        "b",
        "c/f.md",
    ]


def test_a_link_among_a_project_s_root_files_is_not_followed() -> None:
    """Review 6, batch 1.3: a project's render reads no file through a root link."""
    entries, contents = _entries(
        ["loadout/config.toml", "docs/agents.md"], {"CLAUDE.md": "docs/agents.md"}
    )

    assert sorted(select_inputs(Path("."), entries, contents)) == [
        "CLAUDE.md",
        "loadout/config.toml",
    ]


def test_a_root_link_of_a_dual_scope_root_s_own_source_is_followed() -> None:
    """Review 6, batch 1.3: where a global source sits at the root, its files are read."""
    entries, contents = _entries(
        ["loadout/config.toml", "docs/agents.md"], {"CLAUDE.md": "docs/agents.md"}
    )
    entries["loadout.toml"] = Entry(FILE, "m")
    contents["m"] = b'[[source]]\nname = "s"\npath = "."\n'

    assert sorted(select_inputs(Path("."), entries, contents)) == [
        "CLAUDE.md",
        "docs/agents.md",
        "loadout.toml",
        "loadout/config.toml",
    ]


@pytest.mark.integration
def test_growing_link_pairs_at_a_project_root_are_followed_quickly() -> None:
    """Review 6, batch 1.3: the reviewer's attack, six 1 KB pairs at a project root, made
    each commit through the hook take 62.9 s."""
    deep = "/".join(["a"] * 512)
    links = {f"p{i}": f"q{i}/{deep}" for i in range(6)} | {f"q{i}": f"p{i}" for i in range(6)}

    assert _selected_in_a_child(".", links, None, ("loadout/config.toml",)) == sorted(
        [*links, "loadout/config.toml"]
    )


@pytest.mark.integration
def test_a_huge_declared_source_path_is_looked_up_quickly() -> None:
    """Review 6, batch 1.3: a manifest can name a path of any length, and looking up
    every folder above it was quadratic in its length."""
    huge = "/".join(["a"] * 200_000)
    manifest = f'[[source]]\nname = "s"\npath = "{huge}"\n'

    assert _selected_in_a_child(".", {"x": "y"}, manifest) == ["loadout.toml", "x"]


def test_a_source_linked_to_the_root_reads_only_the_root_s_own_files() -> None:
    """Review 6, batch 3 (gt-under-dot): read as its files alone, never whole."""
    entries, contents = _entries(["a.md", "deep/b.md"], {"links/src": ".."})
    entries["config/loadout.toml"] = Entry(FILE, "m")
    contents["m"] = b'[[source]]\nname = "s"\npath = "../links/src"\nuse = []\n'

    assert sorted(select_inputs(Path("config"), entries, contents)) == [
        "a.md",
        "config/loadout.toml",
        "links/src",
    ]


def test_a_path_through_a_link_to_the_root_maps_to_the_root_s_folder() -> None:
    """Review 6, batch 3 (gt-join-dot): `src/sub` through `src -> .` is `sub`."""
    entries, contents = _entries(["sub/f.md"], {"src": "."})
    entries["config/loadout.toml"] = Entry(FILE, "m")
    contents["m"] = b'[[source]]\nname = "s"\npath = "../src/sub"\nuse = []\n'

    assert sorted(select_inputs(Path("config"), entries, contents)) == [
        "config/loadout.toml",
        "src",
        "sub/f.md",
    ]


def test_a_manifest_that_is_not_utf_8_declares_nothing_to_select() -> None:
    """Review 6, batch 3 (gt-declared-badtoml): the render says why it cannot read it."""
    entries, contents = _entries(["instructions/x.md"], {})
    entries["loadout.toml"] = Entry(FILE, "m")
    contents["m"] = b'[[source]]\nname = "s"\npath = "."\n# \xff\n'

    assert sorted(select_inputs(Path("."), entries, contents)) == ["loadout.toml"]
