# Model selection: skills and subagents

Where each harness lets a model be pinned below the session level, on a skill or on a subagent, and
what loadout does with it today.

**Verified 2026-10-11** against Claude Code 2.1.296 (installed binary and docs), Codex 0.161.0
(installed binary and docs), Pi 1.0.1 (shipped `docs/`), and the upstream docs for OpenCode and
Factory Droid. The OpenCode and Droid binaries were **not** inspected for this page, so their rows rest
on upstream documentation only.

## Summary

| | model on a **skill** | **subagents** | where subagents live | subagent model value | effort |
|---|---|---|---|---|---|
| Claude | **yes**: `model`, `effort`; `context: fork` + `agent` | Markdown + YAML frontmatter | `~/.claude/agents/`, `.claude/agents/` | `sonnet`, `opus`, `haiku`, `fable`, a full ID, `inherit` | `effort` |
| Codex | no | **TOML** | `~/.codex/agents/*.toml`, `.codex/agents/*.toml` | an OpenAI model ID | `model_reasoning_effort` |
| OpenCode | no | Markdown + YAML frontmatter | `~/.config/opencode/agents/`, `.opencode/agents/` | `provider/model-id` | none documented |
| Droid | no | Markdown + YAML frontmatter ("custom droids") | `~/.factory/droids/`, `.factory/droids/` | `inherit`, a public model ID, `custom:<id>` | `reasoningEffort` |
| Pi | no | none in core; `pi-subagents` package: Markdown | `~/.pi/agent/agents/`, `.pi/agents/` (package) | Pi model ID, `:level` suffix | `thinking` |

Two facts dominate any design built on this:

- **A model on a skill is Claude-only.** Every other harness enumerates its skill frontmatter, and none
  of them includes a model.
- **Model values do not carry across harnesses.** `opus`, an OpenAI ID, `provider/model-id` and a
  Factory ID are four vocabularies. All four harnesses that have subagents fall back to the parent's
  model when none is given (`inherit`), which is the one portable value.

## Claude

Skills, from the frontmatter reference (`code.claude.com/docs/en/skills`):

> `model` — Model to use when this skill is active. The override applies for the rest of the current
> turn and isn't saved to settings. … Accepts the same values as `/model`, or `inherit` to keep the
> active model. … With `context: fork`, the value sets the forked subagent's model instead.

> `context` — Set to `fork` to run in a forked subagent context.

> `agent` — Which subagent type to use when `context: fork` is set.

`effort` takes `low`, `medium`, `high`, `xhigh` or `max`. The 2.1.296 binary carries all four keys in
its frontmatter key list (`"name","description","model","allowed-tools",…,"effort",…,"context","agent"`).

Subagents (`code.claude.com/docs/en/sub-agents`):

> `model` — Model to use: `sonnet`, `opus`, `haiku`, `fable`, a full model ID such as
> `claude-opus-5-5`, or `inherit`.

**Claude is the only harness where a skill can name the subagent it runs in.** That is the one existing
link between a skill and a model-carrying agent.

## Codex

Skills: the documented `SKILL.md` frontmatter is `name` and `description`. The optional
`agents/openai.yaml` carries `interface`, `policy.allow_implicit_invocation` and `dependencies.tools`.
**Verified negative:** neither has a model field (`developers.openai.com/codex/skills`, read whole).

Subagents (`developers.openai.com/codex/subagents`):

> To define your own custom agents, add standalone TOML files under `~/.codex/agents/` for personal
> agents or `.codex/agents/` for project-scoped agents.

Each file must include `name`, `description` and `developer_instructions`. The instructions are a TOML
string, not a Markdown body. It may add other `config.toml` keys, including `model` and
`model_reasoning_effort`. The 0.161.0 binary's `spawn_agent` tool describes sub-agents *"that inherit
your current model by default"*, and roles resolve through `core/src/agent/role.rs`.

## OpenCode

Skills (`opencode.ai/docs/skills`), the complete list:

> Use these Agent Skills fields: `name`, `description`, `license`, `compatibility`, `metadata`. … Put
> OpenCode-specific behavior in `metadata` instead of adding custom top-level fields.

The recognised `metadata` keys are `opencode/slash` and `opencode/autoinvoke`. Neither is a model.

Agents (`opencode.ai/docs/agents`): Markdown files in `~/.config/opencode/agents/` (global) or
`.opencode/agents/` (per project), with a `model` field:

> The model ID in your OpenCode config uses the format `provider/model-id`.

## Droid

Skills (`docs.factory.ai/cli/configuration/skills`), the field table: `name`, `description`,
`allowed-tools`, `enabled`, `user-invocable`, `disable-model-invocation`, `license`, `compatibility`,
`metadata`, `version`. **No model.** The page's own comparison table puts it elsewhere: *"Custom
droid | Specialized subagent with its own prompt, model, and tool policy."*

Custom droids (`docs.factory.ai/cli/configuration/custom-droids`): Markdown with frontmatter, in
`<repo>/.factory/droids/` (project) or `~/.factory/droids/` (personal). `model` is `inherit` (the
default), a public model ID, or `custom:<id>` for BYOK models. `reasoningEffort` is *"Ignored when
`model` is `inherit`"*.

## Pi

Skills (shipped `docs/skills.md`, *"Write portable frontmatter"*): `name`, `description`, `license`,
`compatibility`, `metadata`, `allowed-tools`, `disable-model-invocation`. No model.

**Verified negative: Pi has no subagents.** None of the 40 files in Pi 1.0.1's shipped `docs/` mentions
*subagent* or *sub-agent*.

**Subagents come from extension packages.** Pi's own site: *"Pi ships with powerful defaults but skips features like
sub-agents and plan mode."* The most used package is `pi-subagents` (nicobailon; 623K downloads a month on
`pi.dev/packages/pi-subagents`, 2026-10-11). Its `docs/agents.md`: *"An agent is a markdown file: YAML frontmatter on top, a
system prompt below"*, read from `~/.pi/agent/agents/**/*.md` (user) and `.pi/agents/**/*.md` (project), and also from the legacy
project path `.agents/**/*.md`. Frontmatter takes `model`, with a `:level` suffix, and `thinking`. Competing packages
(`pi-sub-agent`, `mjakl/pi-subagent`, `tintinweb/pi-subagents`) exist, and their formats were not checked.

Pi does have **virtual models** (`docs/virtual-models.md`): *"A virtual model is a selectable model that
picks a physical model for each request"*, registered from an extension. That is session-level routing,
not a per-skill model, but it is the only Pi mechanism that chooses between models.

## What loadout does today

- **Skill frontmatter is copied verbatim.** A skill with `model: haiku` renders that line into
  `.claude/skills`, `.factory/skills` and `.agents/skills`, which serves Codex, OpenCode and Pi.
  Measured on `4e1768f`. Only Claude reads it. OpenCode's docs ask for custom keys to go under
  `metadata` instead.
- **Subagents are not managed.** D-02 leaves `.claude/agents/` and its counterparts untouched, and
  [droid.md](droid.md) records custom droids as *"not Loadout slices"*.
