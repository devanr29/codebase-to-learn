---
name: codebase-to-course
description: >-
  Explain an indexed codebase inside codemap's explore.html for a non-technical
  "vibe coder". Use when someone wants to "explain this codebase interactively",
  "teach this code", "make a walkthrough from this project", "explain the
  functions / the dependencies", "simulate what happens when I run/click/call
  this", or asks to fill in the Learn tab, the Simulate tab, or the inspector
  explanations of the Codegraph Explorer. Produces .codemap/libraries.json (what
  every imported package and every repo module does, in general and in this
  code — the Learn tab), .codemap/explanations.json (a one-line plain-English
  blurb per symbol, shown in the Graph inspector), and .codemap/scenarios.json
  (the Simulate tab's scenario curriculum — every workflow of the app as an
  ordered, grouped entry, steps derived or hand-narrated) — all baked into the
  page by `codemap explore`.
---

# codebase-to-course

Writes three JSON files under `.codemap/` that the deterministic `codemap
explore` renderer bakes into `explore.html`. It never generates a standalone
site and never edits the renderer.

**Prerequisite:** the `codemap` CLI must be on `PATH` — this skill only
authors content, it doesn't index or render anything itself. If `codemap
--version` fails, install it first (`pipx install git+https://github.com/devanr29/codebase-to-learn`
— see `docs/install.md` in the codemap repo, or tell the user to run that
before continuing).

| File | Tab it fills | What it is |
|---|---|---|
| `libraries.json` | **Learn** | every imported package + every repo module: what it does in general, and its job here |
| `explanations.json` | **Graph** inspector | one plain-English `what` line per symbol |
| `scenarios.json` | **Simulate** | the workflow curriculum — one entry per real run of the app |

## Audience

A **vibe coder**: builds software with AI tools, no CS background. Goals — steer
AI coding tools, catch when the AI is wrong, escape bug loops, make
build-vs-buy calls, and **acquire the vocabulary of software**. They are *not*
becoming software engineers. Write every line for someone who has never heard
of the library you're describing.

## Workflow

1. **Index and emit the analysis pack.**
   ```
   codemap scan            # only if there is no index yet
   codemap explore --emit-brief
   ```
   `.codemap/briefs/00-overview.md` now carries a **Dependency reference**
   (every import + every module, with importing files) and a **Scenario index**
   (every entry point with a suggested workflow group + order). Per-module
   briefs carry verbatim pre-extracted snippets and each symbol's `key:`. Two
   derived packs sit alongside: `libraries-derived.json`, `scenarios-derived.json`.

2. **Read `00-overview.md`**, then the per-module briefs, plus
   `references/content-philosophy.md` and `references/gotchas.md` (always) and
   the three schema files: `references/libraries-schema.md`,
   `references/explanations-schema.md`, `references/scenarios-schema.md`.

3. **Write `.codemap/libraries.json`** — the Learn tab, a library / module
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
   - `see` — optional `data.nodes[].key` list, the call sites worth opening in
     the Graph tab (the briefs print every key).

4. **Write `.codemap/explanations.json`** following
   `references/explanations-schema.md`. One plain-English `what` line for every
   symbol in the per-module briefs (hotspots + entry points) and every key you
   put in a `libraries.json` `see`. Key the JSON by the symbol `key:`. Optional
   `why` and `terms`.

5. **Write `.codemap/scenarios.json`** — the Simulate tab's scenario
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

6. **Bake and review.**
   ```
   codemap explore --open
   ```
   Learn tab: every third-party package and every repo module has a real
   `general` *and* `here` — no "add a line to libraries.json" placeholders left
   on anything that matters; `see` links land on the right symbol. Graph tab:
   focus a hotspot and confirm its inspector shows the explanation. Simulate
   tab: the rail groups your scenarios in workflow order — spot-check the
   grouping, then play each hero scenario through once.

## Non-negotiables

- `.codemap/libraries.json`, `.codemap/explanations.json` and
  `.codemap/scenarios.json` are the only files you write. Never edit
  `explore.css` / `explore.js` / the renderer, and never hand-write a file
  under `.codemap/traces/` — that directory is `codemap trace`'s output only.
- `see` keys (libraries.json), and every key in `explanations.json` /
  `scenarios.json`, must equal a `data.nodes[].key` **verbatim** (the briefs
  print each one). No HTML in any string.
- Max 2–3 plain sentences per `general` / `here` / `what`. No jargon without a
  gloss inline. `here` must name real modules/functions from this repo.
- Cover the whole Dependency reference and the whole Scenario index — not a
  hand-picked few.

See `references/` for the full rules; read each file only when you reach the
step that needs it.
