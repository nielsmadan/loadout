## Web Search & Fetching

**For search, use `mcp__jina__search_web`** (or `mcp__jina__parallel_search_web` for several queries at once) rather than `WebSearch` — it returns better results. Fall back to `WebSearch` only if the Jina MCP is unavailable.

**For fetching a known URL, use `WebFetch`.** It is the default for everything, docs sites included.

**When `WebFetch` fails, use `jina-fetch <url> "<what to extract>"`** (on `PATH`). It fetches through Jina, caches the full page under `TMPDIR`, and returns only the extract — a 200k-char page never reaches your context. Ask a specific question rather than "summarize"; re-running against the same URL is cheap because the page is cached.

**When exact text matters** — code, config, a quoted claim — don't trust the extract, which can paraphrase while looking like a quote. Ask instead for short verbatim anchor phrases, then `grep` or `Read` the cached file to pull just those lines into context. `jina-fetch` prints each page's exact cache path and title on stderr; use that path — never "the newest file in the cache directory", which is wrong whenever another fetch is in flight. Repeat questions against the same URL are cache hits, so several targeted passes cost far less than one `--raw` dump.

**`NO_RELEVANT_CONTENT` means the page does not address your question.** Treat it as a real answer — a negative result — not as a failed call. Do not re-ask the same page in different words hoping for a hit, and never record an unrelated quote from it as evidence. `jina-fetch` also warns when none of the question's key terms appear on the page.

**Never ask it to count or enumerate** occurrences across a page ("how many tables/sections/matches") — models get this wrong on long documents. `grep -c` the cached file instead.

**Go straight to `jina-fetch` for Stack Overflow and Reddit** — both block `WebFetch` and both work through Jina, so don't waste the failed call.

**Reading several pages**: `jina-fetch <url> <url> <url> "<what to extract>"` fetches them in parallel and extracts each separately. Add `--combined` for one extraction across all of them when the question spans sources ("which of these disagree?"). Use this instead of `mcp__jina__parallel_read_url`, which dumps every page into the conversation at once.

Other flags: `--raw` prints whole pages, `--out PATH` saves them, `--model` picks a different OpenRouter model, `--help` covers the rest. Prefer `jina-fetch` over `mcp__jina__read_url` generally; reach for the MCP tools only for Jina's other capabilities (screenshots, PDF figure/table extraction, text classification).

`WebFetch` fails in four ways, and only the first two announce themselves:

1. `"Claude Code is unable to fetch from X"` — refused, never sent
2. HTTP 402/403 from the origin
3. Returns instructions to re-call instead of following a cross-host redirect
4. HTTP 200 with nav/chrome only and the article body missing

If the result is boilerplate without the content you asked for, that is a failure — escalate, don't report it as the page.

**Some hosts defeat both tools.** `jina-fetch` warns when a page comes back near-empty — treat that as unreachable rather than retrying: use `gh`, `glab`, or a project-specific CLI, or tell the user.

Jina does not bypass paywalls — expect the same free prefix, with no marker where it stopped.

**Auth**: `jina-fetch` and the Jina MCP both authenticate automatically from the SOPS-encrypted store via the `claude` zsh wrapper. The agent does not need to know or fetch the token (see Secrets below).
