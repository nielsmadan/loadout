from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from loadout.claude_json import server_path
from loadout.exclude import TEMP_PATTERN
from loadout.json_paths import JsonPath, leaf_digest
from loadout.output import Copied
from loadout.overlay_documents import DocumentRecord, OwnedJson
from loadout.overlay_plan import (
    DiskFacts,
    GitFacts,
    Refusal,
    classify_recorded,
    folder_units,
    kept,
    plan_target,
    plan_units,
    unit_of,
)
from loadout.overlay_record import OverlayRecord, TargetRecord
from loadout.overlay_render import OverlayOutput, OverlayRender
from loadout.overlay_targets import Expansion, RecordedFate, SkippedTarget, Target
from loadout.written import WrittenEntry, text_entry

SETTINGS = Path(".claude/settings.local.json")
OPENCODE = Path("opencode.json")
LOCAL = Path("CLAUDE.local.md")
CODEX = Path(".codex/config.toml")
SKILL = Path(".claude/skills/review/SKILL.md")
HELPER = Path(".claude/skills/review/helper.sh")
ALLOW = ("permissions", "allow")
PERMISSION = {"bash": {"yarn test": "allow"}}
MCP = {"figma": {"type": "remote", "url": "https://figma.example/mcp"}}
FIGMA = {"type": "http", "url": "https://figma.example/mcp"}
WHOLE = (LOCAL, CODEX, SKILL, HELPER)
GIT = GitFacts(tracked=frozenset(), covered=frozenset(), branches={})


def _target(root: Path) -> Target:
    return Target(root=root, common_dir=root / ".git", device=7)


def _settings(*allow: str) -> OwnedJson:
    return OwnedJson(keys=((("model",), "opus"),), rules=((ALLOW, allow),))


def _opencode(*, mcp: bool = True) -> OwnedJson:
    keys: list[tuple[JsonPath, Any]] = [(("permission",), PERMISSION)]
    if mcp:
        keys.append((("mcp",), MCP))
    return OwnedJson(keys=tuple(keys), rules=())


def _render(root: Path, files: dict[Path, OverlayOutput] | None = None) -> OverlayRender:
    source = root.parent / "overlay" / "helper.sh"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("echo review\n")
    defaults: dict[Path, OverlayOutput] = {
        root / LOCAL: "# Local\n\nUse yarn.\n",
        root / CODEX: 'developer_instructions = """Use yarn."""\n',
        root / SETTINGS: _settings("Bash(yarn test:*)"),
        root / OPENCODE: _opencode(),
        root / SKILL: "---\nname: review\n---\nReview.\n",
        root / HELPER: Copied(source=source),
    }
    return OverlayRender(
        files={**defaults, **{root / name: output for name, output in (files or {}).items()}},
        claude_servers={"figma": FIGMA},
        shared={},
        notes=(),
    )


def _disk(root: Path, documents: dict[Path, dict[str, Any] | None] | None = None) -> DiskFacts:
    parsed = {
        root / SETTINGS: None,
        root / OPENCODE: None,
        **{root / name: document for name, document in (documents or {}).items()},
    }
    return DiskFacts(documents=parsed, document_bytes={}, claimed={}, claimed_leaves={})


def _entry(
    root: Path,
    files: dict[Path, WrittenEntry] | None = None,
    documents: dict[Path, DocumentRecord] | None = None,
    leaves: dict[Path, dict[JsonPath, str]] | None = None,
) -> TargetRecord:
    return TargetRecord(
        common_dir=root / ".git",
        device=7,
        block="app",
        files=files or {},
        documents=documents or {},
        leaves=leaves or {},
        pending={},
    )


def _under(root: Path, *names: Path) -> list[Path]:
    return sorted(root / name for name in names)


def _plan(  # noqa: PLR0913
    root: Path,
    render: OverlayRender | None,
    recorded: TargetRecord | None = None,
    git: GitFacts = GIT,
    disk: DiskFacts | None = None,
    *,
    force: bool = False,
) -> Any:
    return plan_target(
        _target(root),
        render,
        recorded,
        git,
        disk or _disk(root),
        name="app",
        force=force,
    )


@pytest.fixture
def root(tmp_path: Path) -> Path:
    folder = tmp_path / "app1"
    (folder / ".git").mkdir(parents=True)
    return folder


