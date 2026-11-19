"""What a render has to say about a source it could not fully honour.

Four reporters existed before this module and none of them reached a user:
`unaddressable` and `unregistered_marketplaces` in the plugins slice,
`unrecognised_events` in hooks, and the adapters' unmapped-event comment, which
was visible only by opening the generated JavaScript. One unreported function is
a footnote; four is a missing surface, and ADR 0015 named that gap rather than
hiding it.

Every notice here is **advisory**. Each of the underlying reports describes a
source that rendered successfully while doing less than it says — a plugin left
switched off, a hook that will never fire — so none of them is drift and none
changes an exit code. Drift means generated output disagrees with its source;
these are cases where the source itself asks for something the harness cannot
carry.

Most functions take documents and return notices. `collect_notices` reads the
source and the environment the way the render does, and hands each check its part.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .adapters import OPENCODE_MAPPINGS, PI_MAPPINGS
from .composition import fragment_paths, load_fragment
from .destinations import resolve_destination
from .emit import (
    PROJECT_DOCUMENTS,
    SKILLS_SUBDIR,
    RenderContext,
    global_rules,
    project_agents,
    project_instruction_sources,
    project_kits,
    project_rules,
    project_settings_path,
    project_skill_trees,
    project_slice_document,
    refuse_unregistered_global,
    selected_target,
    settings_document,
    skill_trees,
    slice_document,
    source_agents,
)
from .errors import LoadoutError
from .harnesses import GLOBAL_PRESET
from .hooks import (
    CLAUDE_EVENTS,
    CODEX_EVENTS,
    CURSOR_EVENTS,
    DEVIN_EVENTS,
    DROID_EVENTS,
    cursor_hooks,
    devin_hooks,
    unrecognised_events,
)
from .kits import kit_divergence, unverifiable_kits
from .manifest import Manifest, load_profile, manifest_path
from .native_documents import jsonc_to_json
from .permissions.renderers import (
    CATCH_ALL_RENDERERS,
    DEVIN_CATCH_ALL,
    cursor_shell,
    cursor_unasked,
    devin_permissions,
    devin_unasked,
    devin_word_prefix,
)
from .permissions.rules import Rules, exact_command, is_exact, is_glob
from .plugins import ADDRESSED_BY, marketplaces, unaddressable, unregistered_marketplaces
from .project import (
    PROJECT_DIR,
    PROJECT_PRESET,
    load_project_config,
    project_config_path,
    project_slices,
)
from .skill_policy import SkillPolicy, render_skill_for, selected_skill_agents
from .skills import Skill, loose_skill_files, render_skill
from .subagents import AGENTS_SUBDIR, Agent, agent_notes, discover_agents

__all__ = [
    "Notice",
    "collect_notices",
    "inexact_shell_entries",
    "kit_notices",
    "known_events",
    "notices_for",
    "opencode_skills_race",
    "unreached_catch_all",
    "unsupported_shell_globs",
]

# Where each shell-carrying renderer that is *not* a catch-all carrier keeps its
# catch-all instead. Keyed by renderer, not agent: `codex` and `codex-project`
# differ in what they write, though neither carries a default. A renderer absent
# here states no shell rules at all (the MCP-only ones), so the key was never its
# to carry and there is nothing to report.
CATCH_ALL_ELSEWHERE = {
    "claude": "Claude's is permissions.defaultMode, hand-maintained through the settings slice",
    "claude-project": "Claude's is permissions.defaultMode, hand-maintained through the settings slice",
    "codex": "Codex's is approval_policy in config.toml, which the defaults slice sets",
    "codex-project": "Codex's is approval_policy in .codex/config.toml, which loadout does not write",
    "droid": "Droid's unmatched-command behavior comes from the session autonomy level",
    "cursor": "Cursor's is approvalMode in cli-config.json, which the settings slice sets",
    "cursor-project": (
        "Cursor's is approvalMode in the global cli-config.json, which the global settings slice sets"
    ),
}

# OpenCode scans `.claude/skills` as well as its own directory and keys the result
# by skill name, so writing the same name to both leaves which copy survives to a
# race — `skill/index.ts` warns on a duplicate and assigns anyway, under
# `concurrency: "unbounded"`. Either variable removes the Claude directories from
# the scan; `disableClaudeCodeSkills` is `broad || direct`, so checking only the
# specific one would report a collision for anyone who set the broad switch.
# `OPENCODE_DISABLE_EXTERNAL_SKILLS` also works and is deliberately not accepted
# here: it drops `.agents` too, which loadout does not write and the user may.
OPENCODE_SKILL_FLAGS = ("OPENCODE_DISABLE_CLAUDE_CODE_SKILLS", "OPENCODE_DISABLE_CLAUDE_CODE")
OPENCODE_EXTERNAL_SKILL_FLAG = "OPENCODE_DISABLE_EXTERNAL_SKILLS"

# Effect's `Config.boolean` parses these; its full accepted set could not be
# verified from the checkout (no node_modules), so an unusual spelling costs one
# advisory line and never an exit code.
_TRUTHY = frozenset({"1", "true", "yes", "on"})


def opencode_skills_race(environ: Mapping[str, str]) -> bool:
    """Whether OpenCode would resolve loadout's skills by race rather than by name.

    Machine state, so a caller supplies it (ADR 0001) — and it is read for a
    *notice*, never for generated content, so ADR 0008 is untouched.
    """
    return not any(
        environ.get(flag, "").strip().lower() in _TRUTHY for flag in OPENCODE_SKILL_FLAGS
    )


def opencode_external_skills_race(environ: Mapping[str, str]) -> bool:
    return environ.get(OPENCODE_EXTERNAL_SKILL_FLAG, "").strip().lower() not in _TRUTHY


@dataclass(frozen=True)
class Notice:
    """One thing a user would want to know, and where it came from: a harness, a source,
    `mcp`, `project` or `all`."""

    owner: str
    slice: str
    message: str

    def render(self) -> str:
        return f"{self.owner}.{self.slice}: {self.message}"


# For Claude and Codex an event outside this set may still be honoured — both
# lists are lower bounds, which is why `unrecognised_events` reports rather than
# filters. For OpenCode and Pi the set is loadout's own translation table, so an
# event outside it provably does not fire: there is no adapter branch to run it.
_ADAPTED = {
    "opencode": frozenset(m.event for m in OPENCODE_MAPPINGS),
    "pi": frozenset(m.event for m in PI_MAPPINGS),
}


def known_events(agent: str) -> frozenset[str]:
    if agent == "claude":
        return CLAUDE_EVENTS
    if agent == "codex":
        return CODEX_EVENTS
    if agent == "droid":
        return DROID_EVENTS
    if agent == "cursor":
        return CURSOR_EVENTS
    if agent == "devin":
        return DEVIN_EVENTS
    return _ADAPTED.get(agent, frozenset())


def unreached_catch_all(agent: str, renderer: str, verdict: str) -> tuple[Notice, ...]:
    """What a stated `[shell] default` does not reach, said out loud at sync time.

    The docs record the split, but a source that states a catch-all and gets it on
    two harnesses of four is the exact shape ADR 0015 named: rendered successfully,
    doing less than it says, with generated files that look untouched. It matters
    more here than for the advisory slices the other notices cover — permissions is
    the enforcing one, and the direction of the gap is toward *wider* than asked.
    """
    if renderer in CATCH_ALL_RENDERERS or renderer not in CATCH_ALL_ELSEWHERE:
        return ()
    return (
        Notice(
            owner=agent,
            slice="permissions",
            message=(
                f'[shell] default = "{verdict}" is not rendered here — '
                f"{CATCH_ALL_ELSEWHERE[renderer]}; "
                f"see docs/reference/README.md#the-catch-all-default"
            ),
        ),
    )


def unsupported_shell_globs(agent: str, renderer: str, rules: Rules) -> tuple[Notice, ...]:
    if renderer != "droid":
        return ()
    return tuple(
        Notice(
            owner=agent,
            slice="permissions",
            message=(
                f"{category} glob {entry!r} is not rendered because Droid treats "
                "permission-list metacharacters literally"
            ),
        )
        # `deny` is absent deliberately: a glob there is refused at render rather
        # than dropped, because Droid's blocklist has no fallback and a silently
        # narrower one is wider than the source asked for. Reporting it here would
        # describe a render that never happens.
        for category in ("allow", "ask")
        for entry in rules.shell(category)
        if is_glob(entry)
    )


# Renderers whose matcher has no exact form: Codex's token prefix and Droid's
# whole-word run. Keyed by renderer, since `codex` and `codex-project` are one
# matcher at two scopes.
PREFIX_ONLY = {"codex": "Codex", "codex-project": "Codex", "droid": "Droid"}


def inexact_shell_entries(agent: str, renderer: str, rules: Rules) -> tuple[Notice, ...]:
    harness = PREFIX_ONLY.get(renderer)
    if harness is None:
        return ()
    omitted = tuple(
        Notice(
            owner=agent,
            slice="permissions",
            message=(
                f"{category} {entry!r} is not rendered because {harness} cannot match a "
                f"command exactly; the command falls through to {harness}'s own approval"
            ),
        )
        for category in ("allow", "ask")
        for entry in rules.shell(category)
        if is_exact(entry)
    )
    widened = tuple(
        Notice(
            owner=agent,
            slice="permissions",
            message=(
                f"deny {entry!r} is rendered as {exact_command(entry)!r} because {harness} "
                f"cannot match a command exactly, so it also blocks that command with arguments"
            ),
        )
        for entry in rules.deny
        if is_exact(entry)
    )
    return omitted + widened


def notices_for(
    agent: str,
    slice_name: str,
    document: Mapping[str, Any],
    known_marketplaces: frozenset[str] = frozenset(),
) -> tuple[Notice, ...]:
    """Everything worth reporting about one agent's slice content."""
    found: list[Notice] = []

    def add(message: str) -> None:
        found.append(Notice(owner=agent, slice=slice_name, message=message))

    if slice_name == "hooks" and agent == "cursor":
        for skipped in cursor_hooks(document)[1]:
            add(f"{skipped} — Cursor's translation of a Claude hook leaves it out")
    elif slice_name == "hooks" and agent == "devin":
        for skipped in devin_hooks(document)[1]:
            add(f"{skipped} — left out, because Devin cannot run it")
        for event in unrecognised_events(document, DEVIN_EVENTS):
            add(f"{event} is not an event Devin loads, so it will not fire")
    elif slice_name == "hooks":
        adapted = agent in _ADAPTED
        for event in unrecognised_events(document, known_events(agent)):
            add(
                f"{event} has no adapter mapping, so it will not fire"
                if adapted
                else f"{event} is not in this harness's known event list"
            )

    if slice_name == "plugins":
        for skipped in unaddressable(document, agent):
            add(f"{skipped} — left switched off")
        if ADDRESSED_BY.get(agent) == "marketplace":
            for name in unregistered_marketplaces(document, known_marketplaces):
                add(f"marketplace {name!r} is not registered; the plugin will not resolve yet")

    return tuple(found)


