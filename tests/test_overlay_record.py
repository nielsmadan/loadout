from __future__ import annotations

import json
import os
import shutil
import stat
from dataclasses import replace
from pathlib import Path

import pytest

from loadout.claude_json import LeafResult, LeafVerdict, server_path
from loadout.errors import LoadoutError
from loadout.output import Copied
from loadout.overlay_documents import DocumentRecord
from loadout.overlay_record import (
    NEWER_RECORD,
    OverlayRecord,
    RecordProblem,
    TargetRecord,
    UnreadableRecords,
    claimed_leaves,
    claimed_paths,
    claimed_skills,
    empty_record,
    every_overlay_record,
    every_overlay_recorded_path,
    foreign_claims,
    overlay_record_path,
    read_overlay_record,
    record_problem_message,
    render_overlay_record,
    scan_overlay_records,
    settled_leaves,
    united_document,
    with_pending,
    with_target,
    without_target,
    write_overlay_record,
)
from loadout.state import sync_state
from loadout.sync import unaccepted_against_record
from loadout.unmanaged import unmanaged_entries
from loadout.written import WrittenEntry, record_written, text_entry

SETTINGS = Path(".claude/settings.local.json")
ALLOW = ("permissions", "allow")


def _target(root: Path, home: Path, *, skill: str = "review") -> TargetRecord:
    claude_json = home / ".claude.json"
    return TargetRecord(
        common_dir=root / ".git",
        device=16777230,
        block="app",
        files={
            root / "CLAUDE.local.md": text_entry(root / "CLAUDE.local.md", "Use yarn.\n"),
            root / f".claude/skills/{skill}/SKILL.md": WrittenEntry(kind="copied", sha256="c0"),
        },
        documents={
            root / SETTINGS: DocumentRecord(
                created=True,
                owned_keys={("model",): "k1", ("permissions", "defaultMode"): "k2"},
                owned_rules={ALLOW: ("Bash(yarn test:*)",), ("permissions", "deny"): ()},
            )
        },
        leaves={claude_json: {server_path(root, "figma"): "l1"}},
        pending={claude_json: {server_path(root, "linear"): "p1"}},
    )


def _record(folder: Path, home: Path, *roots: Path, name: str = "app") -> OverlayRecord:
    record = OverlayRecord(
        folder=folder,
        name=name,
        home=home,
        shared={
            folder / ".loadout/opencode-instructions.md": text_entry(
                folder / ".loadout/opencode-instructions.md", "Use yarn.\n"
            )
        },
        targets={},
    )
    for root in roots:
        record = with_target(record, root, _target(root, home))
    return record


def test_a_written_record_reads_back_equal_with_the_documented_keys_and_sorted_targets(
    tmp_path: Path, fake_home: Path
) -> None:
    folder = tmp_path / "config/app"
    record = _record(folder, fake_home, tmp_path / "app2", tmp_path / "app1")

    write_overlay_record(record)
    text = overlay_record_path(folder).read_text(encoding="utf-8")
    document = json.loads(text)

    assert read_overlay_record(folder, "app", fake_home) == (record, "ok")
    assert text == render_overlay_record(record)
    assert list(document) == ["version", "overlay", "name", "home", "shared", "targets"]
    assert list(document["targets"]) == [str(tmp_path / "app1"), str(tmp_path / "app2")]
    assert list(document["targets"][str(tmp_path / "app1")]) == [
        "common_dir",
        "device",
        "block",
        "written",
        "documents",
        "leaves",
        "pending",
    ]
    assert document["targets"][str(tmp_path / "app1")]["documents"][
        str(tmp_path / "app1" / SETTINGS)
    ]["owned_rules"] == [
        {"path": ["permissions", "allow"], "entries": ["Bash(yarn test:*)"]},
        {"path": ["permissions", "deny"], "entries": []},
    ]


def test_owned_key_and_rule_order_survives_a_round_trip(tmp_path: Path, fake_home: Path) -> None:
    folder = tmp_path / "app"
    record = _record(folder, fake_home, tmp_path / "t")
    write_overlay_record(record)

    read, _ = read_overlay_record(folder, "app", fake_home)
    document = read.targets[tmp_path / "t"].documents[tmp_path / "t" / SETTINGS]

    assert list(document.owned_keys) == [("model",), ("permissions", "defaultMode")]
    assert list(document.owned_rules) == [ALLOW, ("permissions", "deny")]


