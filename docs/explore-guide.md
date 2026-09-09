# `codemap explore` — step-by-step guide

`codemap explore` turns the symbol graph in `.codemap/index.db` into **one
self-contained file**, `.codemap/explore.html`, that you open in a browser. No
server, no build step. Five tabs: Graph, Map, Simulate, Learn, Timeline. It
opens on Learn's Orientation screen (§8) first, not the graph — read the map
before the words.

All commands below assume you've installed the CLI (see
[`install.md`](install.md)) and are running `codemap` from the repo root. If
you're working from a checkout instead, prefix every command with
`.venv/Scripts/python -m` (Windows) or `.venv/bin/python -m` (macOS/Linux) —
see [§12](#12-running-from-a-checkout-instead-of-an-install).

## Contents

1. [Check the tool runs](#1-check-the-tool-runs)
2. [Index the repository](#2-index-the-repository)
3. [Generate and open the explorer](#3-generate-and-open-the-explorer)
4. [The Graph tab](#4-the-graph-tab-the-main-page)
5. [The Map tab](#5-the-map-tab-structure-not-connections)
6. [The Simulate tab](#6-the-simulate-tab-what-happens-when-it-runs-step-by-step)
7. [The Timeline tab](#7-the-timeline-tab)
8. [The Learn tab](#8-the-learn-tab-library--module-reference)
9. [Keep it fresh automatically](#9-keep-it-fresh-automatically-optional)
10. [Config knobs](#10-config-knobs-codemapconfigtoml)
11. [Running against a different repository](#11-running-against-a-different-repository)
12. [Running from a checkout instead of an install](#12-running-from-a-checkout-instead-of-an-install)
13. [Troubleshooting](#13-troubleshooting)

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
| `--max-symbols N` | cap graph nodes (default from config; see [§10](#10-config-knobs-codemapconfigtoml)) |
| `--json` | print the raw graph model as JSON and write nothing |
| `--emit-brief` | write the analysis pack the `codebase-to-course` skill uses to author the Simulate and Learn tabs (§6 and §8) |

## 4. The Graph tab (the main page)

**Left rail — the whole source tree.**
- click a folder to expand / collapse
- click a file to expand its functions
- click a function to focus it
- the number on each row is that symbol's caller count
- press <kbd>Ctrl</kbd>+<kbd>K</kbd> (<kbd>Cmd</kbd>+<kbd>K</kbd> on Mac), or
  click "Find anything on screen" at the top of the rail, to search — not just
  symbol names and file paths, but docstrings and the source shown on the page
  (capped by `max_snippet_lines`, §10 — a real miss says so, it doesn't just
  look empty)

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
  the Timeline tab's intent line, §7)
- the shortest path from an entry point (e.g. `GET /report` → … → `fetch`),
  with a **"Trace back to entry ▶"** link that plays that exact chain as a
  Simulate scenario instead of just printing it (hidden when this symbol *is*
  the entry point — there's nothing to walk back through)
- a "Trace calls from here" link into the Map tab's Run trace (§5)
- the symbol's source, and an "Open in editor" link

**Legend** (bottom-right) — read this:
- **purple edge** = a same-file call the tool is confident about (tier 2)
- **grey edge** = a cross-file guess by name (tier 1, may be over-broad)
- module-level calls are **not** drawn at all

Nothing is ever labelled "dead" — only "no inbound edge at tier N".

## 5. The Map tab (structure, not connections)

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

## 6. The Simulate tab (what happens when it runs, step by step)

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
| ✏ | authored | written into `.codemap/scenarios.json` by the `codebase-to-course` skill (§8); most entries just name a starting function, the page derives the call tree |
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
third-party library the Learn tab actually describes, a third, quieter line
shows that blurb inline — click it to open the full entry in Learn (§8).

A derived (⚡) scenario's banner spells out honestly what it is — a prediction
from the code's shape, not a recorded run — and gives you the exact
`codemap trace` command to record the real thing instead.

To get authored scenarios: same as the Learn tab — run `--emit-brief`, invoke
the `codebase-to-course` skill, re-render (§8). With no authoring at all,
Simulate still works: it offers a derived scenario for the busiest few
functions, plus an on-demand one for any Graph-tab symbol via its "Trace back
to entry ▶" link (§4). See
`skills/codebase-to-course/references/scenarios-schema.md` for the file
format.

## 7. The Timeline tab

Newest commit first. Each card shows:
- the stated intent and where it came from (commit message / note / session)
- coloured bars: structural / behavioral / cosmetic change counts
- the headline change and its blast radius
- any new dependency

Click "Read this first: …" on a card to jump straight to that symbol in the
Graph tab.

## 8. The Learn tab (library & module reference)

This is also where `explore.html` opens by default — on **Orientation**, not
a library entry. It states the tool's whole premise up front (nobody
understands a codebase entirely, including whoever wrote it), lays out which
tabs show what *exists* (Graph, Map, Learn) versus what *happens* (Simulate,
Timeline), lists the repo's own top-level folders before anything else, and
names the four ways to actually move around unfamiliar code: search for text
you saw on screen, jump to a definition, read the history, run it and watch
the order. "Start here" at the top of the rail always returns to it.

Past that screen, the Learn tab explains the code's **dependencies**, not the
code itself. It lists every package the project imports (third-party and
standard library) and every top-level module of the repo, and for each one
shows:

- **In general** — what that library/module is, for someone who's never heard
  of it (one or two plain sentences).
- **In this codebase** — the job it actually does here: which files import it,
  which functions use it.
- **See in the graph** — a few clickable call sites that jump into the Graph tab.

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
   `.codemap/libraries.json` (plus `explanations.json` for the Graph inspector
   and `scenarios.json` for the Simulate tab).
3. Re-render:
   ```
   codemap explore --open
   ```

If `libraries.json` is malformed the tab silently falls back to the built-in
blurbs + import graph — nothing breaks. See
`skills/codebase-to-course/references/libraries-schema.md`.

## 9. Keep it fresh automatically (optional)

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

## 10. Config knobs (`.codemap/config.toml`)

```toml
[explore]
rebuild_on_commit = true    # does the post-commit hook rebuild the page
max_symbols       = 1500    # graph node cap; over this the graph drops to
                             #   file-level and the page says so. The tree
                             #   and file list stay complete.
max_snippet_lines = 40      # longest source excerpt embedded per symbol
```

## 11. Running against a different repository

`--path` works on every subcommand:

```
codemap scan    --path ../other-repo
codemap explore --path ../other-repo --emit-brief
codemap explore --path ../other-repo
```

## 12. Running from a checkout instead of an install

If you're developing `codemap` itself rather than using an installed copy,
run it as a module from the repo root instead of installing it — see
[`install.md`](install.md) for the venv setup:

```
.venv/Scripts/python -m codemap scan
.venv/Scripts/python -m codemap explore --open
```

Or activate the venv for your shell session (`.venv\Scripts\activate` on
Windows, `deactivate` to disconnect) and drop the prefix.

## 13. Troubleshooting

**Yellow "Built from … HEAD is now …" banner**
You've committed since the last render. Run `codemap explore` again.

**"index is empty" / "no index yet"**
Run `codemap scan` first.

**A function you expected shows no callers**
Check the tier badge in the inspector. "No callers at tier 1" means
name-based resolution couldn't see them (aliased imports, dynamic dispatch),
not that none exist.

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
