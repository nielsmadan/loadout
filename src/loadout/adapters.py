"""Generated shims that run ABI hooks on harnesses with no hooks file.

Claude and Codex read a hook document directly (`hooks.py`). OpenCode and Pi
register hooks **in code**, so loadout generates the code: a plugin for OpenCode,
an extension for Pi. Each is the fixed six-step shim of spec 4b-i Decision 3 —
subscribe, build the payload, spawn the command, read the result, apply
`updatedInput`, translate the decision.

Only the embedded document and the subscription list vary between two renderings.
The runtime is a constant, so a diff of two generated adapters shows the hooks
that changed and nothing else.
"""

from __future__ import annotations

import json
from collections.abc import Mapping as MappingABC
from dataclasses import dataclass
from typing import Any

from .hooks import adaptable_document

__all__ = [
    "OPENCODE_MAPPINGS",
    "PI_MAPPINGS",
    "AdapterMapping",
    "render_opencode_adapter",
    "render_pi_adapter",
]


@dataclass(frozen=True)
class AdapterMapping:
    """One (ABI event, harness hook) pair, with the evidence for each capability.

    `blocks` and `mutates` name the harness's expression, or are `None` where the
    hook has no such channel. A `None` is not a gap to fill later: it is a branch
    of the translation table that **cannot fire on this harness**, and the adapter
    reports at runtime rather than dropping the decision silently.

    `reaches` is spec 4b-i Decision 4's requirement — not "is this correct" but
    "what concrete input exercises it". A later reader can check whether that
    condition is still possible, which is answerable; whether the code is still
    correct is not.
    """

    event: str
    hook: str
    blocks: str | None
    mutates: str | None
    reaches: str


# `tool.execute.before`'s `input` is `{tool, sessionID, callID}` and carries no
# arguments — those live only on `output.args`, so the payload's `tool_input`
# comes from the *output* parameter. Verified against @opencode-ai/plugin@1.17.0
# `dist/index.d.ts`, whose whole surface is `(input, output) => Promise<void>`.
#
# **`permission.ask` is not used, and this corrects spec 4b-i Decision 3.** That
# decision prefers it for denial on the grounds that its
# `output.status: "ask" | "deny" | "allow"` is a lossless three-state decision.
# It is — but `Permission` (@opencode-ai/sdk@1.17.0 `types.gen.d.ts:369`) is
# `{id, type, pattern?, sessionID, messageID, callID?, title, metadata, time}`
# and carries **no tool arguments**. A `PreToolUse` hook is handed `tool_input`
# and decides from it; on `permission.ask` there is nothing to hand it. The
# channel is lossless about the *decision* and empty about the *input the
# decision is made from*, so it cannot carry an ABI hook.
OPENCODE_MAPPINGS = (
    AdapterMapping(
        event="PreToolUse",
        hook="tool.execute.before",
        blocks="throw",
        mutates="assign into output.args",
        reaches='any PreToolUse hook whose command exits 2 or returns permissionDecision: "deny"',
    ),
    AdapterMapping(
        event="PostToolUse",
        hook="tool.execute.after",
        blocks=None,
        mutates=None,
        reaches="any PostToolUse hook; the decision branches are unreachable "
        "because the tool has already run",
    ),
)

# Pi's events come from its shipped `docs/extensions.md` (2,988 lines in the
# installed 0.x build), which documents the block and mutate contracts outright:
# "Mutations to `event.input` affect the actual tool execution", "Return values
# from `tool_call` control blocking via `{block, reason?, terminate?}`".
#
# Pi documents that **no re-validation happens after mutation**, so an adapter
# applying `updatedInput` on Pi is the last check there is. Claude schema-checks
# `updatedInput` and rejects a bad one; Pi will run it.
PI_MAPPINGS = (
    AdapterMapping(
        event="PreToolUse",
        hook="tool_call",
        blocks="return {block: true, reason}",
        mutates="mutate event.input in place",
        reaches='any PreToolUse hook whose command exits 2 or returns permissionDecision: "deny"',
    ),
    AdapterMapping(
        event="PostToolUse",
        hook="tool_result",
        blocks=None,
        mutates=None,
        reaches="any PostToolUse hook; `tool_result` can patch the result but "
        "that is not what `updatedInput` means",
    ),
    AdapterMapping(
        event="UserPromptSubmit",
        hook="input",
        blocks='return {action: "handled"}',
        mutates='return {action: "transform", text}',
        reaches="a UserPromptSubmit hook returning additionalContext reaches "
        "the transform branch; one exiting 2 reaches the handled branch",
    ),
    AdapterMapping(
        event="SessionStart",
        hook="session_start",
        blocks=None,
        mutates=None,
        reaches="any SessionStart hook; additionalContext has no channel here, "
        "so that branch is unreachable on Pi",
    ),
    AdapterMapping(
        event="SessionEnd",
        hook="session_shutdown",
        blocks=None,
        mutates=None,
        reaches="any SessionEnd hook",
    ),
)

