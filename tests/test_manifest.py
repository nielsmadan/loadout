from __future__ import annotations

from pathlib import Path, PurePosixPath

import pytest

from loadout.errors import LoadoutError
from loadout.manifest import load_manifest

MINIMAL = """
[[source]]
name = "ac"
path = "."

[instructions.claude]
output       = "claude/CLAUDE.md"
destinations = ["~/.claude/CLAUDE.md"]
order        = ["intro-claude", "web-fetching"]
"""


def write_manifest(tmp_path: Path, body: str) -> Path:
    (tmp_path / "global" / "fragments").mkdir(parents=True, exist_ok=True)
    path = tmp_path / "loadout.toml"
    path.write_text(body, encoding="utf-8")
    return path


def test_parses_sources_and_targets(tmp_path: Path) -> None:
    manifest = load_manifest(write_manifest(tmp_path, MINIMAL))
    assert len(manifest.sources) == 1
    assert manifest.sources[0].name == "ac"
    assert len(manifest.targets) == 1
    target = manifest.targets[0]
    assert target.path == PurePosixPath("claude/CLAUDE.md")
    assert target.fragments == ("intro-claude", "web-fetching")
    assert target.destinations == (PurePosixPath("~/.claude/CLAUDE.md"),)
    assert target.profile is None


def test_profile_is_read(tmp_path: Path) -> None:
    body = (
        MINIMAL
        + """
[instructions.claude-autonomous]
output       = "claude/CLAUDE.autonomous.md"
destinations = ["~/.claude/CLAUDE.md"]
profile      = "autonomous"
order        = ["intro-claude"]
"""
    )
    manifest = load_manifest(write_manifest(tmp_path, body))
    profiles = sorted(t.profile or "default" for t in manifest.targets)
    assert profiles == ["autonomous", "default"]


def test_missing_manifest_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(LoadoutError) as excinfo:
        load_manifest(tmp_path / "loadout.toml")
    assert "loadout.toml" in str(excinfo.value)


def test_malformed_toml_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(LoadoutError):
        load_manifest(write_manifest(tmp_path, "this is not = = toml"))


def test_target_without_order_is_an_error(tmp_path: Path) -> None:
    body = """
[[source]]
name = "ac"
path = "."

[instructions.claude]
output       = "claude/CLAUDE.md"
destinations = ["~/.claude/CLAUDE.md"]
"""
    with pytest.raises(LoadoutError) as excinfo:
        load_manifest(write_manifest(tmp_path, body))
    assert "order" in str(excinfo.value)


def test_target_without_output_is_an_error(tmp_path: Path) -> None:
    body = """
[[source]]
name = "ac"
path = "."

[instructions.claude]
destinations = ["~/.claude/CLAUDE.md"]
order        = ["intro-claude"]
"""
    with pytest.raises(LoadoutError) as excinfo:
        load_manifest(write_manifest(tmp_path, body))
    assert "output" in str(excinfo.value)


def test_no_sources_is_an_error(tmp_path: Path) -> None:
    body = """
[instructions.claude]
output       = "claude/CLAUDE.md"
destinations = ["~/.claude/CLAUDE.md"]
order        = ["intro-claude"]
"""
    with pytest.raises(LoadoutError) as excinfo:
        load_manifest(write_manifest(tmp_path, body))
    assert "source" in str(excinfo.value)


def test_source_as_a_plain_array_is_a_loadout_error_not_a_crash(tmp_path: Path) -> None:
    body = """
source = ["a", "b"]

[instructions.claude]
output       = "claude/CLAUDE.md"
destinations = ["~/.claude/CLAUDE.md"]
order        = ["intro-claude"]
"""
    with pytest.raises(LoadoutError):
        load_manifest(write_manifest(tmp_path, body))


def test_absolute_output_is_an_error(tmp_path: Path) -> None:
    body = """
[[source]]
name = "ac"
path = "."

[instructions.claude]
output       = "/tmp/x.md"
destinations = ["~/.claude/CLAUDE.md"]
order        = ["intro-claude"]
"""
    with pytest.raises(LoadoutError) as excinfo:
        load_manifest(write_manifest(tmp_path, body))
    assert "output" in str(excinfo.value)


def test_empty_output_is_an_error(tmp_path: Path) -> None:
    body = """
[[source]]
name = "ac"
path = "."

[instructions.claude]
output       = ""
destinations = ["~/.claude/CLAUDE.md"]
order        = ["intro-claude"]
"""
    with pytest.raises(LoadoutError) as excinfo:
        load_manifest(write_manifest(tmp_path, body))
    assert "output" in str(excinfo.value)


def test_output_escaping_the_root_is_an_error(tmp_path: Path) -> None:
    body = """
[[source]]
name = "ac"
path = "."

[instructions.claude]
output       = "../escaped.md"
destinations = ["~/.claude/CLAUDE.md"]
order        = ["intro-claude"]
"""
    with pytest.raises(LoadoutError) as excinfo:
        load_manifest(write_manifest(tmp_path, body))
    assert "output" in str(excinfo.value)


def test_zero_instruction_targets_is_an_error(tmp_path: Path) -> None:
    # "instruction" (singular) is a typo for "instructions" — it must not
    # silently parse as a manifest with zero targets.
    body = """
[[source]]
name = "ac"
path = "."

[instruction.claude]
output       = "claude/CLAUDE.md"
destinations = ["~/.claude/CLAUDE.md"]
order        = ["intro-claude"]
"""
    with pytest.raises(LoadoutError) as excinfo:
        load_manifest(write_manifest(tmp_path, body))
    assert "target" in str(excinfo.value)


def test_two_targets_sharing_an_output_is_an_error(tmp_path: Path) -> None:
    body = """
[[source]]
name = "ac"
path = "."

[instructions.claude]
output       = "claude/CLAUDE.md"
destinations = ["~/.claude/CLAUDE.md"]
order        = ["intro-claude"]

[instructions.other]
output       = "claude/CLAUDE.md"
destinations = ["~/.other/CLAUDE.md"]
order        = ["intro-claude"]
"""
    with pytest.raises(LoadoutError) as excinfo:
        load_manifest(write_manifest(tmp_path, body))
    assert "claude/CLAUDE.md" in str(excinfo.value)
