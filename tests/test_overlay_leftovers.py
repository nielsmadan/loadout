from __future__ import annotations

from pathlib import Path

from loadout.overlay_leftovers import exclude_leftover, overlay_blocks, registry_leftover

UVX = "uvx --python 3.13 --from git+https://github.com/nielsmadan/loadout.git@v0.10.0"
PATH = Path("/config/overlays.toml")


def test_overlay_blocks_names_each_0_10_0_block_once() -> None:
    text = (
        "# git ls-files --others --exclude-from=.git/info/exclude\n"
        "# >>> loadout overlay work: managed by loadout; lines inside this block are replaced\n"
        "/CLAUDE.local.md\n"
        "# <<< loadout overlay work\n"
        "# >>> loadout overlays: personal files, managed by loadout\n"
        "# >>> loadout stealth home: managed by loadout; lines inside this block are replaced\n"
        "# >>> loadout overlay side-2: managed by loadout; lines inside this block are replaced\n"
        "# >>> loadout overlay work: managed by loadout; lines inside this block are replaced\n"
    )

    assert overlay_blocks(text) == ("work", "side-2")


def test_an_exclude_with_two_blocks_names_both_and_the_generic_command() -> None:
    text = "# >>> loadout overlay a: x\n# >>> loadout overlay b: x\n"

    assert exclude_leftover(Path("/r/.git/info/exclude"), text) == (
        "/r/.git/info/exclude still holds the 0.10.0 overlay blocks a, b; remove each first "
        f"with `{UVX} loadout overlay remove NAME --yes`"
    )


def test_the_registry_0_10_0_remove_leaves_behind_is_not_a_leftover() -> None:
    header = (
        "# Overlays registered on this machine, managed by `loadout overlay add` and\n"
        "# `loadout overlay remove`.\n"
    )

    assert [registry_leftover(PATH, text) for text in (header, "[overlay]\n")] == [None, None]


def test_a_registry_whose_overlay_key_is_not_a_table_cannot_be_read() -> None:
    assert registry_leftover(PATH, 'overlay = "work"\n') == (
        f"{PATH} is 0.10.0's overlay registry and cannot be read (`overlay` is not a table); "
        f"fix it and remove each overlay with `{UVX} loadout overlay remove NAME --yes`, "
        "or delete the file if no overlay is left"
    )
