"""The README init writes into a new global source describes the layout loadout reads.

It said "skills, one file or directory each", though a loose `skills/*.md` is skipped, and
named neither `agents/` nor the `use` list init no longer writes.
"""

from __future__ import annotations

import re

from loadout.migration import SOURCE_README
from loadout.sources import ARTIFACT_TYPES


def _layout() -> dict[str, str]:
    text = SOURCE_README.decode()
    block = text.split("## Layout\n\n", 1)[1].split("\n\n", 1)[0]
    rows = (re.match(r"\s+(\S+)\s+(.*)", line) for line in block.splitlines())
    return {
        row[1].removesuffix("/").removesuffix(".toml"): row[2] for row in rows if row is not None
    }


def test_the_layout_names_every_category_a_source_holds() -> None:
    assert set(_layout()) - {"loadout"} == ARTIFACT_TYPES


def test_a_skill_is_a_folder_holding_skill_md() -> None:
    assert _layout()["skills"] == "skills, one folder each holding a SKILL.md"
