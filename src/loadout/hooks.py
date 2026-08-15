from __future__ import annotations

import copy
import re
from collections.abc import Mapping
from typing import Any

from .errors import LoadoutError

__all__ = [
    "ADAPTABLE_TYPES",
    "CLAUDE_EVENTS",
    "CODEX_EVENTS",
    "HARNESS_PREFIXES",
    "adaptable_document",
    "foreign_variables",
    "render_claude_hooks",
    "render_codex_hooks",
    "unrecognised_events",
]

# What a generated adapter can carry. An adapter subscribes to a harness event,
# builds the ABI payload, spawns a command and reads its result — so a hook with
# no command to spawn is outside it by construction, not by our ignorance.
#
# `type` is not something we add: it is Claude's own required field, present on
# all 263 hook objects in a 22-repository survey. Reading it beats inferring the
# kind from which other keys happen to be set.
#
# `http` and `prompt` are Claude's types and render to Claude untouched. Whether
# Codex honours them is **unknown** — its binary shares Claude's event names and
# output-schema vocabulary, and every entry in the one `hooks.json` seen is
# `type: "command"`. That is not evidence either way.
ADAPTABLE_TYPES = frozenset({"command"})

# Claude Code 2.1.226, extracted from the binary by matching the shape
# `Name:{summary:"…"` rather than by naming candidates — see
# docs/reference/config.md. A regex over guessed names returned 16 of these and
# looked complete.
CLAUDE_EVENTS = frozenset(
    {
        "ConfigChange",
        "CwdChanged",
        "DirectoryAdded",
        "Elicitation",
        "ElicitationResult",
        "FileChanged",
        "InstructionsLoaded",
        "MessageDisplay",
        "Notification",
        "PermissionDenied",
        "PermissionRequest",
        "PostCompact",
        "PostToolBatch",
        "PostToolUse",
        "PostToolUseFailure",
        "PreCompact",
        "PreToolUse",
        "SessionEnd",
        "SessionStart",
        "Setup",
        "Stop",
        "StopFailure",
        "SubagentStart",
        "SubagentStop",
        "TaskCompleted",
        "TaskCreated",
        "TeammateIdle",
        "UserPromptExpansion",
        "UserPromptSubmit",
        "WorktreeCreate",
        "WorktreeRemove",
    }
)

# codex-cli 0.147.0. **A lower bound, not an enumeration** — this came from a
# query naming its own candidates, and no readable variant table was found in
# the Rust binary. Every one is also in CLAUDE_EVENTS, which is a fact about the
# query rather than about Codex.
CODEX_EVENTS = frozenset(
    {
        "PermissionRequest",
        "PostCompact",
        "PostToolUse",
        "PreCompact",
        "PreToolUse",
        "SessionEnd",
        "SessionStart",
        "Stop",
        "SubagentStart",
        "SubagentStop",
        "UserPromptSubmit",
    }
)


def _looks_like_an_event(key: str) -> bool:
    """Whether a key is an event name rather than a comment.

    JSON has no comments, so hook documents carry them as keys — `_comment`,
    `_comment2`, `// PostToolUse hooks` all appear in public configurations.
    They hold a string where an event holds a list.
    """
    return key[:1].isupper()


def unrecognised_events(document: Mapping[str, Any], known: frozenset[str]) -> tuple[str, ...]:
    """Event-shaped keys this harness is not known to support, in document order.

    Deliberately *not* used to filter. No harness's event list here is
    established as complete — Codex's is an admitted lower bound and Claude's
    enumerates events carrying a summary — so an event missing from `known` may
    still be honoured. Emitting a key the harness ignores is recoverable;
    dropping one it honours is a hook the user believes is wired and is not.

    A deliberate departure from `render_codex`'s skip-globs behaviour, where the
    limitation is documented upstream rather than inferred from a search of our
    own.

    Worth more than it looks: of 32 distinct top-level keys across 22 public
    configurations, four name events that exist on no harness we know of
    (`PostTurn`, `PreTurn`, `PreLLM`, `PreCommit`). People do write hooks that
    can never fire.
    """
    return tuple(key for key in document if _looks_like_an_event(key) and key not in known)


def render_claude_hooks(document: Mapping[str, Any]) -> dict[str, Any]:
    """Claude's `hooks` value. The ABI is Claude's protocol, so this translates
    nothing — it copies.

    The caller writes the result into `settings.json`, which it shares with
    settings, permissions and plugins; hooks owns only the `hooks` key.

    **Nothing is validated.** An earlier version rejected non-PascalCase keys and
    non-list values as malformed; a survey of 22 public configurations falsified
    both in the first sample — comment keys are routine, and hook objects carry
    `statusMessage`, `async`, `blocking`, `if`, `name`, `shell`, and types
    `http` and `prompt` that have no `command` at all. We do not have the schema,
    so the harness decides.

    The one exception is `foreign_variables`, which raises: a Codex-only variable
    in a document bound for Claude cannot work, and fails dangerously.
    """
    _reject_foreign_variables(document, "claude")
    return copy.deepcopy(dict(document))


