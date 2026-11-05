from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from loadout import overlay_sync
from loadout.claude_json import LeafResult, server_path
from loadout.errors import LoadoutError
from loadout.exclude import TEMP_PATTERN
from loadout.json_paths import JsonPath, leaf_digest
from loadout.overlay_documents import DocumentRecord
from loadout.overlay_plan import DiskFacts, RecordedAction, TargetPlan
from loadout.overlay_record import OverlayRecord, TargetRecord, empty_record
from loadout.overlay_registry import RegisteredOverlay
from loadout.overlay_sync import (
    EMPTY_EXPANSION,
    OverlayOutcome,
    OverlayRun,
    _Context,
    _Planned,
    _Prepared,
    report,
    sync_overlay,
)
from loadout.overlay_targets import Target, harness_homes
from loadout.written import text_entry

SERVER = {"type": "http", "url": "https://docs.example/mcp"}
COMMITS = ("_commit_ahead", "_commit_target", "_commit_pending", "_commit_leaves")


def _entry(root: Path, *, block: str | None = None, **parts: Any) -> TargetRecord:
    return TargetRecord(
        common_dir=root / ".git",
        device=1,
        block=block,
        files=parts.get("files", {}),
        documents={},
        leaves=parts.get("leaves", {}),
        pending=parts.get("pending", {}),
    )


def _planned(root: Path, *, whole: bool = False, recorded: TargetRecord | None = None) -> _Planned:
    local, document, orphan = root / "CLAUDE.local.md", root / "opencode.json", root / "old.md"
    target = Target(root=root, common_dir=root / ".git", device=1)
    base = recorded or _entry(root)
    plan = TargetPlan(
        root=root,
        whole=whole,
        writes={} if whole else {local: "# Local\n", document: "{}\n"},
        deletes=() if whole else (orphan,),
        block_before=("/CLAUDE.local.md", "/opencode.json", "/old.md", TEMP_PATTERN),
        block_after=("/CLAUDE.local.md", "/opencode.json", TEMP_PATTERN),
        ahead=_entry(
            root,
            block="app",
            files={
                **base.files,
                local: text_entry(local, "# Local\n"),
                orphan: text_entry(orphan, "old\n"),
            },
            leaves=base.leaves,
            pending=base.pending,
        ),
        record=_entry(
            root,
            block="app",
            files={local: text_entry(local, "# Local\n")},
            leaves=base.leaves,
            pending=base.pending,
        ),
        wanted_leaves={} if whole else {server_path(root, "docs"): SERVER},
        retiring_leaves={},
        refusals=(),
        drift=(),
        carried=(),
        notes=(),
        code=0,
    )
    disk = DiskFacts(
        documents={document: None}, document_bytes={document: None}, claimed={}, claimed_leaves={}
    )
    return _Planned(target, None, recorded, disk, False, plan)


@contextmanager
def _scratch(path: Path) -> Iterator[Path]:
    yield path


