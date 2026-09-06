# Init compatibility corpus

**2026-09-06 correction:** this file preserves the original scaffold-era experiment. Its commands
and success criteria are historical; use the maintained [migration procedure](init-corpus/README.md)
for a new run. Global tests must isolate HOME and all harness relocation variables. The project
filename is `loadout/config.toml`. Exit 0 after scaffolding did not demonstrate adoption.
The retained OpenCode checkout later had another HEAD; the new run explicitly uses the table's
pinned object. That does not establish which revision the old run actually used.

This is a manual compatibility test for `loadout init`. It answers two questions:

1. What does project initialization do to repositories that already contain agent setup?
2. What does global initialization do to repositories used as a person's cross-project agent
   configuration?

The corpus is purposive, not representative. It contains awkward shapes that initialization
needs to handle safely: existing instructions and settings, several harnesses, nested files,
shared skill directories, symlinks and installer scripts. It cannot establish how common any
shape is.

The baseline from 2026-09-05 is in
[init-corpus-results-2026-09-05.md](init-corpus-results-2026-09-05.md).

## Safety boundary

Run the test only against disposable clones. Never point global initialization at the real
machine config or run sync against the real harness directories.

- Clone with HTTPS. The repositories are public; SSH credentials are not required.
- Put every clone, evidence file and fake machine config below one temporary directory.
- Set `XDG_CONFIG_HOME` separately for every global case.
- Run `uv run loadout ...` from the loadout checkout, using `--root` or `--source` to name the
  clone. Running `uv` from a sampled repository can select that repository's Python project.
- Read the complete command output and record the exit code. Expected failures must not be
  hidden with `|| true`.
- Do not run installers from the sampled repositories. Inspect them as evidence of deployment
  topology only.

## Pinned corpus

Use the pinned commits when comparing a Loadout change with the baseline. Refreshing to newer
upstream commits is a separate corpus update and should produce a new dated result file.

### Project cases