# A consequence, never a prohibition, matching `HEADER_LINES`. "do not edit" is
# an imperative, and an imperative in a generated file is a rule an agent can
# lift out of context and apply to the wrong file — which happened, from a
# global AGENTS.md onto a project one. A statement about what will happen to
# this file cannot be misread as an instruction about another.
HEADER = (
    "// Generated by loadout from hooks/*.json.\n"
    "// Edits to this file are replaced on the next sync.\n"
)

# Claude's own matcher, reproduced from the 2.1.233 binary. It is **two
# branches, and only one of them is a regex**:
#
#     if (!t || t === "*") return true
#     if (/^[a-zA-Z0-9_|]+$/.test(t))
#       return t.split("|").map(trim).filter(Boolean).…includes(e)
#     try { return new RegExp(t).test(e) } catch { warn(…); return false }
#
# So `Bash|Edit` is an **exact-string** set, not a pattern — and Claude warns
# "Hook matcher `mcp__server` matches no tool (it is compared as an exact
# string)". `mcp__*` falls to the regex branch only because `*` is outside the
# simple character class, where unanchored `.test` happens to match. Treating
# every matcher as a regex would silently widen the simple ones.
#
# **Not reproduced:** Claude also tests the pattern against two derived name
# sets — tool aliases and MCP name variants — before giving up. Those expansions
# are internal to Claude and have no equivalent on either target, so a matcher
# relying on an alias matches on Claude and not here. Recorded rather than
# guessed at.
_MATCHER = """function matches(toolName, matcher) {
  if (!matcher || matcher === "*") return true
  if (/^[a-zA-Z0-9_|]+$/.test(matcher))
    return matcher.split("|").map((s) => s.trim()).filter(Boolean).includes(toolName)
  try {
    return new RegExp(matcher).test(toolName)
  } catch {
    console.error(`loadout: invalid regex in hook matcher: ${matcher}`)
    return false
  }
}"""

# Exit 2 is the ABI's block; every other non-zero is a script that failed. The
# distinction is Claude's, not ours, which is what lets the adapter avoid the
# choice spec 4b-i worried about: it never has to guess whether a crash was a
# policy decision, because the protocol already separates them. A failed script
# is reported and does **not** block — matching Claude, and refusing to convert a
# bug in a hook into a denial of everything it matches.
_RUNTIME = """const DEFAULT_TIMEOUT_SECONDS = 60

function run(hook, payload) {
  return new Promise((resolve) => {
    const child = spawn(hook.command, { shell: true, stdio: ["pipe", "pipe", "pipe"] })
    let stdout = ""
    let stderr = ""
    let settled = false
    const finish = (result) => {
      if (settled) return
      settled = true
      clearTimeout(timer)
      resolve(result)
    }
    const timer = setTimeout(() => {
      child.kill("SIGKILL")
      finish({ status: "timeout", stdout, stderr })
    }, (hook.timeout ?? DEFAULT_TIMEOUT_SECONDS) * 1000)
    child.stdout.on("data", (d) => { stdout += d })
    child.stderr.on("data", (d) => { stderr += d })
    child.on("error", (e) => finish({ status: "failed", stdout, stderr: String(e) }))
    child.on("close", (code) => finish({ status: "closed", code, stdout, stderr }))
    child.stdin.end(JSON.stringify(payload))
  })
}

function selected(event, toolName) {
  const out = []
  for (const entry of HOOKS[event] ?? []) {
    if (toolName !== undefined && !matches(toolName, entry.matcher)) continue
    for (const hook of entry.hooks ?? []) out.push(hook)
  }
  return out
}

function unsupported(event, what) {
  console.error(`loadout: ${event} returned ${what}, which this harness cannot express`)
}

async function dispatch(event, toolName, payload) {
  const decision = { deny: false, reason: "", updatedInput: undefined, context: [] }
  for (const hook of selected(event, toolName)) {
    const result = await run(hook, payload)
    if (result.status === "timeout") {
      console.error(`loadout: hook timed out: ${hook.command}`)
      continue
    }
    if (result.status === "failed") {
      console.error(`loadout: hook could not start: ${hook.command}: ${result.stderr}`)
      continue
    }
    if (result.code === 2) {
      decision.deny = true
      decision.reason = result.stderr.trim() || `blocked by ${hook.command}`
      continue
    }
    if (result.code !== 0) {
      console.error(`loadout: hook exited ${result.code}: ${hook.command}: ${result.stderr}`)
      continue
    }
    if (!result.stdout.trim()) continue
    let parsed
    try {
      parsed = JSON.parse(result.stdout)
    } catch {
      console.error(`loadout: hook stdout is not JSON: ${hook.command}`)
      continue
    }
    const specific = parsed.hookSpecificOutput ?? {}
    if (specific.permissionDecision === "deny") {
      decision.deny = true
      decision.reason = specific.permissionDecisionReason ?? `blocked by ${hook.command}`
    }
    if (specific.permissionDecision === "allow" || specific.permissionDecision === "ask")
      unsupported(event, `permissionDecision: "${specific.permissionDecision}"`)
    if (specific.updatedInput !== undefined) decision.updatedInput = specific.updatedInput
    if (specific.additionalContext) decision.context.push(specific.additionalContext)
    if (parsed.continue === false) {
      decision.deny = true
      decision.reason = parsed.stopReason ?? decision.reason ?? "stopped by hook"
    }
  }
  return decision
}"""


