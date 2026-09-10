---
name: codebase-to-course
description: >-
  Explain an indexed codebase inside codemap's explore.html for a non-technical
  "vibe coder". Use when someone wants to "explain this codebase interactively",
  "teach this code", "make a walkthrough from this project", "explain the
  functions / the dependencies", "simulate what happens when I run/click/call
  this", or asks to fill in the Learn tab, the Packages tab, the Simulate tab,
  or the inspector explanations of the Codegraph Explorer. Produces
  .codemap/walkthrough.json (what this project is, how its parts relate, a
  repo-wide category map, and one page per folder worth explaining — the Learn
  tab), .codemap/libraries.json (what every imported package and every repo
  module does, in general and in this code — the Packages tab),
  .codemap/explanations.json (a one-line plain-English blurb per symbol, shown
  in the Graph inspector), .codemap/scenarios.json (the Simulate tab's scenario
  curriculum — every workflow of the app as an ordered, grouped entry, steps
  derived or hand-narrated), and .codemap/glossary.json (project-specific term
  definitions that power hover/tap tooltips everywhere else) — all baked into
  the page by `codemap explore`.
---

# codebase-to-course

Writes five JSON files under `.codemap/` that the deterministic `codemap
explore` renderer bakes into `explore.html`. It never generates a standalone
site and never edits the renderer.

**Prerequisite:** the `codemap` CLI must be on `PATH` — this skill only
authors content, it doesn't index or render anything itself. If `codemap
--version` fails, install it first (`pipx install git+https://github.com/devanr29/codebase-to-learn`
— see `docs/install.md` in the codemap repo, or tell the user to run that
before continuing).

| File | Tab it fills | What it is |
|---|---|---|
| `walkthrough.json` | **Learn** | what the project is, how its parts relate, a repo-wide category map, and one page per folder worth explaining |
| `libraries.json` | **Packages** | every imported package + every repo module: what it does in general, and its job here |
| `explanations.json` | **Graph** inspector | one plain-English `what` line per symbol |
| `scenarios.json` | **Simulate** | the workflow curriculum — one entry per real run of the app |
| `glossary.json` | tooltips, every tab | project-specific term definitions, for gaps the two bundled dictionaries don't cover |

## Audience

A **vibe coder**: builds software with AI tools, no CS background. Goals — steer
AI coding tools, catch when the AI is wrong, escape bug loops, make
build-vs-buy calls, and **acquire the vocabulary of software**. They are *not*
becoming software engineers. Write every line for someone who has never heard
of the library, folder, or term you're describing — a bundled or authored
glossary tooltip carries the definition now, so spend your sentences on what
the thing is *for*.

## Workflow

1. **Index and emit the analysis pack.**
   ```
   codemap scan            # only if there is no index yet
   codemap explore --emit-brief
   ```
   `.codemap/briefs/00-overview.md` now carries a **Dependency reference**
   (every import + every module, with importing files), a **Folder reference**
   (every folder candidate, with file counts, direct dependencies, and an
   advisory "nothing imports this" flag where applicable), and a **Scenario
   index** (every entry point with a suggested workflow group + order).
   Per-module briefs carry verbatim pre-extracted snippets and each symbol's
   `key:`. Two derived packs sit alongside: `libraries-derived.json`,
   `scenarios-derived.json`.

2. **Read `00-overview.md`**, then the per-module briefs, plus
   `references/content-philosophy.md` and `references/gotchas.md` (always) and
   the schema files for whatever you're about to write: `references/walkthrough-schema.md`,
   `references/glossary-schema.md`, `references/libraries-schema.md`,
   `references/explanations-schema.md`, `references/scenarios-schema.md`.

3. **Write `.codemap/walkthrough.json`** — the Learn tab, now a project
   walkthrough. Follow `references/walkthrough-schema.md`.
   - `intro` — what the project is, plus a `sides` entry for each top-level
     part actually rooted somewhere in the repo (skip `sides` for a
     single-part repo). Add `seam` only when 2+ sides genuinely connect
     somewhere in the code.
   - `categories` — invent this repo's own "what this app is made of" map.
     Plain outcomes a vibe coder would recognize, never technology labels —
     see `references/gotchas.md`.
   - `folders` — start from the **Folder reference**. Write an entry only for
     folders that earn one; treat an advisory orphan flag as a question to
     investigate, never a fact to assert.
   - Then check the two bundled glossary dictionaries — skim
     `codemap/site/data/glossary-concepts.json` and `glossary-packages.json`
     once — and write only the gaps into `.codemap/glossary.json`, following
     `references/glossary-schema.md`: a term specific to this project's own
     domain, or a term neither bundled file happens to cover.

