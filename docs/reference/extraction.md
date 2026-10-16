# Permissions extraction

`src/loadout/extract.py` inverts the permission renderers: `extract(name, document)` takes a
rendered artifact and returns the `Rules` that produced it, the base it was written into, and a
list of notes for anything the document held that the source cannot represent.

`EXTRACTORS` covers every **permissions** renderer. `VALUE_EXTRACTORS` covers the native
declarative hook renderers, plugin declarations, and JSON MCP server definitions, including
Droid's. Code adapters and TOML-text value renderers that cannot use that parsed-document
contract are named explicitly in `NOT_INVERTED`; a new renderer missing both an inverse and a
reason fails `test_no_renderer_lacks_an_inverse_without_being_named`.

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
| `codex` | non-glob shell; an exact deny as its prefix | **exact allow and ask**, which it omits, and the exactness of a deny. **glob entries.** `render_codex` diverts them to a trailing comment block with no decision attached, so the file does not record whether `gamma-*` was allowed, asked or denied. Reported, never guessed. |
| `codex-project` | shell; an exact deny as its prefix | **exact allow and ask**, and the exactness of a deny. Quote *style*. `render_codex_project` tokenises with `shlex.split`, and a token holding whitespace is re-quoted on the way back, so `echo "a b"` returns as `echo 'a b'` — the document round-trips, the source spelling normalises. |
| `codex-mcp-permissions` | MCP | **source order.** `render_codex_mcp` groups by server and sorts servers and tools, and resolves an entry listed twice to its last category. The emitted order is canonical, so re-rendering is stable. |
| `droid` | non-glob shell; an exact deny as its word run | exact allow and ask, the exactness of a deny, MCP policy, trailing shell globs, and `[shell] default`; Droid's command lists have no representation for them |
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

**What loadout can produce is narrower than what extraction may be handed.** The state that
distinguishes those two renderers is *unreachable through the pipeline*: `merge_rules` resolves
deny > ask > allow before any renderer runs, so nothing loadout writes contains a rule in two
categories, and whole-document comparison never exercises it. That does not narrow anything
here, because **extraction's input is not loadout's output.** It reads what a person, a harness,
or another tool already wrote, where the shape is ordinary.

## A hand-written Codex rules file

Codex's rules files are Starlark, and the ones adoption reads need not be in loadout's spelling:
Codex appends `prefix_rule(pattern=[...], decision="allow")` to `~/.codex/rules/default.rules`
itself, and its documentation writes one rule across several lines with comments. So the file is
parsed, not matched line by line, and every statement ends one of three ways:

- **carried:** a `prefix_rule` call whose `pattern` and `decision` the scope's own renderer writes
  back as the same rule. A union of literals, `["git", ["push", "reset"]]`, becomes one entry per
  alternative. A rule with no `decision` is an allow, Codex's documented default.
- **carried, with a note:** `justification`, `match` and `not_match` are dropped, because the
  source has nowhere to keep them and none of them changes which commands run. `init` names each
  one before it writes anything.
- **refused:** anything else stops `init` with the file, the line and the statement quoted. That
  covers an unknown parameter or decision, a positional argument, a value that is not a literal, a
  statement other than `prefix_rule` (`network_rule`, a variable), invalid syntax, a union whose
  alternatives multiply to more than 1,024 entries, and a rule the renderer would write back
  differently: at global scope a token holding whitespace, a `*` or a
  leading `=`. A rule that silently vanished could be a `forbidden` one, which would loosen the
  policy.

The document property is claimed for files loadout rendered. A hand-written file in another
spelling comes back in loadout's spelling with the same rules, and `init` shows that rewrite as a
diff before applying it.

**Only `permissions.rules` is rendered back.** Codex reads every file in its rules folder, so any
other rules file, `default.rules` included, would go on enforcing its rules beside the source.
Once its rules are adopted, `init` deletes it, backed up like any other original it retires. Codex
writing a new approval there afterwards is reported by `check` as `unmanaged`, for the `/loadout`
skill to port.

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

- **`foo` and `foo *`.** OpenCode and Pi emit both for one source entry, because older versions
  of both matchers did not let `foo *` match a bare `foo` (OpenCode before v1.1.15, Pi before
  v5.12.0). Extraction drops the ` *` form when the bare form carries the same decision. Missing
  this doubles the rule on the next render.
