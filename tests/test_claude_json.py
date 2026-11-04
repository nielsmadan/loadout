from __future__ import annotations

import json
from pathlib import Path

import pytest

from loadout import claude_json
from loadout.claude_json import (
    LeafVerdict,
    claude_json_path,
    judge_leaves,
    server_path,
    write_leaves,
)
from loadout.errors import LoadoutError
from loadout.json_paths import leaf_digest

PROJECT = Path("/work/app.one")
DOCS = {"type": "stdio", "command": "docs", "args": []}
DOCS_PATH = server_path(PROJECT, "docs")


def claude_file(home: Path, document: object, *, newline: bool = False) -> Path:
    path = home / ".claude.json"
    path.write_text(json.dumps(document, indent=2) + ("\n" if newline else ""))
    return path


def user_project() -> dict[str, object]:
    return {
        "numStartups": 4,
        "projects": {
            str(PROJECT): {
                "hasTrustDialogAccepted": True,
                "mcpServers": {"mine": {"type": "stdio", "command": "mine"}},
            }
        },
    }


def test_a_user_server_and_project_state_survive_a_write_and_a_retirement(
    fake_home: Path,
) -> None:
    path = claude_file(fake_home, user_project())
    written = write_leaves(path, {DOCS_PATH: DOCS}, {}, (), force=False)
    assert written.written == (DOCS_PATH,)
    assert written.digests == {DOCS_PATH: leaf_digest(DOCS)}
    expected = user_project()
    expected["projects"][str(PROJECT)]["mcpServers"]["docs"] = DOCS  # type: ignore[index]
    assert path.read_text() == json.dumps(expected, indent=2)
    retired = write_leaves(
        path, {}, {DOCS_PATH: frozenset({leaf_digest(DOCS)})}, (DOCS_PATH,), force=False
    )
    assert retired.retired == (DOCS_PATH,)
    assert retired.digests == {}
    assert path.read_text() == json.dumps(user_project(), indent=2)


@pytest.mark.parametrize("force", [False, True])
def test_a_user_defined_same_name_server_is_refused(fake_home: Path, force: bool) -> None:
    mine = {"type": "stdio", "command": "mine"}
    path = claude_file(fake_home, {"projects": {str(PROJECT): {"mcpServers": {"docs": mine}}}})
    result = write_leaves(path, {DOCS_PATH: DOCS}, {}, (), force=force)
    assert result.refused == (LeafVerdict(DOCS_PATH, "user-defined"),)
    assert path.read_text() == json.dumps(
        {"projects": {str(PROJECT): {"mcpServers": {"docs": mine}}}}, indent=2
    )
    assert "claude mcp remove docs -s local" in claude_json.leaf_refusal(result.refused[0])


def test_a_present_unrecorded_leaf_equal_to_the_wanted_value_is_adopted(fake_home: Path) -> None:
    document = {"projects": {str(PROJECT): {"mcpServers": {"docs": DOCS}}}}
    assert judge_leaves(document, {DOCS_PATH: DOCS}, {}, ()) == (LeafVerdict(DOCS_PATH, "adopt"),)
    path = claude_file(fake_home, document)
    result = write_leaves(path, {DOCS_PATH: DOCS}, {}, (), force=False)
    assert result.digests == {DOCS_PATH: leaf_digest(DOCS)}
    assert result.refused == ()


def test_a_leaf_present_with_its_pending_digest_is_unchanged() -> None:
    document = {"projects": {str(PROJECT): {"mcpServers": {"docs": DOCS}}}}
    owned = {DOCS_PATH: frozenset({leaf_digest(DOCS)})}
    assert judge_leaves(document, {DOCS_PATH: DOCS}, owned, ()) == (
        LeafVerdict(DOCS_PATH, "unchanged"),
    )


def test_owned_leaves_are_judged_against_every_owned_digest() -> None:
    old = {"type": "stdio", "command": "old"}
    edited = {"type": "stdio", "command": "edited"}
    owned = {DOCS_PATH: frozenset({leaf_digest(old), leaf_digest(DOCS)})}

    def at(value: object) -> dict[str, object]:
        return {"projects": {str(PROJECT): {"mcpServers": {"docs": value}}}}

    assert judge_leaves(at(old), {DOCS_PATH: DOCS}, owned, ())[0].kind == "update"
    assert judge_leaves(at(edited), {DOCS_PATH: DOCS}, owned, ())[0].kind == "owned-key"
    assert judge_leaves({}, {DOCS_PATH: DOCS}, owned, ())[0].kind == "owned-key-removed"
    assert judge_leaves(at(edited), {}, owned, (DOCS_PATH,))[0].kind == "owned-key"