CLAUDE_MARKETPLACES = "${CLAUDE_CONFIG_DIR:-~/.claude}/plugins/known_marketplaces.json"


def _known_marketplaces(
    agent: str, document: dict[str, Any], environ: Mapping[str, str]
) -> frozenset[str]:
    """What counts as registered, which is a different question per harness.

    Codex's registrations are ordinary configuration in a file loadout stages, so
    the source answers for itself. Claude's live in a registry the harness
    maintains, carrying timestamps and install paths — machine state a generated
    file must not hold (ADR 0008) — so it is read here and never written. A
    renderer could not do this at all (ADR 0001); a caller may.
    """
    if agent != "claude":
        return frozenset(marketplaces(document))
    try:
        path = resolve_destination(CLAUDE_MARKETPLACES, "plugins.known_marketplaces", environ)
        registry = json.loads(path.read_text(encoding="utf-8"))
    except (LoadoutError, OSError, json.JSONDecodeError, UnicodeDecodeError):
        # A machine that has never registered one is the bootstrap case, not an
        # error: the report exists to say what to run.
        return frozenset()
    return frozenset(registry) if isinstance(registry, dict) else frozenset()


def _global_skill_race(manifest: Manifest, environ: Mapping[str, str]) -> tuple[Notice, ...]:
    """The same collision as `project_notices`, one scope up.

    Global scope reaches it sooner: a project has to declare `opencode` *and*
    carry skills, while `~/.claude/skills` and `~/.config/opencode/skills` are
    both written the moment any skill exists and any manifest names OpenCode.
    Silent until then, which is why the condition is the manifest rather than the
    slice — a source with no skills has no colliding name to lose.
    """
    available = tuple(target.harness for target in manifest.skills)
    if "opencode" not in available:
        return ()
    return _claude_skill_race(
        skill_trees(manifest), available, manifest.skill_policies, environ, "~/.claude/skills"
    )


