# `.codemap/scenarios.json` schema

Authored input for the **Simulate** tab (Lane 2 — see `codemap/site/scenarios.py`, the
loader; `codemap/site/simulate.py`, which resolves it against the graph). Absent or
malformed is not an error: the tab falls back to what it always has — a derived scenario
computed client-side from the call graph (Lane 1), and any recorded `codemap trace` runs
(Lane 3, `.codemap/traces/*.json`, never hand-authored).

```json
{
  "scenarios": [
    {
      "id": "explore-run",
      "title": "Running `codemap explore`",
      "trigger": { "surface": "terminal", "text": "codemap explore" },
      "root": "codemap/cli.py::cmd_explore",
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
`trigger` (`surface`: `terminal` | `browser` | `api` | `file`, `text`: what the user
types/clicks/sends), optional `root` (a `data.nodes[].key` — informational; the real
entry point is just the first `call` step).

**Step** — `node` (a symbol key — **required**, must equal a `data.nodes[].key` verbatim,
same rule as `explanations.json`), `t` (`call` | `return` | `emit` | `note` | `branch`,
default `call`), optional `from` (the caller's key, draws the animated edge), `user` /
`code` (one sentence each — 👤 what the user perceives, ⚙ what the code is doing; either
may be omitted but at least one should carry real content), `emit` (`{surface, text}` —
an `emit` step's `node` is just the frame it happened inside; `surface` should usually
match the scenario's `trigger.surface`), `cond` (`{kind: "if"|"for"|"while"|"try", text}`
— a one-line note about the branch/loop/guard this call sits inside).

## Rules

- Every `steps[].node` must resolve to a **currently kept** graph node (a node ID that
  survived the `max_symbols` budget). A step whose key doesn't resolve is silently
  dropped, same as an unknown `explanations.json` key; a scenario left with fewer than 2
  steps after that is dropped entirely rather than shown broken.
- `call`/`return` must balance — every `call` needs a matching later `return` at the same
  nesting level, since the call-stack pane and the animated flow are both derived from
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