def test_an_edited_owned_leaf_is_refused_until_forced(fake_home: Path) -> None:
    edited = {"type": "stdio", "command": "edited"}
    path = claude_file(fake_home, {"projects": {str(PROJECT): {"mcpServers": {"docs": edited}}}})
    owned = {DOCS_PATH: frozenset({leaf_digest({"type": "stdio", "command": "old"})})}
    refused = write_leaves(path, {DOCS_PATH: DOCS}, owned, (), force=False)
    assert refused.refused == (LeafVerdict(DOCS_PATH, "owned-key"),)
    assert DOCS_PATH in refused.digests
    forced = write_leaves(path, {DOCS_PATH: DOCS}, owned, (), force=True)
    assert forced.written == (DOCS_PATH,)
    assert path.read_text() == json.dumps(
        {"projects": {str(PROJECT): {"mcpServers": {"docs": DOCS}}}}, indent=2
    )


def test_retiring_the_last_server_keeps_mcp_servers_and_the_project(fake_home: Path) -> None:
    path = claude_file(fake_home, {"projects": {str(PROJECT): {"mcpServers": {"docs": DOCS}}}})
    write_leaves(path, {}, {DOCS_PATH: frozenset({leaf_digest(DOCS)})}, (DOCS_PATH,), force=False)
    assert path.read_text() == json.dumps(
        {"projects": {str(PROJECT): {"mcpServers": {}}}}, indent=2
    )


@pytest.mark.parametrize("newline", [False, True])
def test_the_trailing_newline_follows_the_original(fake_home: Path, newline: bool) -> None:
    path = claude_file(fake_home, {"numStartups": 1}, newline=newline)
    write_leaves(path, {DOCS_PATH: DOCS}, {}, (), force=False)
    assert path.read_text().endswith("}\n") is newline


def test_a_new_file_is_private_and_written_like_claude_writes_it(fake_home: Path) -> None:
    path = fake_home / ".claude.json"
    write_leaves(path, {DOCS_PATH: DOCS}, {}, (), force=False)
    expected = {"projects": {str(PROJECT): {"mcpServers": {"docs": DOCS}}}}
    assert path.read_text() == json.dumps(expected, indent=2)
    assert path.stat().st_mode & 0o777 == 0o600


def test_a_symlinked_file_is_written_through(fake_home: Path, tmp_path: Path) -> None:
    real = claude_file(tmp_path, {"numStartups": 1})
    link = fake_home / ".claude.json"
    link.symlink_to(real)
    write_leaves(link, {DOCS_PATH: DOCS}, {}, (), force=False)
    assert link.is_symlink()
    assert real.read_text() == json.dumps(
        {"numStartups": 1, "projects": {str(PROJECT): {"mcpServers": {"docs": DOCS}}}}, indent=2
    )