def _claude_skill_race(
    trees: tuple[Skill, ...],
    harnesses: tuple[str, ...],
    policies: tuple[SkillPolicy, ...],
    environ: Mapping[str, str],
    claude_skills: str,
) -> tuple[Notice, ...]:
    races = any(
        {"claude", "opencode"}.issubset(selected_skill_agents(skill.name, harnesses, policies))
        for skill in trees
    )
    if not races or not opencode_skills_race(environ):
        return ()
    return (
        Notice(
            owner="opencode",
            slice="skills",
            message=(
                f"none of {' or '.join(OPENCODE_SKILL_FLAGS)} is set, so OpenCode also "
                f"scans {claude_skills} and picks between the two copies of each skill at "
                f"random — export one of them; see docs/reference/opencode.md"
            ),
        ),
    )


def cursor_permission_notices(agent: str, renderer: str, rules: Rules) -> tuple[Notice, ...]:
    """What Cursor's two lists cannot say: an ask, and a glob past the first word."""
    if renderer not in {"cursor", "cursor-project"}:
        return ()

    def notice(message: str) -> Notice:
        return Notice(owner=agent, slice="permissions", message=message)

    shell, mcp = cursor_unasked(rules)
    asks = (*rules.ask, *rules.mcp_ask)
    return (
        *(
            notice(
                f"ask {entry!r} has no list in Cursor, so it is left to Cursor's approvalMode, "
                f"which prompts only in allowlist mode"
            )
            for entry in asks
        ),
        *(
            notice(
                f"allow {entry!r} is not rendered because it would also approve something the "
                f"source asks about, and Cursor has no ask list to keep that prompting"
            )
            for entry in (*shell, *mcp)
        ),
        *(
            notice(
                f"deny {entry!r} is rendered as {form!r} because Cursor's * stops at /, so it "
                f"also blocks that command with any arguments"
            )
            for entry in rules.deny
            if is_glob(entry) and (form := cursor_shell(entry, "deny")) != f"Shell({entry})"
        ),
    )


