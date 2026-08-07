## Browser Automation

When a task needs a real browser — driving the running app to verify a UI change, filling a form, reproducing a click-path, grabbing a screenshot — use the **`agent-browser`** CLI (installed machine-wide, on `PATH`). It is built for agents: text-first output, low token cost, and an accessibility-tree snapshot with stable refs instead of brittle CSS selectors.

**Start here**: run `agent-browser skills get core --full` once per session for the full command reference and workflow patterns. Prefer that over guessing flags. Specialized skills exist too (`agent-browser skills list`).

**Core loop**:
- `agent-browser open <url>` — navigate (a `localhost` dev server is the common case).
- `agent-browser snapshot` — accessibility tree with refs like `@e1`, `@e2`. Use those refs to target elements.
- `agent-browser click @e2` / `type @e3 <text>` / `fill @e3 <text>` / `get text @e1` / `get url` — interact and read state.
- `agent-browser screenshot <path>` — save a PNG (then read it to see the page).
- `agent-browser close` — end the session.

The session is **stateful and persists across invocations** until `close`, so each command above is its own shell call against the same live browser. Reach for this whenever the page needs JS execution, interaction, or auth you already have in a logged-in session — plain read-only fetching of static external pages does not need a browser.