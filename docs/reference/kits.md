# Kits

Shared configuration for a *kind of project* — `web`, `flutter`, `react-native`, `railway` — that
a repo opts into by name.

A kit stays linked to its source: every sync picks up its current contents unless you vendor it.
The names and their old spellings, which are refused rather than aliased, are recorded in
[0053](../decisions/0053-overlays-become-stealth-sources-and-templates-become-kits.md).

## Shared catalog parts

Keep reusable kit content under a source's `kits/` directory, separate from its
active global slices:

```text
loadout/
  skills/                         # active global skills
  kits/
    nextjs.toml
    react-native.toml
    skills/review-typescript/SKILL.md
    instructions/typescript.md
    instructions/nextjs.md
    mcp/github.toml
    permissions/node.toml
```

For example, `kits/nextjs.toml` selects:

```toml
skills = ["review-typescript"]
instructions = ["typescript", "nextjs"]
mcp = ["github"]
permissions = ["node"]
```

`react-native.toml` can select the same `review-typescript`, `typescript`, `github` and `node`
parts. Lists are optional and ordered; an empty manifest is valid. Names resolve strictly
within the corresponding catalog folder, without extensions. Paths, symlinks, duplicate
references, missing parts and unknown categories are errors. Skills require `SKILL.md` and
carry their supporting files. MCP and permission fragments use the existing portable TOML
formats. A catalog manifest carries no settings, hooks or plugins; a directory kit can
carry hooks and plugins (below).

Catalog presence never activates global output. A project must select the manifest:

```sh
loadout kit add nextjs --vendored
loadout kit add react-native --vendored
loadout sync
```

Use `kit add <name> --linked` to resolve from the machine source on each render.
Shared parts are stored once. Instructions concatenate in first-reference order. Skills and
MCP server names are replaced by later kits, then by the project's own entries;
permissions use the existing deny-wins merge, with last-tier precedence for `opencode.extra`.
A part referenced again by a later kit participates at that later position, so sharing
does not change precedence. Catalog validation is reused within one render, never across runs.

Vendoring copies the manifest and only its referenced parts into `loadout/kits/`, with
one shared copy per part. A qualified name such as `company/nextjs` uses
`loadout/kits/company/nextjs.toml` and parts beneath that same `company/` directory.
Use consistent qualification for kits sharing parts. A conflicting existing shared
part is refused, not overwritten by adopting another kit.

`kit sync nextjs` previews changed files. If a changed shared part also belongs to
another selected vendored kit, it lists **all affected kits** and requires a yes
before writing. A no or EOF leaves everything unchanged and exits 0. Without a terminal it
needs `--yes`, which `kit add` and `kit sync` both take; without it the command exits 2
and changes nothing. All affected
copies must match their recorded hashes; one local edit blocks the whole update. Accepted
updates refresh every affected hash. Removing a reference keeps a part still used elsewhere;
removing a file inside an updated shared skill removes that file for every consumer. A
sibling manifest using the changed part that `kits` does not list must be added first.

The update renders a prospective copy before writing, rechecks local bytes, modes and
referenced file membership after confirmation, and rolls back applied files on a caught
write failure. It does not merge local edits. Normal `loadout sync` remains a separate step.

## What a kit is

A named bundle of the portable slices, and nothing more exotic than that: **a kit is a
source**. It sits at the bottom of the precedence chain, so anything the project itself declares
outranks it, and it combines with the slice's own operator — union with deny-wins for
permissions, and so on. No new merge rule exists for kits.

A kit need not offer every slice. A source's `use` already covers that shape, so a kit
carrying only skills works with no special case — `railway` in the live source is exactly that.
A kit offering no `permissions.toml` contributes no permission tier rather than failing.

**What a kit should not carry.** A kit describes *work*: which commands to allow, what
the agent should know about React, which tools to reach for. The native slices — settings,
plugins — describe *your* setup: your model, your effort level, your hooks into your own scripts.
Nothing is categorically excluded, because a source's `use` already decides what it contributes,
but a kit that sets `model = "opus"` is a smell rather than a feature.

**What a kit contributes.** A catalog manifest contributes **permissions, instructions,
skills and MCP server definitions**. A directory kit contributes those and also `hooks.json`
and `plugins.json`, each merged before the project's own `loadout/hooks.json` and
`loadout/plugins.json`, so the project's value wins a key both set. Each plugged into the same
resolution as it shipped, without changing anything here, which is what "a dimension rather than
a milestone" meant: see `docs/scopes.md`.