- **A lone `foo *` deny.** An OpenCode or Pi deny `foo *` with no `foo` entry beside it reads as
  the prefix `foo`. From OpenCode v1.1.15 (upstream `62702fbd11`) and Pi's permission system
  v5.12.0 (upstream `c25b0b5`) a trailing ` *` also matches the bare command, so the deny already
  blocks `foo`; read as a prefix it renders on every harness, Droid included, which refuses a deny
  glob. It never blocks less, and blocks more only on an older OpenCode or Pi. A lone `foo *`
  allow or ask stays a glob, since widening it could loosen policy on an older version; Droid
  skips those with a report. A deny whose `foo` itself holds a `*`, or that has a `foo` entry with
  any decision beside it, also stays a glob.
- **`Bash(foo:*)`.** Claude's colon form is stripped back to `foo`.
- **Exact entries.** `Bash(foo)` with neither `:*` nor a `*` reads back as `=foo`, and so does
  an OpenCode or Pi bare form with no `foo *` companion carrying the same decision. Before exact
  entries existed, the first stayed in `[claude.extra]` and never reached another harness, and
  the second re-rendered with a companion it never had. A pattern holding a `*`, or surrounding
  whitespace, is not one command and keeps its previous reading.
- **Pi's MCP fan-out.** One `server/tool` entry emits `server_tool` and `server:tool`, plus
  `mcp_server_<server>` and `mcp_connect_<server>` when the tool is `*`. The colon form is the
  anchor — the only emitted form that splits unambiguously — and the companions are consumed.

The catch-all that Pi and OpenCode write first is not a source rule. A leading `*` in the
**bash** map is read back as `[shell] default` and taken out of the rules; `ask` reads back as
*unstated*, since stating it renders the same bytes as saying nothing. There is no source rule to
confuse it with — `parse_rules` refuses a bare `*` entry — and an unrecognised verdict stays in
the map so the shell collapse reports it, the same contract every other decision gets.

**A `*` that is not first.** Loadout writes the catch-all first, but a hand-written map may put
it later, as in `{"echo *": "allow", "*": "ask"}`. Both harnesses apply the last matching entry,
so nothing before the `*` can ever apply: `echo` asks. Extraction still reads the `*` as
`[shell] default`, keeps the entries after it, and drops the entries before it, naming each one,
and `init` prints those names before it writes. Keeping them instead would render them after the
`*`, where they would start to apply, and here that would allow `echo`. The effective policy is
unchanged, and `init`'s first-sync diff shows the rewritten map.

**At project scope the `*` is never a seed.** `render_pi_project` writes the catch-all only when
the source states a default, because the project map is merged over the global one and an
unstated `*` would override the global catch-all. So in a project Pi document a `*` reads back as
exactly the verdict written, `ask` included, and a map without one carries no note. Reading (b)
applies there as well. With loadout's global Pi file the project `*` takes the global `*`'s first
position when the two maps merge, so entries before it would in fact have applied; dropping them
can only make the policy stricter, never looser. Pi's **mcp** map takes no default either, so
there the strict form still applies: only `*: ask` still in first position at global scope is the
seed, and any other `*` is left for the MCP collapse to report.

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
catch-all default; Codex and Droid are not voters on globs or exact entries, and the MCP-only
renderers are not voters on shell entries. Counting their silence would suppress rules every harness that can express them
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

Codex and Droid write an exact deny `=x` as a deny on `x`, so their `x` is not drift when every
harness that can match exactly is silent on `x` and denies `=x`: the merged `=x` renders the
same prefix back. If any of those also states `x` itself, the disagreement is real and reported.

A name with no declared capability is an error rather than a non-voter. A harness silently
excluded from the vote cannot veto, so adding a renderer would quietly widen the source.

A harness's verdict is every category it put the entry in — `allow+deny`, not whichever came
last — because a source may legitimately list an entry twice; `tests/fixtures/permissions.toml`
does, to demonstrate last-match-wins.

**Reachability.** As with the order loss above, nothing loadout renders can exercise that last
case — `merge_rules` leaves no entry in two categories — and as above, that is a fact about
loadout's output rather than about extraction's input. It is pinned against constructed
`Extraction(...)` values for exactly that reason.
