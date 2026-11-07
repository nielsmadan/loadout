# Release verification

The checks a release candidate gets outside CI: a clean-machine install on macOS and a pass on
Linux. CI and `.github/workflows/prerelease.yml` cover packaging; see
[releasing](../../releasing.md).

## Ground rules

- **Build from the candidate commit.** Copy it with `git archive`, run `uv build` on the copy and
  install that wheel. Record the commit from `git log -1`, not from notes.
- **Probe in a scratch home.** Set `HOME` and `XDG_CONFIG_HOME`, `XDG_STATE_HOME`,
  `XDG_DATA_HOME` and `XDG_CACHE_HOME` to folders under it, and unset `CLAUDE_CONFIG_DIR`,
  `CODEX_HOME`, `FACTORY_HOME_OVERRIDE`, `PI_CODING_AGENT_DIR`, `OPENCODE_CONFIG`,
  `OPENCODE_CONFIG_DIR` and `OPENCODE_CONFIG_CONTENT`. Never run a probe against real harness
  homes.
- **A pass proves nothing until the check fired.** Read the skip counts as well as the pass
  counts: the fish test skips without fish, and the adapter tests skip without node.

## macOS: a clean VM

Use [Tart](https://tart.run). Clone `ghcr.io/cirruslabs/macos-sequoia-base:latest`; when the image
is already local, clone it rather than pulling it again. Start a VM only while
`sysctl -n kern.memorystatus_vm_pressure_level` reads 1. Share the wheel read-only.

| Step | Pass when |
| --- | --- |
| `uv tool install` the wheel in the guest | `loadout --version` prints the candidate version |
| Install the five harness CLIs and run each once, so each writes its own defaults | each harness's config folder exists |
| `loadout init global`, then `sync` and `check` | init converts, `check` exits 0, and each harness starts and loads the rendered config |
| `init project` in a sample repository, `integrate git-hooks install`, then one commit touching sources and one staging a generated file | the first commit passes; the second is refused with exit 1 and the documented advice |
| `integrate completions install` (zsh) and `integrate skill install` | completion works in a new shell; the skill is in each harness's skills folder |
| `uv tool uninstall loadout` | the command is gone; the machine config and sources are intact |

Delete the VM afterwards if it holds a harness login.

## Linux: a container

Use a current Debian or Ubuntu image with uv, and mount the wheel and the candidate tree
read-only.

| Step | Pass when |
| --- | --- |
| `uv tool install` the wheel | `loadout --version` prints the candidate version |
| `just check`, `just test-integration` and `just test-migration` on a copy of the tree | all pass, with every skip named |
| The init corpus ([init-corpus-flow](../init-corpus-flow.md)) | every setup converts |
| The symlinked-config and worktree layouts, plus a symlink loop | the same results as on macOS; the loop is refused with the documented message |
| Each harness CLI that installs without a login, run once, then `init global`, `sync` and `check` | init converts and `check` exits 0; note any harness that could not be installed |

## After the release

In a fresh VM clone, run `brew install nielsmadan/tap/loadout`, then `init global`, `sync` and
`check` as above.
