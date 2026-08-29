from __future__ import annotations

import json
from pathlib import Path

from loadout.emit import check_all, collect_notices, write_all

FRAGMENT = {"model": "gpt-5.6-sol", "model_reasoning_effort": "max"}

EXISTING = """# hand-written
sandbox_mode = "workspace-write"

[projects."/Users/me/one"]
trust_level = "trusted"
"""


def build(root: Path, fragment: dict[str, str]) -> Path:
    (root / "defaults").mkdir(parents=True, exist_ok=True)
    (root / "loadout.toml").write_text(
        '[[source]]\nname = "test"\npath = "."\n\n[codex]\ndefaults = "codex"\n', encoding="utf-8"
    )
    (root / "permissions.toml").write_text('[shell]\nallow = ["ls"]\n', encoding="utf-8")
    (root / "defaults" / "codex.json").write_text(json.dumps(fragment), encoding="utf-8")
    return root


def destination(fake_home: Path) -> Path:
    return fake_home / ".codex" / "config.toml"


def prepare(root: Path, fake_home: Path, fragment: dict[str, str]) -> Path:
    build(root, fragment)
    target = destination(fake_home)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(EXISTING, encoding="utf-8")
    return target


def test_managed_keys_reach_config_toml_without_disturbing_the_rest(
    tmp_path: Path, fake_home: Path
) -> None:
    target = prepare(tmp_path, fake_home, FRAGMENT)

    write_all(tmp_path)
    result = target.read_text(encoding="utf-8")

    assert 'model = "gpt-5.6-sol"' in result
    assert 'sandbox_mode = "workspace-write"' in result
    assert '[projects."/Users/me/one"]' in result
    assert "# hand-written" in result


def test_a_key_removed_from_the_fragment_is_removed_from_config_toml(
    tmp_path: Path, fake_home: Path
) -> None:
    """The reason the record exists.

    Ownership here is derived — the key names are the user's — so the fragment
    alone cannot say a key was ever managed. Without the recorded set unioned in,
    a dropped key is never named, nothing strips it, and it survives every later
    run (ADR 0017).

    `model` is asserted alongside so this cannot pass against a strip that emptied
    the managed keys entirely.
    """
    target = prepare(tmp_path, fake_home, {**FRAGMENT, "plan_mode_reasoning_effort": "max"})
    write_all(tmp_path)
    assert "plan_mode_reasoning_effort" in target.read_text(encoding="utf-8")

    (tmp_path / "defaults" / "codex.json").write_text(json.dumps(FRAGMENT), encoding="utf-8")
    write_all(tmp_path)
    result = target.read_text(encoding="utf-8")

    assert "plan_mode_reasoning_effort" not in result
    assert 'model = "gpt-5.6-sol"' in result
    assert 'sandbox_mode = "workspace-write"' in result


def test_the_record_is_written_beside_its_fragment(tmp_path: Path, fake_home: Path) -> None:
    prepare(tmp_path, fake_home, FRAGMENT)

    write_all(tmp_path)

    record = tmp_path / "defaults" / "codex.owned"
    keys = [line for line in record.read_text(encoding="utf-8").splitlines() if line[:1] != "#"]
    assert keys == ["model", "model_reasoning_effort"]


def test_a_settings_key_nobody_manages_is_left_alone(tmp_path: Path, fake_home: Path) -> None:
    """The slice strips only what it manages, so a machine's own Codex settings
    survive a sync that never mentions them."""
    target = prepare(tmp_path, fake_home, FRAGMENT)

    write_all(tmp_path)

    assert 'sandbox_mode = "workspace-write"' in target.read_text(encoding="utf-8")


def test_a_stale_record_is_reported_as_drift(tmp_path: Path, fake_home: Path) -> None:
    """The record is an input as well as an output, so a hand edit to it silently
    changes what gets stripped. `check` has to say so rather than fix it."""
    prepare(tmp_path, fake_home, FRAGMENT)
    write_all(tmp_path)
    record = tmp_path / "defaults" / "codex.owned"
    record.write_text("# tampered\nmodel\n", encoding="utf-8")

    assert [path for path, _, _ in check_all(tmp_path)] == [record]


def test_a_defaults_fragment_is_not_parsed_as_a_plugins_fragment(
    tmp_path: Path, fake_home: Path
) -> None:
    """Notices read marketplaces out of a *plugins* fragment, which parses the
    document as one. Done for every slice it rejects any other shape: a defaults
    fragment's keys — or a hooks fragment's event names — read as stray sections
    and the whole render fails with `plugins: unknown section(s) model`.

    Latent until a second source-slice kind existed; `hooks` would have hit it
    just as hard on any agent that declared one.
    """
    prepare(tmp_path, fake_home, FRAGMENT)

    assert collect_notices(tmp_path) is not None
    write_all(tmp_path)
