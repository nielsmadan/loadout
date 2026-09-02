from __future__ import annotations

from dataclasses import dataclass

__all__ = ["GLOBAL_PRESET", "SliceOutput", "agent_slices", "known_agents"]


@dataclass(frozen=True)
class SliceOutput:
    """Where one slice of one agent's configuration is written.

    **`destination` and `output` are two different kinds of path, not a primary
    and a fallback.** `destination` is a machine path template resolved at render
    time, so a relocated harness is followed without editing a manifest (ADR
    0011). `output` is relative to the scope's root, which `_target_paths`
    resolves against — the global source directory for `GLOBAL_PRESET`, the
    project for `PROJECT_PRESET`. Global scope uses `output` only where a slice
    is staged for a merge step outside loadout; project scope uses it for
    everything, and sets no destinations at all.

    `source_slice` names the slice supplying this one's own content.

    `owned_key` makes this slice a **contributor**: its renderer produces the
    value of that key and the loop assigns it, rather than transforming the
    whole document. Settings is the residual every file starts from, so it is
    never a `source_slice` — spec 1 §3's ownership map, made executable.

    `preserve` names foreign top-level keys this destination's other owner
    maintains. It is preset-level so a harness-specific runtime key does not
    have to be rediscovered and restated in every manifest.

    `preserve_foreign` is project scope's residual: the existing file becomes the
    renderer's base. It is **not** a spelling of `PermissionTarget.preserve`, and
    merging the two reorders keys:

    | | `preserve` | `preserve_foreign` |
    |---|---|---|
    | input | key names from the manifest | the whole existing file |
    | when | re-read after render, appended last | handed in as the base, keys keep position |
    | guard | errors if a named key is generated | none |
    """

    renderer: str | None = None
    destination: str | None = None
    output: str | None = None
    source_slice: str | None = None
    owned_key: str | None = None
    preserve: tuple[str, ...] = ()
    preserve_foreign: bool = False