def test_a_healthy_first_sync_writes_every_output_and_records_it(root: Path) -> None:
    plan = _plan(root, _render(root))
    outputs = [root / LOCAL, root / CODEX, root / SETTINGS, root / OPENCODE, root / SKILL]
    assert sorted(plan.writes) == sorted([*outputs, root / HELPER])
    assert json.loads(plan.writes[root / SETTINGS]) == {
        "model": "opus",
        "permissions": {"allow": ["Bash(yarn test:*)"]},
    }
    assert sorted(plan.record.files) == sorted(
        [root / LOCAL, root / CODEX, root / SKILL, root / HELPER]
    )
    assert sorted(plan.record.documents) == sorted([root / SETTINGS, root / OPENCODE])
    assert plan.ahead.files == plan.record.files
    assert plan.record.files[root / HELPER].kind == "copied"
    assert plan.block_before == (
        "/CLAUDE.local.md",
        "/.codex/config.toml",
        "/.claude/settings.local.json",
        "/opencode.json",
        "/.claude/skills/review/",
        TEMP_PATTERN,
    )
    assert plan.block_after == plan.block_before
    assert plan.record.block == "app"
    assert plan.wanted_leaves == {server_path(root, "figma"): FIGMA}
    assert (plan.whole, plan.code, plan.refusals) == (False, 0, ())


def test_a_unit_the_excludes_file_covers_stays_out_of_the_block(root: Path) -> None:
    git = replace(GIT, covered=frozenset({"CLAUDE.local.md"}))
    plan = _plan(root, _render(root), git=git)
    assert plan.block_before == (
        "/.codex/config.toml",
        "/.claude/settings.local.json",
        "/opencode.json",
        "/.claude/skills/review/",
        TEMP_PATTERN,
    )
    assert root / LOCAL in plan.writes


def test_a_tracked_output_is_refused_and_its_record_entry_kept(root: Path) -> None:
    before = DocumentRecord(created=True, owned_keys={("permission",): "d1"}, owned_rules={})
    recorded = _entry(root, documents={root / OPENCODE: before})
    git = replace(GIT, tracked=frozenset({"opencode.json"}))
    plan = _plan(root, _render(root), recorded, git)
    assert sorted(plan.writes) == _under(root, *WHOLE, SETTINGS)
    assert plan.record.documents[root / OPENCODE] == before
    assert plan.refusals == (
        Refusal(
            "tracked",
            3,
            "the team tracks opencode.json; the overlay does not write it",
            root / OPENCODE,
        ),
    )
    assert plan.block_before == (
        "/CLAUDE.local.md",
        "/.codex/config.toml",
        "/.claude/settings.local.json",
        "/.claude/skills/review/",
        TEMP_PATTERN,
    )
    assert plan.code == 3


def test_a_tracked_orphan_is_left_in_place_and_dropped_from_the_record(root: Path) -> None:
    agents = root / ".factory/AGENTS.md"
    agents.parent.mkdir(parents=True)
    agents.write_text("The team's own file.\n")
    written = text_entry(agents, "Use yarn.\n")
    recorded = _entry(root, files={agents: written})
    git = replace(GIT, tracked=frozenset({".factory/AGENTS.md"}))
    plan = _plan(root, _render(root), recorded, git)
    assert plan.whole is False
    assert plan.deletes == ()
    assert plan.ahead.files[agents] == written
    assert agents not in plan.record.files
    assert plan.refusals == ()
    assert plan.notes[-1] == (
        "the team tracks .factory/AGENTS.md; it is left in place and no longer recorded"
    )
    assert plan.code == 0


def test_plan_units_collapse_a_skill_folder_and_list_orphans(root: Path) -> None:
    pi = root / ".pi/APPEND_SYSTEM.md"
    recorded = _entry(root, files={pi: text_entry(pi, "Use yarn.\n")})
    assert plan_units(root, _render(root), recorded) == (
        "CLAUDE.local.md",
        ".codex/config.toml",
        ".claude/settings.local.json",
        "opencode.json",
        ".claude/skills/review",
        ".pi/APPEND_SYSTEM.md",
    )


def test_a_tracked_skill_file_spelled_per_file_refuses_its_skill_folder(root: Path) -> None:
    git = replace(GIT, tracked=frozenset({".claude/skills/review/SKILL.md"}))
    plan = _plan(root, _render(root), git=git)
    assert sorted(plan.writes) == _under(root, LOCAL, CODEX, SETTINGS, OPENCODE)
    assert sorted(plan.record.files) == _under(root, LOCAL, CODEX)
    assert plan.refusals == (
        Refusal(
            "tracked",
            3,
            "the team tracks .claude/skills/review; the overlay does not write it",
            root / ".claude/skills/review",
        ),
    )


