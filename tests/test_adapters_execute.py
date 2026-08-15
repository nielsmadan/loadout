"""Run the generated adapters under node, against real hook scripts.

A generated file that parses is not a generated file that works. Everything
interesting here — that exit 2 denies, that exit 1 does **not**, that
`updatedInput` reaches the tool, that a matcher excludes — lives in emitted
JavaScript, which no Python assertion can reach.

Skipped when node is absent rather than failing, so the suite still runs on a
machine without it; `just check` on a dev machine has it.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from loadout.adapters import render_opencode_adapter, render_pi_adapter

node = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")

DOCUMENT: dict[str, Any] = {
    "PreToolUse": [
        {"matcher": "Bash", "hooks": [{"type": "command", "command": "./guard.sh"}]},
        {"matcher": "Write|Edit", "hooks": [{"type": "command", "command": "./rewrite.sh"}]},
    ],
    "PostToolUse": [{"hooks": [{"type": "command", "command": "./after.sh"}]}],
}

# Exit 2 is the ABI's block. Exit 1 is a script that broke, and Claude does not
# treat it as a decision — so neither may an adapter, or a typo in a guard
# denies every call it matches.
GUARD = """#!/bin/sh
payload=$(cat)
echo "$payload" > payload-seen.json
case "$payload" in
  *"rm -rf"*) echo "nope" >&2; exit 2 ;;
  *crash*)    echo "boom" >&2; exit 1 ;;
esac
exit 0
"""

REWRITE = """#!/bin/sh
cat > /dev/null
printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse",\
"updatedInput":{"file_path":"a.txt","content":"REWRITTEN"}}}\\n'
"""


def workspace(tmp_path: Path) -> Path:
    for name, body in (("guard.sh", GUARD), ("rewrite.sh", REWRITE)):
        script = tmp_path / name
        script.write_text(body, encoding="utf-8")
        script.chmod(0o755)
    return tmp_path


def run_node(tmp_path: Path, driver: str, *flags: str) -> dict[str, Any]:
    (tmp_path / "drive.mjs").write_text(driver, encoding="utf-8")
    result = subprocess.run(
        ["node", *flags, "drive.mjs"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, f"driver failed:\n{result.stdout}\n{result.stderr}"
    return json.loads(result.stdout)


OPENCODE_DRIVER = """\
import { LoadoutHooks } from "./plugin.mjs"
const hooks = await LoadoutHooks({ directory: "/repo" })
const out = {}

try {
  await hooks["tool.execute.before"](
    { tool: "Bash", sessionID: "s1", callID: "c1" }, { args: { command: "rm -rf /" } })
  out.exit2 = "did not deny"
} catch (e) { out.exit2 = e.message }

try {
  await hooks["tool.execute.before"](
    { tool: "Bash", sessionID: "s1", callID: "c2" }, { args: { command: "crash" } })
  out.exit1 = "allowed"
} catch (e) { out.exit1 = `denied: ${e.message}` }

try {
  await hooks["tool.execute.before"](
    { tool: "Read", sessionID: "s1", callID: "c3" }, { args: { path: "/etc/passwd" } })
  out.unmatched = "allowed"
} catch (e) { out.unmatched = `denied: ${e.message}` }

const args = { file_path: "a.txt", content: "hi" }
await hooks["tool.execute.before"]({ tool: "Write", sessionID: "s1", callID: "c4" }, { args })
out.mutated = args.content

// `Bash` is a simple matcher, so Claude compares it as an exact string. An
// unanchored regex would match "Bashful" too.
try {
  await hooks["tool.execute.before"](
    { tool: "Bashful", sessionID: "s1", callID: "c5" }, { args: { command: "rm -rf /" } })
  out.prefix = "not matched"
} catch (e) { out.prefix = `matched: ${e.message}` }

console.log(JSON.stringify(out))
"""

PI_DRIVER = """\
import factory from "./ext.ts"
const handlers = {}
factory({ on: (name, fn) => (handlers[name] = fn) })
const ctx = { cwd: "/repo", sessionManager: { getSessionFile: () => "/s/session.jsonl" } }
const out = { subscribed: Object.keys(handlers).sort() }

const denied = await handlers["tool_call"](
  { toolName: "Bash", input: { command: "rm -rf /" } }, ctx)
out.block = denied?.block ?? false
out.reason = denied?.reason ?? ""

const crashed = await handlers["tool_call"]({ toolName: "Bash", input: { command: "crash" } }, ctx)
out.exit1Blocked = crashed?.block ?? false

const event = { toolName: "Write", input: { file_path: "a.txt", content: "hi" } }
const passed = await handlers["tool_call"](event, ctx)
out.mutated = event.input.content
out.returnedOnPass = passed === undefined ? "undefined" : JSON.stringify(passed)

console.log(JSON.stringify(out))
"""


@node
def test_the_opencode_plugin_denies_mutates_and_survives_a_broken_hook(tmp_path: Path) -> None:
    root = workspace(tmp_path)
    (root / "plugin.mjs").write_text(render_opencode_adapter(DOCUMENT), encoding="utf-8")
    out = run_node(root, OPENCODE_DRIVER)

    assert out["exit2"] == "nope", "exit 2 must deny, carrying stderr as the reason"
    assert out["exit1"] == "allowed", "a hook that crashed is not a policy decision"
    assert out["unmatched"] == "allowed", "the Bash matcher must not fire for Read"
    assert out["mutated"] == "REWRITTEN", "updatedInput must reach output.args in place"
    assert out["prefix"] == "not matched", (
        "a simple matcher is compared as an exact string, not as an unanchored "
        "regex — otherwise `Bash` silently also guards `Bashful`"
    )


@node
def test_the_pi_extension_blocks_mutates_and_survives_a_broken_hook(tmp_path: Path) -> None:
    root = workspace(tmp_path)
    (root / "ext.ts").write_text(render_pi_adapter(DOCUMENT), encoding="utf-8")
    out = run_node(root, PI_DRIVER, "--experimental-strip-types", "--no-warnings")

    assert out["subscribed"] == ["tool_call", "tool_result"]
    assert out["block"] is True and out["reason"] == "nope"
    assert out["exit1Blocked"] is False, "a hook that crashed is not a policy decision"
    assert out["mutated"] == "REWRITTEN", "updatedInput must mutate event.input in place"
    assert out["returnedOnPass"] == "undefined", "a pass must return nothing, not block: false"


@node
def test_the_payload_the_hook_receives_is_abi_shaped(tmp_path: Path) -> None:
    """The script is the user's, written against Claude's contract. If the
    payload's field names drift, every hook silently reads `undefined`."""
    root = workspace(tmp_path)
    (root / "plugin.mjs").write_text(render_opencode_adapter(DOCUMENT), encoding="utf-8")
    run_node(root, OPENCODE_DRIVER)

    payload = json.loads((root / "payload-seen.json").read_text(encoding="utf-8"))
    assert payload["hook_event_name"] == "PreToolUse"
    assert payload["tool_name"] == "Bash"
    assert payload["tool_input"] == {"command": "crash"}
    assert payload["cwd"] == "/repo"
    assert payload["session_id"] == "s1"
