# Content philosophy

Read this before writing `libraries.json`, `explanations.json` or the `summary`
lines in `scenarios.json`. Every reader is a **vibe coder** — no CS background,
here to acquire the vocabulary and judgement to steer AI tools.

## Write for someone who's never heard of it

- A `general` line explains the *package*, not this repo: "Graphs as data —
  build nodes and edges, then run algorithms." Someone should be able to read it
  with zero context and know whether it matters to them.
- A `here` / `what` line is the opposite: ruthlessly specific to this codebase.
  Name the real modules and functions. If the sentence would read the same for
  any project using that library, it's not a `here` line.

## Two or three sentences, then stop

- `general`, `here`, `what`: 2–3 plain sentences each. A fourth sentence means
  you're explaining two things — split them or cut one.
- No jargon without an inline gloss. "A **flag** is an option on a command."
  Everyday dev jargon counts: REPL, CLI, entry point, PATH, namespace, WSGI,
  ORM, ASGI, decorator. Every acronym on first use. **The vocabulary is the
  learning.** (Don't gloss terms the reader owns from their own domain.)

## Anchor in what they did

Prefer "you ran `codemap explore` — this is the package that parses your files"
over "tree-sitter is an incremental parsing library." Tie the abstract thing to
an action they've taken with the software.

## Never touch the code

`see` keys and the symbols you cite must be real. Don't invent a call site, a
function name, or a file path — the reader will open the file and check.

## Cover everything, rank nothing

The Dependency reference and the Scenario index are the scope. Don't hand-pick
"the interesting five" — a newcomer doesn't know which five those are yet. A
one-line `general` for a boring dependency still saves them a web search.