# Destinations carry each harness's config-directory variable rather than a
# literal path. The variables differ in name and in kind, and two of OpenCode's
# look right and are not — see docs/reference/README.md, which records how each
# was verified. Keeping them here means a manifest never spells one out.
GLOBAL_PRESET: dict[str, dict[str, SliceOutput]] = {
    "claude": {
        "skills": SliceOutput(destination="${CLAUDE_CONFIG_DIR:-~/.claude}/skills"),
        # A root, not a file — see the pi entry below. Claude's case is hook
        # scripts: the hooks slice registers a command by path, and this puts the
        # script at that path. Two slices, one for the declaration and one for the
        # file it names.
        "module-config": SliceOutput(destination="${CLAUDE_CONFIG_DIR:-~/.claude}"),
        "instructions": SliceOutput(destination="${CLAUDE_CONFIG_DIR:-~/.claude}/CLAUDE.md"),
        "permissions": SliceOutput(
            renderer="claude",
            destination="${CLAUDE_CONFIG_DIR:-~/.claude}/settings.json",
        ),
        "mcp-permissions": SliceOutput(
            renderer="claude-mcp-permissions",
            destination="${CLAUDE_CONFIG_DIR:-~/.claude}/mcp-permissions.json",
        ),
        # Shares settings.json with permissions and the settings residual, so it
        # contributes one key rather than transforming the document.
        "hooks": SliceOutput(
            renderer="claude-hooks",
            destination="${CLAUDE_CONFIG_DIR:-~/.claude}/settings.json",
            source_slice="hooks",
            owned_key="hooks",
        ),
        # The fourth slice landing in settings.json. Enablement only: the
        # marketplace a plugin comes from is registered in
        # ~/.claude/plugins/known_marketplaces.json, which carries `lastUpdated`
        # and `installLocation`, so ADR 0008 forbids rendering it. loadout
        # reports it instead — `plugins.unregistered_marketplaces`.
        "plugins": SliceOutput(
            renderer="claude-plugins",
            destination="${CLAUDE_CONFIG_DIR:-~/.claude}/settings.json",
            source_slice="plugins",
            owned_key="enabledPlugins",
        ),
        # ${CLAUDE_CONFIG_DIR}/.claude.json is runtime state — history, project
        # entries, caches — and settings.json has no mcpServers key, so there is
        # no file to write. loadout renders this staged document and stops;
        # something else feeds it to `claude mcp add-json`. Render and invoke
        # stay separate (ADR 0004).
        "mcp": SliceOutput(renderer="claude-servers", output="claude/mcp-servers.generated.json"),
    },
    "codex": {
        "skills": SliceOutput(destination="${CODEX_HOME:-~/.codex}/skills"),
        "instructions": SliceOutput(destination="${CODEX_HOME:-~/.codex}/AGENTS.md"),
        "permissions": SliceOutput(
            renderer="codex",
            destination="${CODEX_HOME:-~/.codex}/rules/permissions.rules",
        ),
        # Codex is the one harness whose hooks have a file of their own, so this
        # lands without agent-first rendering. Claude's `hooks` key shares
        # settings.json with permissions and settings, which needs two slices to
        # compose into one document — spec 1 §3, not yet built.
        # `hooks` is the only key loadout writes here, so the residual is empty.
        # That is a claim about what loadout writes, not about the file: a
        # hand-made hooks.json may carry keys neither of us anticipated — Cursor's
        # equivalent has a `version` — and this drops them. `preserve` is the
        # mechanism if one is ever identified; none is, so none is named.
        "hooks": SliceOutput(
            renderer="codex-hooks",
            destination="${CODEX_HOME:-~/.codex}/hooks.json",
            source_slice="hooks",
            owned_key="hooks",
        ),
        # Definitions and approval policy land in the same `[mcp_servers.<name>]`
        # table, so one renderer emits both: two slices would declare the table
        # twice and Codex would refuse to parse its own config.
        "mcp": SliceOutput(
            renderer="codex-config",
            destination="${CODEX_HOME:-~/.codex}/config.toml",
        ),
        # Opt-in, not automatic: it strips every key it manages, so a machine that
        # never asked for it must never have its hand-maintained settings touched.
        # Ownership here is derived from the fragment — the key names are the
        # user's, not a set loadout could know — so it carries an owned-key record
        # beside its fragment. See `_attach_records`.
        "defaults": SliceOutput(
            renderer="codex-settings",
            destination="${CODEX_HOME:-~/.codex}/config.toml",
            source_slice="defaults",
        ),
        # The same destination, disjoint keys. config.toml also holds
        # `[projects.…]` Codex writes itself and a block another tool manages, so
        # loadout declares what it owns and strips only that (ADR 0017) rather
        # than rewriting the file from a base it could not hold.
        "plugins": SliceOutput(
            renderer="codex-plugins",
            destination="${CODEX_HOME:-~/.codex}/config.toml",
            source_slice="plugins",
        ),
    },
    "opencode": {
        "skills": SliceOutput(destination="${XDG_CONFIG_HOME:-~/.config}/opencode/skills"),
        # A document at a path, exactly like the other three — *not* the
        # `instructions` key in opencode.json, which is a separate feature for
        # including rule files someone else already wrote (globs and remote URLs
        # among them). Upstream calls this one "global rules … applied across all
        # opencode sessions".
        #
        # Until this existed OpenCode fell back to `~/.claude/CLAUDE.md`, which
        # loadout also writes — so it was reading Claude's document, with Claude's
        # fragments in it, and nothing looked broken.
        "instructions": SliceOutput(destination="${XDG_CONFIG_HOME:-~/.config}/opencode/AGENTS.md"),
        "permissions": SliceOutput(
            renderer="opencode",
            destination="${XDG_CONFIG_HOME:-~/.config}/opencode/opencode.json",
        ),
        # No hooks *file* — OpenCode registers hooks in code, so loadout
        # generates the code. The file is a plugin, auto-discovered from
        # `plugins/`, and it owns itself: nothing composes into a JS module.
        "hooks": SliceOutput(
            renderer="opencode-hooks",
            destination=("${XDG_CONFIG_HOME:-~/.config}/opencode/plugins/loadout-hooks.js"),
            source_slice="hooks",
        ),
        # Same renderer as project scope's `mcp`, one key of a document
        # `permission` also owns.
        "mcp": SliceOutput(
            renderer="opencode-servers",
            destination="${XDG_CONFIG_HOME:-~/.config}/opencode/opencode.json",
            owned_key="mcp",
        ),
    },
    "pi": {
        "skills": SliceOutput(destination="${PI_CODING_AGENT_DIR:-~/.pi/agent}/skills"),
        # The destination is a *root*, not a file: each path under
        # <source>/module-config/pi/ lands beneath it as authored. Pi fixes the
        # directory and never the filename, and a module's directory need not
        # even match its package — pi-subagents reads extensions/subagent/ — so
        # there is nothing here to derive a filename from.
        "module-config": SliceOutput(destination="${PI_CODING_AGENT_DIR:-~/.pi/agent}"),
        "instructions": SliceOutput(destination="${PI_CODING_AGENT_DIR:-~/.pi/agent}/AGENTS.md"),
        "permissions": SliceOutput(
            renderer="pi",
            destination=(
                "${PI_CODING_AGENT_DIR:-~/.pi/agent}/extensions/pi-permission-system/config.json"
            ),
        ),
        # Pi's extension directory, the same one the permission system lives in.
        # A single `.ts` file is Pi's documented simplest extension shape, and
        # jiti loads it without a build step.
        "hooks": SliceOutput(
            renderer="pi-hooks",
            destination="${PI_CODING_AGENT_DIR:-~/.pi/agent}/extensions/loadout-hooks.ts",
            source_slice="hooks",
        ),
        # Pi has no marketplace concept: a package names its own source and
        # enablement is installation. So this is the one harness where the
        # portable reference's `source` is what gets rendered.
        "plugins": SliceOutput(
            renderer="pi-plugins",
            destination="${PI_CODING_AGENT_DIR:-~/.pi/agent}/settings.json",
            source_slice="plugins",
            owned_key="packages",
            preserve=("lastChangelogVersion",),
        ),
        # pi-mcp-adapter's own file, written directly — Pi has no runtime
        # state co-mingled with server definitions the way Claude does.
        "mcp": SliceOutput(
            renderer="pi-servers", destination="${PI_CODING_AGENT_DIR:-~/.pi/agent}/mcp.json"
        ),
    },
}

# OpenCode has no plugins slice, and that is a finding rather than an omission: a
# plugin is on there because a file exists in `~/.config/opencode/plugins/`, and
# its dependencies live in an npm manifest `npm`/`bun` owns. There is no
# enablement list to render, so naming `plugins` under `[opencode]` is an error
# listing what that agent does offer.


def known_agents() -> frozenset[str]:
    return frozenset(GLOBAL_PRESET)


def agent_slices(agent: str) -> dict[str, SliceOutput]:
    return GLOBAL_PRESET[agent]