def test_a_hand_edited_whole_file_refuses_the_target_until_forced(root: Path) -> None:
    (root / LOCAL).write_text("# Local\n\nUse npm.\n")
    recorded = _entry(
        root,
        files={root / LOCAL: text_entry(root / LOCAL, "# Local\n\nUse yarn.\n")},
        leaves={root.parent / ".claude.json": {server_path(root, "old"): "l1"}},
    )
    plan = _plan(root, _render(root, {LOCAL: "# Local\n\nUse pnpm.\n"}), recorded)
    assert plan.whole is True
    assert plan.code == 1
    assert (plan.writes, plan.wanted_leaves, plan.retiring_leaves) == ({}, {}, {})
    assert plan.record == recorded
    guard = next(r for r in plan.refusals if r.path == root / LOCAL)
    assert guard.message == "CLAUDE.local.md was edited outside loadout"
    assert "-Use npm.\n" in guard.diff
    assert plan.refusals[-1].message.startswith("Sync aborted")

    forced = _plan(root, _render(root, {LOCAL: "# Local\n\nUse pnpm.\n"}), recorded, force=True)
    assert forced.whole is False
    assert forced.writes[root / LOCAL] == "# Local\n\nUse pnpm.\n"


def test_shown_and_withheld_diffs_each_get_their_abort_sentence(root: Path) -> None:
    codex = 'developer_instructions = """Use yarn."""\n'
    (root / LOCAL).write_text("# Local\n\nUse npm.\n")
    (root / CODEX).parent.mkdir(parents=True)
    (root / CODEX).write_text(
        'developer_instructions = """Deploy with API_KEY=sk-live-0123456789abcdef."""\n'
    )
    recorded = _entry(
        root,
        files={
            root / LOCAL: text_entry(root / LOCAL, "# Local\n\nUse yarn.\n"),
            root / CODEX: text_entry(root / CODEX, codex),
        },
    )
    plan = _plan(root, _render(root), recorded)
    assert (plan.whole, plan.code) == (True, 1)
    shown, withheld = [r.message for r in plan.refusals if r.kind == "abort"]
    assert shown == (
        "Sync aborted — the '-' lines would be lost. Port them into the source with "
        "/loadout, or run `loadout sync --overlay app --force` to discard them."
    )
    assert withheld.startswith("Sync aborted — a file whose diff is not shown")
    assert withheld.endswith("or run `loadout sync --overlay app --force` to discard it.")


def test_a_saved_approval_is_carried_without_drift(root: Path) -> None:
    on_disk = {
        "model": "opus",
        "permissions": {"allow": ["Bash(yarn test:*)", "Bash(npm run x)"]},
    }
    path = root / SETTINGS
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(on_disk))
    recorded = _entry(
        root,
        documents={
            path: DocumentRecord(
                created=True,
                owned_keys={("model",): leaf_digest("opus")},
                owned_rules={ALLOW: ("Bash(yarn test:*)",)},
            )
        },
    )
    plan = _plan(root, _render(root), recorded, disk=_disk(root, {SETTINGS: on_disk}))
    assert plan.carried == (".claude/settings.local.json: permissions.allow: Bash(npm run x)",)
    assert sorted(d.path for d in plan.drift) == _under(root, *WHOLE, OPENCODE)
    assert sorted(plan.writes) == _under(root, *WHOLE, OPENCODE)
    assert plan.code == 0


def test_an_owned_rule_dropped_from_the_source_is_removed_and_the_approval_kept(
    root: Path,
) -> None:
    on_disk = {
        "model": "opus",
        "permissions": {"allow": ["Bash(yarn test:*)", "Bash(yarn lint)", "Bash(npm run x)"]},
    }
    path = root / SETTINGS
    recorded = _entry(
        root,
        documents={
            path: DocumentRecord(
                created=True,
                owned_keys={("model",): leaf_digest("opus")},
                owned_rules={ALLOW: ("Bash(yarn test:*)", "Bash(yarn lint)")},
            )
        },
    )
    plan = _plan(root, _render(root), recorded, disk=_disk(root, {SETTINGS: on_disk}))
    assert json.loads(plan.writes[path])["permissions"]["allow"] == [
        "Bash(yarn test:*)",
        "Bash(npm run x)",
    ]
    assert plan.record.documents[path].owned_rules == {ALLOW: ("Bash(yarn test:*)",)}
    assert [(d.path, d.kind) for d in plan.drift if d.path == path] == [(path, "source-changed")]
    assert plan.code == 0


