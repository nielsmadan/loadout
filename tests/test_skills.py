"""The skills slice: discovery, composition, and the guarantee the majority relies on.

The load-bearing property is the *first* test: a skill with no markers must come
back byte-for-byte, banner aside. 49 of 50 skills in the live source vary not at
all, so a mechanism that reformats the shared case taxes every skill to serve
one.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from loadout.emit import Copied, render_all
from loadout.errors import LoadoutError
from loadout.skills import (
    BANNER,
    apply_body,
    apply_frontmatter,
    discover_skills,
    loose_skill_files,
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


def test_a_file_directly_in_skills_is_not_a_skill(tmp_path: Path) -> None:
    """The flat form is gone: a skill is `<name>/SKILL.md`. A README documents the
    directory, so only the other loose file is reported."""
    _write(tmp_path, "doc", PLAIN)
    (tmp_path / "README.md").write_text("# About these skills\n", encoding="utf-8")
    (tmp_path / "review.md").write_text(PLAIN, encoding="utf-8")

    assert [skill.name for skill in discover_skills(tmp_path)] == ["doc"]
    assert loose_skill_files(tmp_path) == (tmp_path / "review.md",)


def test_a_local_tree_replaces_the_public_skill(tmp_path: Path) -> None:
    public = _write(tmp_path, "review", "public\n")
    (public / "public.txt").write_text("public\n", encoding="utf-8")
    local = _write(tmp_path, "review.local", "local\n")
    (local / "private.txt").write_text("private\n", encoding="utf-8")

    (skill,) = discover_skills(tmp_path)

    assert skill.name == "review"
    assert skill.document == local / "SKILL.md"
    assert skill.supporting == (Path("private.txt"),)


def test_build_artifacts_are_not_skill_content(tmp_path: Path) -> None:
    """A tree carries __pycache__ in the live source today; copying stale
    bytecode into five harnesses is a bug waiting to be filed."""
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


def test_a_directory_form_source_renders_every_skill_and_file_unchanged(
    tmp_path: Path, fake_home: Path
) -> None:
    """The regression guard for the form every real source uses: skill documents
    come back as source plus banner, supporting files byte for byte with their
    modes, at the same paths before and after the flat form was dropped."""
    source = tmp_path / "source"
    skills = source / "skills"
    _write(skills, "doc", PLAIN)
    tool = _write(skills, "tool", PLAIN.replace("name: doc", "name: tool"))
    (tool / "scripts").mkdir()
    (tool / "scripts" / "run.sh").write_text("#!/bin/sh\necho run\n", encoding="utf-8")
    (tool / "scripts" / "run.sh").chmod(0o755)
    (tool / "references" / "deep").mkdir(parents=True)
    (tool / "references" / "deep" / "notes.md").write_text("notes\n", encoding="utf-8")
    _write(skills, "doc.local", PLAIN.replace("Body text", "Local body text"))
    (source / "permissions.toml").write_text("", encoding="utf-8")
    (source / "loadout.toml").write_text(
        '[[source]]\nname = "s"\npath = "."\nuse = ["skills", "permissions"]\n\n[claude]\n',
        encoding="utf-8",
    )

    outputs = render_all(source)

    destination = fake_home / ".claude" / "skills"
    skill_outputs = {p: c for p, c in outputs.items() if p.is_relative_to(destination)}
    assert set(skill_outputs) == {
        destination / "doc" / "SKILL.md",
        destination / "tool" / "SKILL.md",
        destination / "tool" / "scripts" / "run.sh",
        destination / "tool" / "references" / "deep" / "notes.md",
    }
    banner = BANNER.format(name="doc")
    assert skill_outputs[destination / "doc" / "SKILL.md"] == PLAIN.replace(
        "Body text", "Local body text"
    ).replace("---\n\n# Doc", f"---\n\n{banner}\n\n# Doc", 1)
    script = skill_outputs[destination / "tool" / "scripts" / "run.sh"]
    assert isinstance(script, Copied)
    assert script.read_bytes() == b"#!/bin/sh\necho run\n"
    assert script.file_mode() & 0o111
