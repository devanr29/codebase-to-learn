# `codemap explore` — step-by-step guide

`codemap explore` turns the symbol graph in `.codemap/index.db` into **one
self-contained file**, `.codemap/explore.html`, that you open in a browser. No
server, no build step. Seven tabs: Graph, Architecture, Map, Simulate, Learn,
Packages, Timeline. It opens on Learn's Orientation screen (§9) first, not the graph —
read the map before the words.

All commands below assume you've installed the CLI (see
[`install.md`](install.md)) and are running `codemap` from the repo root. If
you're working from a checkout instead, prefix every command with
`.venv/Scripts/python -m` (Windows) or `.venv/bin/python -m` (macOS/Linux) —
see [§14](#14-running-from-a-checkout-instead-of-an-install).

## Contents

1. [Check the tool runs](#1-check-the-tool-runs)
2. [Index the repository](#2-index-the-repository)
3. [Generate and open the explorer](#3-generate-and-open-the-explorer)
4. [The Graph tab](#4-the-graph-tab-the-main-page)
5. [The Architecture tab](#5-the-architecture-tab-the-system-as-layers)
6. [The Map tab](#6-the-map-tab-structure-not-connections)
7. [The Simulate tab](#7-the-simulate-tab-what-happens-when-it-runs-step-by-step)
8. [The Timeline tab](#8-the-timeline-tab)
9. [The Learn tab](#9-the-learn-tab-project-walkthrough)
10. [The Packages tab](#10-the-packages-tab-library--module-reference)
11. [Keep it fresh automatically](#11-keep-it-fresh-automatically-optional)
12. [Config knobs](#12-config-knobs-codemapconfigtoml)
13. [Running against a different repository](#13-running-against-a-different-repository)
14. [Running from a checkout instead of an install](#14-running-from-a-checkout-instead-of-an-install)
15. [Troubleshooting](#15-troubleshooting)

## 1. Check the tool runs

```
codemap status
```

On the first run this creates the `.codemap/` directory and a `config.toml`.
If it prints `index: empty`, that's expected — go to step 2.

## 2. Index the repository

```
codemap scan
```

This walks every commit since the last one it saw, parses the files that
changed, and stores the symbol graph in `.codemap/index.db`. The first run
indexes the whole history. Re-run `scan` whenever you've committed and want
the explorer to reflect it.

## 3. Generate and open the explorer

```
codemap explore --open
```

Writes `.codemap/explore.html` and opens it in your browser. Drop `--open` to
just write the file. The file is fully self-contained: copy it anywhere, send
it to someone, commit it in another repo — it needs nothing else to work.

Useful flags:

| Flag | Does |
|---|---|
| `--out <path>` | write somewhere other than `.codemap/explore.html` |
| `--quiet` | no summary line |
| `--max-symbols N` | cap graph nodes (default from config; see [§12](#12-config-knobs-codemapconfigtoml)) |
| `--json` | print the raw graph model as JSON and write nothing |
| `--emit-brief` | write the analysis pack the `codebase-to-course` skill uses to author the Simulate, Learn, and Packages tabs (§7, §9, §10) and to correct the Architecture tab (§5) — including the folder brief that seeds Learn's walkthrough |

## 4. The Graph tab (the main page)

**Left rail — the whole source tree.**
- click a folder to expand / collapse
- click a file to expand its functions
- click a function to focus it
- the number on each row is that symbol's caller count
- press <kbd>Ctrl</kbd>+<kbd>K</kbd> (<kbd>Cmd</kbd>+<kbd>K</kbd> on Mac), or
  click "Find anything on screen" at the top of the rail, to search — not just
  symbol names and file paths, but docstrings and the code itself, including
  text deep inside a long function (source is embedded up to `max_source_bytes`,
  §12 — a real miss says so, it doesn't just look empty)

**Centre canvas — the neural graph.**
- the Package / Module / File / Function buttons change what a dot means
- drag the background to pan; scroll to zoom
- the +/−/recenter buttons are bottom-left
- click any dot (or a tree symbol) to **focus** it

**Focus = the neuron view.** The focused symbol sits in the middle. Everything
that calls it fans out to the left (afferent); everything it calls fans right
(efferent). The "hops" slider in the top bar grows the dendrites one call hop
at a time. The "Whole graph" button clears the focus.

**Right inspector**, for the focused symbol:
- fan-in / fan-out counts
- a file-anatomy strip: the whole file as a bar, each symbol a block at its
  real line range, coloured by kind — the empty gaps are module-level code
- a blast-radius bar: "editing this reaches N callers across M files"
- **history** — how many commits actually changed this symbol and, for the
  most recent one, the reason someone recorded at the time (never a guess —
  `codemap` captures intent, it doesn't reverse-engineer it, same source as
  the Timeline tab's intent line, §8)
- the shortest path from an entry point (e.g. `GET /report` → … → `fetch`),
  with a **"Trace back to entry ▶"** link that plays that exact chain as a
  Simulate scenario instead of just printing it (hidden when this symbol *is*
  the entry point — there's nothing to walk back through)
- a "Trace calls from here" link into the Map tab's Run trace (§6)
- the symbol's source: a numbered, syntax-coloured preview of its first 30
  lines, a **View source** button (the source viewer, below), and an "Open in
  editor" link

**Source viewer.** "View source" (or the "+N more lines" link under the
preview) slides a panel over the right side of the graph with the symbol's
**whole file**: line numbers, syntax colours, the symbol's own lines shaded and
scrolled into view.
- a small square in the margin marks where each symbol starts — click it to
  focus that symbol in the graph
- a chip at the end of a line (`head ↗`) marks a call the graph knows about —
  click it to jump to the callee; the viewer stays open and follows you
- the thin strip on the right is the whole file at a glance (every symbol at
  its true position); click it to jump
- <kbd>Esc</kbd> or the ✕ closes it. The address (`#/graph/<symbol>/src`) is
  shareable
- the highlighter is built into the page (comments, strings, numbers,
  keywords, names) — no library is loaded, so it works offline
- files that missed the size budget (`max_source_bytes`, §12) or look minified
  show just the symbol's own excerpt, and the panel says so

**Legend** (bottom-right) — read this:
- **purple edge** = a same-file call the tool is confident about (tier 2)
- **grey edge** = a cross-file guess by name (tier 1, may be over-broad)
- module-level calls are **not** drawn at all
- **Folder | Layer** switch — colours the dots and edges by folder (the
  default) or by the architecture layer the Architecture tab (§5) placed each
  file in. Layer mode lists the layers with their file counts; click one to
  isolate it (the rest fade, they aren't hidden), click again to show all. The
  layout doesn't move, only the colours. Layers are **inferred** from names and
  imports, so treat them as a good guess; the folder view is the ground truth.
  The switch only appears when the Architecture tab found layers

Nothing is ever labelled "dead" — only "no inbound edge at tier N".

## 5. The Architecture tab (the system as layers)

The Graph tab shows every call and the Map tab shows import depth. The
Architecture tab draws the picture you'd sketch on a whiteboard for a new
teammate: the classic "routes → views / API → logic → models" diagram, with
each part labelled by what it's built with. It's derived from the index, so
it needs no extra step, and it works on any repo.

**What's on it**
- **Clouds at the top**: who drives the app, taken from the entry points
  codemap found:
  - *Internet*: HTTP routes
  - *Terminal*: CLI commands, or a `__main__` program
  - *User's screen*: app screens
  - *Scheduler*: background tasks
- **Coloured bands**: the layers, top to bottom. **Routes & entry**, then
  **Views & UI** beside **API**, then **Logic**, then **Data & models**. A
  layer with nothing in it isn't drawn.
- **Boxes**: the parts of the app. A box is a folder, or a single file when
  its folder mixes layers. The second line names what the part is built with
  (Flask, React, SQLAlchemy, …), or its size when no known library shows up. A
  **dashed** box is a best guess (see "How placement works" below).
- **Cylinders** in the Data band: the databases, caches, search engines and
  queues the code talks to (PostgreSQL, SQLite, Redis, Solr, …). codemap finds
  them from the driver packages the code imports, and also from `docker-compose`
  images and `package.json` / `requirements.txt` / `pyproject.toml` / `go.mod`
  dependencies. A **dashed** cylinder is declared in one of those files, but no
  indexed file imports a client for it.
- **Clouds at the bottom**: outside services such as Stripe, OpenAI,
  Anthropic and AWS.
- **Side panel**: shared & cross-cutting code (config, utils, scripts,
  plugins), with dashed lines to the layers that use it. The "Show tests"
  button adds the tests.

**The lines**
- a pair of block arrows between two layers: files in the upper layer import
  files in the lower one, so calls go down and results come back up. The arrow
  sits over the layer it points into; hover it for the two layers, how many
  imports cross, and one real pair of parts as an example. No arrows means no
  imports cross there. Hover a box to see exactly which parts it talks to.
- a sideways pair of block arrows in the gap between **Views & UI** and
  **API**: those two layers sit side by side, so this is the screens calling the
  API layer
- a dotted line down the right edge: imports that skip a layer (Routes
  straight to Data)
- a red dashed line with a **!**: a wrong-way import, where a lower layer
  imports a higher one. Sometimes it's a shortcut, sometimes it's a real cycle.

**Reading it**
- hover any box, cylinder or cloud to light up everything it talks to
- click an item to open the right-hand panel, which shows:
  - **why it's in that layer**: every reason the placement scored, e.g.
    "folder named routes/", "3× HTTP route", "imports flask"
  - what it's **built with** (click a library to open it in Packages, §10)
  - what it **talks to** and what **uses** it
  - its **entry points** (▶ plays one in Simulate, §7)
  - its **files** (click one to open it in the Graph tab)
  - a link to its folder in Learn (§9)
- with nothing selected, the panel shows the stack, how big each layer is,
  the list of wrong-way imports, and a legend
- every selection is a link: `#/arch/<layer>:<path>`

**How placement works.** Every file is scored:
- folder names (`routes/`, `views/`, `api/`, `services/`, `models/`, `utils/`,
  …) weigh most
- then file names (`cli.py`, `db.py`, `render.py`)
- then detected entry points. An HTTP handler inside `api/` counts toward API,
  not Routes.

An imported library only breaks ties. A library imported almost everywhere,
like `sqlite3` used as a type hint in a dozen modules, doesn't count toward a
layer at all. A file with no signal lands in Logic as a dashed best guess.
To rename boxes, move misplaced ones, or add a database the imports don't
reveal, the `codebase-to-course` skill (§10) writes corrections into
`.codemap/architecture.json`. See
`skills/codebase-to-course/references/architecture-schema.md` for the file
format.

## 6. The Map tab (structure, not connections)

The Graph tab answers "what calls what". The Map tab answers three questions
it doesn't: what *shape* is the system, what happens when it *runs*, and
*where* is the code. Three views, switched with the buttons at top left. Built
from the same graph — no extra step, no extra data.

**Layers — the dependency skeleton.** Files are stacked into layers by their
in-repo imports: L0 at the bottom imports nothing else in the repo (config,
logging, small helpers), each layer above imports only from below, entry
points end up at the top.
- block width = lines of code
- block colour = folder (same colours as the Graph tab legend)
- a block marked with a loop arrow is an **import cycle** — several files that
  import each other, collapsed into one block; hover it for the members
- hover any block to light up what it imports (downward) and what imports it
  (upward)
- click a block to jump to that file in the Graph tab
- "Hide tests" drops `tests/**` so the production skeleton stands alone

**Run trace — one call chain as a subway map.** Pick a starting function from
the dropdown (ranked by how much they reach). Columns are call depth: the root
on the left, what it calls in the next column, and so on.
- dot size = how many callers that function has
- dot colour = folder
- "+N more calls" appears when a column is busy; click to expand it
- the `<` / `>` buttons step a highlight one hop at a time, so you can watch
  the cascade instead of reading a wall of lines
- a lightning-bolt card means the chain hit a call the indexer can't follow
  statically — e.g. a CLI's `args.func(args)`, where the real target is chosen
  at runtime. The trace stops there **on purpose** and tells you, rather than
  pretending the function is a dead end.

The Graph tab's inspector has a "Trace calls from here" link that opens this
view on the focused symbol.

**Mass — a treemap of the whole repo.**
- rectangle area = lines of code, grouped by folder
- fill = how many commits have touched the file (darker = quiet, brighter =
  changes a lot)
- a hatched rectangle = every function in that file is unreachable at the
  current tier — a candidate for deletion, or a sign of missing edges
- click a rectangle to open the file in the Graph tab

## 7. The Simulate tab (what happens when it runs, step by step)

The Graph and Map tabs are about **structure**. Simulate is about **time**: it
replays one run of the code as an animation you drive with a transport bar
(play / pause / step / scrub / speed).

**Left rail — the scenario curriculum.** A pinned card above the list points
at whichever scenario sorts first (lowest `order`) — "if you follow only one,
follow this one." One thread followed all the way through beats reading files
at random; the rest of the rail is there for later, not for first. Scenarios
are grouped into sections ("Startup", "A request comes in", "Talking to the
database", "When it breaks", …) and ordered the way the app actually runs.
Only the section you're in (and the first one) is expanded; click a section
header to open another, or use the filter box when there are many. Each row
has a lane icon:

| Icon | Lane | Meaning |
|---|---|---|
| ⚡ | derived | computed from the call graph, not a real run — shown only when nobody has authored `scenarios.json` |
| ✏ | authored | written into `.codemap/scenarios.json` by the `codebase-to-course` skill (§10); most entries just name a starting function, the page derives the call tree |
| ⏺ | recorded | a real run captured with `codemap trace` (real branches taken, real loop counts, real output) |

**Main area** — four resizable, collapsible panes plus a narration line. Drag
the handle between two panes to resize; the caret button on a pane header
collapses it; double-click a pane header (or its corners-out button) to
maximise that one pane full-bleed — <kbd>Esc</kbd>, or double-click again,
restores.

- **Stage** — the user's world, rendered as one of seven mockups (terminal,
  browser, API, background job, database, UI component tree, file) that fills
  in as output is produced. Which one is picked adapts to the scenario: an
  authored `trigger.surface` always wins, otherwise it's inferred from what the
  root symbol actually is — an HTTP route, a background task, a React/Vue
  component, a database call, and so on (see `scenarios-schema.md` in the
  `codebase-to-course` skill for the full inference order).
- **Flow** — the call graph for this run; the active function lights up, a
  token flies along each call edge as it happens.
- **Trace log** — every step of the run as a scrollable list. The current step
  is highlighted; steps you haven't reached yet are dimmed. Click any row to
  jump straight to that point — forward or back. A breadcrumb line at the top
  (`f() › g() › h()`) is the live call stack.
- **Source** — the source of the running function, with the executing line
  highlighted.

Under the panes, two lines per step: a person icon = what the user sees, a
gear icon = what the code is doing. The first time a step touches a
third-party library the Packages tab actually describes, a third, quieter
line shows that blurb inline — click it to open the full entry in Packages
(§10).

A derived (⚡) scenario's banner spells out honestly what it is — a prediction
from the code's shape, not a recorded run — and gives you the exact
`codemap trace` command to record the real thing instead.

To get authored scenarios: same as the Packages tab — run `--emit-brief`,
invoke the `codebase-to-course` skill, re-render (§10). With no authoring at all,
Simulate still works: it offers a derived scenario for the busiest few
functions, plus an on-demand one for any Graph-tab symbol via its "Trace back
to entry ▶" link (§4). See
`skills/codebase-to-course/references/scenarios-schema.md` for the file
format.

## 8. The Timeline tab

Newest commit first. Each card shows:
- the stated intent and where it came from (commit message / note / session)
- coloured bars: structural / behavioral / cosmetic change counts
- the headline change and its blast radius
- any new dependency

Click "Read this first: …" on a card to jump straight to that symbol in the
Graph tab.

## 9. The Learn tab (project walkthrough)

This is where `explore.html` opens by default — on **Orientation**, not the
graph. It states the tool's whole premise up front (nobody understands a
codebase entirely, including whoever wrote it), lays out which tabs show what
*exists* (Graph, Architecture, Map, Learn, Packages) versus what *happens* (Simulate,
Timeline), lists the repo's own top-level folders before anything else, and
names the four ways to actually move around unfamiliar code: search for text
you saw on screen, jump to a definition, read the history, run it and watch
the order. "Start here" at the top of the rail always returns to it.

Past Orientation, Learn is a guided tour of the codebase itself — what its
parts are and how they fit together — driven by `.codemap/walkthrough.json`
when one exists:

- **The parts of this app** — shown only when `walkthrough.json`'s `intro.sides`
  authors at least one side. A plain-language frontend/backend-style split (or
  whatever division the project actually has): each side gets an invented
  name, a short title, and a couple of sentences on what lives under its root
  folder, with a link into that folder's own page. An optional **seam** note
  says where and how two sides actually connect, with clickable call sites
  into the Graph tab.
- **What this app is made of** — shown only when `walkthrough.json` authors
  `categories`. Groups references by *topic* instead of by file or folder —
  "Website design → Animation," "Money movement → Syncing" — the kind of
  grouping a file tree can't show on its own; each group links into the graph.
- **One page per folder** — every folder `codemap` derives from the file tree
  gets a page, indented by depth in the rail, whether or not anyone authored a
  word about it. This only covers folders holding at least one file in an
  indexed language; a folder containing exclusively unindexed file types
  (Markdown-only docs, a data-only directory, a skill folder that's just a
  `SKILL.md`) never enters the file tree at all, so it never becomes a
  folder page and is skipped entirely, authored or not — there's no derived
  row to attach prose to. With no authored entry, a folder's page shows what can be
  derived: file and symbol counts, languages, and its direct dependencies. An
  authored `walkthrough.json` entry replaces that with real prose — what the
  folder is actually *for*, an optional "start reading here" file, a caveat,
  and its own "see in the graph" links (or, if the folder just points back at
  the shared category list, the matching category groups scoped to that
  folder). A hollow circle next to a folder name flags it as an **import
  orphan** — nothing in the repo imports it — worth checking, not a verdict;
  the folder's own page is where that gets confirmed or explained.

Folder coverage is **not** required to be exhaustive the way the Packages
dependency reference is — the skill writes a folder's prose only where it
earns one; most folders are fine left as a bare row in the tree.

**Left rail** lists whichever of the three screens above actually have
content, then every surviving folder in parent-before-child order. Past 12
folders a filter box appears, matching on path and name.

If `walkthrough.json` is missing or malformed the tab falls back to a bare
derived folder tree — no intro, no categories, nothing breaks. See
`skills/codebase-to-course/references/walkthrough-schema.md` for the file
format.

Wherever authored prose renders — here, in Packages' "In general"/"In this
codebase" lines (§10), anywhere else it's shown — an underlined word is a
glossary hookup: hover it, or reach it with keyboard focus, for a
plain-language definition. It's sourced from two dictionaries bundled with
`codemap` itself (well-known packages, common technical concepts) plus an
optional per-project `.codemap/glossary.json` for anything project-specific.
Which term gets underlined is picked automatically — nothing about it is
authored per instance.

## 10. The Packages tab (library & module reference)

The Packages tab explains the code's **dependencies**, not the code itself —
this is what the Learn tab used to be, moved to its own tab now that Learn is
a walkthrough. It lists every package the project imports (third-party and
standard library) and every top-level module of the repo, and for each one
shows:

- **In general** — what that library/module is, for someone who's never heard
  of it (one or two plain sentences).
- **In this codebase** — the job it actually does here: which files import it,
  which functions use it.
- **See in the graph** — a few clickable call sites that jump into the Graph tab.

With no `#/libs/<slug>` in the URL, Packages opens on the first entry in the
list rather than a landing screen of its own.

**Left rail** groups the entries: Third-party, Standard library, This repo's
modules. A hollow circle next to a name means nobody has written a description
for it yet. Past 12 entries a filter box appears above the list — it matches
on name and kind (e.g. "internal" finds this repo's own modules).

Out of the box the "in general" line comes from a small built-in table of
well-known libraries (React, Flask, networkx, pandas, the stdlib, …), and "in
this codebase" is derived from the import graph. To fill the gaps — the repo's
own modules, niche packages, and a hand-written "in this codebase" line:

1. Emit the analysis pack:
   ```
   codemap explore --emit-brief
   ```
   `00-overview.md` now has a "Dependency reference" section listing every
   entry to describe, and a `libraries-derived.json` to annotate.
2. In Claude Code, invoke the skill — say "explain this codebase" or run
   `/codemap:course`. It reads the briefs and `references/`, then writes
   `.codemap/libraries.json` (plus `explanations.json` for the Graph inspector,
   `scenarios.json` for the Simulate tab, and `walkthrough.json` for the Learn
   tab, §9).
3. Re-render:
   ```
   codemap explore --open
   ```

If `libraries.json` is malformed the tab silently falls back to the built-in
blurbs + import graph — nothing breaks. See
`skills/codebase-to-course/references/libraries-schema.md`.

## 11. Keep it fresh automatically (optional)

```
codemap install-hook
```

Installs a post-commit hook that runs scan → explain → explore in the
background after every commit, so `explore.html` is always current. It can
never block or fail a commit.

To turn the auto-rebuild off for this repo, set in `.codemap/config.toml`:

```toml
[explore]
rebuild_on_commit = false
```

Manual `codemap explore` still works when this is `false`.

## 12. Config knobs (`.codemap/config.toml`)

```toml
[explore]
rebuild_on_commit = true    # does the post-commit hook rebuild the page
max_symbols       = 1500    # graph node cap; over this the graph drops to
                             #   file-level and the page says so. The tree
                             #   and file list stay complete.
max_snippet_lines = 40      # longest source excerpt embedded per symbol
                             #   (Simulate reads these; the viewer shows any length)
max_source_bytes  = 4000000 # whole-file source embedded for the source viewer
                             #   and code search. Most relevant files first
                             #   (entry points, then churn + connectivity); past
                             #   the budget a file keeps only its short excerpts.
                             #   0 = embed no files.
```

`max_source_bytes` is the knob for page size: on this repo the whole page is
about 2.3 MB with every file embedded. Minified files and files with a line
over 4,000 characters are never embedded.

## 13. Running against a different repository

`--path` works on every subcommand:

```
codemap scan    --path ../other-repo
codemap explore --path ../other-repo --emit-brief
codemap explore --path ../other-repo
```

## 14. Running from a checkout instead of an install

If you're developing `codemap` itself rather than using an installed copy,
run it as a module from the repo root instead of installing it — see
[`install.md`](install.md) for the venv setup:

```
.venv/Scripts/python -m codemap scan
.venv/Scripts/python -m codemap explore --open
```

Or activate the venv for your shell session (`.venv\Scripts\activate` on
Windows, `deactivate` to disconnect) and drop the prefix.

## 15. Troubleshooting

**Yellow "Built from … HEAD is now …" banner**
You've committed since the last render. Run `codemap explore` again.

**"index is empty" / "no index yet"**
Run `codemap scan` first.

**A function you expected shows no callers**
Check the tier badge in the inspector. "No callers at tier 1" means
name-based resolution couldn't see them (aliased imports, dynamic dispatch),
not that none exist.

**The Architecture tab puts a part in the wrong layer, or misses a database**
Click the box and read "Why it's here": every reason it was placed there is
listed. A dashed box had no clear signal and landed in Logic by default. The
usual cause is a folder whose name says nothing about its job (`lib/`, `src/`,
`app/`). A database or service reached with no import and no manifest entry,
such as a plain HTTP call or a connection URL read from the environment, has
nothing to detect. Fix either by adding `.codemap/architecture.json` (the
`codebase-to-course` skill writes it, or see §5 for the schema), then run
`codemap explore` again.

**Icons or fonts look off**
Phosphor Icons are vendored into the page itself (base64, no CDN) — they
render the same offline or on a network that blocks arbitrary CDNs. Only the
body font (Inter) loads from Google Fonts; if that's blocked it degrades to a
system font and the page keeps working. If icons are still blank, you're
likely on a build from before this was fixed — regenerate with
`codemap explore`.

The generated page never contacts the internet except for that one optional
Google Fonts stylesheet. The optional LLM narrative stage (`[llm] enabled =
false` by default) is the only thing that would call an API, and the explorer
never uses it.
