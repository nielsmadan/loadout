"""Who may read an adopted source file (review 3, 1.5), pinned at the unit tier and for
every kind of file adoption writes (review 4, batches 4.1 and 4.4)."""

from __future__ import annotations

from pathlib import Path

import pytest

from loadout.migration import _owner_only, _source_mode

TOKEN = "ghp_" + "a1B2" * 9


@pytest.mark.parametrize(
    ("mode", "only"), [(0o600, True), (0o700, True), (0o640, False), (0o604, False)]
)
def test_an_original_is_owner_only_when_no_group_or_other_bit_is_set(mode: int, only: bool) -> None:
    assert _owner_only(mode) is only


@pytest.mark.parametrize(
    ("mode", "content", "restricted", "result"),
    [
        (0o644, b"plain = 1\n", False, 0o644),
        (0o644, b"plain = 1\n", True, 0o600),
        (0o755, b"plain = 1\n", True, 0o700),
        (0o644, f'token = "{TOKEN}"\n'.encode(), False, 0o600),
    ],
)
def test_a_source_file_is_owner_only_when_restricted_or_holding_a_secret(
    mode: int, content: bytes, restricted: bool, result: int
) -> None:
    assert _source_mode(mode, content, Path("x.toml"), restricted=restricted) == result
