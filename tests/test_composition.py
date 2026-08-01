from __future__ import annotations

from pathlib import Path, PurePosixPath

import pytest

from loadout.composition import load_fragment, render
from loadout.errors import LoadoutError
from loadout.targets import HEADER, Target


def test_load_fragment_strips_surrounding_whitespace(tmp_path: Path) -> None:
    (tmp_path / "alpha.md").write_text("\n\n## Alpha\n\nbody\n\n\n")
    assert load_fragment(tmp_path, "alpha") == "## Alpha\n\nbody"


def test_load_fragment_supports_dotted_variant_names(tmp_path: Path) -> None:
    (tmp_path / "git-policy.autonomous.md").write_text("autonomous rules\n")
    assert load_fragment(tmp_path, "git-policy.autonomous") == "autonomous rules"


def test_load_fragment_raises_on_missing_fragment(tmp_path: Path) -> None:
    with pytest.raises(LoadoutError) as excinfo:
        load_fragment(tmp_path, "nope")
    assert "nope.md" in str(excinfo.value)


def test_render_joins_header_intro_and_fragments(tmp_path: Path) -> None:
    (tmp_path / "one.md").write_text("ONE\n")
    (tmp_path / "two.md").write_text("TWO\n")
    target = Target(
        path=PurePosixPath("out.md"),
        intro="INTRO",
        fragments=("one", "two"),
        destinations=(PurePosixPath("~/out.md"),),
    )
    assert render(target, tmp_path) == f"{HEADER}\n\nINTRO\n\nONE\n\nTWO\n"


def test_render_ends_with_exactly_one_newline(tmp_path: Path) -> None:
    (tmp_path / "one.md").write_text("ONE\n\n\n")
    target = Target(
        path=PurePosixPath("out.md"),
        intro="INTRO",
        fragments=("one",),
        destinations=(PurePosixPath("~/out.md"),),
    )
    out = render(target, tmp_path)
    assert out.endswith("ONE\n")
    assert not out.endswith("ONE\n\n")
