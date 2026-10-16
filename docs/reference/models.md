# Model selection: skills and subagents

Where each harness lets a model be pinned below the session level, on a skill or on a subagent, and
what loadout does with it.

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

## Effort values and identity

**Verified 2026-10-13** against source or binaries; each line below is quoted from it.

- **Claude** (2.1.296 docs and binary): `effort` takes *"`low`, `medium`, `high`, `xhigh`, `max`;
  available levels depend on the model"*; the binary also accepts an integer
  (`Valid options: ${Ia.join(", ")} or an integer`). Identity is `name`: *"The filename doesn’t have
  to match."*
- **Codex** (`rust-v0.162.1`, `codex-rs/protocol/src/openai_models.rs`): `ReasoningEffort`'s
  variants are `None`, `Minimal`, `Low`, `Medium` (the default), `High`, `XHigh`, `Max`, `Ultra`,
  `Persistent` and `Custom(String)`, and any other non-empty string parses as `Custom`. An agent file deserializes into the same `ConfigToml`, so the
  values are the same as in `config.toml`. `ultra` resolves per model and `persistent` is sent as
  `disabled`. Identity is `name`: *"Codex identifies the custom agent by its `name` field. Matching
  the filename to the agent name is the simplest convention, but the `name` field is the source of
  truth."* `name`, `description` and `developer_instructions` are required.
- **Droid** (docs; 0.240.1 binary): `reasoningEffort` is *"`low`, `medium`, or `high` for models that
  support it"*, checked against the chosen model's own list
  (`supportedReasoningEfforts.includes(r)`). Identity is `name`, with the file name as fallback:
  `name:f.name||Ve.basename(a,".md")`.
- **OpenCode** (`v1.19.0`, `packages/core/src/v1/config/agent.ts`): no effort key. Unknown keys go
  to provider options (`if (!KNOWN_KEYS.has(key)) options[key] = value`), so a provider-specific
  `reasoningEffort` passes through, but it is not an OpenCode field. Identity is the file name
  (`configEntryNameFromPath(...)`), which a `name` key overrides. `mode` defaults to `all`
  (`mode: "all"` in `packages/opencode/src/agent/agent.ts`), which also lists the agent as a
  primary agent; only `mode: subagent` keeps it out of that set.
- **Pi** (`pi-subagents` `v0.78.0`, `src/shared/model-info.ts`): `THINKING_LEVELS = ["off",
  "minimal", "low", "medium", "high", "xhigh", "max"]`, for `thinking` or a `:level` suffix on
  `model`. Identity is `name`; a file without `name` and `description` is skipped silently.

## What loadout does

loadout renders one source agent, `agents/<name>.md` in a global source or
`loadout/agents/<name>.md` in a project, to every configured harness: the directories in
[config.md](config.md#agents). The file name is the agent's identity; a `name` field, if given,
must equal it, so renaming the file retires the old agent everywhere.

- **`model`** is `inherit` or a per-harness map. A harness the map leaves out gets no model line,
  so it inherits. A single other value is an error: no model name means the same model in all
  five. Names are not validated, except that OpenCode's needs `provider/model-id`.
- **`effort`** is one of `off`, `minimal`, `low`, `medium`, `high`, `xhigh`, `max`, written as
  Claude's `effort`, Codex's `model_reasoning_effort` (`off` becomes `none`), Droid's
  `reasoningEffort` and Pi's `thinking`. A level a harness lacks becomes the nearest lower one it
  has, with a note at `sync`; with none lower, none is written. OpenCode gets no effort, with a
  note. A per-harness map is written verbatim instead, which is how Codex's `ultra` is reached.
- **OpenCode** gets `mode: subagent` unless the agent's OpenCode block sets a mode.
- **Pi** output needs the `pi-subagents` package installed; Pi core has no subagents.
- Anything else harness-specific goes in a block keyed by the harness and is written only there.
- `description` may be plain, quoted, or a `|` or `>` block; it is written as a double-quoted
  string, so a colon or `#` in it stays text. A quoted value in a Codex block, such as
  `sandbox_mode: "true"`, stays a TOML string; a plain `true` or integer keeps its type.

**A skill's Claude-only keys go to Claude only.** `model`, `effort`, `context` and `agent` at the top
level of a skill's frontmatter are written into Claude's copy and left out of every other harness's,
without a note, since only Claude reads them. A harness's own block can still set any of them for
that harness. A skill whose `[skills.<name>]` block sets `render = false` is copied verbatim, keys
and all.

**A skill can hand its work to a source agent.** `agent: <name>` must name an agent the source
defines; any other name is an error naming the skill and the agent, Claude's built-in agent types
included. Claude's copy gets `context: fork` beside it, unless the skill sets `context` itself. Every
other harness's copy gets *"Delegate this task to the `<name>` subagent."* as the first line of its
body, below the banner, so it is read before the task. Pi needs `pi-subagents` for that line to mean
anything. A skill copied with `render = false` is not checked.
