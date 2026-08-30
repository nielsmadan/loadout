# Permissions extraction

`src/loadout/extract.py` inverts the permission renderers: `extract(name, document)` takes a
rendered artifact and returns the `Rules` that produced it, the base it was written into, and a
list of notes for anything the document held that the source cannot represent.

`EXTRACTORS` is keyed by the same names as `RENDERERS`, and covers every **permissions**
renderer. `RENDERERS` also holds the hooks slice's two `ValueSpec` entries, which have no
inverse yet; they are named in `NOT_INVERTED`, and a renderer that lacks an inverse without
being named there fails `test_no_renderer_lacks_an_inverse_without_being_named`. Inverting them
is separate work — a hook fragment is not `Rules`, and a value renderer is handed no base to
hold a residual in.

## The two properties

Both are asserted in `tests/test_extract_roundtrip.py` over an enumerated rule space, and in
`tests/test_extract_fixtures.py` over the artifacts in `tests/fixtures/expected/`.

**Rules round trip.** `extract(render(rules)) == carried(rules)`, where `carried` is a projection
declared per renderer — what that harness's document is *able* to carry. Declaring the loss is
the point; a lenient comparison would hide it.

**Document round trip.** `render(extract(document)) == document`, byte for byte. This is the
acceptance criterion in the extraction spec.

The two are not redundant. Removing the pair collapse produces a *different source* that renders
the *same bytes*, so only the rules property catches it; the mutation check in this repo's
history confirmed that directly.

An extractor that knows it lost something says so in `Extraction.notes`, and the document
property is claimed only where `notes` is empty. Silence is a claim of exactness.

That makes `notes` mean exactly one thing: **`notes == ()` if and only if the round trip closes
byte-identically.** A note exists *because* something did not survive, and nothing else belongs
there — an informational finding recorded as a note would silently buy an exemption from the
document property for a document that round-trips perfectly. Findings a user should act on but
which do not break the round trip belong at render time, where they can still be acted on.

## What each renderer loses

| renderer | carries | loses |
|---|---|---|
| `claude`, `claude-project` | shell, MCP, `claude.extra` | nothing; `opencode.extra` is not its to carry |
| `claude-mcp-permissions` | MCP | — |
| `codex` | non-glob shell | **glob entries.** `render_codex` diverts them to a trailing comment block with no decision attached, so the file does not record whether `gamma-*` was allowed, asked or denied. Reported, never guessed. |
| `codex-project` | shell | quote *style*. `render_codex_project` tokenises with `shlex.split`, and a token holding whitespace is re-quoted on the way back, so `echo "a b"` returns as `echo 'a b'` — the document round-trips, the source spelling normalises. |
| `codex-mcp-permissions` | MCP | **source order.** `render_codex_mcp` groups by server and sorts servers and tools, and resolves an entry listed twice to its last category. The emitted order is canonical, so re-rendering is stable. |
| `opencode` | shell, MCP, `opencode.extra` | see *order loss* below |
| `pi`, `pi-project` | shell, MCP | see *order loss* below |

**Order loss.** `render_pi_project` and `render_opencode` assign into their pattern map in place,
so a rule listed in two categories keeps its *first* category's position while carrying its
*last* category's value — and no source ordering renders back to that. `render_pi` deletes the
key before reassigning it, so the key moves to the end and the map stays grouped by decision,
which stays invertible. This is [ADR 0006](../decisions/0006-faithful-ports-reproduce-upstream-quirks.md)'s
divergence deciding invertibility, and it is why harmonising the two renderers now costs more
than output fidelity — see [ADR 0013](../decisions/0013-a-renderer-change-is-checked-against-extraction.md).

Extraction diagnoses it structurally — a map whose key order is not the concatenation of its
decision groups — and emits a note. It does not diagnose it by re-rendering, so the document
property stays an independent check rather than a tautology.

