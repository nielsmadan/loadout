<p align="center"><img src="docs/assets/logo.svg" alt="Loadout backpack" width="140"></p>

# loadout

One source of truth for AI coding-agent configuration, rendered out to every harness.

**Switch agents. Keep your setup.** Reuse project templates and compose shared and personal
configuration from one source. Loadout renders for Claude Code, Codex, Factory Droid, OpenCode
and Pi.

[Documentation](https://loadoutai.dev/) · [Getting started](https://loadoutai.dev/getting-started/)
· [Templates](https://loadoutai.dev/guides/templates/)

To preview, build, or publish the site, see [Publishing the documentation](docs/publishing.md).
For versioned packages and Homebrew distribution, see [Releasing Loadout](docs/releasing.md).

## Install

    just install

This installs a snapshot of the current checkout onto `PATH`. Run it again after changes
to replace the installed code, even when the package version is unchanged.
Use `just install-editable` to have source edits take effect without reinstalling.
`just uninstall` removes the CLI installation and preserves configuration and generated files.

To work on loadout itself, `just setup` installs the dependencies and the Git hooks, and
`just check` runs lint, format check, type check and unit tests. `just` with no arguments lists
every recipe.

## Quick start

Adopt the configuration your agents already have on this machine into a global source:

    mkdir -p ~/agent-config
    loadout init global ~/agent-config --dry-run   # preview
    loadout init global ~/agent-config             # apply; registers ~/agent-config/loadout

Adopt a repository's configuration into its own `loadout/` folder:

    cd ~/src/my-repo
    loadout init project --harness claude --harness codex

Then edit the source, not the generated files, and render:

    loadout sync              # in a project; add --global for this machine's configuration
    loadout check             # exit 1 if a generated file has drifted

Install the bundled `/loadout` skill, so your agents can make those edits for you:

    loadout integrate skill install

## Commands

    loadout sync [--root PATH | --global] [--profile NAME] [--force]
    loadout check [--root PATH | --global] [--profile NAME] [--json | --staged]
    loadout init global [DIR] [--harness NAME]... [--registration keep|replace] [--dry-run] [--json] [--yes]
    loadout init project [--root PATH] [--harness NAME]... [--dry-run] [--json] [--yes]
    loadout template add [NAME] [--tracked | --vendored] [--yes]
    loadout template sync NAME [--yes]
    loadout template list
    loadout integrate skill install|status|uninstall|copy
    loadout integrate git-hooks install|status|uninstall [--root PATH]
    loadout integrate completions install|status|uninstall [--shell SHELL]
    loadout --version

`check` is the preview of `sync`. `check --staged` validates what the Git index would commit, and
is what the pre-commit hook runs. Every option is in the
[CLI reference](docs/user/reference/cli.md).

## Configuration

A global source's `loadout.toml` names its sources and the harnesses it renders for. Destinations
come from a built-in preset, so the manifest never spells out a machine path:

```toml
[[source]]
name = "personal"
path = "."

[all]
instructions = ["workflow"]   # instructions/workflow.md, for every harness below

[claude]
settings = "claude"           # settings/claude.json

[codex]
[pi]
```

Permissions, MCP servers, skills, subagents and module files render automatically from the
source's `permissions.toml`, `mcp.toml`, `skills/`, `agents/` and `module-config/`. A project keeps
its own source in `loadout/`, with `loadout/config.toml`:

```toml
harnesses = ["claude", "codex"]
instructions = ["conventions", "testing"]
templates = ["frontend"]
```

The full schema, from the machine config to permission rules and both source layouts, is in the
[configuration reference](docs/user/reference/configuration.md).

## Exit codes

| Code | Meaning |
| ---- | ------- |
| 0 | success: nothing to do, or a check that found no differences |
| 1 | drift, a refused safe update, or an init that failed, whether or not it could put everything back |
| 2 | usage error, unresolved init choices, missing noninteractive approval, or an init killed part way, found by the next one |
| 3 | source error: missing or invalid input, an unsafe destination, or an ownership collision |
| 4 | internal error: an unexpected exception; a traceback is printed to stderr |
| 130 | `init` stopped with Ctrl-C, after putting back every path it changed or trying to |

## Documentation

- [Getting started](docs/user/getting-started.md)
- Guides: [global setup](docs/user/guides/global.md), [project setup](docs/user/guides/projects.md),
  [templates](docs/user/guides/templates.md), [composition](docs/user/guides/composition.md),
  [configuring your agents](docs/user/guides/configuration.md)
- Reference: [CLI](docs/user/reference/cli.md),
  [configuration files](docs/user/reference/configuration.md),
  [harness support](docs/user/reference/harnesses.md)
- [Troubleshooting](docs/user/troubleshooting.md)
- [Scopes](docs/scopes.md): what loadout is for, and committed versus personal configuration
- [Harness reference](docs/reference/README.md): matcher semantics, verified quirks, and how
  [sync](docs/reference/sync.md), [init](docs/reference/migration.md) and
  [Git integration](docs/reference/git-integration.md) behave
- [Decisions](docs/decisions/): the architecture decision records
