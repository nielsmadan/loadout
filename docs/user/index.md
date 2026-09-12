---
title: Home
description: Switch agents without rebuilding your configuration. Loadout shares instructions, permissions, and tools through reusable project templates and composable sources.
hide:
  - toc
---

<div class="loadout-hero">
  <img class="loadout-logo-dark" src="assets/logo.svg" alt="Loadout backpack" width="140" height="140">
  <img class="loadout-logo-light" src="assets/logo-light.svg" alt="Loadout backpack" width="140" height="140">
  <h1>Switch agents.<br>Keep your setup.</h1>
  <p>One source for your agent configuration, rendered for Claude Code, Codex, OpenCode, and Pi.</p>
</div>

<div class="loadout-actions" markdown>
[Get started](getting-started.md){ .md-button .md-button--primary }
[Explore templates](guides/templates.md){ .md-button }
</div>

<div class="loadout-benefits" markdown>
<div class="loadout-benefit" markdown>

## Configure once. Use across agents.

Keep shared instructions and rules together. Loadout writes each agent's native configuration, so adding another agent doesn't mean rebuilding your setup.

[Set up two agents →](getting-started.md)

</div>
<div class="loadout-benefit" markdown>

## Give each project the context it needs.

Keep project-specific context out of your global instructions and token budget. Reuse templates for instructions, skills, and tools, and update projects from a shared source.

[Reuse a project template →](guides/templates.md)

</div>
<div class="loadout-benefit" markdown>

## Compose it your way.

Combine organisation, team, and personal sources. Keep global defaults and project configuration separate. Use profiles and ordered fragments to assemble the setup you need.

[Compose your configuration →](guides/composition.md)

</div>
</div>

## A shared source, native configuration

A project names its agents and the instruction fragments they should read:

```toml title="loadout/config.toml"
harnesses = ["claude", "codex"]
instructions = ["project", "testing"]
```

Write the instructions in `loadout/instructions/project.md` and `testing.md`, then run:

```sh
loadout sync
```

Loadout composes `CLAUDE.md` and `AGENTS.md` and renders the project's permission rules into
each agent's format. Edit the source once, sync it, and use either agent.

[Follow the complete walkthrough](getting-started.md).

## Start with the setup you need

- **For your own agents:** [global setup](guides/global.md) applies configuration across projects.
- **For a shared repository:** [project setup](guides/projects.md) keeps shared sources in git
  and your personal permission rules local.
- **For several similar projects:** [templates](guides/templates.md) let you maintain a common
  configuration without filling every session with unrelated instructions.

Loadout renders configuration. Your agents still decide how to load and enforce it, and some
features have different support across harnesses. The [support reference](reference/harnesses.md)
explains those differences.
