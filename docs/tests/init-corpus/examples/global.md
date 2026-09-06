# Small global migration example

These excerpts come from the executed original synthetic-global fixture in the
[2026-09-06 result](../runs/2026-09-06-migration.json), whose `example` object contains all
before/source/after documents. The fixture is authored test data, not an upstream extract;
its four harness destinations were beneath a disposable fake HOME. No program below was run.

## Before

The original `.claude/settings.json` was one line, with a trailing newline:

```json
{"model":null,"permissions":{"allow":["Bash(echo:*)","Read(src/**)"],"deny":[]},"hooks":{},"enabledPlugins":{},"env":{}}
```

## Serialized source

Migration split owned categories. `loadout/permissions/native/claude/settings.json` contained:

```json
{
  "permissions": {
    "allow": [
      "Bash(echo:*)",
      "Read(src/**)"
    ],
    "deny": []
  }
}
```

Its `loadout/artifacts.toml` record retained the original top-level order and separate settings,
permissions, hooks and plugin contributor paths. The same index recorded the inert skill tree:

```toml
[[artifact]]
agents = ["claude"]
destination = "${CLAUDE_CONFIG_DIR:-~/.claude}/skills"
format = "tree"
category = "skills"
source = "skills/native/claude/skills"

[artifact.modes]
"probe/SKILL.md" = 420
"probe/run.sh" = 493
```

Those decimal modes are 0644 and 0755. They are authored metadata, not machine state in a
generated file. Git-only source reconstruction re-emitted the executable support file.

## After, including Git-only reconstruction

The complete Claude settings document retained all owned values and order, while adopting
canonical JSON formatting and the native document's 0600 mode:

```json
{
  "model": null,
  "permissions": {
    "allow": [
      "Bash(echo:*)",
      "Read(src/**)"
    ],
    "deny": []
  },
  "hooks": {},
  "enabledPlugins": {},
  "env": {}
}
```

Init/check/sync/repeated init/staged check and fresh Git-source reconstruction all exited 0.
The fixture's 10 outputs across all four harnesses were compared, not just this excerpt.
Subsequent source skill addition, edit, rename and last-file deletion also passed.