## Linked and vendored

Both are first-class. They are the same source resolved from two places, and switching between
them is not a migration.

**Linked** — the kit lives outside the project, in a source you already have. A fix to the
kit reaches every project at once.

**Vendored** — the kit is copied into `loadout/kits/` and committed, so the repo
stands alone. This is what an open-source project whose contributors do not install loadout needs.

The mode belongs to the project's entry for the kit. `kit add <name> --vendored` on a kit the
project already lists changes it to vendored, copying the tree and recording its hash;
`--linked` on a vendored one switches back once the source can still resolve the name.
Switching back deletes the copy, so it applies `kit sync`'s gate (`kits.py:has_local_edits`): a
copy that cannot be proved unmodified is refused with sync's diff and exit 1 for a directory, exit
3 for a catalog manifest, and nothing overrides it, `--yes` included. Deleting the copy and
running `--linked` again discards the edits and drops the stale `[kit.<name>]` hash. A catalog
copy is the manifest plus each part no sibling manifest in its folder uses
(`kits.py:vendored_files`, which also counts the upstream manifest's parts, so a copy whose
manifest was edited still names them); a part a sibling uses stays, as in `kit sync`, so no other
kit is affected and no `--yes` applies. The refusal names exactly those files. Deleting the
manifest alone left those parts behind, and the next `--vendored` read them and refused them as
conflicting shared parts once the source had changed one. Dropping the hash also drops the blank
line `record_hash` put before it, so a vendor and link round trip leaves `config.toml` byte for
byte as it was.
After either, `kit add` names each project skill the kit supplies with the same files
and bytes, `loadout/skills/<name> is byte for byte the skill <kit> already supplies`, with the
`rm -r` that takes the kit's copy instead. It never deletes the copy itself: the project's
skill replaces the kit's as a whole, so keeping it pins the project to this version.
Bare `kit add` lists every kit this machine can resolve, vendored copies first, then
each source's, then the bundled kits, and prompts for a number and
a mode; without a terminal it requires both as arguments. Neither prompt has a default: an empty
or unrecognised answer is a usage error (exit 2) and changes nothing.

Every `kit` command holds to sync's containment before it reads anything: a link in
`loadout/` that leads out of it is refused, and so is any link on the way to
`loadout/config.toml` or `loadout/kits/<name>`, even one that stays inside the project,
since those are the paths the commands write and replace. A kit may not be named like a
folder loadout never reads, such as `node_modules`.

Vendored kit content stays under `loadout/kits/` and is **never merged into the
project's own fragments**. If a kit's content were mixed into yours, nothing could later distinguish
kit-owned content from content you wrote, and sync would be impossible — which is the
cookiecutter failure mode, where every generated project is a fork on day one.

## Reference by name, never by path

```toml
# loadout/config.toml — the project's, committed
harnesses = ["claude", "codex"]
kits = ["web", "railway"]

[kit.web]
vendored = "sha256:9f2a1c4e…"
```

A path in a committed file is wrong for everyone who is not its author: `~/ac/kits/web`
means nothing on a colleague's machine and less in CI. `[kit.<name>]` accepts `vendored` and
nothing else, so a path cannot be written there either.

## Resolution order

The same algorithm `resolve_fragment` uses, one level up:

1. If `loadout/kits/<name>/` or `<name>.toml` exists in this project, it is **vendored**, and resolution
   stops. No machine config is read and no external source is consulted — which is precisely what
   lets a clone build without the kit repo.
2. Otherwise, search the `kits/` directory of every source the **machine's global manifest**
   declares whose `use` admits kits. Project scope carries no `[[source]]` list of its own
   and must not, per the rule above; the machine config
   ([0010](../decisions/0010-a-machine-config-locates-the-global-source.md)) is where this
   machine's paths already live. Directory and manifest forms with the same name are
   ambiguous and refused.
3. With no source match, `frontend` and `backend` resolve from the installed package's
   offline catalog. These names also work without machine configuration. A malformed configured
   machine source remains an error. Qualifying a source name never selects the package fallback.
4. No match: `kit 'x' not found. Available: …`, listing every name the project's vendored
   copies, the sources and the bundled kits offer. `kit sync`, and `--linked`
   switching a vendored copy back, look past the vendored copy and name every path they searched
   instead.