def devin_permission_notices(agent: str, renderer: str, rules: Rules) -> tuple[Notice, ...]:
    """What Devin's word-prefix rules cannot say: a glob, an ask an allow would beat, and a
    catch-all deny."""
    if renderer not in {"devin", "devin-project"}:
        return ()

    def notice(message: str) -> Notice:
        return Notice(owner=agent, slice="permissions", message=message)

    shell, mcp = devin_unasked(rules)
    return (
        *(
            notice(
                f"allow {entry!r} is not rendered because Devin has no glob; the command falls "
                f"through to Devin's own approval"
            )
            for entry in rules.allow
            if is_glob(entry)
        ),
        *(
            notice(
                f"{category} {entry!r} is rendered as 'Exec({devin_word_prefix(entry)})' "
                f"because Devin has no glob, so it also covers that command with any arguments"
            )
            for category in ("ask", "deny")
            for entry in rules.shell(category)
            if is_glob(entry)
        ),
        *(
            notice(
                f"allow {entry!r} is not rendered because Devin lets an allow at least as "
                f"specific as an ask override it, so it would approve what the source asks about"
            )
            for entry in (*shell, *mcp)
        ),
        *(
            (
                notice(
                    '[shell] default = "deny" is rendered as an ask of every command, because '
                    "Devin cannot deny what no rule names without also denying what the source "
                    "allows"
                ),
            )
            if rules.default == "deny"
            else ()
        ),
    )


