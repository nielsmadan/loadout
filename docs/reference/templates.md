# Templates

Shared configuration for a *kind of project* — `web`, `flutter`, `react-native`, `railway` — that
a repo opts into by name.

## Shared catalog parts

Keep reusable template content under a source's `templates/` directory, separate from its
active global slices:

```text
loadout/
  skills/                         # active global skills
  templates/
    nextjs.toml
    react-native.toml
    skills/review-typescript/SKILL.md
    instructions/typescript.md
    instructions/nextjs.md
    mcp/github.toml
    permissions/node.toml
```

For example, `templates/nextjs.toml` selects:

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
formats. Settings, hooks and plugins remain explicit per-agent artifact sources.

Catalog presence never activates global output. A project must select the manifest:

```sh
loadout template vendor nextjs
loadout template vendor react-native
loadout sync
```

Use `template add` instead of `vendor` to resolve from the machine source on each render.
Shared parts are stored once. Instructions concatenate in first-reference order. Skills and
MCP server names are replaced by later templates, then by the project's own entries;
permissions use the existing deny-wins merge, with last-tier precedence for `opencode.extra`.
A part referenced again by a later template participates at that later position, so sharing
does not change precedence. Catalog validation is reused within one render, never across runs.

Vendoring copies the manifest and only its referenced parts into `loadout/templates/`, with
one shared copy per part. A qualified name such as `company/nextjs` uses
`loadout/templates/company/nextjs.toml` and parts beneath that same `company/` directory.
Use consistent qualification for templates sharing parts. A conflicting existing shared
part is refused, not overwritten by adopting another template.

`template sync nextjs` previews changed files. If a changed shared part also belongs to
another selected vendored template, it lists **all affected templates** and requires a yes
before writing. A no, EOF or noninteractive input leaves everything unchanged. All affected
copies must match their recorded hashes; one local edit blocks the whole update. Accepted
updates refresh every affected hash. Removing a reference keeps a part still used elsewhere;
removing a file inside an updated shared skill removes that file for every consumer. An
undeclared sibling manifest using the changed part must be declared first.

The update renders a prospective copy before writing, rechecks local bytes, modes and
referenced file membership after confirmation, and rolls back applied files on a caught
write failure. It does not merge local edits. Normal `loadout sync` remains a separate step.

## What a template is

A named bundle of the portable slices, and nothing more exotic than that: **a template is a
source**. It sits at the bottom of the precedence chain, so anything the project itself declares
outranks it, and it combines with the slice's own operator — union with deny-wins for
permissions, and so on. No new merge rule exists for templates.

A template need not offer every slice. A source's `use` already covers that shape, so a template
carrying only skills works with no special case — `railway` in the live source is exactly that.
A template offering no `permissions.toml` contributes no permission tier rather than failing.

**What a template should not carry.** A template describes *work*: which commands to allow, what
the agent should know about React, which tools to reach for. The native slices — settings,
plugins — describe *your* setup: your model, your effort level, your hooks into your own scripts.
Nothing is categorically excluded, because a source's `use` already decides what it contributes,
but a template that sets `model = "opus"` is a smell rather than a feature.

**Legacy preset scope.** Templates contribute **permissions, instructions, skills and MCP server
definitions**. Each plugged into the
same resolution as it shipped, without changing anything here, which is what "a dimension rather
than a milestone" meant: see `docs/scopes.md`.

## Declared and vendored

Both are first-class. They are the same source resolved from two places, and switching between
them is not a migration.

**Declared** — the template lives outside the project, in a source you already have. A fix to the
template reaches every project at once.

**Vendored** — the template is copied into `loadout/templates/` and committed, so the repo
stands alone. This is what an open-source project whose contributors do not install loadout needs.

Vendored template content stays under `loadout/templates/` and is **never merged into the
project's own fragments**. If a template's content were mixed into yours, nothing could later distinguish
template-owned content from content you wrote, and sync would be impossible — which is the
cookiecutter failure mode, where every generated project is a fork on day one.

