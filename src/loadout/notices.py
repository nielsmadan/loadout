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
from .composition import load_fragment
from .destinations import resolve_destination
from .emit import (
    PROJECT_DOCUMENTS,
    SKILLS_SUBDIR,
    RenderContext,
    global_rules,
    global_servers,
    project_agents,
    project_instruction_sources,
    project_rules,
    project_skill_trees,
    project_slice_document,
    project_templates,
    refuse_unregistered_global,
    selected_target,
    skill_trees,
    slice_document,
    source_agents,
)
from .errors import LoadoutError
from .hooks import CLAUDE_EVENTS, CODEX_EVENTS, DROID_EVENTS, unrecognised_events
from .manifest import Manifest, load_profile, manifest_path
from .permissions.renderers import CATCH_ALL_RENDERERS
from .permissions.rules import Rules, exact_command, is_exact, is_glob
from .plugins import ADDRESSED_BY, marketplaces, unaddressable, unregistered_marketplaces
from .project import (
    PROJECT_DIR,
    PROJECT_PRESET,
    load_project_config,
    project_config_path,
    project_slices,
)
from .servers import Server
from .skill_policy import SkillPolicy, render_skill_for, selected_skill_agents
from .skills import Skill, loose_skill_files, render_skill
from .subagents import AGENTS_SUBDIR, Agent, agent_notes, discover_agents
from .templates import template_divergence, unverifiable_templates

__all__ = [
    "Notice",
    "collect_notices",
    "inexact_shell_entries",
    "known_events",
    "notices_for",
    "opencode_skills_race",
    "template_notices",
    "unpermitted_servers",
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
    "codex": "Codex's is approval_policy in config.toml, which loadout does not write",
    "codex-project": "Codex's is approval_policy in config.toml, which loadout does not write",
    "droid": "Droid's unmatched-command behavior comes from the session autonomy level",
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


def unpermitted_servers(servers: Mapping[str, Server], rules: Rules) -> tuple[str, ...]:
    """Servers defined but named by no tool policy, in declaration order.

    Two files, two edits: define the server, then allow its tools. Forgetting the
    second gives a server whose tools are all denied and no error anywhere. This
    is the locality benefit of one section, without giving one file two merge
    algebras.
    """
    named = {
        entry.split("/", 1)[0] for entry in (*rules.mcp_allow, *rules.mcp_ask, *rules.mcp_deny)
    }
    return tuple(name for name in servers if name not in named)


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

    if slice_name == "hooks":
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


def _renderer_notices(agent: str, renderer: str, rules: Rules) -> list[Notice]:
    """What a renderer cannot carry of these rules."""
    unreached = unreached_catch_all(agent, renderer, rules.default) if rules.default else ()
    return [
        *unreached,
        *unsupported_shell_globs(agent, renderer, rules),
        *inexact_shell_entries(agent, renderer, rules),
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


def _unpermitted_servers(manifest: Manifest) -> tuple[Notice, ...]:
    """Every server no source's `[mcp]` policy names, across every source.

    Two files, two merge algebras (docs/reference/config.md), so this reads both
    independently rather than through `project_servers`. Silent when no source
    offers mcp.toml: a manifest defining no servers has nothing to check.
    """
    servers = global_servers(manifest)
    if not servers:
        return ()
    rules = global_rules(manifest)
    return tuple(
        Notice(
            owner="mcp",
            slice="servers",
            message=(
                f"{name} is defined but no [mcp] policy allow/ask/deny entry names it "
                f"— its tools are all denied with no error"
            ),
        )
        for name in unpermitted_servers(servers, rules)
    )


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


def template_notices(root: Path) -> tuple[Notice, ...]:
    """Vendored templates whose copy no longer matches what was vendored, or that have
    nothing to compare against. Reported, never failed: a vendored copy is source, so
    it falls outside drift by definition (ADR 0014), but saying nothing would let a
    project drift from its template indefinitely."""
    return (
        *(
            Notice(
                "project",
                "templates",
                f"the vendored template {name!r} was modified after it was vendored — "
                f"`loadout template sync {name}` will refuse until those edits go upstream",
            )
            for name in template_divergence(root)
        ),
        *(
            Notice(
                "project",
                "templates",
                f"the vendored template {name!r} has no recorded provenance, so nothing can "
                f"tell your edits from its source — delete loadout/templates/{name} and run "
                f"`loadout template add {name} --vendored` to take the source wholesale and "
                "record one",
            )
            for name in unverifiable_templates(root)
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
        *template_notices(root),
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
    found.extend(_permission_notices(manifest, profile))
    found.extend(_unpermitted_servers(manifest))
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
    templates = project_templates(root, config, RenderContext())
    rules = project_rules(root, templates)
    permissions: list[Notice] = []
    documents: list[Notice] = []
    for agent, slice_name, spec in project_slices(config.harnesses):
        if slice_name == "permissions" and spec.renderer:
            permissions.extend(_renderer_notices(agent, spec.renderer, rules))
        elif slice_name in PROJECT_DOCUMENTS:
            document, contributed = project_slice_document(root, slice_name, templates)
            if contributed:
                known = _marketplaces(agent, slice_name, document, environ)
                documents.extend(notices_for(agent, slice_name, document, known))
    trees = project_skill_trees(root, templates)
    return (
        *found,
        *permissions,
        *documents,
        *_repeated_heading_notices(root, project_instruction_sources(root, config, templates)),
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


def _repeated_heading_notices(root: Path, sources: tuple[Path, ...]) -> tuple[Notice, ...]:
    fragments = (
        (
            str(path.relative_to(root)) if path.is_relative_to(root) else str(path),
            load_fragment(path),
        )
        for path in sources
    )
    return tuple(
        Notice(
            "project",
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