DEVIN_OVERRIDING_MODES = frozenset({"dangerous", "yolo", "bypass"})
_DEVIN_RULE = re.compile(r"^(?P<kind>Exec|ExecExact)\((?P<inner>.+)\)$")
_DEVIN_MCP_RULE = re.compile(r"^mcp__(?P<server>.+?)__(?P<tool>.+)$")


def _devin_reach(token: object) -> tuple[str, ...] | None:
    """What one Devin rule matches: every command, a prefix, an exact command, or MCP tools."""
    if token in {DEVIN_CATCH_ALL, "exec"}:
        return ("all",)
    if token == "mcp__*":
        return ("mcp", "*", "*")
    if not isinstance(token, str):
        return None
    if (shell := _DEVIN_RULE.match(token)) is not None:
        return ("exact" if shell["kind"] == "ExecExact" else "prefix", shell["inner"])
    if (mcp := _DEVIN_MCP_RULE.match(token)) is not None:
        return ("mcp", mcp["server"], mcp["tool"])
    return None


def _devin_covers(prefix: tuple[str, ...], other: tuple[str, ...]) -> bool:
    """Whether a prefix rule matches some command another shell rule matches."""
    command = other[1]
    reached = command == prefix[1] or command.startswith(f"{prefix[1]} ")
    longer = other[0] == "prefix" and prefix[1].startswith(f"{command} ")
    return reached or longer


def _devin_overlap(allow: tuple[str, ...], rule: tuple[str, ...]) -> bool:
    """Whether some call matches both rules."""
    if allow[0] == "all":
        return rule[0] in {"prefix", "exact"}
    if "mcp" in {allow[0], rule[0]}:
        same_kind = allow[0] == rule[0]
        servers = same_kind and (allow[1] == rule[1] or "*" in {allow[1], rule[1]})
        return servers and (allow[2] == rule[2] or "*" in {allow[2], rule[2]})
    if allow[0] == "prefix":
        return _devin_covers(allow, rule)
    if rule[0] == "prefix":
        return _devin_covers(rule, allow)
    return allow[1] == rule[1]


def devin_overridden(rules: Rules, user: Mapping[str, Any]) -> tuple[Notice, ...]:
    """The project's Devin denies and asks that the user's own config.json overrides.

    Devin 3000.14 applies a project deny or ask only while the user level neither
    allows the call nor runs in dangerous mode (docs/reference/devin.md#observed-decisions),
    so the project file is written and these are said out loud.
    """
    lists = devin_permissions(rules)
    restricting = [
        (category, token)
        for category in ("deny", "ask")
        for token in lists[category]
        if token != DEVIN_CATCH_ALL
    ]
    if not restricting:
        return ()
    see = "see docs/reference/devin.md#observed-decisions"
    mode = user.get("permission_mode")
    if mode in DEVIN_OVERRIDING_MODES:
        return (
            Notice(
                "devin",
                "permissions",
                f"your Devin config.json sets permission_mode {mode!r}, which overrides every "
                f"project deny and ask, so this project's Devin rules have no effect; {see}",
            ),
        )
    permissions = user.get("permissions")
    listed = permissions.get("allow", []) if isinstance(permissions, Mapping) else []
    allows = listed if isinstance(listed, list) else []
    found: list[Notice] = []
    for category, token in restricting:
        rule = _devin_reach(token)
        overriding = next(
            (
                allow
                for allow in allows
                if rule is not None
                and (reach := _devin_reach(allow)) is not None
                and _devin_overlap(reach, rule)
            ),
            None,
        )
        if overriding is not None:
            found.append(
                Notice(
                    "devin",
                    "permissions",
                    f"{category} {token!r} in .devin/config.json does not apply where "
                    f"{overriding!r} in your Devin config.json allows the same call, because "
                    f"Devin lets a user-level allow override a project rule; {see}",
                )
            )
    return tuple(found)


