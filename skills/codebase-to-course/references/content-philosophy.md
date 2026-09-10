# Content philosophy

Read this before writing `walkthrough.json`, `libraries.json`,
`explanations.json` or the `summary` lines in `scenarios.json`. Every reader
is a **vibe coder** — no CS background, here to acquire the vocabulary and
judgement to steer AI tools.

## Write for someone who's never heard of it

- A `general` line explains the *package*, not this repo: "Graphs as data —
  build nodes and edges, then run algorithms." Someone should be able to read it
  with zero context and know whether it matters to them.
- A `here` / `what` line is the opposite: ruthlessly specific to this codebase.
  Name the real modules and functions. If the sentence would read the same for
  any project using that library, it's not a `here` line.
- A folder `purpose` line answers **what would be lost, not what's inside**:
  "what would I lose if this folder were deleted," never "what files live in
  here." `components/ui holds the buttons, cards, and inputs` is an inventory
  and fails; `without this folder there's nothing to build a screen out of`
  names the job and passes.

## Two or three sentences, then stop

- `general`, `here`, `what`, folder `purpose`: 2–3 plain sentences each. A
  fourth sentence means you're explaining two things — split them or cut one.
- **The tooltip carries jargon now, not the sentence.** Two bundled
  dictionaries (~200 package/tool names, ~75 core programming concepts —
  REPL, CLI, entry point, PATH, namespace, WSGI, ORM, ASGI, decorator, and
  more) already power a hover/tap tooltip on those words wherever your prose
  is rendered. A term either dictionary covers needs **no inline gloss at
  all** — just write "a **hook**," not "a **hook** — a function that lets a
  component tap into state or lifecycle events —". This is a real change from
  before: don't write the old parenthetical-gloss pattern anymore. A term
  *neither* dictionary covers — this project's own vocabulary, or an
  everyday term that slipped through both lists — gets no inline gloss
  either; it goes in `.codemap/glossary.json` instead (see
  `references/glossary-schema.md`), never a parenthetical bolted onto the
  sentence. **The vocabulary is still the learning** — it just lives in the
  glossary pool now, not the prose. (Don't gloss terms the reader owns from
  their own domain.)

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

## Navigation, not completion

Cover everything (previous section) — but never write as if finishing the
list is the point. "Nobody understands a codebase entirely — not even the
people who wrote it" is the tool's own opening line (the Learn tab's
Orientation screen says this before anything else, unconditionally, whether or
not this skill has run). Authored prose should read the same way: a `summary`
is "what you'll see if you look here," not "step 12 of 40." Never write
"complete this scenario" or number scenarios as a sequence to finish — `group`
and `order` already tell the rail how to sort them; the copy shouldn't imply a
checklist on top of that. A learner who reads three entries and stops has not
failed to finish anything.

## The restaurant — one house metaphor, reused

Reuse this one analogy across every module's `here` line instead of inventing
a new one per file — a vibe coder who has seen it once in the Map tab's own
legend should recognize it again in your prose:

> The **menu** is what the outside world can ask for (routes, public
> functions). The **waiter** carries the request back but doesn't cook
> (controllers, thin request handlers). The **kitchen** does the actual work
> (services, business logic — most of the code lives here). The **fridge /
> pantry** is what survives after everyone goes home (the database, the
> filesystem, persisted state). The **health inspector** checks the kitchen
> without doing any cooking (tests).

Use the piece that actually fits — don't force all five onto a repo that only
has three. A `here` line for a `services/` module can say "this is the
kitchen: the actual `<verb>` logic other layers call into," reusing language
the reader has already met once, instead of restating the whole architecture
from scratch on every page.

## The dining room and the kitchen

When a repo splits into a frontend and a backend, this is now the single most
valuable thing `walkthrough.json`'s `intro` explains — and the restaurant
metaphor extends across that seam instead of stopping at it. The frontend is
the **dining room**: what the customer sees, sits in, and touches — screens,
buttons, layout. The backend is the **kitchen and storage**: where the work
actually happens and where things are kept, out of the customer's sight —
routes, business logic, the database. Use this framing for `intro.sides` and
`intro.seam` specifically; it's a different cut than the five-role breakdown
above, which describes what happens *inside* one side (usually the kitchen),
not the split between the two.

## Reinforce the four moves, don't compete with them

The Orientation screen already teaches four ways to move around any codebase:
search for text you saw on screen, jump to a definition, read the history,
run it and watch the order. Authored `summary` lines should point at these,
not around them — "watch what prints, in order" is a better scenario summary
than "understand the explore command," because it tells the reader which of
the four moves they're about to practice.