def test_the_ahead_record_keeps_the_old_rules_and_marks_the_new_ones_pending(
    root: Path,
) -> None:
    on_disk = {"model": "opus", "permissions": {"allow": ["Bash(yarn test:*)", "Bash(yarn lint)"]}}
    path = root / SETTINGS
    recorded = _entry(
        root,
        documents={
            path: DocumentRecord(
                created=True,
                owned_keys={("model",): leaf_digest("opus")},
                owned_rules={ALLOW: ("Bash(yarn test:*)", "Bash(yarn lint)")},
            )
        },
    )
    render = _render(root, {SETTINGS: _settings("Bash(yarn test:*)", "Bash(yarn build)")})
    plan = _plan(root, render, recorded, disk=_disk(root, {SETTINGS: on_disk}))
    assert json.loads(plan.writes[path])["permissions"]["allow"] == [
        "Bash(yarn test:*)",
        "Bash(yarn build)",
    ]
    assert plan.ahead.documents[path].owned_rules == {
        ALLOW: ("Bash(yarn test:*)", "Bash(yarn lint)")
    }
    assert plan.ahead.documents[path].pending_rules == {ALLOW: ("Bash(yarn build)",)}
    assert plan.record.documents[path].owned_rules == {
        ALLOW: ("Bash(yarn test:*)", "Bash(yarn build)")
    }


def test_a_removed_slice_takes_its_key_out_and_keeps_a_foreign_one(root: Path) -> None:
    on_disk = {"permission": PERMISSION, "mcp": MCP, "theme": "dark"}
    recorded = _entry(
        root,
        documents={
            root / OPENCODE: DocumentRecord(
                created=True,
                owned_keys={("permission",): leaf_digest(PERMISSION), ("mcp",): leaf_digest(MCP)},
                owned_rules={},
            )
        },
    )
    plan = _plan(
        root,
        _render(root, {OPENCODE: _opencode(mcp=False)}),
        recorded,
        disk=_disk(root, {OPENCODE: on_disk}),
    )
    assert json.loads(plan.writes[root / OPENCODE]) == {"permission": PERMISSION, "theme": "dark"}
    assert list(plan.record.documents[root / OPENCODE].owned_keys) == [("permission",)]


def test_a_user_defined_key_is_refused_and_the_other_keys_still_written(root: Path) -> None:
    theirs = {"bash": {"*": "ask"}}
    plan = _plan(root, _render(root), disk=_disk(root, {OPENCODE: {"permission": theirs}}))
    assert json.loads(plan.writes[root / OPENCODE]) == {"permission": theirs, "mcp": MCP}
    assert [(r.kind, r.code, r.path) for r in plan.refusals] == [
        ("user-defined", 3, root / OPENCODE)
    ]
    assert list(plan.record.documents[root / OPENCODE].owned_keys) == [("mcp",)]
    assert plan.whole is False
    assert plan.code == 3


def test_a_document_owning_nothing_stays_out_of_the_block(root: Path) -> None:
    theirs = {"permission": {"bash": {"*": "ask"}}, "mcp": {"local": {"type": "local"}}}
    plan = _plan(root, _render(root), disk=_disk(root, {OPENCODE: theirs}))
    assert plan.block_before == (
        "/CLAUDE.local.md",
        "/.codex/config.toml",
        "/.claude/settings.local.json",
        "/.claude/skills/review/",
        TEMP_PATTERN,
    )
    assert sorted(plan.record.documents) == [root / SETTINGS]
    assert [(r.kind, r.path) for r in plan.refusals] == [
        ("user-defined", root / OPENCODE),
        ("user-defined", root / OPENCODE),
    ]


