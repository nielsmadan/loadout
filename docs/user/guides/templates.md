---
title: Templates
description: Reuse project-specific instructions, permissions, skills, and MCP servers without placing them in every agent session.
---

# Give each project the context it needs

A template bundles configuration for a kind of work: Python, a web frontend, mobile development,
or a deployment environment.

Keep that context in the projects that use it. Framework-specific instructions then stay out of
your global instruction document, while related projects share an updateable source.

## Create a template

Start with the [global source](global.md) from the setup guide. Create a directory under that
source's `templates/`:

```sh
mkdir -p ~/agent-config/loadout/templates/python
```

Create its instructions:

```markdown title="~/agent-config/loadout/templates/python/instructions.md"
# Python projects

Use the project's declared Python version.
Keep dependency changes in pyproject.toml and its lockfile.
Run the existing test and lint commands after changing code.
```

Add permissions if the template needs them:

```toml title="~/agent-config/loadout/templates/python/permissions.toml"
[shell]
allow = ["uv run pytest", "uv run ruff check"]
```

A template can contain any combination of the project-supported slices:

```text
templates/python/
├── instructions.md
├── permissions.toml
├── skills/
│   └── python-review/
│       └── SKILL.md
└── mcp.toml
```

Every file is optional. The template directory name is the name projects use.

## Apply it to a project

In an initialised project:

```sh
loadout template add python
loadout sync
loadout template list
```

The declaration is recorded in `loadout/config.toml`:

```toml
harnesses = ["claude", "codex"]
instructions = ["project"]
templates = ["python"]
```

Template instructions come before project fragments. Permissions merge with the project's
shared and personal rules. The [composition rules](composition.md#how-content-combines) explain
what happens when two sources overlap.

## Choose an update model

| Model | Where the template lives | How updates reach a project |
| --- | --- | --- |
| Declared | A source in your machine's global manifest | The next `loadout sync` reads the current template |
| Vendored | `loadout/templates/python/` in the project | Run `loadout template sync python`, then `loadout sync` |

Declared templates work well across your own projects or a team that shares the same source.
Edit the template once, then sync the projects that consume it.

Vendored templates travel with the repository. Contributors still need Loadout to render the
agent files, but they do not need the original template source just to use the vendored copy.

## Vendor a copy

```sh
loadout template vendor python
loadout sync
```

Loadout copies the template to `loadout/templates/python/` and records its content hash in
`loadout/config.toml`. Commit both the source copy and the manifest change through your normal
git workflow.

## Update a vendored template

After updating the upstream template on disk:

```sh
loadout template sync python
loadout sync
```

Loadout updates an unchanged vendored copy and records the new hash. If you modified the copy,
it prints a diff and refuses to overwrite it. Reconcile those changes manually.

Keep project customisations in the project's own fragments where possible, so the vendored
template stays easy to update. `loadout check` reports a modified or unverifiable vendored
source separately from generated-file drift.

## Share a template source

Add the source repository to each machine's global manifest:

```toml
[[source]]
name = "company"
path = "~/src/company-agent-config"

[[source]]
name = "personal"
path = "."
```

The company source can now offer `templates/python/`. Loadout resolves the template by name,
so the project's committed configuration contains no machine-specific path.

If two sources offer the same name, qualify it, for example `company/python`.
A vendored project copy is resolved before searching global sources.

Loadout does not fetch template repositories. Use git or your existing distribution process
to update the source on disk, then sync the consuming projects.

## Remove a template

Remove its name from `templates` in `loadout/config.toml`. For a vendored template, also
remove its matching `[template.NAME]` hash block and the copy in `loadout/templates/`.

Run `loadout sync` and inspect the generated files. Loadout does not currently clean up every
orphaned file when a source, skill, or output disappears. Remove obsolete generated files after
reviewing what stopped producing them.
