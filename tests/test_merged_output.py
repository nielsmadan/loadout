from __future__ import annotations

from pathlib import Path

import pytest

from loadout import emit
from loadout.emit import Merged, check_all, write_all

OWNED = frozenset({"model", "mcp_servers"})
DOCUMENT = 'model = "gpt-5.6-sol"\n\n[mcp_servers.jina]\ndefault_tools_approval_mode = "approve"\n'

FOREIGN = '# hand-written\n[projects."/Users/me/ac"]\ntrust_level = "trusted"\n'


@pytest.fixture
def destination(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A Merged destination, stood up without a preset that uses one yet."""
    path = tmp_path / "config.toml"
    monkeypatch.setattr(
        emit, "render_all", lambda root, profile="default": {path: Merged(OWNED, DOCUMENT)}
    )
    return path


def test_write_applies_owned_keys_without_disturbing_the_rest(destination: Path) -> None:
    destination.write_text(FOREIGN, encoding="utf-8")

    write_all(destination.parent)
    result = destination.read_text(encoding="utf-8")

    assert 'model = "gpt-5.6-sol"' in result
    assert "[mcp_servers.jina]" in result
    assert '[projects."/Users/me/ac"]' in result
    assert "# hand-written" in result


def test_check_is_clean_right_after_write(destination: Path) -> None:
    destination.write_text(FOREIGN, encoding="utf-8")
    write_all(destination.parent)

    assert check_all(destination.parent) == []


def test_the_harness_writing_its_own_table_is_not_drift(destination: Path) -> None:
    """Codex appends a `[projects."…"]` table whenever a project is opened. Applying
    is identity on everything loadout does not own, so the comparison sees only
    owned keys — otherwise every project you open would report as drift and push
    you toward `--force`, which is the guard's whole failure mode.
    """
    destination.write_text(FOREIGN, encoding="utf-8")
    write_all(destination.parent)

    with destination.open("a", encoding="utf-8") as handle:
        handle.write('\n[projects."/Users/me/other"]\ntrust_level = "trusted"\n')

    assert check_all(destination.parent) == []


def test_editing_an_owned_key_is_drift(destination: Path) -> None:
    """The other half: the guard still bites on what loadout does own."""
    destination.write_text(FOREIGN, encoding="utf-8")
    write_all(destination.parent)
    destination.write_text(
        destination.read_text(encoding="utf-8").replace("gpt-5.6-sol", "something-else"),
        encoding="utf-8",
    )

    drift = check_all(destination.parent)

    assert [path for path, _, _ in drift] == [destination]


def test_a_destination_that_does_not_exist_is_created(destination: Path) -> None:
    write_all(destination.parent)

    assert 'model = "gpt-5.6-sol"' in destination.read_text(encoding="utf-8")