@pytest.mark.parametrize("force", [False, True])
def test_a_recorded_rule_list_turned_into_a_string_is_refused_and_kept(
    root: Path, *, force: bool
) -> None:
    on_disk = {"model": "opus", "permissions": {"allow": "Bash(yarn test:*)"}}
    recorded = _entry(root, documents={root / SETTINGS: _created_settings()})
    disk = _disk(root, {SETTINGS: on_disk})
    plan = _plan(root, _render(root), recorded, disk=disk, force=force)
    assert plan.whole is False
    assert sorted(plan.writes) == _under(root, *WHOLE, OPENCODE)
    assert plan.record.documents[root / SETTINGS] == _created_settings()
    assert plan.refusals == (
        Refusal(
            "unparsable",
            1,
            ".claude/settings.local.json: permissions.allow is not a list of rules; "
            "loadout does not overwrite it — fix the value by hand",
            root / SETTINGS,
        ),
    )
    assert plan.code == 1


@pytest.mark.parametrize("force", [False, True])
def test_a_non_object_parent_in_an_unrecorded_document_is_refused(
    root: Path, *, force: bool
) -> None:
    disk = _disk(root, {SETTINGS: {"permissions": "x"}})
    plan = _plan(root, _render(root), disk=disk, force=force)
    assert plan.whole is False
    assert sorted(plan.writes) == _under(root, *WHOLE, OPENCODE)
    assert sorted(plan.record.documents) == [root / OPENCODE]
    assert plan.block_before == (
        "/CLAUDE.local.md",
        "/.codex/config.toml",
        "/opencode.json",
        "/.claude/skills/review/",
        TEMP_PATTERN,
    )
    assert plan.refusals == (
        Refusal(
            "unparsable",
            1,
            ".claude/settings.local.json: cannot write permissions.allow: permissions is not "
            "a JSON object; loadout does not overwrite it — fix the value by hand",
            root / SETTINGS,
        ),
    )
    assert plan.code == 1


def _created_settings() -> DocumentRecord:
    return DocumentRecord(
        created=True,
        owned_keys={("model",): leaf_digest("opus")},
        owned_rules={ALLOW: ("Bash(yarn test:*)",)},
    )


def test_retirement_unlinks_a_created_document_holding_only_owned_parts(root: Path) -> None:
    owned_only = {"model": "opus", "permissions": {"allow": ["Bash(yarn test:*)"]}}
    recorded = _entry(root, documents={root / SETTINGS: _created_settings()})
    plan = _plan(root, None, recorded, disk=_disk(root, {SETTINGS: owned_only}))
    assert plan.deletes == (root / SETTINGS,)
    assert plan.writes == {}
    assert plan.block_before == ("/.claude/settings.local.json", TEMP_PATTERN)
    assert plan.block_after == ()
    assert plan.record.documents == {}


def test_retirement_keeps_a_saved_approval_and_claudes_keys(root: Path) -> None:
    on_disk = {
        "model": "opus",
        "permissions": {"allow": ["Bash(yarn test:*)", "Bash(npm run x)"]},
        "outputStyle": "Explanatory",
    }
    recorded = _entry(root, documents={root / SETTINGS: _created_settings()})
    plan = _plan(root, None, recorded, disk=_disk(root, {SETTINGS: on_disk}))
    assert json.loads(plan.writes[root / SETTINGS]) == {
        "permissions": {"allow": ["Bash(npm run x)"]},
        "outputStyle": "Explanatory",
    }
    assert plan.deletes == ()


def test_a_retired_target_releases_the_ignore_line_of_a_document_it_leaves_behind(
    root: Path,
) -> None:
    on_disk = {"model": "opus", "permissions": {"allow": ["Bash(yarn test:*)", "Bash(npm x)"]}}
    recorded = _entry(root, documents={root / SETTINGS: _created_settings()})
    plan = _plan(root, None, recorded, disk=_disk(root, {SETTINGS: on_disk}))
    assert json.loads(plan.writes[root / SETTINGS]) == {"permissions": {"allow": ["Bash(npm x)"]}}
    assert plan.block_before == ("/.claude/settings.local.json", TEMP_PATTERN)
    assert plan.block_after == ()
    assert plan.record.block is None


def test_a_retired_whole_file_leaves_the_block_after_deleting(root: Path) -> None:
    pi = root / ".pi/APPEND_SYSTEM.md"
    pi.parent.mkdir(parents=True)
    pi.write_text("Use yarn.\n")
    recorded = _entry(root, files={pi: text_entry(pi, "Use yarn.\n")})
    plan = _plan(root, _render(root), recorded)
    assert plan.deletes == (pi,)
    assert plan.block_before[-3:] == (
        "/.claude/skills/review/",
        "/.pi/APPEND_SYSTEM.md",
        TEMP_PATTERN,
    )
    assert plan.block_after[-2:] == ("/.claude/skills/review/", TEMP_PATTERN)
    assert sorted(plan.record.files) == _under(root, *WHOLE)
    assert sorted(plan.ahead.files) == sorted([*_under(root, *WHOLE), pi])
    assert [(d.path, d.kind) for d in plan.drift if d.path == pi] == [(pi, "orphaned")]