**This page and `coverage.md` describe different halves, and neither is the whole story.**
[coverage](coverage.md#reachable-only-by-calling-a-renderer-directly) records that the state
distinguishing those two renderers is *unreachable through the pipeline*: `merge_rules` resolves
deny > ask > allow before any renderer runs, so nothing loadout writes contains a rule in two
categories. That is true and it does not narrow anything here, because **extraction's input is
not loadout's output.** It reads what a person, a harness, or another tool already wrote, where
the shape is ordinary. Read `coverage.md` for what loadout can produce and this page for what
extraction may be handed.

## `base` is the render-time residual, not the settings fragment

`Extraction.base` holds everything the renderer did not write, so that
`render(rules, base)` reproduces the document. It is **not** yet a settings fragment, and the
difference matters because `settings.json` has four owners — settings, permissions, hooks and
plugins.

`extract_claude` strips only the three keys `render_claude` writes, so a real
`~/.claude/settings.json` yields a base still holding `hooks` and `enabledPlugins`. Writing that
out as a settings fragment is the failure the extraction spec names: the fragment carries keys
that lose to their own slices on every render, so a user edits `hooks` there and watches the
edit vanish with nothing erroring.

Stripping them **here** is not the fix — it would drop content this renderer is contractually
required to reproduce, and break the document round trip. The ownership map belongs to whatever
composes the slices into one file, applied once, where it can see every owner:

| slice | owns in `settings.json` |
|---|---|
| permissions | `permissions.allow` / `deny` / `ask` |
| hooks | `hooks` |
| plugins | `enabledPlugins` |
| settings | everything else |

A value renderer's inverse produces no base at all — it was handed no residual, so it has none
to return — which is what keeps the residual produced exactly once rather than once per owner.
The boundary is pinned by `test_the_base_keeps_keys_other_slices_own`.

## Collapsing the lossy pattern forms

Rendering is not injective, and three forms have to be undone:

- **`foo` and `foo *`.** OpenCode and Pi emit both for one source entry, because neither matcher
  lets `foo *` match a bare `foo`. Extraction drops the ` *` form when the bare form carries the
  same decision. Missing this doubles the rule on the next render.
- **`Bash(foo:*)`.** Claude's colon form is stripped back to `foo`.
- **Pi's MCP fan-out.** One `server/tool` entry emits `server_tool` and `server:tool`, plus
  `mcp_server_<server>` and `mcp_connect_<server>` when the tool is `*`. The colon form is the
  anchor — the only emitted form that splits unambiguously — and the companions are consumed.

The catch-all that Pi and OpenCode write first is not a source rule. A leading `*` in the
**bash** map is read back as `[shell] default` and taken out of the rules; `ask` reads back as
*unstated*, since stating it renders the same bytes as saying nothing. There is no source rule to
confuse it with — `parse_rules` refuses a bare `*` entry — and an unrecognised verdict stays in
the map so the shell collapse reports it, the same contract every other decision gets.

**Only where a seed was written.** `render_pi_project` emits no catch-all, so a leading `*` in a
project-scope Pi document is a genuine rule; `pi-project` therefore has its own extractor that
does not split one off. Reading it as the default deleted policy and re-rendered a different
document while reporting no loss at all. Pi's **mcp** map takes no default either, so there the
strict form still applies: only `*: ask` still in first position is the seed, and any other
leading `*` is left for the MCP collapse to report.

## Ambiguities that do not change the bytes

Four readings are genuinely undecidable from the document. In each case both readings render
back identically, so the document property holds and the source differs only in spelling:

- A source entry of `foo:*` is itself a glob, so `claude_pattern` emits `Bash(foo:*)` — the same
  bytes as the non-glob entry `foo`. The bare reading is taken.
- An OpenCode MCP key splits on its last `_`, so a tool name containing `_` splits wrong, and a
  passthrough key containing one reads as an MCP target.
- A `claude.extra` value shaped like `Bash(...)` or `mcp__x__y` reads as a shell or MCP rule.
- A stated `default = "ask"` renders exactly what an unstated key renders, so it reads back
  unstated. Only the spelling is lost; across tiers it also loses a vote, which is why
  `parse_rules` keeps the two apart even though one document holds both.

## Divergence is reported, never unioned

`merge_extractions` reconciles several harnesses into one source. Where they disagree about an
entry, no single source rule reproduces all of them, and taking the permissive reading would
render a **wider** permission set than what was on disk — a privilege escalation performed by an
onboarding tool. The entry is withheld and named in the report for a person to resolve.

Silence counts as disagreement, but only from a harness that could have spoken. `CAPABILITIES`
declares, per renderer, whether its document can state a shell rule, an MCP rule, a glob, and a
catch-all default; Codex is not a voter on globs, and the MCP-only renderers are not voters on
shell entries. Counting their silence would suppress rules every harness that can express them
agrees on.

Only OpenCode and Pi vote on the default, because only they *author* one: Claude's
`permissions.defaultMode` is preserved from the base rather than written, and Codex's
`approval_policy` is in a file loadout does not write at all. Disagreement is reported but
**not** withheld the way an entry is — withholding an entry drops it to the catch-all, while
withholding the catch-all drops it to `ask`, the middle verdict, so a stated `deny` would come
back a prompt. It settles on the strictest stated verdict instead.

A dropped entry falls through to the catch-all, so while any shell entry is withheld the
catch-all is not stated looser than the `ask` it used to fall through to. Without that, adopting
a pair of files that disagree about one deny renders that command executable.

A name with no declared capability is an error rather than a non-voter. A harness silently
excluded from the vote cannot veto, so adding a renderer would quietly widen the source.

A harness's verdict is every category it put the entry in — `allow+deny`, not whichever came
last — because a source may legitimately list an entry twice; `tests/fixtures/permissions.toml`
does, to demonstrate last-match-wins.

**Reachability.** As with the order loss above, nothing loadout renders can exercise that last
case — `merge_rules` leaves no entry in two categories — and as above, that is a fact about
loadout's output rather than about extraction's input. It is pinned against constructed
`Extraction(...)` values for exactly that reason.