def devin_claude_import(
    harnesses: Iterable[str], settings: Mapping[str, Any]
) -> tuple[Notice, ...]:
    """Devin reads Claude's instructions, skills and hooks unless its `claude` import is off,
    so with both declared it sees what loadout writes for Claude beside its own copy."""
    imports = settings.get("read_config_from")
    off = isinstance(imports, Mapping) and imports.get("claude") is False
    if off or not {"claude", "devin"}.issubset(harnesses):
        return ()
    return (
        Notice(
            "devin",
            "settings",
            "Devin also reads Claude's instructions, skills and hooks while its claude import is "
            "on, as it is by default, so it sees what loadout writes for Claude beside its own "
            'copy; set {"read_config_from": {"claude": false}} in a Devin settings fragment to '
            "stop it; see docs/reference/devin.md#third-party-imports",
        ),
    )


def _devin_settings(manifest: Manifest, root: Path) -> Mapping[str, Any]:
    target = next((t for t in manifest.permissions if t.harness == "devin" and t.settings), None)
    return settings_document(target, manifest, root) if target is not None else {}


def devin_user_config(environ: Mapping[str, str]) -> Mapping[str, Any]:
    """The user's live Devin config.json, read for a notice and never for output (ADR 0008)."""
    template = GLOBAL_PRESET["devin"]["permissions"].destination
    assert template is not None
    try:
        path = resolve_destination(template, "devin.permissions", environ)
        document = json.loads(jsonc_to_json(path.read_text(encoding="utf-8")))
    except (LoadoutError, OSError, ValueError):
        return {}
    return document if isinstance(document, dict) else {}


def _renderer_notices(agent: str, renderer: str, rules: Rules) -> list[Notice]:
    """What a renderer cannot carry of these rules."""
    unreached = unreached_catch_all(agent, renderer, rules.default) if rules.default else ()
    return [
        *unreached,
        *unsupported_shell_globs(agent, renderer, rules),
        *inexact_shell_entries(agent, renderer, rules),
        *cursor_permission_notices(agent, renderer, rules),
        *devin_permission_notices(agent, renderer, rules),
    ]


def _permission_notices(manifest: Manifest, profile: str) -> tuple[Notice, ...]:
    """Every selected target whose permission renderer carries less than the source.

    Reads the same tiers `render_global` merges, so the verdict named is the one
    that actually rendered. Silent when no source states the key — a notice that
    fires for everyone teaches nothing.
    """
    selected = [t for t in manifest.permissions if selected_target(t, profile)]
    if not selected:
        return ()
    rules = global_rules(manifest)
    found: list[Notice] = []
    for target in selected:
        # `permissions = []` deselects the whole source, not this key in particular —
        # the autonomous profile's mechanism, and reporting it here would fire on
        # every sync that uses it. A legacy target names no agent, so it is
        # labelled by the renderer it declared.
        if not target.select_all:
            continue
        found.extend(_renderer_notices(target.harness or target.renderer, target.renderer, rules))
    return tuple(dict.fromkeys(found))


def _loose_skill_notices(skill_roots: Iterable[Path]) -> tuple[Notice, ...]:
    return tuple(
        Notice(
            owner="all",
            slice="skills",
            message=f"{path} is not a skill and was skipped; a skill is skills/<name>/SKILL.md",
        )
        for skills_root in skill_roots
        for path in loose_skill_files(skills_root)
    )


def kit_notices(root: Path) -> tuple[Notice, ...]:
    """Vendored kits whose copy no longer matches what was vendored, or that have
    nothing to compare against. Reported, never failed: a vendored copy is source, so
    it falls outside drift by definition (ADR 0014), but saying nothing would let a
    project drift from its kit indefinitely."""
    return (
        *(
            Notice(
                "project",
                "kits",
                f"the vendored kit {name!r} was modified after it was vendored — "
                f"`loadout sync` in a terminal offers to push the edits to its source when "
                f"the source has not changed since and is not bundled with loadout, and "
                f"`loadout kit sync {name}` refuses until they are there",
            )
            for name in kit_divergence(root)
        ),
        *(
            Notice(
                "project",
                "kits",
                f"the vendored kit {name!r} has no recorded provenance, so nothing can "
                f"tell your edits from its source — delete loadout/kits/{name} and run "
                f"`loadout kit add {name} --vendored` to take the source wholesale and "
                "record one",
            )
            for name in unverifiable_kits(root)
        ),
    )


