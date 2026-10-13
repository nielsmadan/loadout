---
description: Reviews a diff for defects before it is committed.
model:
  claude: sonnet
  codex: gpt-5.5
  opencode: anthropic/claude-sonnet-5-5
effort: max
claude:
  tools: Read, Grep, Glob
---

Review the staged diff. Report each defect with its file and line.

::: codex
Run `git diff --staged` yourself; no diff is passed in.
:::