def test_a_change_before_the_rename_is_merged_rather_than_lost(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = claude_file(fake_home, {"numStartups": 1})
    real_read = claude_json._read_bytes
    calls: list[Path] = []

    def racing_read(target: Path) -> bytes | None:
        calls.append(target)
        if len(calls) == 2:
            target.write_text(json.dumps({"numStartups": 2, "tipsHistory": {"a": 1}}, indent=2))
        return real_read(target)

    monkeypatch.setattr(claude_json, "_read_bytes", racing_read)
    write_leaves(path, {DOCS_PATH: DOCS}, {}, (), force=False)
    assert len(calls) == 4
    assert path.read_text() == json.dumps(
        {
            "numStartups": 2,
            "tipsHistory": {"a": 1},
            "projects": {str(PROJECT): {"mcpServers": {"docs": DOCS}}},
        },
        indent=2,
    )


def test_a_file_that_keeps_changing_is_refused(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = claude_file(fake_home, {"numStartups": 1})
    real_read = claude_json._read_bytes
    calls: list[Path] = []

    def racing_read(target: Path) -> bytes | None:
        calls.append(target)
        if len(calls) % 2 == 0:
            target.write_text(json.dumps({"numStartups": len(calls)}))
        return real_read(target)

    monkeypatch.setattr(claude_json, "_read_bytes", racing_read)
    with pytest.raises(LoadoutError, match="changed while loadout held the locks"):
        write_leaves(path, {DOCS_PATH: DOCS}, {}, (), force=False)
    assert path.read_text() == json.dumps({"numStartups": 4})


def test_an_unparsable_file_is_refused(fake_home: Path) -> None:
    path = fake_home / ".claude.json"
    path.write_text("[1]")
    with pytest.raises(LoadoutError, match=r"not a JSON document") as raised:
        write_leaves(path, {DOCS_PATH: DOCS}, {}, (), force=True)
    assert "~/.claude/backups" in str(raised.value)
    assert "--force" not in str(raised.value)
    assert "delete" not in str(raised.value)
    assert path.read_text() == "[1]"


def test_the_path_follows_claude_config_dir_without_resolving(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    (tmp_path / "linked").symlink_to(real)
    environ = {"HOME": str(tmp_path / "home"), "CLAUDE_CONFIG_DIR": str(tmp_path / "linked")}
    assert claude_json_path(environ) == tmp_path / "linked" / ".claude.json"
    assert claude_json_path({"HOME": str(tmp_path / "home")}) == tmp_path / "home" / ".claude.json"


def test_a_non_empty_legacy_config_is_refused(tmp_path: Path) -> None:
    (tmp_path / ".config.json").write_text("{}")
    with pytest.raises(LoadoutError, match=r"\.config\.json"):
        claude_json_path({"HOME": "/nowhere", "CLAUDE_CONFIG_DIR": str(tmp_path)})


def test_a_custom_oauth_url_is_refused(tmp_path: Path) -> None:
    with pytest.raises(LoadoutError, match=r"\.claude-custom-oauth\.json"):
        claude_json_path({"HOME": str(tmp_path), "CLAUDE_CODE_CUSTOM_OAUTH_URL": "https://x"})


def test_a_relative_claude_config_dir_is_refused() -> None:
    with pytest.raises(LoadoutError, match="must be an absolute path"):
        claude_json_path({"HOME": "/nowhere", "CLAUDE_CONFIG_DIR": "relative/dir"})


def test_the_default_location_legacy_file_is_refused(tmp_path: Path) -> None:
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / ".config.json").write_text("{}")
    with pytest.raises(LoadoutError, match=r"\.config\.json"):
        claude_json_path({"HOME": str(tmp_path)})


def test_an_empty_writable_legacy_file_is_refused_only_without_a_claude_json(
    tmp_path: Path,
) -> None:
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / ".config.json").write_text("")
    environ = {"HOME": str(tmp_path)}
    with pytest.raises(LoadoutError, match=r"\.config\.json"):
        claude_json_path(environ)
    (tmp_path / ".claude.json").write_text("{}")
    assert claude_json_path(environ) == tmp_path / ".claude.json"


def test_an_adopt_only_write_leaves_the_file_untouched(fake_home: Path) -> None:
    text = f'{{"projects":{{"{PROJECT}":{{"mcpServers":{{"docs":{json.dumps(DOCS)}}}}}}}}}'

    path = fake_home / ".claude.json"
    path.write_text(text)
    before = path.stat().st_mtime_ns
    result = write_leaves(path, {DOCS_PATH: DOCS}, {}, (), force=False)
    assert result.written == ()
    assert path.read_text() == text
    assert path.stat().st_mtime_ns == before


def test_a_lone_surrogate_escape_round_trips_in_the_unowned_part(fake_home: Path) -> None:
    path = fake_home / ".claude.json"
    path.write_text('{\n  "note": "x\\ud83d",\n  "numStartups": 1\n}')
    write_leaves(path, {DOCS_PATH: DOCS}, {}, (), force=False)
    expected = json.dumps(
        {"note": "x", "numStartups": 1, "projects": {str(PROJECT): {"mcpServers": {"docs": DOCS}}}},
        indent=2,
    ).replace('"x"', '"x\\ud83d"')
    assert path.read_text() == expected


def test_a_leaf_not_written_keeps_the_owned_digest_that_is_on_disk(fake_home: Path) -> None:
    other = server_path(Path("/work/other"), "docs")
    on_disk = {"type": "stdio", "command": "recorded"}
    pending = {"type": "stdio", "command": "pending"}
    path = claude_file(fake_home, {"projects": {"/work/other": {"mcpServers": {"docs": on_disk}}}})
    owned = {other: frozenset({leaf_digest(on_disk), leaf_digest(pending)})}
    result = write_leaves(path, {DOCS_PATH: DOCS}, owned, (), force=False)
    assert result.digests == {DOCS_PATH: leaf_digest(DOCS), other: leaf_digest(on_disk)}


def test_a_refused_owned_leaf_keeps_its_recorded_digest_not_the_wanted_one(
    fake_home: Path,
) -> None:
    recorded = {"type": "stdio", "command": "old"}
    edited = {"type": "stdio", "command": "edited"}
    path = claude_file(fake_home, {"projects": {str(PROJECT): {"mcpServers": {"docs": edited}}}})
    owned = {DOCS_PATH: frozenset({leaf_digest(recorded)})}
    result = write_leaves(path, {DOCS_PATH: DOCS}, owned, (), force=False)
    assert result.refused == (LeafVerdict(DOCS_PATH, "owned-key"),)
    assert result.digests == {DOCS_PATH: leaf_digest(recorded)}
