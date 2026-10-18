# Releasing Loadout

Loadout's release flow is adapted from Splashdown. `just release` runs
[curtains](https://github.com/nielsmadan/curtaincall), which prepares a version and pushes its
tag. GitHub Actions tests the package, publishes a GitHub Release and updates
`Formula/loadout.rb` in `nielsmadan/homebrew-tap`. PyPI publication is not part of this flow:
the PyPI name `loadout` belongs to another project.

## One-time GitHub setup

Create a fine-grained personal access token with `nielsmadan/homebrew-tap` as its selected
repository and **Contents: Read and write** permission. In the Loadout repository, open
[Settings → Secrets and variables → Actions](https://github.com/nielsmadan/loadout/settings/secrets/actions)
and add a repository secret named `HOMEBREW_TAP_TOKEN` with that value.

The secret is stored on GitHub. A secret configured on Splashdown is not automatically
available to Loadout. The workflow's automatic `GITHUB_TOKEN` handles Loadout's own release;
the separate tap token permits updating the tap repository. Rotate the token there when it
expires. Do not store it in the checkout or pass it to `just release`.

The workflow creates the formula on the first release. A placeholder formula is not needed.
The tap repository itself must exist and allow the token to push to `main`.

## Preview and release

Install curtains first with `brew install nielsmadan/tap/curtaincall` (see
[curtaincall](https://github.com/nielsmadan/curtaincall)). Use a clean `main` checkout with
complete Git history. Every `vMAJOR.MINOR.PATCH` tag, not just the latest, must exist both
locally and on origin and point to the same commit. Curtains never fetches: the fetched
`origin/main` must be an ancestor of `HEAD`, so fetch deliberately before releasing. Python
3.13+, Git 2.45+, Just and uv/uvx are required. The release runs on Git alone and needs no
GitHub CLI or API access. Run a release only when publication is intended.

```sh
just release --dry-run
just release
just release patch
just release 0.1.0
```

The dry run inspects Git state and prints the proposal without changing files. It skips the
tool check and every policy command, including the checks and the git-cliff version suggestion,
so its proposal is curtains' built-in conventional-commit estimate. A normal release runs
`just check` and `just build-docs`, then shows the proposed version, files and publication
steps. Enter `y` to proceed, another version or bump to revise the proposal, or Enter to cancel.
`--yes` explicitly bypasses the prompt for automation.

The first release defaults to `0.1.0`. Later proposals use git-cliff: `feat` produces a minor
bump, `fix` a patch, and breaking changes a minor bump while the version is `0.x`.

After confirmation, curtains runs `scripts/prepare_release.py` to update both version fields,
refreshes `uv.lock`, generates `CHANGELOG.md`, commits those files, creates an annotated
`vVERSION` tag and atomically pushes the branch and that tag. Existing local commits are
included and counted in the preview. The command returns once the push succeeds and prints the
workflow and release URLs; publication then runs asynchronously on GitHub. A successful command
confirms the push, not the publication.

`CHANGELOG.md` is generated from commit history through `cliff.toml`; do not edit it by hand.
Use `just changelog` to regenerate it. Update the lockfile before committing the version.

## Packaging is checked before a tag exists

`.github/workflows/prerelease.yml` runs the packaging steps against `main`, pull requests, and
on demand. It builds the wheel, exercises it with the installation checker, renders the
Homebrew formula, installs and tests it through a temporary tap, and generates release notes —
the same work the tagged `package` job does.

It differs in one way. The formula's `url` points at a tag archive that does not exist yet, so
the workflow builds an equivalent archive from `HEAD` with `git archive --prefix=`, which is
the shape GitHub serves for a tag, and rewrites the rendered formula's `url` to that file.
Nothing else is substituted.

This exists because the `package` job only runs on a tag push. Releases 0.9.0 through 0.9.2
each failed inside it — a stale installation checker, a formula test colliding with Homebrew's
`HOME`, and changelog generation calling the GitHub API — and every failure spent a public
version number. Those three are the failures this workflow would have caught on `main`.

## What GitHub verifies and publishes

1. Run lint, formatting, strict type checks, the full test suite with Node available, and a
   strict docs build on Linux. The same CI workflow checks pull requests and `main`.
2. On macOS, verify the tag matches both version fields and the lockfile, then build the
   source archive and wheel. Exercise the installed wheel using the installation checker.
3. Calculate the tagged source archive's SHA-256 and render a Homebrew formula. Install that
   formula through a temporary tap and run its functional initialization and sync test.
4. Publish the tested wheel and source archive as GitHub Release assets, with generated notes.
5. Copy the tested formula into the real tap, creating or updating it in a separate job.

The formula installs Python 3.13 and includes runtime dependency resources from `uv.lock`.
The generator follows runtime dependencies only, including shared transitive dependencies.
Ambiguous or conditional lock entries stop generation for explicit handling. This avoids
resolving dependencies from the unrelated PyPI project with the same name.

After the first successful release, users can install with:

```sh
brew install nielsmadan/tap/loadout
```

For source installations, use the GitHub URL in the getting-started guide. A tagged source
can be selected explicitly:

```sh
uv tool install --python 3.13 git+https://github.com/nielsmadan/loadout.git@v0.1.0
```

## Maintenance and recovery

`scripts/release.json` is the curtains release policy. `scripts/prepare_release.py` maintains
the versions, and `scripts/homebrew.py` renders `scripts/loadout.rb.in` using the lockfile.
The template and lockfile are the formula source; subsequent releases replace the tap copy.
Update the template's Python dependency when changing the supported Homebrew runtime.

Use `just build` to check packaging locally. Version preparation is tested in
`tests/test_release_prepare.py`, and `tests/test_release_config.py` checks that the policy's
checks name real Justfile recipes; formula generation is covered by `tests/test_homebrew.py`.

A failed preparation or push leaves local changes, commits and tags available for inspection;
curtains does not automatically resume. A failed GitHub workflow leaves the remote tag in place.
Fix the cause, then use **Re-run failed jobs** on that workflow. If the tap update fails after
publication, rerun only its failed job; the GitHub release already exists. Do not rerun the
successful publication job or replace a public tag. Changes that require new source code need a
new release tag.

GitHub retains build outputs as workflow artifacts. Routine verification results stay in
the workflow logs and conversation, rather than dated reports in the repository.