## Reference by name, never by path

```toml
# loadout/config.toml — the project's, committed
harnesses = ["claude", "codex"]
templates = ["web", "railway"]

[template.web]
vendored = "sha256:9f2a1c4e…"
```

A path in a committed file is wrong for everyone who is not its author: `~/ac/templates/web`
means nothing on a colleague's machine and less in CI. `[template.<name>]` accepts `vendored` and
nothing else, so a path cannot be written there either.

## Resolution order

The same algorithm `resolve_fragment` uses, one level up:

1. If `loadout/templates/<name>/` or `<name>.toml` exists in this project, it is **vendored**, and resolution
   stops. No machine config is read and no external source is consulted — which is precisely what
   lets a clone build without the template repo.
2. Otherwise, search the `templates/` directory of every source the **machine's global manifest**
   declares whose `use` admits templates. Project scope carries no `[[source]]` list of its own
   and must not, per the rule above; the machine config
   ([0010](../decisions/0010-a-machine-config-locates-the-global-source.md)) is where this
   machine's paths already live.
   Artifacts-only global manifests without a source list offer their own `loadout/templates/`
   catalog automatically, without enabling any global slice. Directory and manifest forms
   with the same name are ambiguous and refused.
3. With no declared-source match, `frontend` and `backend` resolve from the installed package's
   offline catalog. These names also work without machine configuration. A malformed configured
   machine source remains an error. Qualifying a source name never selects the package fallback.
4. No match: an error naming every place searched, the vendored path included.
5. **More than one declared-source match: an error listing both.** Never a silent preference — the winner would
   otherwise depend on manifest order rather than on anything the author wrote. Qualify as
   `company/web` to disambiguate.

Given `~/ac/loadout.toml` declaring `[[source]] path = "loadout"`, templates resolve from
`~/ac/loadout/templates/<name>/`. Everything under a source belongs to loadout, which is what
makes "where do I edit" answerable from the path.

## Bundled starters and native projects

`loadout init --project --starter frontend` or `--starter backend` vendors the selected template
with normal content-hash provenance. `--starter none` is the default. Interactive fresh-project
init offers this choice unless `--yes` already accepts the default. Selection is project-scoped;
a global source can offer these template names but cannot activate their advice machine-wide.

The packaged seeds cover frontend accessibility, responsive layouts and UI states, or backend
boundaries, authorization, failures and compatibility. Each includes empty category scaffolds.
They add no dependency installs, permissive rules, model choices or credentials. The seed is
editable source at `loadout/templates/<name>/instructions.md`; later local edits use normal
modified-copy refusal during `template sync`.

For `presets = false`, a template's UTF-8 `instructions.md` is trimmed and concatenated in
declared order, with two newlines between tiers and before each original native body. Only
required project copy/text instruction routes explicitly setting `template_instructions = true`
receive that prefix. Source bodies remain byte-identical, including their trailing whitespace,
and outputs retain the source file's full mode. A selected template also activates an empty
opted-in route. Removing all template text makes such an otherwise empty route dormant again.
Existing nested instructions and command trees keep their independent routes and content.

Fresh project migration marks the top-level `CLAUDE.md` and `AGENTS.md` routes, including dormant
ones. Every configured agent must have an opted-in route when template prose is selected.
An existing manually authored native config gets an actionable route error if one is missing;
legacy `instructions = [...]` remains unsupported in this mode.

Legacy directory templates in native projects compose instruction text only. Empty `permissions.toml`, `mcp.toml`
and `.gitkeep` scaffolds are accepted. A populated permission, MCP, skill or other contribution
is refused with its category/path and a remedy: place that content in an explicitly routed
project source, then remove it from the template. `template add`, `vendor` and `sync` preflight
these limits before changing configuration, copies or provenance. Source symlinks are refused.
Native `template sync` validates both vendored and upstream trees before reading template
contents for hashes or refusal diffs.
This prevents template data from being silently ignored or overlaying native producers.

