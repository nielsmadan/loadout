from __future__ import annotations

from pathlib import Path

import pytest

from loadout.errors import LoadoutError
from loadout.record import FORMAT, read_record, render_record


def test_new_records_distinguish_literal_names_from_nested_paths(tmp_path: Path) -> None:
    record = tmp_path / "codex.owned"
    keys = frozenset({'"skills.config"', "skills.config", 'profiles."coding\u2028notes".budget'})
    record.write_text(render_record(keys))
    assert record.read_text().split("\n")[1] == FORMAT
    assert read_record(record) == keys


def test_legacy_records_keep_top_level_names_literal(tmp_path: Path) -> None:
    record = tmp_path / "codex.owned"
    record.write_text("# legacy\nmodel\nskills.config\nspace in name\ncoding\u2028notes\n")
    assert read_record(record) == {
        "model",
        '"skills.config"',
        '"space in name"',
        '"coding\u2028notes"',
    }


@pytest.mark.parametrize(
    "content",
    [
        "# loadout-owned-format: 99\nmodel\n",
        "# loadout-owned-format: 2\n# loadout-owned-format: 2\nmodel\n",
        '# loadout-owned-format: 2\n"unterminated\n',
    ],
)
def test_unknown_or_invalid_record_formats_are_refused(tmp_path: Path, content: str) -> None:
    record = tmp_path / "codex.owned"
    record.write_text(content)
    with pytest.raises(
        LoadoutError, match=r"ownership record format|invalid managed TOML key path"
    ):
        read_record(record)
    assert record.read_text() == content
