---
title: Kits
description: "A kit stays linked to its source: every sync picks up its current contents unless you vendor it."
---

# Kits

A kit stays linked to its source: every sync picks up its current contents unless you vendor it.

A kit bundles configuration for a kind of work: Python, a web frontend, mobile development, or a
deployment environment. It gives each project the instructions, permissions, skills and MCP
servers it needs without placing them in every agent session.

Keep that context in the projects that use it. Framework-specific instructions then stay out of
your global instruction document, while related projects share an updateable source.

## Create a kit

Start with the [global source](global.md) from the setup guide. The source `init global` wrote
has no `use` list, so it offers kits as it is; a source with a `use` list offers them only
when the list includes `kits`.

A kit is either a directory, `kits/<name>/`, holding its own files, or a catalog
manifest, `kits/<name>.toml`, that selects shared parts. The directory form is the simpler
start. Create a directory under that source's `kits/`:

```sh
mkdir -p ~/agent-config/loadout/kits/python
```

Create its instructions:

```markdown title="~/agent-config/loadout/kits/python/instructions.md"
# Python projects

Use the Python version the project requires.
Keep dependency changes in pyproject.toml and its lockfile.
Run the existing test and lint commands after changing code.
```

Add permissions if the kit needs them:

```toml title="~/agent-config/loadout/kits/python/permissions.toml"
[shell]
allow = ["uv run pytest", "uv run ruff check"]
```

A kit can contain any combination of the project-supported slices:

```text
kits/python/
├── instructions.md
├── permissions.toml
├── skills/
│   └── python-review/
│       └── SKILL.md
├── mcp.toml
├── hooks.json
└── plugins.json
```

Every file is optional. The kit directory name is the name projects use. `hooks.json` and
`plugins.json` reach Factory Droid only, the one harness with project hooks and plugins.

### Share parts between kits

When several kits repeat the same instructions or skills, keep each part once and list it
from a catalog manifest instead. `kits/nextjs.toml` selects parts by name from folders
beside it:

```toml title="~/agent-config/loadout/kits/nextjs.toml"
instructions = ["typescript", "nextjs"]
skills = ["review-typescript"]
mcp = ["github"]
permissions = ["node"]
```

The parts live at `kits/instructions/typescript.md`, `kits/skills/review-typescript/`,
`kits/mcp/github.toml` and `kits/permissions/node.toml`. Projects use the manifest's
name, `nextjs`, exactly as they use a directory kit's. A directory and a manifest with the
same name are ambiguous and refused. The
[kit reference](https://github.com/nielsmadan/loadout/blob/main/docs/reference/kits.md#shared-catalog-parts)
covers the catalog rules.

## Apply it to a project

In an initialised project:

```sh
loadout kit add python --linked
loadout sync
loadout kit list
```

The kit's name is recorded in `loadout/config.toml`:

```toml
harnesses = ["claude", "codex"]
instructions = ["project"]
kits = ["python"]
```

Project fragments come before kit instructions, so the document opens with what is specific
to the project. Permissions merge with the project's
shared and personal rules. The [composition rules](composition.md#how-content-combines) explain
what happens when two sources overlap.

## Choose an update model

| Model | Where the kit lives | How updates reach a project |
| --- | --- | --- |
| Linked | A source in your machine's global manifest | The next `loadout sync` reads the current kit |
| Vendored | `loadout/kits/python/` in the project | Run `loadout kit sync python`, then `loadout sync` |

Linked kits work well across your own projects or a team that shares the same source.
Edit the kit once, then sync the projects that consume it.

Vendored kits travel with the repository. Contributors still need Loadout to render the
harness files, but they do not need the original kit source just to use the vendored copy.

## Vendor a copy

```sh
loadout kit add python --vendored
loadout sync
```

The mode belongs to the project's entry for the kit. Running `add` with `--vendored` on a kit the
project already lists changes it from linked to vendored; `--linked` on a vendored one switches
back once the source can still resolve the name. Linking deletes the copy, so a copy you modified
is refused the way `kit sync` refuses it: Loadout prints the diff, changes nothing and exits 1.
Keep the edits by leaving the kit vendored, or discard them by deleting the files the refusal
names and running `--linked` again. For a catalog kit those are its manifest and every part no
other vendored kit uses; linking an unmodified one deletes the same files and leaves the shared
parts in place. Bare `loadout kit add` lists every
kit this machine can resolve and prompts for a name and a mode; without a terminal it
requires both.

Loadout copies the kit to `loadout/kits/python/` and records its content hash in
`loadout/config.toml`. Commit both the source copy and the manifest change through your normal
git workflow.

## Update a vendored kit

After updating the upstream kit on disk:

```sh
loadout kit sync python
loadout sync
```

Loadout updates an unchanged vendored copy and records the new hash. If you modified the copy,
it prints a diff and refuses to overwrite it. Reconcile those changes manually.

Keep project customisations in the project's own fragments where possible, so the vendored
kit stays easy to update. `loadout check` reports a modified or unverifiable vendored
source separately from generated-file drift.

## Share a kit source

Add the source repository to each machine's global manifest:

```toml
[[source]]
name = "company"
path = "~/src/company-agent-config"

[[source]]
name = "migrated"
path = "."
```

A source path may be absolute, start with `~/`, or be relative to the manifest's directory.
The company source can now offer `kits/python/` or `kits/python.toml`. Loadout resolves
the kit by name, so the project's committed configuration contains no machine-specific path.

If two sources offer the same name, qualify it, for example `company/python`.
A vendored project copy is resolved before searching global sources.

Loadout does not fetch kit repositories. Use git or your existing distribution process
to update the source on disk, then sync the consuming projects.

## Remove a kit

Remove its name from `kits` in `loadout/config.toml`. For a vendored kit, also
remove its matching `[kit.NAME]` hash block and the copy in `loadout/kits/`.

Run `loadout sync`. It retires the generated files it wrote for the kit earlier and no
longer renders, printing `retired <path>` for each. A file you edited since is refused instead;
`loadout check` lists both kinds before you sync.

## Upgrading from 0.10.0

In loadout 0.10.0 a kit was called a template, and its linked mode was `--tracked`. The old
spellings are refused with an error that names the new one, and nothing reads them as aliases.
After upgrading, rename `templates = [...]` to `kits = [...]` in `loadout/config.toml`, each
`[template.NAME]` block to `[kit.NAME]`, and `loadout/templates/` to `loadout/kits/`. In a source,
rename the `templates/` folder to `kits/` and `templates` in its `use` list to `kits`. Where you
ran `loadout template add NAME --tracked`, run `loadout kit add NAME --linked`.