def collect_notices(
    root: Path,
    profile: str = "default",
    environ: Mapping[str, str] | None = None,
    *,
    context: RenderContext | None = None,
) -> tuple[Notice, ...]:
    """Advisory findings about project setup and global slice fragments."""
    environ = os.environ if environ is None else environ
    found: list[Notice] = [
        *project_notices(root, environ),
        *kit_notices(root),
    ]
    if not manifest_path(root).is_file():
        return tuple(found)
    refuse_unregistered_global(root, context)
    manifest = load_profile(root, profile)
    found.extend(
        _loose_skill_notices(
            source.path / SKILLS_SUBDIR
            for source in manifest.sources
            if SKILLS_SUBDIR in source.use
        )
    )
    found.extend(_global_skill_race(manifest, environ))
    declared = (
        {target.harness for target in manifest.permissions if target.harness}
        | {target.harness for target in manifest.skills}
        | {target.harness for target in manifest.agents}
    )
    found.extend(devin_claude_import(declared, _devin_settings(manifest, root)))
    found.extend(_agent_notices(source_agents(manifest), tuple(t.harness for t in manifest.agents)))
    found.extend(
        Notice(
            owner=source.name,
            slice=AGENTS_SUBDIR,
            message=(
                f"{source.path / AGENTS_SUBDIR} holds agents, but source {source.name!r} does "
                f'not list "agents" in `use`, so none of them is rendered'
            ),
        )
        for source in manifest.sources
        if AGENTS_SUBDIR not in source.use and discover_agents(source.path / AGENTS_SUBDIR)
    )
    found.extend(
        notice
        for target in manifest.targets
        if selected_target(target, profile)
        for notice in _repeated_heading_notices(
            root, fragment_paths(target, manifest), target.name or "all"
        )
    )
    found.extend(_permission_notices(manifest, profile))
    for target in manifest.permissions:
        # A legacy `[permissions.*]` target names no agent and carries no slice
        # content, so there is nothing to report about it.
        if target.harness is None or target.content_slice is None:
            continue
        if not selected_target(target, profile):
            continue
        document = slice_document(target.content, target.content_slice, manifest)
        known = _marketplaces(target.harness, target.content_slice, document, environ)
        found.extend(notices_for(target.harness, target.content_slice, document, known))
    return tuple(found)


def project_notices(root: Path, environ: Mapping[str, str]) -> tuple[Notice, ...]:
    """What project scope rendered while doing less than its source says.

    Three kinds: a permission rule the selected renderer cannot carry, a slice
    document naming something the harness will not act on, and setup on the
    rendering machine that OpenCode needs at run time.

    Driven by `PROJECT_PRESET` rather than by harness name, for the reason
    `_permission_notices` is at global scope: a renderer that loses a catch-all or
    a glob should report it whoever selected it, and a sixth harness should not
    need an edit here to be heard.

    The OpenCode variable matters on the machine OpenCode *runs* on, and this reads
    the machine loadout is *rendering* on — the same box, because generated project
    files are never committed and adoption is all-or-nothing, so everyone working
    in a repo syncs it themselves. CI is the exception, where this is one advisory
    line in a log and cannot touch an exit code.
    """
    path = project_config_path(root)
    if not path.is_file():
        return ()
    config = load_project_config(path)
    found: list[Notice] = []
    found.extend(_loose_skill_notices((root / PROJECT_DIR / SKILLS_SUBDIR,)))
    found.extend(
        _agent_notices(
            project_agents(root),
            tuple(h for h in config.harnesses if AGENTS_SUBDIR in PROJECT_PRESET[h]),
        )
    )
    kits = project_kits(root, config, RenderContext())
    rules = project_rules(root, kits, config.server_policies)
    permissions: list[Notice] = []
    documents: list[Notice] = []
    for agent, slice_name, spec in project_slices(config.harnesses):
        if slice_name == "permissions" and spec.renderer:
            permissions.extend(_renderer_notices(agent, spec.renderer, rules))
        elif slice_name in PROJECT_DOCUMENTS:
            document, contributed = project_slice_document(root, slice_name, kits)
            if contributed:
                known = _marketplaces(agent, slice_name, document, environ)
                documents.extend(notices_for(agent, slice_name, document, known))
    if "devin" in config.harnesses:
        permissions.extend(devin_overridden(rules, devin_user_config(environ)))
        settings = project_settings_path(root, "devin")
        found.extend(
            devin_claude_import(
                config.harnesses, json.loads(settings.read_text()) if settings.is_file() else {}
            )
        )
    trees = project_skill_trees(root, kits)
    return (
        *found,
        *permissions,
        *documents,
        *_repeated_heading_notices(root, project_instruction_sources(root, config, kits)),
        *_claude_skill_race(
            trees, config.harnesses, config.skill_policies, environ, ".claude/skills"
        ),
        *_codex_skill_race(trees, config.harnesses, config.skill_policies, environ),
    )