def test_a_dropped_server_retires_for_its_own_target_only(tmp_path: Path) -> None:
    app1, app2 = tmp_path / "app1", tmp_path / "app2"
    claude_json = tmp_path / ".claude.json"
    recorded1 = _entry(
        app1, leaves={claude_json: {server_path(app1, "figma"): "f", server_path(app1, "old"): "o"}}
    )
    recorded2 = _entry(app2, leaves={claude_json: {server_path(app2, "old"): "o"}})
    plan = _plan(app1, _render(app1), recorded1)
    assert plan.retiring_leaves == {claude_json: (server_path(app1, "old"),)}
    assert plan.wanted_leaves == {server_path(app1, "figma"): FIGMA}

    other = kept(app2, recorded2, Refusal("error", 3, "git failed"))
    assert (other.wanted_leaves, other.retiring_leaves) == ({}, {})
    assert other.record.leaves == recorded2.leaves
    assert (other.whole, other.code) == (True, 3)


def test_a_claimed_leaf_is_refused_and_never_retired(root: Path) -> None:
    claude_json = root.parent / ".claude.json"
    figma = server_path(root, "figma")
    recorded = _entry(root, leaves={claude_json: {figma: "f"}})
    disk = replace(_disk(root), claimed_leaves={figma: "overlay other"})
    plan = _plan(root, _render(root), recorded, disk=disk)
    assert plan.wanted_leaves == {}
    assert plan.retiring_leaves == {}
    assert [r.message for r in plan.refusals] == [
        "MCP server figma is written by overlay other; the overlay does not write it"
    ]


def test_a_claimed_orphan_leaves_the_record_with_a_note(root: Path) -> None:
    pi = root / ".pi/APPEND_SYSTEM.md"
    pi.parent.mkdir(parents=True)
    pi.write_text("Theirs.\n")
    recorded = _entry(root, files={pi: text_entry(pi, "Use yarn.\n")})
    disk = replace(_disk(root), claimed={pi: "overlay other"})
    plan = _plan(root, _render(root), recorded, disk=disk)
    assert plan.deletes == ()
    assert sorted(plan.record.files) == _under(root, *WHOLE)
    assert plan.notes == (
        ".pi/APPEND_SYSTEM.md is now written by overlay other; the overlay no longer records it",
    )
    assert plan.whole is False


def test_a_claimed_skill_folder_drops_every_file_in_it(root: Path) -> None:
    disk = replace(_disk(root), claimed={root / ".claude/skills/review": "overlay other"})
    plan = _plan(root, _render(root), disk=disk)
    assert sorted(plan.writes) == _under(root, LOCAL, CODEX, SETTINGS, OPENCODE)
    assert [(r.kind, r.message) for r in plan.refusals] == [
        (
            "claimed",
            ".claude/skills/review is written by overlay other; the overlay does not write it",
        )
    ]


def test_an_edited_owned_key_refuses_the_target_until_forced(root: Path) -> None:
    on_disk = {"model": "sonnet", "permissions": {"allow": ["Bash(yarn test:*)"]}}
    recorded = _entry(root, documents={root / SETTINGS: _created_settings()})
    disk = _disk(root, {SETTINGS: on_disk})
    plan = _plan(root, _render(root), recorded, disk=disk)
    assert (plan.whole, plan.code) == (True, 1)
    assert [(d.path, d.kind) for d in plan.drift if d.path == root / SETTINGS] == [
        (root / SETTINGS, "owned-key")
    ]
    forced = _plan(root, _render(root), recorded, disk=disk, force=True)
    assert json.loads(forced.writes[root / SETTINGS])["model"] == "opus"


def test_a_missing_document_is_missing_drift(root: Path) -> None:
    plan = _plan(root, _render(root))
    kinds = {d.path: d.kind for d in plan.drift}
    assert kinds[root / SETTINGS] == "missing"
    assert kinds[root / LOCAL] == "missing"