5. **More than one source match: an error listing both.** Never a silent preference — the winner would
   otherwise depend on manifest order rather than on anything the author wrote. Qualify as
   `company/web` to disambiguate.

Given `~/ac/loadout.toml` declaring `[[source]] path = "loadout"`, kits resolve from
`~/ac/loadout/kits/<name>.toml` manifests, or from a directory kit's
`~/ac/loadout/kits/<name>/` folder. Everything under a source belongs to loadout, which is what
makes "where do I edit" answerable from the path.

## Bundled kits and native projects

`frontend` and `backend` ship in the package and resolve offline. `loadout kit add
frontend --vendored` (or `backend`) vendors one with normal content-hash provenance, the same way
as any other kit, and `--linked` links one that resolves on every render. `kit add` is how a kit is applied, before or after init. A global
source can offer these kit names but cannot activate their advice machine-wide.

The packaged seeds cover frontend accessibility, responsive layouts and UI states, or backend
boundaries, authorization, failures and compatibility. Each includes empty category scaffolds.
They add no dependency installs, permissive rules, model choices or credentials. The seed is
editable source at `loadout/kits/<name>/instructions.md`; later local edits use normal
modified-copy refusal during `kit sync`.

Project instruction fragments come first, then the kit instructions, trimmed and concatenated
in kit order, so the document opens with what is specific to the project (ruling D-11).
When two of those fragments bring the same heading, `check` and `sync` print a note naming the
heading and both fragments, such as a section copied by hand into the project's instructions that
a kit now supplies; it never fails either command.
Kit permissions, skills and MCP servers enter the same project slices as directly authored
sources; project source wins within each slice. Project skills replace a same-named kit skill as a
whole, including supporting files. MCP project entries replace whole servers. Portable permission
rules retain deny-wins and project-tier precedence.

Project module configuration has no destination table. A Droid project hook that names a support
script therefore cannot carry that script through Loadout yet; keep the script at its harness path.
This is a known limitation of project module configuration.

Catalog manifests remain selected lists rather than inherited manifests:
`kit_catalog.py:_load_catalog` accepts only the four category lists and rejects `extends`.
Source symlinks remain refused.

A catalog override of `frontend` or `backend` is applied the same way, with `kit add
--vendored` (or `--linked`). A vendored clone resolves before reading machine configuration and
works independently of its original kit source.

## The content hash

Recorded when a kit is vendored, and the only question `sync` needs answered: *has this copy
been modified since it was vendored?*

- current hash **==** recorded → clean
- current hash **!=** recorded → locally modified
- **no recorded hash at all** → unverifiable; nothing can tell the user's edits from the
  source's, so `sync` refuses and `check` reports it

That third state is not hypothetical. Until 2026-08-17, `loadout harness add` (a command since
removed) rewrote `loadout/config.toml` from
the harness list alone, destroying every `[kit.<name>] vendored` block
along with `kits` and `instructions`, so repos that ran it are in it today. The message to
search for is **"has no recorded provenance"**. `kit add <name> --vendored` takes the source
wholesale and records a hash again; so does `kit sync` when the copy still matches the
source, since matching *is* proof it is unmodified.

It is a **content** hash, not a git SHA, because a kit may come from a plain directory with
no repository behind it.

The definition, stated precisely enough to reimplement. Over every content file, sorted by
relative POSIX path, sha256 absorbs:

```text
<relative path>\0<"x" if executable else "-">\0<byte length>\0<bytes>\0
```

and the digest is rendered as `sha256:<64 hex digits>`. Notes on each part:

- **Only the path *relative* to the kit root** is hashed, so the digest is
  path-independent: vendoring a kit does not change its hash, which is what lets one
  recorded value compare a copy against its upstream.
- **The byte length** pins the file boundary, so no arrangement of bytes across two files can
  collide with a different arrangement across two others.
- **The executable bit** is included; a kit carries skills, and three skills in the live
  source have executable `scripts/` files.
- **Build output is excluded** — the same directories, suffixes and names `skills.py` excludes.
  Otherwise a kit that once had a `__pycache__` in it would never compare equal to the same
  kit checked out fresh.
- The algorithm is named in the value rather than assumed, so changing it is detectable instead
  of silent.

