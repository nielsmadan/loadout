# The plugins slice

A plugin is two things: **content on disk, and a declaration that it is switched on.** This
slice owns the second only. loadout never clones, fetches, `npm install`s or registers a
marketplace — fetching is git's job and the harness's, as with the global source
([0010](../decisions/0010-a-machine-config-locates-the-global-source.md)).

[config.md](config.md#plugins) records where each harness keeps the declaration, and how each was
verified. This page records what loadout does with it.

## The portable unit is a reference, not an install

A plugins fragment is `<source>/plugins/<name>.json`, and it holds two maps:

```json
{
  "marketplaces": {
    "nolabs-ai": { "source_type": "local", "source": "/Users/me/.codex/plugins/marketplaces/nolabs-ai" }
  },
  "plugins": {
    "superpowers": {
      "source": "git:github.com/obra/superpowers",
      "marketplace": "claude-plugins-official",
      "pi": { "extensions": [] }
    }
  }
}
```

A reference carries at most three keys:

- **`source`** — where the package comes from, in Pi's syntax (`npm:`, `git:`, a URL, or a
  path). What Pi installs from; no other harness renders it.
- **`marketplace`** — the marketplace a plugin is addressed through. What Claude and Codex
  render; Pi has no marketplace concept at all.
- **`pi`** — per-package options, passed through untouched.

Two sections rather than plugin names at the top level, so a plugin called `marketplaces`
cannot collide with the registrations. A key outside the three above is an **error**: this
vocabulary is loadout's own, so a typo here is loadout's to catch. The nested blocks are the
opposite case and are passed through verbatim — `pi` holds Pi's filter schema and a
`[marketplaces.<name>]` table holds Codex's registration schema, and we do not have either
schema, so the harness decides. (Validation invented from one machine's files is what
`render_claude_hooks` records being falsified by the first sample of a survey.)

## What each harness gets

| harness | destination | rendered |
|---|---|---|
| Claude | `~/.claude/settings.json` → `enabledPlugins` | `"<name>@<marketplace>": true` |
| Codex | `~/.codex/config.toml` → `plugins`, `marketplaces` | `[plugins."<name>@<marketplace>"]`, `[marketplaces.<name>]` |
| Pi | `~/.pi/agent/settings.json` → `packages` | the source, or an object carrying its filters |
| OpenCode | — | nothing; see below |

**Claude's is the fourth slice landing in `settings.json`**, after settings, permissions and
hooks. It owns one key and nothing else in the file.