def _expansion(**fields: Any) -> Expansion:
    base = Expansion(targets=(), skipped=(), matched=frozenset(), unlisted=(), unlisted_under=())
    return replace(base, **fields)


def _record(home: Path, *roots: Path) -> OverlayRecord:
    return OverlayRecord(
        folder=home / "overlay",
        name="app",
        home=home,
        shared={},
        targets={root: _entry(root) for root in roots},
    )


def test_classify_plans_matched_targets_and_keeps_matched_skips(tmp_path: Path) -> None:
    app1, app2 = tmp_path / "app1", tmp_path / "app2"
    expansion = _expansion(
        targets=(_target(app1),),
        skipped=(SkippedTarget(app2, "git-failed", "git failed"),),
        matched=frozenset({app1, app2}),
    )
    actions, refusals = classify_recorded(
        _record(tmp_path, app1, app2), expansion, {}, scope=None, home=tmp_path, force=False
    )
    assert actions == {app1: "plan", app2: "keep"}
    assert refusals == ()


def test_classify_retires_gone_and_keeps_unreachable_and_unlisted(tmp_path: Path) -> None:
    live, gone, lost, hidden = (tmp_path / n for n in ("live", "gone", "lost", "under/x"))
    expansion = _expansion(
        targets=(_target(live),),
        matched=frozenset({live}),
        unlisted_under=(tmp_path / "under",),
    )
    fates: dict[Path, RecordedFate] = {gone: "gone", lost: "unreachable", hidden: "gone"}
    actions, refusals = classify_recorded(
        _record(tmp_path, live, gone, lost, hidden),
        expansion,
        fates,
        scope=None,
        home=tmp_path,
        force=False,
    )
    assert actions == {live: "plan", gone: "retire", lost: "keep", hidden: "keep"}
    assert refusals == ()


def test_classify_keeps_everything_under_another_home(tmp_path: Path) -> None:
    live, gone = tmp_path / "live", tmp_path / "gone"
    expansion = _expansion(targets=(_target(live),), matched=frozenset({live}))
    actions, refusals = classify_recorded(
        _record(tmp_path / "old-home", live, gone),
        expansion,
        {gone: "gone"},
        scope=None,
        home=tmp_path,
        force=True,
    )
    assert actions == {live: "plan", gone: "keep"}
    assert [r.code for r in refusals] == [3]
    assert str(tmp_path / "old-home") in refusals[0].message
    assert f"HOME is now {tmp_path};" in refusals[0].message


def test_classify_retires_nothing_when_no_folder_matched_unless_forced(tmp_path: Path) -> None:
    app1, app2 = tmp_path / "app1", tmp_path / "app2"
    record = _record(tmp_path, app1, app2)
    fates: dict[Path, RecordedFate] = {app1: "gone", app2: "present"}
    actions, refusals = classify_recorded(
        record, _expansion(), fates, scope=None, home=tmp_path, force=False
    )
    assert actions == {app1: "keep", app2: "keep"}
    assert [r.message for r in refusals] == [
        (
            "the patterns matched no folder; nothing is retired — fix `targets`, or run "
            "`loadout sync --overlay app --force` to retire 2 targets"
        )
    ]
    forced, none = classify_recorded(
        record, _expansion(), fates, scope=None, home=tmp_path, force=True
    )
    assert (forced, none) == ({app1: "retire", app2: "retire"}, ())


def test_classify_with_a_scope_classifies_only_the_scoped_root(tmp_path: Path) -> None:
    app1, app2, gone = tmp_path / "app1", tmp_path / "app2", tmp_path / "gone"
    expansion = _expansion(targets=(_target(app1),), matched=frozenset({app1, app2}))
    actions, refusals = classify_recorded(
        _record(tmp_path, app1, app2, gone),
        expansion,
        {gone: "gone"},
        scope=app1,
        home=tmp_path,
        force=False,
    )
    assert actions == {app1: "plan"}
    assert refusals == ()


def test_a_unit_is_the_skill_folder_a_path_sits_in_or_else_the_path_itself(root: Path) -> None:
    paths = (root / SKILL, root / HELPER, root / LOCAL, root / ".claude/skills")
    assert [unit_of(root, path) for path in paths] == [
        ".claude/skills/review",
        ".claude/skills/review",
        "CLAUDE.local.md",
        ".claude/skills",
    ]
    assert folder_units(root, paths) == {".claude/skills/review"}