4. **Write `.codemap/libraries.json`** — the Packages tab, a library / module
   reference. Follow `references/libraries-schema.md`. Start from
   `libraries-derived.json` and cover **every** entry in the Dependency
   reference:
   - `general` — one or two plain sentences: what this package/module *is*, for
     someone who's never used it. Skip it only for obvious stdlib (`json`,
     `os`); the renderer has a bundled blurb for the common ecosystem, so your
     job is the gaps and the repo's own modules.
   - `here` — one or two concrete sentences: the job it does *in this repo*.
     Name the modules and functions that touch it. If your sentence would read
     the same for any project, it belongs in `general` or nowhere.
   - `see` — `data.nodes[].key` list, the call sites worth opening in the Graph
     tab (the briefs print every key). Technically optional, but worth filling
     in: `see` is also how the Simulate tab knows to show this library's blurb
     inline the first time a scenario's steps actually touch it — an empty
     `see` means that cross-link never fires for this entry, even when a
     scenario clearly calls into it.

5. **Write `.codemap/explanations.json`** following
   `references/explanations-schema.md`. One plain-English `what` line for every
   symbol in the per-module briefs (hotspots + entry points) and every key you
   put in a `libraries.json` `see`. Key the JSON by the symbol `key:`. Optional
   `why` and `terms`.

6. **Write `.codemap/scenarios.json`** — the Simulate tab's scenario
   *curriculum*, following `references/scenarios-schema.md`.

   **Index first, cheaply.** The `## Scenario index` in `00-overview.md` lists
   every entry point with a suggested `group` and `order`. In **one pass**, turn
   it into an ordered, grouped list — each entry an `id` + `title` + `root` (the
   `key:` shown) + `group` + `order` + one-line `summary`, **no `steps`** —
   sorted by how the app really runs (startup → an inbound request → background
   jobs → when it breaks). Ship them *all*: 40 routes → 40 scenarios. The
   renderer derives every call tree, so a `root`-only entry is a few bytes and
   still plays.

   **Hero steps only.** Hand-author `steps` for just the 3–6 most important
   scenarios — narrate and trim the real tree in `scenarios-derived.json`, never
   reconstruct a call tree by hand. For real branch/loop/output fidelity record
   an actual run: `codemap trace --name "<title>" -- <command>` (never edit its
   output by hand).

   **Frontend screens are scenarios too.** If the Scenario index has `screen`/
   `layout` entries (codemap detects expo-router, Next.js app/pages router,
   Remix/React Router flat routes, and React Navigation registrations), they
   sort into their own groups ("The app shell mounts", "A screen opens") ahead
   of the backend — treat them exactly like a route: ship every one as a
   `root`-only entry, no different authoring effort. If the repo has **both** a
   frontend and a backend, make at least one hero scenario cross the seam:
   root it at a screen, and narrate through to the API-client call and the
   backend route it hits (`scenarios-derived.json`'s derived tree for a screen
   root already includes that hop when the code is written to import the API
   client directly). That one scenario is worth more than either half alone —
   it is the only place in the whole surface that shows how the two sides of
   the app actually connect.

   **Point at a safe first edit.** codemap never edits code — it only reads
   and explains it — but a `screen`/`layout` scenario's `summary` can still
   name one harmless, literal string visible in that screen's source (a
   button label, a heading, a placeholder) worth changing and re-running. That
   moment — a string in a file becoming a thing on screen — is when "this is
   all just text a person typed" actually clicks. Only ever point at a plain
   string literal already sitting in the excerpt; never suggest touching logic.

7. **Bake and review.**
   ```
   codemap explore --open
   ```
   Learn tab: `intro` renders top to bottom (the `what` line, each side's
   `body`, the `seam` note if there is one); the category map's groups read as
   things a vibe coder would recognize, not tech labels; every folder the
   brief flagged either has authored prose or reads sensibly with none.
   Packages tab: every third-party package and every repo module still has a
   real `general` *and* `here` — no "add a line to libraries.json"
   placeholders left on anything that matters; `see` links land on the right
   symbol. Graph tab: focus a hotspot and confirm its inspector shows the
   explanation, and hover a glossed term to confirm the tooltip fires.
   Simulate tab: the rail groups your scenarios in workflow order —
   spot-check the grouping, then play each hero scenario through once.

## Non-negotiables

- `.codemap/walkthrough.json`, `.codemap/libraries.json`,
  `.codemap/explanations.json`, `.codemap/scenarios.json` and
  `.codemap/glossary.json` are the only files you write. Never edit
  `explore.css` / `explore.js` / the renderer, and never hand-write a file
  under `.codemap/traces/` — that directory is `codemap trace`'s output only.
- `see` keys (libraries.json, walkthrough.json), and every key in
  `explanations.json` / `scenarios.json`, must equal a `data.nodes[].key`
  **verbatim** (the briefs print each one) — except a `walkthrough.json` `see`
  entry, which may also be a bare repo-relative file path for something
  codemap doesn't index. No HTML in any string.
- Max 2–3 plain sentences per `general` / `here` / `what` / folder `purpose`.
  A term already covered by one of the two bundled glossary dictionaries
  needs no inline gloss — the tooltip carries it; a term in neither one goes
  in `glossary.json`, never just a parenthetical. `here` must name real
  modules/functions from this repo.
- A folder's `purpose` answers "what would I lose if this folder were
  deleted," not "what files live in here."
- Cover the whole Dependency reference and the whole Scenario index — not a
  hand-picked few. `walkthrough.json`'s `folders` is different: write an entry
  only for the folders that earn one.

See `references/` for the full rules; read each file only when you reach the
step that needs it.