def test_a_missing_record_is_absent_and_empty(tmp_path: Path, fake_home: Path) -> None:
    folder = tmp_path / "app"

    assert read_overlay_record(folder, "app", fake_home) == (
        empty_record(folder, "app", fake_home),
        "absent",
    )


@pytest.mark.parametrize(
    "corrupt",
    [
        lambda text: text[:-5],
        lambda text: text.replace('"version": 2', '"version": 1'),
        lambda text: text.replace('"device": 16777230', '"device": "16777230"'),
        lambda text: text.replace('"created": true', '"created": "yes"'),
        lambda text: text.replace('"kind": "text"', '"kind": "merged"'),
        lambda text: text.replace('"version": 2,', '"version": 2, "extra": 1,'),
        lambda text: text.replace('"device":', '"extra": 1, "device":'),
        lambda text: text.replace('"created":', '"extra": 1, "created":'),
        lambda text: text.replace('"kind": "copied"', '"kind": "copied", "extra": "x"'),
        lambda text: text.replace('"sha256": "l1"', '"sha256": "l1", "extra": "x"'),
        lambda text: text.replace('"entries":', '"extra": 1, "entries":'),
        lambda text: json.dumps(
            {
                **json.loads(text),
                "targets": {
                    root: {key: value for key, value in target.items() if key != "pending"}
                    for root, target in json.loads(text)["targets"].items()
                },
            }
        ),
    ],
    ids=[
        "corrupt-json",
        "older-version",
        "bad-device",
        "bad-created",
        "merged-entry",
        "unknown-record-key",
        "unknown-target-key",
        "unknown-document-key",
        "unknown-entry-key",
        "unknown-digest-key",
        "unknown-rule-key",
        "missing-pending",
    ],
)
def test_a_record_loadout_cannot_trust_is_unreadable_and_read_as_empty(
    tmp_path: Path, fake_home: Path, corrupt
) -> None:
    folder = tmp_path / "app"
    write_overlay_record(_record(folder, fake_home, tmp_path / "t"))
    path = overlay_record_path(folder)
    text = path.read_text(encoding="utf-8")
    assert corrupt(text) != text
    path.write_text(corrupt(text), encoding="utf-8")

    assert read_overlay_record(folder, "app", fake_home) == (
        empty_record(folder, "app", fake_home),
        "unreadable",
    )


def test_a_record_naming_another_folder_is_unreadable(tmp_path: Path, fake_home: Path) -> None:
    folder = tmp_path / "app"
    other = tmp_path / "other"
    overlay_record_path(folder).parent.mkdir(parents=True)
    overlay_record_path(folder).write_text(
        render_overlay_record(_record(other, fake_home, tmp_path / "t")), encoding="utf-8"
    )

    assert read_overlay_record(folder, "app", fake_home)[1] == "unreadable"


def _newer(folder: Path, home: Path) -> Path:
    write_overlay_record(_record(folder, home, folder.parent / "t", name=folder.name))
    path = overlay_record_path(folder)
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace('"version": 2', '"version": 3'), encoding="utf-8")
    return path


def test_a_record_from_a_newer_loadout_is_newer_and_is_never_overwritten(
    tmp_path: Path, fake_home: Path
) -> None:
    folder = tmp_path / "app"
    path = _newer(folder, fake_home)
    text = path.read_text(encoding="utf-8")

    assert read_overlay_record(folder, "app", fake_home) == (
        empty_record(folder, "app", fake_home),
        "newer",
    )
    with pytest.raises(LoadoutError, match=f"{path} {NEWER_RECORD}"):
        write_overlay_record(empty_record(folder, "app", fake_home))
    assert path.read_text(encoding="utf-8") == text


def _without_norm(record: OverlayRecord, root: Path) -> OverlayRecord:
    path = root / "CLAUDE.local.md"
    return with_target(
        record,
        root,
        replace(_target(root, record.home), files={path: WrittenEntry(kind="text", sha256="s")}),
    )


def _without_sha(record: OverlayRecord, root: Path) -> OverlayRecord:
    path = root / "run.sh"
    return with_target(
        record, root, replace(_target(root, record.home), files={path: WrittenEntry(kind="copied")})
    )


def _relative(record: OverlayRecord, root: Path) -> OverlayRecord:
    return with_target(
        record,
        root,
        replace(
            _target(root, record.home),
            files={Path("CLAUDE.local.md"): text_entry(root / "CLAUDE.local.md", "x\n")},
        ),
    )