**Codex's writes `config.toml` directly, owning two keys of it.** Enablement and marketplace
registration both live there, alongside `[projects.…]` Codex writes itself and everything else
it keeps — a file loadout cannot rewrite from a source. It was staged for a merge step outside
loadout until declared ownership removed the need: loadout now strips `plugins` and
`marketplaces` and leaves the rest untouched. See
[0017](../decisions/0017-ownership-may-be-declared-instead-of-derived.md) and
[codex.md](codex.md#configtoml-is-co-owned).

**Pi renders a bare source string when nothing filters the package, and an object when
something does.** Both are Pi's own forms (`docs/packages.md`, shipped with the binary): the
string loads every resource the package offers, the object narrows it with `skills`,
`extensions`, `prompts` and `themes`. That filter map is why a `pi` block exists — see
[Enablement as a volume control](#enablement-gets-used-as-a-volume-control).

**Pi's file is co-owned with runtime state.** Verified negative against Pi 0.84.1's shipped
`docs/settings.md` **All Settings** enumeration: `lastChangelogVersion` is not a setting.
`dist/modes/interactive/interactive-mode.js:getChangelogForDisplay` reads and advances it as a
last-seen changelog cursor. The Pi preset preserves that key from the live destination, so an
upgrade does not create loadout drift; a settings fragment naming it is refused rather than
turning the cursor into versioned source.

**OpenCode is out of the slice**, and that is a finding rather than an omission: a plugin is on
there because a `.ts` file exists in `~/.config/opencode/plugins/`, and its dependencies live in
an npm manifest `npm`/`bun` owns. There is no enablement list to render, so `plugins` under
`[opencode]` is an error listing what that agent does offer. Placing those files is *content*,
not enablement.

## On and off

**Presence is enablement.** Every rendered entry is on; a plugin is switched off by taking the
reference out, which a profile does with a `null` overlay:

```json
{ "plugins": { "nono": null } }
```

`merge_documents` removes the key before any renderer sees it
([spec 1 §8](config.md)), so one overlay key replaces restating a list. Being deep-merged,
plugins takes no variant suffix.

A harness may also be able to say *off* explicitly — Claude's map holds a boolean and Codex's
table holds `enabled` — but nothing loadout renders needs it, and whether either honours `false`
is not settled here. Extraction reads such an entry as **not enabled** and reports it, because
absence is the only representation a fragment has.

## What is reported rather than rendered

Three pure functions in `plugins.py`, none of which reads a file:

- **`unaddressable(document, harness)`** — references the harness has no way to name: no
  `marketplace` on Claude and Codex, no `source` on Pi. **Skipped, not refused.** A set holding
  both halves is the ordinary case, so an error would take down a render that is doing exactly
  what it should, and skipping fails safe — the plugin is left where it was. (Contrast the hooks
  slice's foreign-variable check, which refuses because *its* failure denies tool calls.)
- **`unregistered_marketplaces(document, known)`** — marketplaces the document names that
  `known` does not carry. The caller supplies `known`, because the answer differs by harness and
  neither source is a file a renderer may read
  ([0001](../decisions/0001-render-never-reads-its-own-output.md)).
- Claude's marketplace registry is **read and reported, never written**. It lives in
  `~/.claude/plugins/known_marketplaces.json` and carries `lastUpdated` timestamps and
  `installLocation` paths — machine state a generated file must not hold
  ([0008](../decisions/0008-generated-files-carry-no-machine-state.md)) — and the harness
  maintains it. Codex is the easier case: its registration is ordinary configuration in the file
  loadout already stages, so it renders. See
  [0015](../decisions/0015-enablement-is-rendered-installation-is-reported.md).

These have no command surface yet; they are pure functions with tests, exactly as
`unrecognised_events` arrived with the hooks slice.

## Reading it back

Per-harness losses, since each document states only the half it addresses by. Full properties in
[extraction.md](extraction.md) and `tests/test_extract_plugins.py`.

| renderer | recovers | loses |
|---|---|---|
| `claude-plugins` | name, marketplace | `source`, `pi` |
| `codex-plugins` | name, marketplace, the registrations its plugins reach | `source`, `pi`, a marketplace no plugin names |
| `pi-plugins` | source, `pi` | `marketplace`, **the name**, and the object form of an unfiltered entry |

Pi's is the interesting one: a package is a source and nothing else, so the key a reference is
filed under is not in the document. It is derived from the last path segment ahead of the pinned
ref, a collision falls back to the source itself, and **every entry is reported** — a derived
identifier is an invention however sensible it looks, and it is the identifier a profile overlay
names to switch the plugin off. The document round trip otherwise closes, because re-rendering
needs only the source, which survives exactly.

The one shape that does change bytes is `{"source": x}` with nothing filtering it, which
`pi install` writes and the renderer emits as the string form. Pi's docs make the two
equivalent; equivalent is not identical, so extraction reports it and adopting loadout
normalises that entry once.

Verified against this machine on 2026-08-15: Claude's six `enabledPlugins` entries round-trip
byte for byte, and all eight Pi packages recover a sensible name — `pi-permission-system` from
`npm:@gotgenes/pi-permission-system@23.0.0`, `superpowers` from its git source. The weak case is
a local path whose last segment is the harness name: `…/nolabs-ai/pi` files as `pi`. Determinate
and reported, but a name worth overriding by hand.

## Enablement gets used as a volume control

`~/.pi/agent/settings.json` on this machine carries
`{"source": "git:github.com/obra/superpowers", "extensions": []}`. The empty list is not an
oversight: that extension injected a bootstrap block on every session start, including trivial
ones, so it was switched off while the package's skills were kept.

That is a design signal rather than a quirk. Per-resource enablement is real and load-bearing —
Pi's `extensions` / `skills` sub-lists let a package be *partly* on, and Claude's boolean does
not — so a portable representation that flattened to a single on/off would lose a lever someone
actually reached for. What was wanted underneath is conditional injection, by project or task or
profile, which is nearer the hook ABI than to plugin enablement. Noted, not scoped.

## Not verified

Each is a real gap, not a judgement that the feature is absent:

- Whether Codex honours `enabled = false`, or whether absence is the only way off. Nothing
  loadout renders depends on it — off is absence — so it stays open.
- Whether Claude accepts a plugin named without its marketplace. `<name>@<marketplace>` is the
  only form observed in the live install, which is presence evidence and settles nothing about
  the alternative. loadout skips a reference it cannot address this way.
- What `~/.codex/plugins/.remote-plugin-install-staging` is for. Present and empty; if remote
  installs register differently from local ones, the Codex half of
  [0015](../decisions/0015-enablement-is-rendered-installation-is-reported.md) may be
  incomplete.
- Project scope, on all four harnesses.
