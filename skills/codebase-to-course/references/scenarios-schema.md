# `.codemap/scenarios.json` schema

Authored input for the **Simulate** tab (Lane 2 — see `codemap/site/scenarios.py`, the
loader; `codemap/site/simulate.py`, which resolves it against the graph). Absent or
malformed is not an error: the tab falls back to what it always has — a derived scenario
computed client-side from the call graph (Lane 1), and any recorded `codemap trace` runs
(Lane 3, `.codemap/traces/*.json`, never hand-authored).

**This file is a curriculum, not a demo.** Ship every real workflow of the app as one
entry — a repo with 100 routes gets 100 scenarios. The Simulate rail groups them by
`group` and orders them ascending by `order`, so they read the way the app actually runs
(startup → an inbound request → background jobs → when it breaks). Keep authoring cheap:
most entries are **`root`-only** (no `steps`) and the renderer's Lane-1 `deriveSteps`
fills the call tree in; hand-author `steps` only for the few hero scenarios a Learn
screen links via `"sim"`. `codemap explore --emit-brief` writes a ready-made **Scenario
index** into `.codemap/briefs/00-overview.md` and real step trees for the first heroes
into `.codemap/briefs/scenarios-derived.json` — start from those.

```json
{
  "scenarios": [
    {
      "id": "wallet-sync",
      "title": "Syncing the wallet",
      "trigger": { "surface": "api", "text": "POST /wallets/{id}/sync" },
      "root": "features/budget/blueprint.py::wallet_sync_pull",
      "group": "A request comes in",
      "order": 21,
      "summary": "how an inbound sync call fans out to the ledger and the DB"
    },
    {
      "id": "budget-overview-screen",
      "title": "Opening the budget screen",
      "trigger": { "surface": "ui", "text": "tap the Budget tab" },
      "root": "mobile/src/app/(tabs)/budget/index.tsx::BudgetOverviewScreen",
      "group": "A screen opens",
      "order": 16,
      "summary": "what mounts and which API calls fire when the budget tab opens"
    },
    {
      "id": "explore-run",
      "title": "Running `codemap explore`",
      "trigger": { "surface": "terminal", "text": "codemap explore" },
      "root": "codemap/cli.py::cmd_explore",
      "group": "Scripts & tools",
      "order": 50,
      "summary": "what the command does between you pressing enter and the file landing",
      "steps": [
        {
          "node": "codemap/cli.py::cmd_explore",
          "t": "call",
          "user": "You run the command. The terminal shows nothing yet.",
          "code": "cmd_explore starts and asks model.build() for the graph."
        },
        {
          "node": "codemap/site/model.py::build",
          "t": "call",
          "from": "codemap/cli.py::cmd_explore",
          "cond": { "kind": "for", "text": "once per file in the index" },
          "user": "Still nothing — this is the slow part.",
          "code": "build() reads every symbol recorded at this commit."
        },
        {
          "node": "codemap/site/model.py::build",
          "t": "return"
        },
        {
          "node": "codemap/cli.py::cmd_explore",
          "t": "emit",
          "emit": { "surface": "terminal", "text": "wrote .codemap/explore.html (45 files, 372 symbols, 791 edges)" },
          "user": "The line you were waiting for finally prints.",
          "code": "cmd_explore prints the summary and returns 0."
        },
        {
          "node": "codemap/cli.py::cmd_explore",
          "t": "return"
        }
      ]
    }
  ]
}
```

## Fields

**Scenario** — `id` (unique, used in the URL — keep it short and stable), `title`,
`trigger` (`surface`: `terminal` | `browser` | `api` | `ui` | `job` | `db` | `file`,
`text`: what the user types/clicks/sends).

- **`surface` is optional — leave it out and the renderer infers one.** The
  Stage pane (`explore.js`'s `resolveSurface`) tries, in order: your explicit
  `surface`; the majority `emit.surface` across the steps; the root symbol's
  entry-point kind (a `route`/`controller` → `browser` or `api`, a `screen`/
  `layout` → `ui`, a `task` → `job`, a `cli`/`script`/`main`/`docker` →
  `terminal`); then the root symbol's file path and code (`.tsx`/`.jsx`/
  `components/` → `ui`, SQL/`cursor.execute` → `db`, `threading.Thread`/
  `.delay(`/`worker` → `job`, a file write → `file`). It falls back to
  `terminal` only when none of that matches. Set `surface` yourself only when
  the inference gets it wrong — e.g. a route that returns JSON but has no
  `jsonify`/`serialize` in its snippet, so it's guessed as `browser` instead
  of `api`.
