from __future__ import annotations

import loadout


def test_version_is_exposed() -> None:
    assert isinstance(loadout.__version__, str)
    assert loadout.__version__


def test_no_args_prints_usage_and_returns_2(capsys) -> None:
    assert loadout.main([]) == 2
    assert "usage" in capsys.readouterr().err.lower()