Catalog manifests additionally compose skills, MCP and portable permissions through existing
native routes. Fresh init supplies these routes for all four agents. With Codex configured,
fresh projects use one `.agents/skills` collection shared by configured Codex, OpenCode and Pi
consumers. A shared route refuses a skill whose rendered variants differ between its agents.
Existing routes retain their authored destinations and ownership receipts.

A catalog needs one compatible consumer route per agent and category. Set `template_parts =
false` on routes that should not receive catalog skills, MCP or permissions; this is useful
for separate personal or auxiliary routes. Instruction participation still uses the independent
`template_instructions` flag. Skill routes must point at a collection, not an individual skill.
Project skills replace the whole same-named template skill, including supporting files.
Tree `modes` overrides apply to template skill documents and supporting files too.
MCP project entries replace whole servers, preserving native sub-settings. Codex combines MCP
definitions and approval policy in its MCP route, including policy-only servers. Native policy
restrictions participate in the permission merge, and unrelated server/tool settings survive.
Codex's shell rule route alone cannot consume MCP policy.

Native JSON permissions must round-trip through their portable adapter without loss, including
key order; otherwise selection refuses and asks for an explicit portable source. An explicit
native catch-all `ask` remains a restriction during merging, whereas an unstated portable
default contributes no vote. Nonempty Codex rule text needs an explicit portable text renderer.
An absent optional permission source contributes no native rules; required sources remain
required. Unsupported or ambiguous routes fail before add/vendor/sync changes source.

Catalog overrides of `frontend` and `backend` also work with `init --starter`, including all
referenced files in privacy checks, approval preconditions, expected outputs and staging.
Other manifest names are selected with `template add` or `template vendor` after init.

Init includes the selected template in preview metadata, source writes, output expectations,
fresh-source validation, staging and recovery. Private/Git-ignored or credential-bearing template
overrides require a public template before init can vendor them into committed source. Errors
name paths without printing content. The same conservative credential heuristic used for
migration does not prove arbitrary content secret-free.
The approved plan records template files and directory membership, including empty directories;
adding a file or directory invalidates it. Upstream Git privacy decisions and applicable ignore
policies are preserved through preparation, application and interrupted resume, including
effective exclude-file targets. A policy change requires rediscovery even if it has not yet
changed whether an existing template file is ignored.

Repeat init with an explicit starter reports the exact `template vendor`/`template sync` and
normal `sync` commands instead of discarding the choice. A vendored clone resolves before reading
machine configuration and works independently of its original template source.

## The content hash

Recorded when a template is vendored, and the only question `sync` needs answered: *has this copy
been modified since it was vendored?*

- current hash **==** recorded → clean
- current hash **!=** recorded → locally modified
- **no recorded hash at all** → unverifiable; nothing can tell the user's edits from the
  source's, so `sync` refuses and `check` reports it

That third state is not hypothetical. `loadout harness add` rewrote `loadout/config.toml` from
the harness list alone until 2026-08-17, destroying every `[template.<name>] vendored` block
along with `templates` and `instructions`, so repos that ran it are in it today. The message to
search for is **"has no recorded provenance"**. `template vendor <name>` takes the source
wholesale and records a hash again; so does `template sync` when the copy still matches the
source, since matching *is* proof it is unmodified.

It is a **content** hash, not a git SHA, because a template may come from a plain directory with
no repository behind it.

The definition, stated precisely enough to reimplement. Over every content file, sorted by
relative POSIX path, sha256 absorbs:

```text
<relative path>\0<"x" if executable else "-">\0<byte length>\0<bytes>\0
```

and the digest is rendered as `sha256:<64 hex digits>`. Notes on each part:

