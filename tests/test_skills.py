"""The skills slice: discovery, composition, and the guarantee the majority relies on.

The load-bearing property is the *first* test: a skill with no markers must come
back byte-for-byte, banner aside. 49 of 50 skills in the live source vary not at
all, so a mechanism that reformats the shared case taxes every skill to serve
one.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from loadout.errors import LoadoutError
from loadout.skills import (
    BANNER,
    apply_body,
    apply_frontmatter,
    discover_skills,
    render_skill,
    split_frontmatter,
)

PLAIN = """---
name: doc
description: Assess documentation.
effort: high
---

# Doc

Body text with `code` and a list:

- one
- two
"""


def _write(root: Path, name: str, text: str) -> Path:
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "SKILL.md").write_text(text, encoding="utf-8")
    return directory


def test_an_unmarked_skill_is_reproduced_exactly_but_for_the_banner(tmp_path: Path) -> None:
    _write(tmp_path, "doc", PLAIN)
    (skill,) = discover_skills(tmp_path)

    rendered = render_skill(skill, "claude")

    banner = BANNER.format(name="doc")
    assert rendered == PLAIN.replace("---\n\n# Doc", f"---\n\n{banner}\n\n# Doc", 1)


def test_the_banner_sits_below_the_frontmatter(tmp_path: Path) -> None:
    """Above the opening ---, the frontmatter goes unparsed and the skill shows
    the banner as its description. Already hit once in ~/ac/skills/sync.py."""
    _write(tmp_path, "doc", PLAIN)
    (skill,) = discover_skills(tmp_path)

    lines = render_skill(skill, "claude").split("\n")

    assert lines[0] == "---"
    assert lines.index(BANNER.format(name="doc")) > lines.index("---", 1)


def test_supporting_files_are_found_and_sorted(tmp_path: Path) -> None:
    directory = _write(tmp_path, "pdf", PLAIN)
    (directory / "references").mkdir()
    (directory / "references" / "b.md").write_text("b", encoding="utf-8")
    (directory / "references" / "a.md").write_text("a", encoding="utf-8")
    (directory / "scripts").mkdir()
    (directory / "scripts" / "run.py").write_text("print()", encoding="utf-8")

    (skill,) = discover_skills(tmp_path)

    assert skill.supporting == (
        Path("references/a.md"),
        Path("references/b.md"),
        Path("scripts/run.py"),
    )


def test_build_artifacts_are_not_skill_content(tmp_path: Path) -> None:
    """A tree carries __pycache__ in the live source today; copying stale
    bytecode into four harnesses is a bug waiting to be filed."""
    directory = _write(tmp_path, "tool", PLAIN)
    cache = directory / "scripts" / "__pycache__"
    cache.mkdir(parents=True)
    (cache / "mod.cpython-314.pyc").write_bytes(b"\x00")
    (directory / "scripts" / "mod.py").write_text("x = 1", encoding="utf-8")

    (skill,) = discover_skills(tmp_path)

    assert skill.supporting == (Path("scripts/mod.py"),)


def test_a_directory_without_a_skill_document_is_not_a_skill(tmp_path: Path) -> None:
    _write(tmp_path, "real", PLAIN)
    (tmp_path / "stray").mkdir()
    (tmp_path / "stray" / "notes.md").write_text("hi", encoding="utf-8")

    assert [s.name for s in discover_skills(tmp_path)] == ["real"]


def test_marked_sections_go_only_to_the_named_harness() -> None:
    body = "shared\n\n::: claude\nclaude only\n:::\n\n::: codex opencode\nthe others\n:::\n\ntail"

    assert apply_body(body, "claude", "s") == "shared\n\nclaude only\n\n\ntail"
    assert apply_body(body, "codex", "s") == "shared\n\n\nthe others\n\ntail"
    assert apply_body(body, "pi", "s") == "shared\n\n\n\ntail"


def test_a_marker_inside_a_code_fence_is_content() -> None:
    """The marker rule is 'wraps whole blocks, never sits inside one'. A skill
    documenting this syntax must be able to show it."""
    body = "before\n\n```markdown\n::: claude\nexample\n:::\n```\n\nafter"

    assert apply_body(body, "codex", "s") == body


def test_an_unknown_harness_in_a_marker_is_refused() -> None:
    with pytest.raises(LoadoutError, match="unknown harness"):
        apply_body("::: gemini\nx\n:::", "claude", "s")


def test_an_unclosed_marker_is_refused() -> None:
    with pytest.raises(LoadoutError, match="never closed"):
        apply_body("::: claude\nx", "claude", "s")


def test_a_stray_close_marker_is_refused() -> None:
    with pytest.raises(LoadoutError, match="never opened"):
        apply_body("x\n:::", "claude", "s")


def test_frontmatter_overrides_replace_the_shared_value() -> None:
    lines = [
        "name: second-opinion",
        "description: Ask the others.",
        "claude:",
        "  description: Ask Codex and OpenCode.",
        "codex:",
        "  description: Ask Claude and OpenCode.",
    ]

    assert apply_frontmatter(lines, "claude") == [
        "name: second-opinion",
        "description: Ask Codex and OpenCode.",
    ]
    assert apply_frontmatter(lines, "codex") == [
        "name: second-opinion",
        "description: Ask Claude and OpenCode.",
    ]


def test_every_harness_block_is_stripped_even_for_an_unnamed_harness() -> None:
    lines = ["name: x", "claude:", "  description: c"]

    assert apply_frontmatter(lines, "pi") == ["name: x"]


def test_frontmatter_without_a_harness_block_is_untouched() -> None:
    lines = ["name: x", "description: y", "allowed-tools: [Read, Grep]"]

    assert apply_frontmatter(lines, "claude") == lines


def test_split_frontmatter_reports_absence_rather_than_guessing() -> None:
    assert split_frontmatter("# Just a heading\n") == (None, "# Just a heading\n")


def test_unclosed_frontmatter_is_refused() -> None:
    with pytest.raises(LoadoutError, match="never closed"):
        split_frontmatter("---\nname: x\n")