def _carried(
    document: MappingABC[str, Any], mappings: tuple[AdapterMapping, ...]
) -> dict[str, Any]:
    """Only the events this harness subscribes to.

    An unmapped event left in the embedded table would sit there looking wired,
    and nothing in the file would say it never fires. The `_unmapped` comment
    says so once, in prose, and the data then holds only what runs.
    """
    known = {m.event for m in mappings}
    return {event: entries for event, entries in document.items() if event in known}


def _embed(document: MappingABC[str, Any]) -> str:
    return "const HOOKS = " + json.dumps(document, indent=2, ensure_ascii=False)


def _skipped_note(skipped: tuple[str, ...]) -> str:
    """Report unadaptable hooks in the file itself.

    There is no warning channel in the render path, and inventing one to carry a
    fact about this file would put the report somewhere the file's reader is not.
    A `prompt` hook has no command to spawn, so it is dropped; a user who never
    sees that believes a hook is wired when it is not.
    """
    if not skipped:
        return ""
    lines = "".join(f"// - {entry}\n" for entry in dict.fromkeys(skipped))
    return f"\n// Not carried — an adapter spawns a command, and these have none:\n{lines}"


def _unmapped(document: MappingABC[str, Any], mappings: tuple[AdapterMapping, ...]) -> str:
    unmapped = [e for e in document if e not in {m.event for m in mappings}]
    if not unmapped:
        return ""
    lines = "".join(f"// - {event}\n" for event in unmapped)
    return f"\n// No mapping on this harness, so these are not subscribed:\n{lines}"


_OPENCODE_SUBSCRIPTIONS = {
    "PreToolUse": """  "tool.execute.before": async (input, output) => {
    const decision = await dispatch("PreToolUse", input.tool, {
      hook_event_name: "PreToolUse",
      session_id: input.sessionID,
      cwd: directory,
      tool_name: input.tool,
      tool_input: output.args,
    })
    if (decision.updatedInput !== undefined) Object.assign(output.args, decision.updatedInput)
    if (decision.context.length) unsupported("PreToolUse", "additionalContext")
    if (decision.deny) throw new Error(decision.reason)
  },""",
    "PostToolUse": """  "tool.execute.after": async (input, output) => {
    const decision = await dispatch("PostToolUse", input.tool, {
      hook_event_name: "PostToolUse",
      session_id: input.sessionID,
      cwd: directory,
      tool_name: input.tool,
      tool_input: input.args,
      tool_response: output,
    })
    if (decision.deny) unsupported("PostToolUse", "a deny, after the tool has run")
  },""",
}