- **Only the path *relative* to the template root** is hashed, so the digest is
  path-independent: vendoring a template does not change its hash, which is what lets one
  recorded value compare a copy against its upstream.
- **The byte length** pins the file boundary, so no arrangement of bytes across two files can
  collide with a different arrangement across two others.
- **The executable bit** is included; a template carries skills, and three skills in the live
  source have executable `scripts/` files.
- **Build output is excluded** — the same directories, suffixes and names `skills.py` excludes.
  Otherwise a template that once had a `__pycache__` in it would never compare equal to the same
  template checked out fresh.
- The algorithm is named in the value rather than assumed, so changing it is detectable instead
  of silent.

The hash lives in `loadout/config.toml`, which is **source**, not generated output.
For a catalog manifest, the hashed files are the manifest itself and its referenced closure,
with paths relative to the catalog directory. Unselected catalog parts are excluded.
[0008](../decisions/0008-generated-files-carry-no-machine-state.md)'s prohibition on stamping a
hash governs generated files, and exists so generated content stays a pure function of the
source; a hash recorded in the source *is* the source. It carries no machine state — the digest
covers relative paths only.

## `template sync` is refuse-and-diff

This section describes directory templates. Catalog manifests use the shared-update flow
above: provenance and ownership errors exit 3; declining the shared update exits 1.

    loadout template add web       # declare it; it resolves from a source on every render
    loadout template vendor web    # copy it in, record the hash
    loadout template sync web      # update the vendored copy from its source
    loadout template list          # what this project uses, and how each resolves

Two templates offering one skill name resolve last-declared-wins: `templates = ["a", "b"]`
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

The cost is honest and worth stating: a template fix that must reach twelve modified projects is
twelve manual merges. If that becomes the common case rather than the rare one, that is the
signal to build three-way.

## `check` reports divergence; it does not fail on it

A vendored copy is source, so a user editing it is not drift. `loadout check` notes a copy that
no longer matches its recorded hash — or has none recorded — on stdout and leaves its exit code
alone; real drift still exits 1. The two are separate reports because the second is the *absence*
of the evidence the first is measured against: `template_divergence` can only speak about copies
it has a base for, so a missing hash read there as "no divergence" rather than "cannot say". See [0014](../decisions/0014-a-vendored-template-is-source-not-output.md), which argues
that a vendored template falls outside `check`'s jurisdiction by definition rather than by
exemption.

## Not built

Each with its reason, so a later reader knows whether the reason still holds.

- **Three-way merge** — deferred deliberately, as above. The hash is chosen so this stays
  possible without a state migration.
- **Fetching or versioning template repositories.** A template arrives on disk however you like —
  git clone, submodule, a shared drive. loadout resolves names against directories and fetches
  nothing, the same position [0010](../decisions/0010-a-machine-config-locates-the-global-source.md)
  takes on the global source.
- **Per-template slice filtering** (`templates = [{name = "web", use = ["permissions"]}]`) —
  cheap syntactically, but it is a second place slice selection lives, and nothing yet needs it.
- **A template declaring templates** — one level, resolved eagerly. Nesting turns name resolution
  into a graph needing cycle detection, for no demonstrated benefit.
- **A command that creates a template** — extraction produces a *source*; promoting a directory to
  a template is `cp` plus this document.
- **Project-type detection.** loadout does not guess that a repo is a React project. Declaring
  `templates = ["web"]` is a deliberate act.

## Prior art

- **copier** is the closest match — records the template and its version in the project
  (`.copier-answers.yml`), and `copier update` re-applies template changes onto a modified copy.
- **cruft** exists purely to bolt that onto **cookiecutter**, storing a template commit in
  `.cruft.json`. Its existence is the evidence that copy-in without an update path is a mistake
  people pay to fix afterwards.
- **cookiecutter** is the cautionary tale: no update path at all.
- **Go's `vendor/`** and **`git subtree`** are the same shape at a different scale — copy in, keep
  provenance, update deliberately.