_HEADING = re.compile(r"#{1,6}[ \t]+\S")


def _headings(text: str) -> list[str]:
    """The Markdown headings in `text`, outside fenced code, where `#` starts a comment."""
    found: list[str] = []
    fenced = False
    for line in text.splitlines():
        if line.lstrip().startswith(("```", "~~~")):
            fenced = not fenced
        elif not fenced and _HEADING.match(line):
            found.append(line.rstrip())
    return found


def repeated_headings(fragments: Iterable[tuple[str, str]]) -> dict[str, tuple[str, ...]]:
    """Each heading more than one of `fragments`, as (label, text), brings into the
    document they compose, with the labels of those that do, in order."""
    sources: dict[str, list[str]] = {}
    for label, text in fragments:
        for heading in dict.fromkeys(_headings(text)):
            sources.setdefault(heading, []).append(label)
    return {heading: tuple(labels) for heading, labels in sources.items() if len(labels) > 1}


def _repeated_heading_notices(
    root: Path, sources: tuple[Path, ...], owner: str = "project"
) -> tuple[Notice, ...]:
    fragments = (
        (
            str(path.relative_to(root)) if path.is_relative_to(root) else str(path),
            load_fragment(path),
        )
        for path in sources
    )
    return tuple(
        Notice(
            owner,
            "instructions",
            f'the instruction document repeats the heading "{heading}", from '
            f"{', '.join(labels[:-1])} and {labels[-1]}; keep one section, with /loadout",
        )
        for heading, labels in repeated_headings(fragments).items()
    )


def _marketplaces(
    harness: str, slice_name: str, document: Any, environ: Mapping[str, str]
) -> frozenset[str]:
    """The marketplaces a plugins document can name. Only a plugins fragment has
    them, and reading them parses the document as one: evaluated for any other
    slice, a `defaults` fragment's keys or a hooks fragment's event names read as
    stray sections of a plugins document and the whole render fails."""
    if slice_name != "plugins":
        return frozenset()
    return _known_marketplaces(harness, document, environ)


def _codex_skill_race(
    trees: tuple[Skill, ...],
    harnesses: tuple[str, ...],
    policies: tuple[SkillPolicy, ...],
    environ: Mapping[str, str],
) -> tuple[Notice, ...]:
    """OpenCode also scans `.agents/skills`, where Codex's variant of a skill lands."""
    if "opencode" not in harnesses or not opencode_external_skills_race(environ):
        return ()
    differing = any(
        {"codex", "opencode"}.issubset(selected_skill_agents(skill.name, harnesses, policies))
        and render_skill_for(skill.name, policies)
        and render_skill(skill, "codex") != render_skill(skill, "opencode")
        for skill in trees
    )
    if not differing:
        return ()
    return (
        Notice(
            owner="opencode",
            slice="skills",
            message=(
                f"{OPENCODE_EXTERNAL_SKILL_FLAG} is not set, so OpenCode also scans "
                ".agents/skills and picks between its variant and Codex's at random; "
                "export it or restrict the skill's agents; see docs/reference/opencode.md"
            ),
        ),
    )


def _agent_notices(agents: tuple[Agent, ...], harnesses: tuple[str, ...]) -> tuple[Notice, ...]:
    return tuple(
        Notice(owner=harness, slice=AGENTS_SUBDIR, message=note)
        for agent in agents
        for harness, note in agent_notes(agent, harnesses)
    )