def render_codex_hooks(document: Mapping[str, Any]) -> dict[str, Any]:
    """The value of Codex's `hooks` key, which is the whole of `hooks.json`.

    Codex implements Claude's hook protocol — same event names, same entry
    shape, same `hookSpecificOutput` vocabulary, same exit-code-2 blocking — so
    this differs from Claude's only in which harness's variables it rejects.

    Both produce a *value*, not a document. Claude's lands beside `permissions`
    and the settings residual; Codex's is the only key in its file. That the two
    files look different is the `owned_key` mechanism, not these renderers.
    """
    _reject_foreign_variables(document, "codex")
    return copy.deepcopy(dict(document))


def adaptable_document(
    document: Mapping[str, Any], adaptable: frozenset[str] = ADAPTABLE_TYPES
) -> tuple[dict[str, Any], tuple[str, ...]]:
    """Split a document into the part a generated adapter can carry, plus a report.

    Returns `(document, skipped)`, where `skipped` names each dropped hook as
    `"<event>: <type>"`.

    **This filters, where `unrecognised_events` deliberately does not**, and the
    difference is the evidence behind each. An event missing from our list may
    still be supported — that list is an admitted lower bound, so dropping risks
    silently unwiring a hook the user believes works. A `prompt` hook reaching an
    adapter that spawns commands has nothing to spawn: the limitation is
    structural, so emitting it would produce an adapter that cannot run. Skip the
    proven case, report the inferred one.

    Claude and Codex never come through here. They are rendered declaratively and
    take the document whole, `http` and `prompt` included.

    Entries and events left empty by filtering are dropped, so a target that can
    adapt nothing renders nothing rather than an envelope of empty lists.
    """
    kept: dict[str, Any] = {}
    skipped: list[str] = []
    for event, entries in document.items():
        if not isinstance(entries, list):
            kept[event] = copy.deepcopy(entries)
            continue
        kept_entries: list[Any] = []
        for entry in entries:
            if not isinstance(entry, Mapping):
                kept_entries.append(copy.deepcopy(entry))
                continue
            hooks = entry.get("hooks")
            if not isinstance(hooks, list):
                kept_entries.append(copy.deepcopy(dict(entry)))
                continue
            kept_hooks = []
            for hook in hooks:
                kind = hook.get("type") if isinstance(hook, Mapping) else None
                if kind in adaptable:
                    kept_hooks.append(copy.deepcopy(hook))
                else:
                    skipped.append(f"{event}: {kind}")
            if kept_hooks:
                kept_entries.append({**copy.deepcopy(dict(entry)), "hooks": kept_hooks})
        if kept_entries:
            kept[event] = kept_entries
    return kept, tuple(skipped)


# Each harness's own environment-variable namespace. A hook may use its own
# harness's variables freely; using another's is what breaks.
#
# Claude defines `CLAUDE_PROJECT_DIR` (18 references in 2.1.226) and
# `CLAUDE_PLUGIN_ROOT` (35) — 202 of 263 commands in a 22-repository survey use
# the former. **Codex defines no project or plugin root at all**: its binary has
# 16 `CODEX_*` variables, all of them config-dir, auth or protocol. So this is
# not a translation loadout declines to do; there is nothing to translate into.
HARNESS_PREFIXES = {"claude": "CLAUDE_", "codex": "CODEX_"}

_VARIABLE = re.compile(r"\$\{?([A-Z][A-Z0-9_]*)\}?")


def foreign_variables(document: Mapping[str, Any], harness: str) -> tuple[str, ...]:
    """Variables from another harness's namespace, as `"<event>: $NAME"`.

    A hook using its *own* harness's variables is correct and common, so this is
    scoped to the target rather than banning the namespaces outright.
    """
    own = HARNESS_PREFIXES.get(harness)
    foreign = {p for h, p in HARNESS_PREFIXES.items() if p != own}
    found: list[str] = []
    for event, entries in document.items():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, Mapping):
                continue
            hooks = entry.get("hooks")
            for hook in hooks if isinstance(hooks, list) else []:
                if not isinstance(hook, Mapping):
                    continue
                for value in hook.values():
                    if not isinstance(value, str):
                        continue
                    for name in _VARIABLE.findall(value):
                        if any(name.startswith(p) for p in foreign):
                            found.append(f"{event}: ${name}")
    return tuple(found)


def _reject_foreign_variables(document: Mapping[str, Any], harness: str) -> None:
    """Raise if a hook references a variable this harness does not define.

    An error rather than a warning, because the failure is silent and dangerous
    rather than loud and safe. `$CLAUDE_PROJECT_DIR` is unset under Codex, so
    `"$CLAUDE_PROJECT_DIR"/guard.sh` runs as `/guard.sh`, which does not exist,
    which exits **2** — the code both harnesses read as *block*. A `PreToolUse`
    guard that failed to resolve therefore denies every call it matches instead
    of doing nothing. Rendering that is worse than refusing to.

    Contrast `render_codex`'s skipped globs, which fail *safe*: the command falls
    through to a prompt. Same posture, opposite direction, because the
    consequences differ.

    The fix is either a portable command — a relative path or inline shell, as
    49 of 259 surveyed hooks use — or scoping the fragment to the harness whose
    variable it needs.
    """
    offenders = foreign_variables(document, harness)
    if not offenders:
        return
    listed = ", ".join(dict.fromkeys(offenders))
    raise LoadoutError(
        f"hooks: {harness} does not define {listed}. Unset variables expand to "
        f"nothing, so the command runs against a path that does not exist and "
        f"exits 2 — which {harness} reads as 'block', denying every matching "
        f"tool call. Use a relative path or inline shell, or scope this fragment "
        f"to the harness that defines the variable."
    )