def _empty_leaf(record: OverlayRecord, root: Path) -> OverlayRecord:
    return with_target(
        record, root, replace(_target(root, record.home), leaves={root / ".claude.json": {(): "l"}})
    )


@pytest.mark.parametrize(
    "break_record",
    [_without_norm, _without_sha, _relative, _empty_leaf],
    ids=["text-without-norm", "copied-without-sha", "relative-path", "empty-json-path"],
)
def test_a_record_that_would_not_read_back_is_refused_before_it_is_written(
    tmp_path: Path, fake_home: Path, break_record
) -> None:
    folder = tmp_path / "app"
    good = _record(folder, fake_home, tmp_path / "t")
    write_overlay_record(good)
    before = overlay_record_path(folder).read_text(encoding="utf-8")

    with pytest.raises(LoadoutError, match="would not read back as rendered"):
        write_overlay_record(break_record(good, tmp_path / "t"))

    assert overlay_record_path(folder).read_text(encoding="utf-8") == before


def test_the_record_and_its_directory_are_synced(
    tmp_path: Path, fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    synced: list[bool] = []
    real_fsync = os.fsync

    def recording(descriptor: int) -> None:
        synced.append(stat.S_ISDIR(os.fstat(descriptor).st_mode))
        real_fsync(descriptor)

    monkeypatch.setattr(os, "fsync", recording)

    write_overlay_record(_record(tmp_path / "app", fake_home))

    assert synced == [False, True]


def test_a_failed_write_leaves_the_old_record_and_no_temporary_file(
    tmp_path: Path, fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = tmp_path / "app"
    write_overlay_record(_record(folder, fake_home))

    def failing(source: str, target: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", failing)

    with pytest.raises(LoadoutError, match="disk full"):
        write_overlay_record(_record(folder, fake_home, tmp_path / "t"))

    assert [path.name for path in overlay_record_path(folder).parent.iterdir()] == [
        overlay_record_path(folder).name
    ]
    assert read_overlay_record(folder, "app", fake_home)[0] == _record(folder, fake_home)


def test_a_record_whose_folder_now_resolves_elsewhere_is_found_and_moved_on_write(
    tmp_path: Path, fake_home: Path
) -> None:
    (tmp_path / "volume1/app").mkdir(parents=True)
    (tmp_path / "volume2/app").mkdir(parents=True)
    link = tmp_path / "wrksp"
    link.symlink_to(tmp_path / "volume1")
    folder = link / "app"
    record = _record(folder, fake_home, tmp_path / "t")
    write_overlay_record(record)
    old = overlay_record_path(folder)
    link.unlink()
    link.symlink_to(tmp_path / "volume2")

    assert overlay_record_path(folder) != old
    assert read_overlay_record(folder, "app", fake_home) == (record, "ok")
    assert every_overlay_record() == (record,)

    write_overlay_record(record)

    assert sorted(old.parent.iterdir()) == [overlay_record_path(folder)]
    assert read_overlay_record(folder, "app", fake_home) == (record, "ok")


def test_a_record_holding_another_folder_at_this_folder_s_name_is_not_overwritten(
    tmp_path: Path, fake_home: Path
) -> None:
    folder = tmp_path / "app"
    path = overlay_record_path(folder)
    path.parent.mkdir(parents=True)
    path.write_text(
        render_overlay_record(_record(tmp_path / "other", fake_home, name="other")),
        encoding="utf-8",
    )

    with pytest.raises(LoadoutError, match="holds overlay other"):
        write_overlay_record(empty_record(folder, "app", fake_home))

    assert read_overlay_record(tmp_path / "other", "other", fake_home) == (
        _record(tmp_path / "other", fake_home, name="other"),
        "ok",
    )


def test_a_record_whose_target_was_deleted_still_holds_its_leaves_and_documents(
    tmp_path: Path, fake_home: Path
) -> None:
    folder = tmp_path / "app"
    root = tmp_path / "app1"
    (root / ".claude").mkdir(parents=True)
    write_overlay_record(_record(folder, fake_home, root))
    shutil.rmtree(root)

    record, status = read_overlay_record(folder, "app", fake_home)

    assert status == "ok"
    assert record.targets[root].leaves == {
        fake_home / ".claude.json": {server_path(root, "figma"): "l1"}
    }
    assert record.targets[root].documents == _target(root, fake_home).documents


def test_without_target_keeps_every_other_target(tmp_path: Path, fake_home: Path) -> None:
    record = _record(tmp_path / "app", fake_home, tmp_path / "a", tmp_path / "b")

    assert list(without_target(record, tmp_path / "a").targets) == [tmp_path / "b"]


def test_a_record_that_cannot_be_written_raises(tmp_path: Path, monkeypatch) -> None:
    blocker = tmp_path / "config-is-a-file"
    blocker.write_text("", encoding="utf-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(blocker))

    with pytest.raises(LoadoutError, match="cannot write the overlay record"):
        write_overlay_record(empty_record(tmp_path / "app", "app", tmp_path))


def test_every_overlay_record_lists_the_readable_records(tmp_path: Path, fake_home: Path) -> None:
    write_overlay_record(_record(tmp_path / "a", fake_home, name="a"))
    write_overlay_record(_record(tmp_path / "b", fake_home, name="b"))
    broken = overlay_record_path(tmp_path / "c")
    broken.write_text("{", encoding="utf-8")

    assert sorted(record.name for record in every_overlay_record()) == ["a", "b"]
    assert scan_overlay_records().problems == (RecordProblem(broken, "unreadable", None, None),)


def test_a_broken_record_naming_no_overlay_says_to_delete_it_and_a_named_one_to_remove(
    tmp_path: Path,
) -> None:
    broken = tmp_path / "0123.json"

    anonymous = record_problem_message(RecordProblem(broken, "unreadable", None, None))
    named = record_problem_message(RecordProblem(broken, "unreadable", None, "app"))

    assert anonymous == (
        f"the overlay record {broken} cannot be read; fix it, or delete it: it names no overlay "
        "`overlay remove` could take"
    )
    assert named == (
        f"the overlay record {broken} cannot be read; fix it, or run "
        "`loadout overlay remove app --force`"
    )


def test_two_overlays_claim_each_other_s_paths_and_leaves(tmp_path: Path, fake_home: Path) -> None:
    root = tmp_path / "app1"
    app, docs = tmp_path / "config/app", tmp_path / "config/docs"
    write_overlay_record(_record(app, fake_home, root, name="app"))
    other = _target(root, fake_home, skill="writing")
    write_overlay_record(with_target(empty_record(docs, "docs", fake_home), root, other))
    app_skill = root / ".claude/skills/review/SKILL.md"
    docs_skill = root / ".claude/skills/writing/SKILL.md"
    leaf = (fake_home / ".claude.json", server_path(root, "figma"))
    pending = (fake_home / ".claude.json", server_path(root, "linear"))

    assert every_overlay_recorded_path([root / ".claude/skills"]) == {app_skill, docs_skill}
    assert claimed_paths([app_skill, docs_skill], foreign_claims(app)) == {
        docs_skill: "overlay docs"
    }
    assert claimed_paths([docs_skill, root / SETTINGS], foreign_claims(docs)) == {
        root / SETTINGS: "overlay app"
    }
    assert claimed_leaves([leaf, pending], foreign_claims(app)) == {
        leaf: "overlay docs",
        pending: "overlay docs",
    }


def test_claimed_paths_names_the_root_of_a_project_sync_record(
    tmp_path: Path, fake_home: Path
) -> None:
    root = tmp_path / "app1"
    root.mkdir()
    instructions = root / "CLAUDE.local.md"
    record_written(root, {instructions: text_entry(instructions, "Team.\n")})

    assert claimed_paths([instructions, root / "AGENTS.md"], foreign_claims(tmp_path / "app")) == {
        instructions: str(root.resolve())
    }


def test_claimed_paths_names_another_overlay_s_shared_file(tmp_path: Path, fake_home: Path) -> None:
    docs = tmp_path / "config/docs"
    write_overlay_record(_record(docs, fake_home, name="docs"))
    shared = docs / ".loadout/opencode-instructions.md"

    assert claimed_paths([shared, docs / "other.md"], foreign_claims(tmp_path / "config/app")) == {
        shared: "overlay docs"
    }


def test_claimed_skills_claims_a_whole_skill_folder_another_owner_records_a_file_in(
    tmp_path: Path, fake_home: Path
) -> None:
    root = tmp_path / "app1"
    root.mkdir()
    write_overlay_record(_record(tmp_path / "config/docs", fake_home, root, name="docs"))
    helper = root / ".agents/skills/lint/helper.sh"
    record_written(root, {helper: text_entry(helper, "echo\n")})
    skills = root / ".claude/skills"
    resolved = root.resolve() / ".agents/skills/lint"

    assert claimed_skills(
        [skills / "review", skills / "mine", resolved], foreign_claims(tmp_path / "config/app")
    ) == {skills / "review": "overlay docs", resolved: str(root.resolve())}


def _foreign_problem(tmp_path: Path, fake_home: Path, kind: str) -> Path:
    if kind == "newer":
        return _newer(tmp_path / "config/broken", fake_home)
    path = overlay_record_path(tmp_path / "config/broken")
    path.write_text('{"version": 2, "overlay": "/x", "name": "broken"}', encoding="utf-8")
    return path


@pytest.mark.parametrize("kind", ["unreadable", "newer"])
def test_claims_refuse_while_another_overlay_s_record_is_unreadable_or_newer(
    tmp_path: Path, fake_home: Path, kind: str
) -> None:
    root = tmp_path / "app1"
    app = tmp_path / "config/app"
    write_overlay_record(_record(tmp_path / "config/docs", fake_home, root, name="docs"))
    path = _foreign_problem(tmp_path, fake_home, kind)
    skill = root / ".claude/skills/review/SKILL.md"
    leaf = (fake_home / ".claude.json", server_path(root, "figma"))

    with pytest.raises(UnreadableRecords, match="overlay remove broken --force") as raised:
        foreign_claims(app)

    assert [(problem.path, problem.status) for problem in raised.value.problems] == [(path, kind)]
    assert str(path) in str(raised.value)
    forced = foreign_claims(app, force=True)
    assert claimed_paths([skill], forced) == {skill: "overlay docs"}
    assert claimed_leaves([leaf], forced) == {leaf: "overlay docs"}
    assert claimed_skills([skill.parent], forced) == {skill.parent: "overlay docs"}


def test_claims_proceed_past_this_overlay_s_own_unreadable_record(
    tmp_path: Path, fake_home: Path
) -> None:
    root = tmp_path / "app1"
    app = tmp_path / "config/app"
    write_overlay_record(_record(tmp_path / "config/docs", fake_home, root, name="docs"))
    overlay_record_path(app).write_text("{", encoding="utf-8")
    skill = root / ".claude/skills/review/SKILL.md"

    assert claimed_paths([skill], foreign_claims(app)) == {skill: "overlay docs"}


def test_an_unlistable_record_directory_is_a_problem_that_claims_refuse(
    tmp_path: Path, fake_home: Path
) -> None:
    directory = overlay_record_path(tmp_path / "app").parent
    directory.parent.mkdir(parents=True, exist_ok=True)
    directory.write_text("", encoding="utf-8")

    assert scan_overlay_records().problems == (RecordProblem(directory, "unreadable", None, None),)
    with pytest.raises(UnreadableRecords, match=str(directory)):
        foreign_claims(tmp_path / "app")


def test_united_document_keeps_the_old_ownership_and_marks_the_new_pending(tmp_path: Path) -> None:
    before = DocumentRecord(
        created=True,
        owned_keys={("model",): "old", ("theme",): "t"},
        owned_rules={ALLOW: ("Bash(ls)", "Bash(rm:*)")},
    )
    after = DocumentRecord(
        created=False,
        owned_keys={("model",): "new", ("effort",): "e"},
        owned_rules={ALLOW: ("Bash(yarn:*)", "Bash(ls)"), ("permissions", "deny"): ("x",)},
    )

    assert united_document(before, after) == DocumentRecord(
        created=True,
        owned_keys={("model",): "old", ("theme",): "t"},
        owned_rules={ALLOW: ("Bash(ls)", "Bash(rm:*)")},
        pending_keys={("model",): "new", ("effort",): "e"},
        pending_rules={ALLOW: ("Bash(yarn:*)",), ("permissions", "deny"): ("x",)},
    )
    assert united_document(None, after) == DocumentRecord(
        created=False,
        owned_keys={},
        owned_rules={},
        pending_keys=after.owned_keys,
        pending_rules=after.owned_rules,
    )


def test_settled_leaves_keeps_a_refused_leaf_s_recorded_digest_and_drops_its_pending_one(
    tmp_path: Path, fake_home: Path
) -> None:
    root = tmp_path / "app1"
    file = fake_home / ".claude.json"
    old_file = tmp_path / "old/.claude.json"
    figma, linear, slack, jira = (
        server_path(root, n) for n in ("figma", "linear", "slack", "jira")
    )
    entry = TargetRecord(
        common_dir=root / ".git",
        device=1,
        block="app",
        files={},
        documents={},
        leaves={file: {figma: "f0", slack: "s0", jira: "j0"}, old_file: {figma: "o0"}},
        pending={},
    )
    pending = with_pending(entry, file, {figma: "f1", linear: "l1"})
    result = LeafResult(
        written=(linear,),
        retired=(slack,),
        refused=(LeafVerdict(figma, "owned-key"),),
        digests={linear: "l1", jira: "j0", server_path(tmp_path / "x", "a"): "x"},
    )

    settled = settled_leaves(pending, file, result)

    assert pending.pending == {file: {figma: "f1", linear: "l1"}}
    assert settled.leaves == {
        old_file: {figma: "o0"},
        file: {figma: "f0", jira: "j0", linear: "l1"},
    }
    assert settled.pending == {}


def test_settled_leaves_drops_a_file_once_its_last_leaf_is_retired(tmp_path: Path) -> None:
    root = tmp_path / "app1"
    file = tmp_path / ".claude.json"
    figma = server_path(root, "figma")
    entry = TargetRecord(
        common_dir=root / ".git",
        device=1,
        block=None,
        files={},
        documents={},
        leaves={file: {figma: "f0"}, tmp_path / "other.json": {figma: "o0"}},
        pending={},
    )

    settled = settled_leaves(entry, file, LeafResult((), (figma,), (), {}))

    assert settled.leaves == {tmp_path / "other.json": {figma: "o0"}}


def _instructions(root: Path, text: str) -> Path:
    path = root / "CLAUDE.local.md"
    path.write_text(text, encoding="utf-8")
    return path


def test_an_unrecorded_file_that_differs_from_the_render_is_unknown(tmp_path: Path) -> None:
    path = _instructions(tmp_path, "Mine.\n")

    found = unaccepted_against_record({path: "Use yarn.\n"}, {}, {})

    assert [(u.path, u.kind, u.actual, u.expected) for u in found] == [
        (path, "unknown", "Mine.\n", "Use yarn.\n")
    ]


def test_a_recorded_file_edited_since_is_hand_edited(tmp_path: Path) -> None:
    path = _instructions(tmp_path, "Edited.\n")
    written = {path: text_entry(path, "Use npm.\n")}

    found = unaccepted_against_record({path: "Use yarn.\n"}, written, {})

    assert [(u.path, u.kind, u.orphaned) for u in found] == [(path, "hand-edited", False)]


@pytest.mark.parametrize("on_disk", ["Use yarn.\n", "Use npm.\n"], ids=["render", "record"])
def test_a_file_equal_to_the_render_or_the_record_is_accepted(tmp_path: Path, on_disk: str) -> None:
    path = _instructions(tmp_path, on_disk)
    written = {path: text_entry(path, "Use npm.\n")}

    assert unaccepted_against_record({path: "Use yarn.\n"}, written, {}) == []


def test_an_edited_orphan_is_hand_edited_and_orphaned(tmp_path: Path) -> None:
    path = _instructions(tmp_path, "Edited.\n")
    entry = text_entry(path, "Use npm.\n")

    found = unaccepted_against_record({}, {path: entry}, {path: entry})

    assert [(u.path, u.kind, u.orphaned) for u in found] == [(path, "hand-edited", True)]


def test_an_unrecorded_copy_that_differs_from_its_source_is_unknown(tmp_path: Path) -> None:
    source = tmp_path / "source.sh"
    source.write_text("echo source\n", encoding="utf-8")
    copy = tmp_path / "copy.sh"
    copy.write_text("echo mine\n", encoding="utf-8")

    found = unaccepted_against_record({copy: Copied(source)}, {}, {})

    assert [(u.path, u.kind) for u in found] == [(copy, "unknown")]


def test_unmanaged_entries_accounts_for_an_overlay_s_skill_in_a_project_target(
    tmp_path: Path, fake_home: Path
) -> None:
    root = tmp_path / "app1"
    (root / "loadout").mkdir(parents=True)
    (root / "loadout/config.toml").write_text('harnesses = ["claude"]\n', encoding="utf-8")
    for skill in ("review", "mine"):
        (root / f".claude/skills/{skill}").mkdir(parents=True)
        (root / f".claude/skills/{skill}/SKILL.md").write_text(
            f"---\nname: {skill}\ndescription: d\n---\nBody.\n", encoding="utf-8"
        )
    write_overlay_record(_record(tmp_path / "config/app", fake_home, root))

    found = unmanaged_entries(sync_state(root), {})

    assert found == [root / ".claude/skills/mine"]