- **A frontend screen** (`entrypoints.py` detected it from a route file —
  `app/**/*.tsx` for expo-router/Next, `pages/**` for Next's pages router, or
  a React Navigation `<Stack.Screen>` registration) gets entry kind `screen`
  or `layout` and, left with no explicit `trigger`, resolves to the `ui`
  surface automatically. **Make at least one hero scenario cross the seam**
  when a repo has both a frontend and a backend: root it at the screen, and
  let a later step land on the API-client call and the backend route it hits
  — that single scenario teaches more about how the app actually works than
  either half alone.
- **`emit.text` is rendered verbatim as what the user sees** — a browser page
  line, an API response body, a job's log line, a mounted UI block — not
  discarded narration. Write it as the actual output, not a description of it:
  `"redirected to /tools/refresh-data/"`, not `"the page redirects"`. An
  `emit.surface` is optional too, same inference rule as the trigger — most
  emits should just omit it and inherit the scenario's resolved surface; set it
  only when one scenario genuinely mixes surfaces (rare).

- `root` — a `data.nodes[].key`. **Required whenever `steps` is omitted** (it is what
  the renderer derives the call tree from); with `steps` present it is informational —
  the real entry point is the first `call` step.
- `steps` — **optional**. Omit it for a `root`-only curriculum entry and let Lane-1
  `deriveSteps` build the tree client-side. Provide it only for hero scenarios that need
  hand-written narration or a recorded run's fidelity.
- `group` — the Simulate-rail section header / learning phase, e.g. `"Startup"`,
  `"A request comes in"`, `"Talking to the database"`, `"Background jobs"`,
  `"When it breaks"`. Entries with no `group` collect under one default section.
- `order` — integer position in the app's real workflow; the rail sorts ascending. Use
  the `suggested_order` from the brief as a starting point and adjust.
- `summary` — one plain line, the "what you'll learn watching this" shown under the
  title in the rail.

**Step** — `node` (a symbol key — **required**, must equal a `data.nodes[].key` verbatim,
same rule as `explanations.json`), `t` (`call` | `return` | `emit` | `note` | `branch`,
default `call`), optional `from` (the caller's key, draws the animated edge), `user` /
`code` (one sentence each — 👤 what the user perceives, ⚙ what the code is doing; either
may be omitted but at least one should carry real content), `emit` (`{text, surface?}` —
an `emit` step's `node` is just the frame it happened inside; `text` is the real output,
shown verbatim on the Stage — a page line, a response body, a job log line, a mounted
UI block, a file written; omit `surface` unless this one emit belongs on a different
stage than the rest of the scenario), `cond` (`{kind: "if"|"for"|"while"|"try", text}`
— a one-line note about the branch/loop/guard this call sits inside).

## Rules

- Every `steps[].node` (and `root`) must resolve to a **currently kept** graph node (a
  node ID that survived the `max_symbols` budget). A step whose key doesn't resolve is
  silently dropped, same as an unknown `explanations.json` key.
- A scenario that authored `steps` but is left with fewer than 2 resolvable ones is
  dropped rather than shown broken. A `root`-only scenario is dropped only if its `root`
  itself doesn't resolve — otherwise it is kept and its steps are derived.
- `call`/`return` must balance — every `call` needs a matching later `return` at the same
  nesting level, since the trace-log pane and the animated flow are both derived from
  that pairing (`explore.js`'s `normalizeSteps`).
- Prefer 15–40 steps. Longer scenarios are allowed but get harder to follow; split a long
  one into two scenarios (e.g. "the happy path" and "when it fails") instead.
- Use real snippets and real symbol keys — never invent a call that doesn't exist in the
  graph. If you want a scenario for something the static graph can't see (a branch taken,
  a loop count, real output), that's what `codemap trace` (Lane 3) is for — write
  `scenarios.json` for the narration a recorded run can't provide on its own, not as a
  substitute for actually running the code.
- No HTML in any string. `user`/`code`/`emit.text` are one or two plain sentences.

## Where this fits

See `SKILL.md` (the `codebase-to-course` skill) for the authoring workflow, and
`references/interactive-elements.md` for how a Learn-tab screen can link into a scenario
via `"sim": "<scenario-id>"`.