class Recorder:
    def __init__(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.records: list[OverlayRecord] = []
        self.leaf_calls: list[tuple[Path, dict[JsonPath, Any], tuple[JsonPath, ...]]] = []
        self.owned: list[dict[JsonPath, frozenset[str]]] = []
        self.failing: frozenset[Path] = frozenset()
        self.tracked: frozenset[str] = frozenset()
        self.current = tmp_path / "home" / ".claude.json"
        for name in COMMITS:
            self._spy(monkeypatch, name)
        fakes = {
            "write_overlay_record": self.write_record,
            "write_repo_block": self.block,
            "unignored": lambda root, units, folders: {},
            "tracked_units": lambda root, units: self.tracked & frozenset(units),
            "write_outputs": self.files,
            "_write_document": self.document,
            "_delete": self.delete,
            "write_leaves": self.leaves,
            "claude_json_path": lambda: self.current,
            "scratch_repo": lambda: _scratch(tmp_path),
        }
        for name, fake in fakes.items():
            monkeypatch.setattr(overlay_sync, name, fake)

    def _spy(self, monkeypatch: pytest.MonkeyPatch, name: str) -> None:
        real = getattr(overlay_sync, name)

        def spy(*args: Any, **kwargs: Any) -> Any:
            self.calls.append((name, None))
            return real(*args, **kwargs)

        monkeypatch.setattr(overlay_sync, name, spy)

    def write_record(self, record: OverlayRecord) -> None:
        self.records.append(record)
        self.calls.append(("write-record", None))

    def block(self, common_dir: Path, name: str, patterns: Sequence[str]) -> bool:
        self.calls.append(("block", tuple(patterns)))
        return True

    def files(self, outputs: Mapping[Path, Any], sources: Any = None) -> list[Path]:
        if any(path.parent in self.failing for path in outputs):
            raise OSError("disk full")
        self.calls.append(("files", tuple(sorted(outputs))))
        return list(outputs)

    def document(self, path: Path, text: str, expected: bytes | None) -> None:
        self.calls.append(("document", path))

    def delete(
        self, paths: Sequence[Path], entry: TargetRecord, expected: Mapping[Path, bytes | None]
    ) -> list[Path]:
        self.calls.append(("delete", tuple(paths)))
        return list(paths)

    def leaves(  # noqa: PLR0913
        self,
        path: Path,
        wanted: Mapping[JsonPath, Any],
        owned: Mapping[JsonPath, frozenset[str]],
        retiring: Sequence[JsonPath],
        *,
        force: bool,
        pending: Mapping[JsonPath, str],
    ) -> LeafResult:
        self.calls.append(("leaves", path))
        self.leaf_calls.append((path, dict(wanted), tuple(retiring)))
        self.owned.append(dict(owned))
        return LeafResult(
            written=tuple(wanted),
            retired=tuple(retiring),
            refused=(),
            digests={leaf: leaf_digest(value) for leaf, value in wanted.items()},
        )


def _run(  # noqa: PLR0913
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    planned: Sequence[_Planned],
    record: OverlayRecord | None = None,
    *,
    actions: Mapping[Path, RecordedAction] | None = None,
    retire_all: bool = False,
) -> Any:
    overlay = RegisteredOverlay("app", tmp_path / "overlay")
    held = record or empty_record(overlay.folder, "app", tmp_path / "home")
    context = _Context(
        overlay, False, None, lambda excludes, units, folders: frozenset(), retire_all
    )
    prepared = _Prepared(
        held, "ok", EMPTY_EXPANSION, dict(actions or {}), tuple(planned), (), context
    )
    monkeypatch.setattr(overlay_sync, "_prepare", lambda run, scratch: prepared)
    run = OverlayRun(
        overlay=overlay, scope=None, force=False, interactive=False, retire_all=retire_all
    )
    return sync_overlay(run)


def test_a_target_is_recorded_ahead_blocked_written_deleted_and_recorded_in_that_order(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    recorder = Recorder(monkeypatch, tmp_path)
    root = tmp_path / "app1"

    outcome = _run(monkeypatch, tmp_path, [_planned(root)])

    assert outcome.code == 0
    assert recorder.calls == [
        ("_commit_ahead", None),
        ("write-record", None),
        ("block", ("/CLAUDE.local.md", "/opencode.json", "/old.md", TEMP_PATTERN)),
        ("files", (root / "CLAUDE.local.md",)),
        ("document", root / "opencode.json"),
        ("delete", (root / "old.md",)),
        ("block", ("/CLAUDE.local.md", "/opencode.json", TEMP_PATTERN)),
        ("_commit_target", None),
        ("write-record", None),
        ("_commit_pending", None),
        ("write-record", None),
        ("leaves", recorder.current),
        ("_commit_leaves", None),
        ("write-record", None),
    ]


def test_the_pending_commit_names_the_leaf_before_it_is_written(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    recorder = Recorder(monkeypatch, tmp_path)
    root = tmp_path / "app1"
    leaf = server_path(root, "docs")

    _run(monkeypatch, tmp_path, [_planned(root)])

    pending, settled = recorder.records[-2], recorder.records[-1]
    assert pending.targets[root].pending == {recorder.current: {leaf: leaf_digest(SERVER)}}
    assert pending.targets[root].leaves == {}
    assert settled.targets[root].leaves == {recorder.current: {leaf: leaf_digest(SERVER)}}
    assert settled.targets[root].pending == {}


def test_a_wholesale_plan_makes_no_calls(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    recorder = Recorder(monkeypatch, tmp_path)
    planned = _planned(tmp_path / "app1", whole=True)

    outcome = _run(monkeypatch, tmp_path, [planned])

    assert outcome.plans == (planned.plan,)
    assert [name for name, _ in recorder.calls if name not in COMMITS] == []


def test_a_target_failing_mid_apply_leaves_the_later_ones_applied_with_their_leaves(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    recorder = Recorder(monkeypatch, tmp_path)
    broken, healthy = tmp_path / "app1", tmp_path / "app2"
    recorder.failing = frozenset({broken})

    outcome = _run(monkeypatch, tmp_path, [_planned(broken), _planned(healthy)])

    codes = {plan.root: (plan.code, plan.whole) for plan in outcome.plans}
    assert outcome.code == 3
    assert codes == {broken: (3, True), healthy: (0, False)}
    assert outcome.plans[0].refusals[-1].message == "disk full"
    assert ("files", (healthy / "CLAUDE.local.md",)) in recorder.calls
    assert recorder.leaf_calls == [
        (recorder.current, {server_path(healthy, "docs"): SERVER}, ()),
    ]
    final = recorder.records[-1]
    assert sorted(final.targets) == [broken, healthy]
    assert final.targets[broken].block == "app"
    assert final.targets[broken].leaves == {}


def test_leaves_recorded_under_another_claude_json_are_retired_there(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    recorder = Recorder(monkeypatch, tmp_path)
    root = tmp_path / "app1"
    old = tmp_path / "old-config" / ".claude.json"
    leaf = server_path(root, "docs")
    recorded = _entry(root, block="app", leaves={old: {leaf: leaf_digest(SERVER)}})
    overlay_folder = tmp_path / "overlay"
    record = OverlayRecord(
        folder=overlay_folder,
        name="app",
        home=harness_homes().home,
        shared={},
        targets={root: recorded},
    )

    outcome = _run(monkeypatch, tmp_path, [_planned(root, recorded=recorded)], record)

    assert outcome.code == 0
    assert recorder.leaf_calls == [
        (recorder.current, {leaf: SERVER}, ()),
        (old, {}, (leaf,)),
    ]
    assert recorder.records[-1].targets[root].leaves == {
        recorder.current: {leaf: leaf_digest(SERVER)}
    }


def test_leaves_recorded_under_the_recorded_home_are_kept_while_home_points_elsewhere(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    recorder = Recorder(monkeypatch, tmp_path)
    root = tmp_path / "app1"
    real = tmp_path / "real-home" / ".claude.json"
    leaf = server_path(root, "docs")
    recorded = _entry(root, block="app", leaves={real: {leaf: leaf_digest(SERVER)}})
    record = OverlayRecord(
        folder=tmp_path / "overlay",
        name="app",
        home=tmp_path / "real-home",
        shared={},
        targets={root: recorded},
    )

    outcome = _run(monkeypatch, tmp_path, [_planned(root, recorded=recorded)], record)

    assert outcome.code == 0
    assert recorder.leaf_calls == [(recorder.current, {leaf: SERVER}, ())]
    assert recorder.records[-1].targets[root].leaves == {
        recorder.current: {leaf: leaf_digest(SERVER)},
        real: {leaf: leaf_digest(SERVER)},
    }


def test_leaves_recorded_under_another_spelling_of_the_same_claude_json_stay_owned(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    recorder = Recorder(monkeypatch, tmp_path)
    real = tmp_path / "real-home"
    real.mkdir()
    (real / ".claude.json").write_text("{}\n", encoding="utf-8")
    (tmp_path / "linked-home").symlink_to(real)
    recorder.current = tmp_path / "linked-home" / ".claude.json"
    root = tmp_path / "app1"
    leaf = server_path(root, "docs")
    recorded = _entry(root, block="app", leaves={real / ".claude.json": {leaf: "recorded"}})
    record = OverlayRecord(
        folder=tmp_path / "overlay",
        name="app",
        home=harness_homes().home,
        shared={},
        targets={root: recorded},
    )

    outcome = _run(monkeypatch, tmp_path, [_planned(root, recorded=recorded)], record)

    assert outcome.code == 0
    assert recorder.leaf_calls == [(recorder.current, {leaf: SERVER}, ())]
    assert recorder.owned == [{leaf: frozenset({"recorded"})}]
    assert recorder.records[-1].targets[root].leaves == {
        recorder.current: {leaf: leaf_digest(SERVER)}
    }


def test_a_leaf_left_pending_by_a_crash_is_created_not_judged_removed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    recorder = Recorder(monkeypatch, tmp_path)
    root = tmp_path / "app1"
    leaf, kept_leaf = server_path(root, "docs"), server_path(root, "kept")
    recorded = replace(
        _entry(root, block="app", leaves={recorder.current: {kept_leaf: "recorded"}}),
        pending={recorder.current: {leaf: leaf_digest(SERVER), kept_leaf: "pending"}},
    )
    record = OverlayRecord(
        folder=tmp_path / "overlay",
        name="app",
        home=tmp_path / "home",
        shared={},
        targets={root: recorded},
    )

    _run(monkeypatch, tmp_path, [_planned(root, recorded=recorded)], record)

    assert recorder.owned == [{leaf: frozenset(), kept_leaf: frozenset({"recorded", "pending"})}]


def test_units_the_team_starts_tracking_mid_sync_are_neither_written_nor_deleted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    recorder = Recorder(monkeypatch, tmp_path)
    recorder.tracked = frozenset({"CLAUDE.local.md", "old.md"})
    root = tmp_path / "app1"
    orphan = root / "old.md"
    recorded = _entry(root, block="app", files={orphan: text_entry(orphan, "old\n")})
    record = OverlayRecord(
        folder=tmp_path / "overlay",
        name="app",
        home=tmp_path / "home",
        shared={},
        targets={root: recorded},
    )

    outcome = _run(monkeypatch, tmp_path, [_planned(root, recorded=recorded)], record)

    assert outcome.code == 3
    assert [(r.kind, r.path, r.message) for r in outcome.plans[0].refusals] == [
        (
            "tracked",
            root / "CLAUDE.local.md",
            "the team tracks CLAUDE.local.md; the overlay does not write it",
        ),
    ]
    assert outcome.plans[0].notes[-1] == (
        "the team tracks old.md; it is left in place and no longer recorded"
    )
    assert [call for call in recorder.calls if call[0] in {"files", "document", "delete"}] == [
        ("document", root / "opencode.json"),
        ("delete", ()),
    ]
    assert recorder.records[-1].targets[root].files == {}


def _record_of(tmp_path: Path, targets: Mapping[Path, TargetRecord]) -> OverlayRecord:
    return OverlayRecord(
        folder=tmp_path / "overlay",
        name="app",
        home=tmp_path / "home",
        shared={},
        targets=dict(targets),
    )


def test_an_empty_retired_target_is_dropped_even_when_the_leaf_phase_fails_early(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    recorder = Recorder(monkeypatch, tmp_path)
    kept_root, retired_root = tmp_path / "app1", tmp_path / "app2"
    empty = _entry(retired_root, block="app")
    retiring = _planned(retired_root, recorded=empty)
    retired_plan = replace(
        retiring.plan, writes={}, deletes=(), ahead=empty, record=empty, wanted_leaves={}
    )

    def refusing() -> Path:
        raise LoadoutError("CLAUDE_CODE_CUSTOM_OAUTH_URL is set")

    monkeypatch.setattr(overlay_sync, "claude_json_path", refusing)
    record = _record_of(tmp_path, {kept_root: _entry(kept_root), retired_root: empty})

    outcome = _run(
        monkeypatch,
        tmp_path,
        [_planned(kept_root), replace(retiring, plan=retired_plan)],
        record,
        actions={kept_root: "keep", retired_root: "retire"},
    )

    assert outcome.code == 3
    assert outcome.errors == ("CLAUDE_CODE_CUSTOM_OAUTH_URL is set",)
    assert sorted(recorder.records[-1].targets) == [kept_root]


def test_a_unit_the_team_starts_tracking_mid_removal_is_left_and_dropped_from_the_record(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    recorder = Recorder(monkeypatch, tmp_path)
    recorder.tracked = frozenset({"old.md"})
    root = tmp_path / "app1"
    orphan = root / "old.md"
    recorded = _entry(root, block="app", files={orphan: text_entry(orphan, "old\n")})

    outcome = _run(
        monkeypatch,
        tmp_path,
        [_planned(root, recorded=recorded)],
        _record_of(tmp_path, {root: recorded}),
        retire_all=True,
    )

    assert outcome.code == 0
    assert outcome.plans[0].notes == (
        "the team tracks old.md; it is left in place and no longer recorded",
    )
    assert recorder.records[-1].targets[root].files == {
        root / "CLAUDE.local.md": text_entry(root / "CLAUDE.local.md", "# Local\n")
    }


def test_a_document_stripped_but_still_recorded_is_reported_retired(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "app1"
    local, document = root / "CLAUDE.local.md", root / "opencode.json"
    plan = _planned(root).plan
    record = replace(
        plan.record,
        documents={
            document: DocumentRecord(created=False, owned_keys={}, owned_rules={}),
        },
    )
    stripped = replace(plan, deletes=(), record=record)

    report(OverlayOutcome("app", (stripped,), (), (), (), 0))

    assert capsys.readouterr().out.splitlines() == [
        f"overlay app: {root}: wrote CLAUDE.local.md",
        f"overlay app: {root}: retired opencode.json",
        f"overlay app: {root}: 1 written, 0 unchanged, 1 retired",
    ]
    assert local in stripped.writes


def test_the_user_server_files_follow_the_environment_they_are_given(tmp_path: Path) -> None:
    environ = {
        "HOME": str(tmp_path / "home"),
        "CODEX_HOME": str(tmp_path / "codex"),
        "FACTORY_HOME_OVERRIDE": str(tmp_path / "factory"),
        "XDG_CONFIG_HOME": str(tmp_path / "xdg"),
        "PI_CODING_AGENT_DIR": str(tmp_path / "pi"),
        "OPENCODE_CONFIG": str(tmp_path / "custom.json"),
    }
    harnesses = frozenset({"codex", "droid", "opencode", "pi"})
    assert overlay_sync._user_files(harnesses, environ) == [
        (tmp_path / "codex/config.toml", "mcp_servers"),
        (tmp_path / "factory/.factory/mcp.json", "mcpServers"),
        (tmp_path / "xdg/opencode/opencode.json", "mcp"),
        (tmp_path / "xdg/opencode/opencode.jsonc", "mcp"),
        (tmp_path / "pi/mcp.json", "mcpServers"),
        (tmp_path / "custom.json", "mcp"),
    ]
