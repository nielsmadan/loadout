# Init migration CLI QA

Run this after changing onboarding, checkpoint/recovery behavior, source routing or bundled
skill installation. It exercises the current checkout through `python -m loadout`, using
disposable repositories and the existing fake-home fixtures in
[conftest.py](../../../tests/conftest.py). It never installs into real agent destinations.

The executable procedure is [manual_init_qa.py](../../../tests/manual_init_qa.py). Its filename
keeps it outside routine pytest discovery; invoke it explicitly. It records command arguments,
stdout, stderr, exit status, package location, revision, production-source fingerprint and
driver fingerprint. Successful commands also require assertions about resulting files and Git.

## Run

Prerequisites: the checkout's development environment, Python 3.13+, Git, and a POSIX PTY for
interactive confirmation. No network or credentials are needed for these synthetic CLI cases.
Run from the repository root. The test fixtures supply local Git identities and isolate HOME
and harness relocation variables; do not change the invoking shell's HOME or real agent config.

```sh
qa_evidence=$(mktemp -d -t loadout-init-qa)
LOADOUT_QA_REPORT_DIR="$qa_evidence" uv run pytest -q tests/manual_init_qa.py
printf '%s\n' "$qa_evidence"
```

Use a fresh evidence directory for each execution. Read the complete pytest summary and every
failure, not only successful CLI output. Preserve failed JSON records when rerunning. Copy
useful synthetic records beside a dated run before temporary fixture directories disappear.
The records identify protected recovery journals by path but do not copy their private contents.

## Expected behavior

| Case | Expected result |
|---|---|
| Empty project, four explicit harnesses | Read-only preview; noninteractive approval required; every category ready; empty instruction outputs absent; repeat leaves HEAD/index unchanged |
| Populated project with partial unrelated staging | Original config survives in source; generated paths leave the index; unrelated staged/unstaged notes stay distinct; source edits sync and output edits refuse |
| Global source and bundled skill lifecycle | Manifest registration points at the source; Pi runtime version survives sync; all four source-managed skill copies install, repeat safely and uninstall |
| Conflicting global copies | Preview and `--yes` refuse without mutation; explicit source selection determines deployed content |
| None/frontend/backend starters | Optional templates are vendored with provenance, render their instructions and pass staged validation |
| PTY decline and SIGINT at confirmation | Actual pending prompt appears; either cancellation leaves source and Git unchanged |
| Held failing pre-commit hook | CLI is observed pending; failed checkpoint retains HEAD; supported recovery and retry succeed |
| Post-commit concurrent source change | Successful checkpoint survives interruption; foreign change is preserved; removing the fixture fault allows resume without another checkpoint |

Faults are fixture-owned Git hooks, not changes to product code. The pre-commit hook is bounded
and explicitly released; the post-commit hook creates a known competing source file. Tests stop
their own child processes and remove the injected hooks on successful completion. Failed fixture
state is retained by pytest for inspection; do not reset or clean the development checkout.

## Complementary evidence

The [public corpus procedure](../init-corpus/README.md) covers pinned third-party inputs and
source-only reconstruction. Routine regression checks cover additional ownership, privacy,
symlink, alternate-index and hook-event variants. Label that evidence separately from direct
CLI exploration when completing the scenario matrix.

Actual agent-host skill loading and natural-language triggering require a separate bounded
host session. CLI installation and wheel-resource checks do not establish those behaviors.
Record host/package identity, prompt, observable tool activity and result; leave unsupported or
unsafe-to-isolate hosts untested rather than extrapolating from one host.
The driver also prepares a source-managed project skill and an empty disposable Claude config
directory. Its JSON identifies those paths; that preparation is not a host invocation result.

## Recorded runs

- [2026-09-06 first execution](runs/2026-09-06-0134-first.md): three driver assumptions failed;
  original evidence retained.
- [2026-09-06 corrected-driver retest](runs/2026-09-06-0136-retest.md): 12 cases passed;
  includes the completed partial-coverage matrix and isolated-host limitation.
