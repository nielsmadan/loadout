# 0032 — A file holding a secret is never diffed

**Status:** accepted (2026-10-16, ruling D-09). Supersedes the redaction clause of D-07 in
[0030](0030-adoption-rulings-recorded.md), which is left as written: init still adopts a secret
as it is, names the file and writes it owner-only.

## Context

Every diff loadout prints replaced each secret it recognised with `<redacted>`. Review rounds 4 to
6 kept finding redaction that either printed part of a value (a value running past where its
span ended, a spelling the span missed) or hid a real change (a line that differed only inside a
redacted value showed as unchanged, and `--force` then overwrote the real token). Each fix added a
rule to the span machinery, and the next round found the next gap.

## Decision

- **A file either side of which holds a secret is not diffed.** `diffs.diff_lines` asks the
  credential check about both texts first; when either holds a secret it returns one line instead
  of the diff: the file, how many lines differ, and why it counts as a secret, never the value.
- **Every printed diff goes through that function**: `check`, the sync abort and its
  `sync-abort.diff`, the init preview and `template sync`.
- **The check reads the whole text**, past init's 1 MiB scan cap and past a NUL, since the diff
  would have shown all of it.
- **Detection only decides whether a file holds a secret.** It no longer places spans, so the
  machinery that did (redaction, the carried values, the escape and line mappings) is gone.

## Consequences

- No value is printed, so a gap in detection's spans cannot leak one; a gap in detection itself
  still shows the diff in full, as before.
- No change is hidden: the count includes a change inside the secret, so a withheld file never
  reads as unchanged. The reader compares the file on disk with the source directly.
- A file that looks like it holds a secret but does not loses its diff, not its count.