The hash lives in `loadout/config.toml`, which is **source**, not generated output.
For a catalog manifest, the hashed files are the manifest itself and its referenced closure,
with paths relative to the catalog directory. Unselected catalog parts are excluded.
[0008](../decisions/0008-generated-files-carry-no-machine-state.md)'s prohibition on stamping a
hash governs generated files, and exists so generated content stays a pure function of the
source; a hash recorded in the source *is* the source. It carries no machine state — the digest
covers relative paths only.

## `kit sync` is refuse-and-diff

This section describes directory kits. Catalog manifests use the shared-update flow
above: provenance and ownership errors exit 3; declining the shared update exits 0 and changes
nothing.

    loadout kit add web --linked     # link it; it resolves from a source on every render
    loadout kit add web --vendored   # copy it in, record the hash
    loadout kit sync web             # update the vendored copy from its source
    loadout kit list                 # what this project uses, and how each resolves

Two kits offering one skill name resolve last-listed-wins: `kits = ["a", "b"]`
gives `b`'s copy. A skill the project itself defines beats both. Permission decisions use
deny-wins rather than this replacement rule.

`sync` resolves the upstream past the vendored copy, compares, and:

- **Vendored copy unmodified** → update it, re-record the hash, report what changed.
- **Vendored copy modified** → print the diff against the upstream and exit 1. **Change nothing.**
- **No recorded hash** → print the diff and exit 1, changing nothing, unless the copy already
  matches the source.

The gate refuses unless the copy can be proved *unmodified*, rather than refusing only when it
can be proved *modified*. Those differ exactly where there is no provenance to compare against,
and fail-open there let a command whose whole contract is refuse-rather-than-merge overwrite
local edits silently — the guarantee held while nothing had gone wrong and evaporated the moment
something had.

The refused case is deliberately not automated. The alternative is a three-way merge — keep the
version you vendored, diff upstream-then against upstream-now, apply that patch onto your copy,
leave conflict markers — which is what copier and cruft do, and is the largest single piece of
work in this design. Refuse-and-diff is never wrong, never silently mangles anything, and can be
upgraded later **without changing the recorded state**: the content hash is exactly the base a
three-way merge would need.

The cost is honest and worth stating: a kit fix that must reach twelve modified projects is
twelve manual merges. If that becomes the common case rather than the rare one, that is the
signal to build three-way.

## `check` and `sync` report divergence; they do not fail on it

A vendored copy is source, so a user editing it is not drift. `loadout check` and `loadout sync`
note a copy that no longer matches its recorded hash — or has none recorded — on stdout, as
`note: project.kits: …`, and leave their exit code alone; real drift still exits 1. The two are separate reports because the second is the *absence*
of the evidence the first is measured against: `kit_divergence` can only speak about copies
it has a base for, so a missing hash read there as "no divergence" rather than "cannot say". See [0014](../decisions/0014-a-vendored-template-is-source-not-output.md), which argues
that a vendored kit falls outside `check`'s jurisdiction by definition rather than by
exemption.

## Not built

Each with its reason, so a later reader knows whether the reason still holds.

- **Three-way merge** — deferred deliberately, as above. The hash is chosen so this stays
  possible without a state migration.
- **Fetching or versioning kit repositories.** A kit arrives on disk however you like —
  git clone, submodule, a shared drive. loadout resolves names against directories and fetches
  nothing, the same position [0010](../decisions/0010-a-machine-config-locates-the-global-source.md)
  takes on the global source.
- **Per-kit slice filtering** (`kits = [{name = "web", use = ["permissions"]}]`) —
  cheap syntactically, but it is a second place slice selection lives, and nothing yet needs it.
- **A kit listing kits** — one level, resolved eagerly. Nesting turns name resolution
  into a graph needing cycle detection, for no demonstrated benefit.
- **A command that creates a kit** — extraction produces a *source*; promoting a directory to
  a kit is `cp` plus this document.
- **Project-type detection.** loadout does not guess that a repo is a React project. Listing
  `kits = ["web"]` is a deliberate act.

## Prior art

- **copier** is the closest match — records the template and its version in the project
  (`.copier-answers.yml`), and `copier update` re-applies template changes onto a modified copy.
- **cruft** exists purely to bolt that onto **cookiecutter**, storing a template commit in
  `.cruft.json`. Its existence is the evidence that copy-in without an update path is a mistake
  people pay to fix afterwards.
- **cookiecutter** is the cautionary tale: no update path at all.
- **Go's `vendor/`** and **`git subtree`** are the same shape at a different scale — copy in, keep
  provenance, update deliberately.
