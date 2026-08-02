### Commit Granularity

When the user asks you to commit, aim for a clean final history rather than maximum granularity:

- **Small checkpoint commits while working are fine** — they're useful for recovery and incremental review.
- **Multiple commits are fine** when each represents a logical, self-contained change (e.g. "refactor X" + "add feature Y on top").
- **Avoid leaving "half a feature" in history.** A commit that only makes sense in conjunction with the next one (WIP, "part 1 of N", broken intermediate state) is a checkpoint, not a history entry.
- **Look back as soon as a sub-feature is done** — don't wait for the whole task to wrap up. The moment a sub-feature is complete, combine its initial development, the fixes that went on top, its tests, and its docs into one commit. Workflows that emit many small commits (e.g. superhuman, gsd) make this especially important; most of those commits are checkpoints, not history entries.
- When a task wraps up, proactively suggest squashing checkpoint commits into the smallest set that each stand on their own. Don't rewrite history without explicit approval — propose the squash plan and let the user confirm. The `squash-commits` skill automates this over the unpushed range.

### Commit Message Format

Use only these three types — no others, no scopes:

- **`feat`** — something was added that is noticeable by the user
- **`fix`** — something was not working correctly and was fixed
- **`chore`** — everything else (refactoring, fixing tests, internal changes, etc.)

Do **not** use parentheses/scopes: write `feat: add login button`, not `feat(ui): add login button`.

**Body**: keep it short or omit it entirely. The body identifies *what* was done, not *why* or *how*. No essays, no implications, no test counts, no rationale. Max 4 sentences — and don't pad to reach 4. One sentence or no body at all is usually right.