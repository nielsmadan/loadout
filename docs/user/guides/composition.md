---
title: Composition and inheritance
description: Combine organisation, team, and personal sources and use profiles without duplicating entire configurations.
---

# Compose configuration around how you work

Loadout provides several ways to reuse configuration. Each has a different purpose:

| Mechanism | Use it for |
| --- | --- |
| Global and project scopes | Choose where configuration applies |
| Sources | Combine configuration maintained in different directories or repositories |
| Instruction fragments | Assemble an ordered document from reusable pieces |
| Templates | Apply shared context to selected projects |
| Profiles | Switch between machine-wide variants of your setup |

Organisation, team, and personal are names you give sources. They are not built-in privileged tiers.

## Combine sources

A global manifest can use several sources:

```toml title="loadout.toml"
[[source]]
name = "company"
path = "~/src/company-agent-config"

[[source]]
name = "team"
path = "~/src/team-agent-config"

[[source]]
name = "personal"
path = "."

[all]
instructions = ["company/engineering", "team/testing", "personal/workflow"]

[claude]
[codex]
```

Each source can provide its own `instructions/`, `permissions.toml`, skills, and other
supported slices. A source path may be absolute, start with `~/`, or be relative to the manifest
directory.

A bare fragment name must resolve uniquely. Qualify it with its source name when several sources
provide that name. The instruction order is explicit, so directory sorting never decides which
guidance comes first.

Fragments are concatenated as they are, so two that open the same section repeat it. When one
instruction document would carry a Markdown heading from more than one fragment, `sync` and
`check` print a note naming the heading and the fragments:

```text
note: claude.instructions: the instruction document repeats the heading "## Testing", from instructions/workflow.md and instructions/claude.md; keep one section, with /loadout
```

The note covers each agent's global document, and a project's document together with its
templates' instructions. It never changes the exit code.

A source's optional `use` list restricts what it contributes:

```toml
[[source]]
name = "company"
path = "~/src/company-agent-config"
use = ["instructions", "permissions", "templates"]
```

Omitting `use` makes its available slices eligible.

## Share defaults across agents

```toml
[all]
instructions = ["workflow", "testing"]

[claude]

[codex]
instructions = ["workflow", "testing", "codex"]
```

An agent's explicit value replaces the corresponding `[all]` default. An instruction list is
replaced as a list, rather than appended automatically. Only named agents are enabled.

## How content combines

| Content | Combination rule |
| --- | --- |
| Instructions | Selected fragments concatenate in their declared order |
| Permission entries | Union of source tiers; for the same entry, deny beats ask beats allow |
| Shell default | Strictest explicitly stated default wins |
| JSON fragments | Maps merge recursively, arrays concatenate, scalar values replace, `null` removes keys |
| Global MCP definitions | Later sources replace a server definition with the same name |
| Module files | Copy bytes verbatim; duplicate destination paths are errors |

Project permissions have template, committed-project, and personal tiers. Templates use the
same merge operator as the content they contribute. A project allow therefore cannot cancel a
template deny.

For project skills, a project copy replaces the template's copy under the same name. When two
templates offer the same skill, the last declared template supplies it.

A few names belong to a harness, which writes them into a folder Loadout also writes. A global
skill named `synced` would land on Claude Code's own `~/.claude/skills/synced`, and a global agent
named `scrutiny-feature-reviewer` or `user-testing-flow-validator` on one of Droid's mission
droids. Sync and check refuse such a name with exit 3:

```text
loadout: Claude Code writes /home/you/.claude/skills/synced itself; rename the skill 'synced' so the two do not overwrite each other
```

Rename the skill or agent in the source.

These rules describe Loadout's composition. Each agent still applies its native matching and
scope precedence to the generated files.

## Profiles

The global source's `loadout.toml` is the `default` profile. Create a sibling file for a
variant:

```toml title="focused.toml"
extends = "default"

[codex]
substitute = { workflow = "workflow-focused" }
```

Create `instructions/workflow-focused.md` in a source. Then render the variant:

```sh
loadout sync --global --profile focused
loadout check --global --profile focused
```

Return to the default with:

```sh
loadout sync --global --profile default
```

Profile blocks inherit per key. An omitted key keeps the parent value; an explicit empty list
is an explicit empty selection. `substitute` swaps selected instruction fragment names without
restating their order.

Use the exact selected name in a substitution. For the qualified source example above, the key
would be `"personal/workflow"` and its replacement `"personal/workflow-focused"`.

A profile can extend another profile. Cycles are errors. Set the machine configuration's
`profile` key to choose the default for future `--global` commands.

Project configuration has no profile selector of its own. Profiles vary your machine setup;
project-specific permissions use `permissions.local.toml`.

## Keep the two scope axes clear

Global configuration applies across projects. Project configuration is generated inside a
repository. Loadout does not flatten them into one combined document or impose one universal
global-to-project precedence rule.

Use [templates](templates.md) to reuse project configuration, sources to combine shared ownership,
and profiles to select a machine-wide variant.