| repository | commit | `--harness` arguments | shape exercised |
|---|---|---|---|
| [`semantic-release/gitlab`](https://github.com/semantic-release/gitlab) | `63f73173c7b181c13343f174ee7628e12d3551df` | `codex` | root agent instructions |
| [`psf/pypistats.org`](https://github.com/psf/pypistats.org) | `6ed2ed7fce0487ae6c0b58265c49edc1c2621721` | `claude` | existing Claude setup |
| [`cpisciotta/xcbeautify`](https://github.com/cpisciotta/xcbeautify) | `513e4b12c3f6c965d1d3b66bd5cd9d635f03112d` | `claude`, `codex` | `CLAUDE.md` symlinked to `AGENTS.md` |
| [`CodelyTV/agent-harness`](https://github.com/CodelyTV/agent-harness) | `e600f553a429592d19875b00584716d231ca8bd5` | `claude`, `codex`, `opencode` | shared `.agents/skills` reached by harness symlinks |
| [`Integralist/agent-skills`](https://github.com/Integralist/agent-skills) | `ed3462c8d284e81481f300c4808068aff4117861` | `claude`, `pi` | global-style, multi-harness source used as a project edge case |
| [`ChrisWiles/claude-code-showcase`](https://github.com/ChrisWiles/claude-code-showcase) | `a95518f0cb67e86230119da40429169bc4c35a6f` | `claude` | broad Claude project configuration |
| [`modelcontextprotocol/servers`](https://github.com/modelcontextprotocol/servers) | `d73f99efbfd40c3aa1b61e88728b3d49fb52608f` | `claude` | large repository with project instructions |
| [`anomalyco/opencode`](https://github.com/anomalyco/opencode) | `5cf9f517cfec3ef68d3e68a12a6a4b3163947f44` | `opencode` | the OpenCode repository's own setup |
| [`getsentry/sentry`](https://github.com/getsentry/sentry) | `a7b4a8a2a78a666418e527d812e81528147789da` | `claude`, `codex` | destructive-adoption stress case with populated permissions |

### Global cases

| repository | commit | shape exercised |
|---|---|---|
| [`Integralist/agent-skills`](https://github.com/Integralist/agent-skills) | `ed3462c8d284e81481f300c4808068aff4117861` | one source for Claude, Pi, OpenCode and generic agent files; shared skill symlinks |
| [`dwmkerr/dotfiles`](https://github.com/dwmkerr/dotfiles) | `f9c841a67478f4c07a006afdfd6e49f1cc65b109` | Claude, Codex and OpenCode files deployed from a wider dotfiles repository |
| [`Dbochman/dotfiles`](https://github.com/Dbochman/dotfiles) | `f72c03a8c9d262119634f0af370d86437953ef0b` | Claude and Codex instructions, settings, rules, hooks and skills with an install/sync script |
| [`archibate/dotfiles-claude`](https://github.com/archibate/dotfiles-claude) | `3cd0d62c58c5cbdf3c8493cab0fe2c807af4e48c` | repository intended to live directly at `~/.claude` |
| [`datasci-iopsy/anaiis-dotfiles`](https://github.com/datasci-iopsy/anaiis-dotfiles) | `2f5d1487e8704eed299233aaef57e4f38d17cb1e` | canonical dotfiles checkout symlinked into `~/.claude` |

## Prepare the clones

From the Loadout checkout:

```sh
CORPUS_ROOT=$(mktemp -d "${TMPDIR:-/tmp}/loadout-init-corpus.XXXXXX")
EVIDENCE_ROOT="$CORPUS_ROOT/evidence"
MACHINE_CONFIG_ROOT="$CORPUS_ROOT/machine"
GH_CONFIG_DIR="$CORPUS_ROOT/gh-config"
export CORPUS_ROOT EVIDENCE_ROOT MACHINE_CONFIG_ROOT GH_CONFIG_DIR
mkdir -p "$EVIDENCE_ROOT" "$MACHINE_CONFIG_ROOT" "$GH_CONFIG_DIR"
gh config set git_protocol https
```

For each table row, clone and detach at the pinned commit:

```sh
gh repo clone OWNER/REPOSITORY "$CORPUS_ROOT/CASE" -- --filter=blob:none
git -C "$CORPUS_ROOT/CASE" checkout --detach COMMIT
git -C "$CORPUS_ROOT/CASE" status --short
git -C "$CORPUS_ROOT/CASE" rev-parse HEAD
```

The initial status must be empty and the printed commit must equal the table. Store the full
output of later commands below `EVIDENCE_ROOT`; do not rely on terminal scrollback.

For a global case, also establish whether it already contains a Loadout manifest. Use two
complete tree listings and record both the total path count and every `loadout.toml` match:

```sh
git -C "$CORPUS_ROOT/CASE" ls-files >"$EVIDENCE_ROOT/CASE-ls-files.txt"
git -C "$CORPUS_ROOT/CASE" ls-tree -r --name-only HEAD >"$EVIDENCE_ROOT/CASE-ls-tree.txt"
wc -l "$EVIDENCE_ROOT/CASE-ls-files.txt" "$EVIDENCE_ROOT/CASE-ls-tree.txt"
awk '$0 == "loadout.toml" || $0 ~ /\/loadout\.toml$/' \
  "$EVIDENCE_ROOT/CASE-ls-files.txt" "$EVIDENCE_ROOT/CASE-ls-tree.txt"
```

An empty match is meaningful only after both complete listings have been produced and their
counts agree.

## Project procedure

Run these commands once per project case, substituting all of the row's harness arguments:

```sh
repo="$CORPUS_ROOT/CASE"

uv run loadout init --root "$repo" \
  --harness HARNESS1 --harness HARNESS2 \
  >"$EVIDENCE_ROOT/CASE-init.txt" 2>&1
init_exit=$?
cat "$EVIDENCE_ROOT/CASE-init.txt"
printf 'init_exit=%s\n' "$init_exit"

git -C "$repo" status --short
git -C "$repo" diff
find "$repo/loadout" -type f -print -exec wc -c {} \;

uv run loadout check --root "$repo" \
  >"$EVIDENCE_ROOT/CASE-check-before.txt" 2>&1
check_before_exit=$?
cat "$EVIDENCE_ROOT/CASE-check-before.txt"
printf 'check_before_exit=%s\n' "$check_before_exit"

uv run loadout sync --root "$repo" \
  >"$EVIDENCE_ROOT/CASE-sync.txt" 2>&1
sync_exit=$?
cat "$EVIDENCE_ROOT/CASE-sync.txt"
printf 'sync_exit=%s\n' "$sync_exit"

uv run loadout check --root "$repo" \
  >"$EVIDENCE_ROOT/CASE-check-after.txt" 2>&1
check_after_exit=$?
cat "$EVIDENCE_ROOT/CASE-check-after.txt"
printf 'check_after_exit=%s\n' "$check_after_exit"

git -C "$repo" status --short
git -C "$repo" diff
```

Inspect all added and changed files. In particular, verify:

- the scaffold is minimal and contains no imported content;
- `.gitignore` covers every generated path and the personal source;
- warnings name any existing root instruction file;
- symlinks, nested instruction files and existing settings are either preserved or visibly
  changed;
- the first sync's warning and diff make any adoption loss visible;
- the final check really ran and returned the recorded exit code.

For Sentry, count the populated Claude permission list before initialization and after sync,
and retain the complete JSON diff. This is the destructive-adoption sentinel: it should reveal
whether an empty Loadout source would replace existing owned permission fields.

## Global procedure

Run these commands once per global case:

```sh
repo="$CORPUS_ROOT/CASE"
case_config="$MACHINE_CONFIG_ROOT/CASE"

XDG_CONFIG_HOME="$case_config" uv run loadout init --global --source "$repo" \
  >"$EVIDENCE_ROOT/CASE-global-init.txt" 2>&1
init_exit=$?
cat "$EVIDENCE_ROOT/CASE-global-init.txt"
printf 'init_exit=%s\n' "$init_exit"

git -C "$repo" status --short
git -C "$repo" diff --exit-code
tracked_diff_exit=$?
printf 'tracked_diff_exit=%s\n' "$tracked_diff_exit"
find "$repo/loadout" -maxdepth 2 -print -exec sh -c \
  'for path do if [ -f "$path" ]; then wc -c "$path"; sed -n "1,240p" "$path"; fi; done' \
  sh {} +
sed -n '1,240p' "$case_config/loadout/config.toml"

XDG_CONFIG_HOME="$case_config" uv run loadout check --global \
  >"$EVIDENCE_ROOT/CASE-global-check.txt" 2>&1
check_exit=$?
cat "$EVIDENCE_ROOT/CASE-global-check.txt"
printf 'check_exit=%s\n' "$check_exit"

XDG_CONFIG_HOME="$case_config" uv run loadout sync --global \
  >"$EVIDENCE_ROOT/CASE-global-sync.txt" 2>&1
sync_exit=$?
cat "$EVIDENCE_ROOT/CASE-global-sync.txt"
printf 'sync_exit=%s\n' "$sync_exit"

XDG_CONFIG_HOME="$case_config" uv run loadout init --global --source "$repo" \
  >"$EVIDENCE_ROOT/CASE-global-init-again.txt" 2>&1
init_again_exit=$?
cat "$EVIDENCE_ROOT/CASE-global-init-again.txt"
printf 'init_again_exit=%s\n' "$init_again_exit"

git -C "$repo" status --short
git -C "$repo" diff
```

The tracked diff is the safety check. The untracked scaffold is expected, but initialization
must not rewrite the sampled repository's existing config. Read its installer and enumerate
its tracked symlinks with `git ls-files -s`; record what a migration would have to understand:

- which files are canonical sources and which are generated, copied or symlinked;
- which harnesses are configured;
- instructions, settings, permissions, MCP servers, hooks, plugins and skills;
- nested or conditional files;
- destination paths and environment-dependent relocations;
- content Loadout cannot currently represent.

## Recording a result

A dated result should include:

- Loadout commit and test date;
- every upstream repository and exact commit;
- per-case exit codes for init, check, sync and the final check or repeated init;
- tracked and untracked changes after each mutation;
- corpus-wide invariant results, not just selected examples;
- preservation or loss in existing destination files;
- observations that require migration judgment;
- environmental failures separately from product behavior;
- a conclusion about what mechanical init can safely promise.

Keep raw evidence outside the repository. The checked-in report should contain enough exact
commands, commits and outcomes to reproduce the finding without committing third-party files.
