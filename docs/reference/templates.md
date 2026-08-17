# Templates

Shared configuration for a *kind of project* — `web`, `flutter`, `react-native`, `railway` — that
a repo opts into by name.

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

**Scope as built.** The mechanism is complete; it is wired to **permissions**, the one artifact
type project scope has. Instructions, skills and MCP plug into the same resolution when project
scope grows them — see `docs/scopes.md`, which is why this is a dimension rather than a milestone.

## Declared and vendored

Both are first-class. They are the same source resolved from two places, and switching between
them is not a migration.

**Declared** — the template lives outside the project, in a source you already have. A fix to the
template reaches every project at once.

**Vendored** — the template is copied into `loadout/templates/<name>/` and committed, so the repo
stands alone. This is what an open-source project whose contributors do not install loadout needs.

A vendored template gets its own directory and is **never merged into the project's own
fragments**. If a template's content were mixed into yours, nothing could later distinguish
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

1. If `loadout/templates/<name>/` exists in this project, it is **vendored**, and resolution
   stops. No machine config is read and no external source is consulted — which is precisely what
   lets a clone build without the template repo.
2. Otherwise, search the `templates/` directory of every source the **machine's global manifest**
   declares whose `use` admits templates. Project scope carries no `[[source]]` list of its own
   and must not, per the rule above; the machine config
   ([0010](../decisions/0010-a-machine-config-locates-the-global-source.md)) is where this
   machine's paths already live.
3. No match: an error naming every place searched, the vendored path included.
4. **More than one match: an error listing both.** Never a silent preference — the winner would
   otherwise depend on manifest order rather than on anything the author wrote. Qualify as
   `company/web` to disambiguate.

Given `~/ac/loadout.toml` declaring `[[source]] path = "loadout"`, templates resolve from
`~/ac/loadout/templates/<name>/`. Everything under a source belongs to loadout, which is what
makes "where do I edit" answerable from the path.

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
[0008](../decisions/0008-generated-files-carry-no-machine-state.md)'s prohibition on stamping a
hash governs generated files, and exists so generated content stays a pure function of the
source; a hash recorded in the source *is* the source. It carries no machine state — the digest
covers relative paths only.

## `template sync` is refuse-and-diff

    loadout template add web       # declare it; it resolves from a source on every render
    loadout template vendor web    # copy it in, record the hash
    loadout template sync web      # update the vendored copy from its source
    loadout template list          # what this project uses, and how each resolves

**Two templates offering one skill name resolve last-declared-wins, silently**, the same way
their permission tiers do — `templates = ["a", "b"]` gives `b`'s copy. Consistent rather than
chosen: nobody weighed reporting it, and if it should report, that is a question for the notices
surface rather than a property of resolution. A skill the project itself defines beats both.

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
