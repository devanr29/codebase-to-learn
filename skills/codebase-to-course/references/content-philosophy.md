# Content philosophy

Read this before you write any prose: an explanation, a package entry, a
walkthrough page, a scenario `summary`, or an architecture title. The reader
is **an engineer new to this repo**. They can program. What they lack is a map
of *this* codebase: what each part is responsible for, how data moves through
it, and where the traps are.

## Say what a reader can't get from the signature

- A `what` line states the **responsibility**, the **inputs → outputs**, and
  any **side effect**: "Builds the explorer's data model from the index: reads
  every symbol at the graph's commit, resolves calls, and returns one
  JSON-ready dict. Read-only on the DB." Don't restate the name ("Builds the
  model.") or the docstring.
- `why` carries what bites: callers that depend on an odd behaviour, a slow
  path, an invariant, a known false positive. Leave it out if there's nothing
  to say.
- A `general` line explains the *package* for someone who hasn't used it:
  "Graph data structures and algorithms; here, directed graphs plus shortest
  paths." A `here` line is ruthlessly specific to this repo. It names the real
  modules and functions. If the sentence would read the same for any project
  using that library, it isn't a `here` line.
- A folder `purpose` answers **what would break or be missing without it**,
  never "what files live in here". `components/ui holds buttons and inputs` is
  an inventory and fails. `the shared UI kit every screen is built from; change
  a component here and every screen changes` passes.

## Precise, short, checkable

- Two or three sentences per `what` / `general` / `here` / `purpose`. A fourth
  sentence means you're explaining two things: split them or cut one.
- Technical terms are fine. Use the right one ("idempotent", "N+1 query",
  "middleware") instead of a paraphrase. A term specific to *this* project
  (its domain words, its own abstractions) goes in `glossary.json` so it gets
  a tooltip everywhere it appears. Don't gloss it inline.
- Name things the reader can open: `site/model.py::build`, not "the model
  builder". Every key you cite must be real (`find_symbol`). The reader will
  click it.
- No analogies in place of facts. A short comparison is fine when it's
  accurate ("a dispatch table: command name → handler function"), but the
  explanation must stand without it.

## Ground every behavioural claim

How the code *behaves* — who calls what, what a request hits, "only X writes
Y" — is a factual claim. It ships only when `calls` / `impact` / `search_code`
backs it, or an answered question's evidence does. Mark hops the graph can't
show (a framework calling your handler, a registry lookup) as exactly that, in
words. Never present them as calls you traced.

## Cover everything, rank nothing

The open questions, the dependency list and the entry points are the scope.
Don't hand-pick "the interesting five". A newcomer doesn't know which five
those are yet. A one-line `general` for a boring dependency still saves them a
search.

## Navigation, not completion

"Nobody understands a codebase entirely — not even the people who wrote it."
The page opens with that line, and your prose should read the same way. A
scenario `summary` is "what you'll see if you follow this": "the request path
from the router to the SQL query, including the retry". It is never "step 12
of 40". Never number scenarios as a checklist; `group` and `order` already
sort the rail.

## Frontend and backend

When the repo splits into a client and a server, the most valuable thing
`walkthrough.json`'s `intro` explains is the **seam**:
- which requests cross it;
- who sends each one;
- which handler serves it;
- what shape the data has on each side.

Give each side a `sides` entry and point `seam.see` at the real client call and
the real handler. Answered `unmatched_request` questions are your source.

## Point at the four moves

The Orientation screen teaches four ways to move around any codebase:
- search for text you saw on screen;
- jump to a definition;
- read the history;
- run it and watch the order.

Write summaries that point at these. "Watch which handler runs first, then
where the DB write happens" tells the reader which move they're about to
practise.