def render_opencode_adapter(document: MappingABC[str, Any]) -> str:
    """An OpenCode plugin, for `~/.config/opencode/plugins/`.

    Auto-discovery of that directory is recorded in `docs/reference/config.md`
    and confirmed against upstream's plugin documentation. The export is a named
    const returning the hooks object, which is the documented shape.

    `transcript_path` and `permission_mode` are **omitted, not defaulted**.
    Neither has a source in the plugin input, and a hook that reads
    `permission_mode` to decide how strict to be would read a fabricated
    `"default"` as fact. `undefined` is the honest answer, and spec 4b-i's open
    question 3 is answered no for OpenCode.
    """
    adaptable, skipped = adaptable_document(document)
    carried = _carried(adaptable, OPENCODE_MAPPINGS)
    body = "\n".join(
        _OPENCODE_SUBSCRIPTIONS[m.event] for m in OPENCODE_MAPPINGS if m.event in carried
    )
    return (
        f"{HEADER}{_skipped_note(skipped)}{_unmapped(adaptable, OPENCODE_MAPPINGS)}\n"
        'import { spawn } from "node:child_process"\n\n'
        f"{_embed(carried)}\n\n{_MATCHER}\n\n{_RUNTIME}\n\n"
        f"export const LoadoutHooks = async ({{ directory }}) => ({{\n{body}\n}})\n"
    )


_PI_SUBSCRIPTIONS = {
    "PreToolUse": """  pi.on("tool_call", async (event, ctx) => {
    const decision = await dispatch("PreToolUse", event.toolName, {
      hook_event_name: "PreToolUse",
      transcript_path: ctx.sessionManager.getSessionFile(),
      cwd: ctx.cwd,
      tool_name: event.toolName,
      tool_input: event.input,
    })
    if (decision.updatedInput !== undefined) Object.assign(event.input, decision.updatedInput)
    if (decision.context.length) unsupported("PreToolUse", "additionalContext")
    if (decision.deny) return { block: true, reason: decision.reason }
  })""",
    "PostToolUse": """  pi.on("tool_result", async (event, ctx) => {
    const decision = await dispatch("PostToolUse", event.toolName, {
      hook_event_name: "PostToolUse",
      transcript_path: ctx.sessionManager.getSessionFile(),
      cwd: ctx.cwd,
      tool_name: event.toolName,
      tool_input: event.input,
      tool_response: event.content,
    })
    if (decision.deny) unsupported("PostToolUse", "a deny, after the tool has run")
  })""",
    "UserPromptSubmit": """  pi.on("input", async (event, ctx) => {
    const decision = await dispatch("UserPromptSubmit", undefined, {
      hook_event_name: "UserPromptSubmit",
      transcript_path: ctx.sessionManager.getSessionFile(),
      cwd: ctx.cwd,
      prompt: event.text,
    })
    if (decision.deny) return { action: "handled" }
    if (decision.context.length)
      return { action: "transform", text: [event.text, ...decision.context].join("\\n") }
  })""",
    "SessionStart": """  pi.on("session_start", async (event, ctx) => {
    const decision = await dispatch("SessionStart", undefined, {
      hook_event_name: "SessionStart",
      transcript_path: ctx.sessionManager.getSessionFile(),
      cwd: ctx.cwd,
      source: event.reason,
    })
    if (decision.context.length) unsupported("SessionStart", "additionalContext")
  })""",
    "SessionEnd": """  pi.on("session_shutdown", async (_event, ctx) => {
    await dispatch("SessionEnd", undefined, {
      hook_event_name: "SessionEnd",
      transcript_path: ctx.sessionManager.getSessionFile(),
      cwd: ctx.cwd,
    })
  })""",
}


def render_pi_adapter(document: MappingABC[str, Any]) -> str:
    """A Pi extension, for `~/.pi/agent/extensions/`.

    Pi loads `.ts` through jiti, so no build step stands between this file and
    execution. The default export is a factory taking `ExtensionAPI`, which is
    the documented shape.

    `session_id` and `permission_mode` are **omitted**. Pi's `ctx.mode` is a run
    mode — `"tui" | "rpc" | "json" | "print"` — and mapping it onto Claude's
    `permission_mode` would hand a hook a value that looks like a permission
    posture and is not. `transcript_path` is supplied from
    `ctx.sessionManager.getSessionFile()`, which is the session's own file.
    """
    adaptable, skipped = adaptable_document(document)
    carried = _carried(adaptable, PI_MAPPINGS)
    body = "\n\n".join(_PI_SUBSCRIPTIONS[m.event] for m in PI_MAPPINGS if m.event in carried)
    return (
        f"{HEADER}{_skipped_note(skipped)}{_unmapped(adaptable, PI_MAPPINGS)}\n"
        'import { spawn } from "node:child_process"\n'
        'import type { ExtensionAPI } from "@earendil-works/pi-coding-agent"\n\n'
        f"{_embed(carried)}\n\n{_MATCHER}\n\n{_RUNTIME}\n\n"
        f"export default function (pi: ExtensionAPI) {{\n{body}\n}}\n"
    )
