# File mode: authoring without the MCP server

Use this only when the `codemap` MCP tools are not available in your session
(another client, or the plugin's server failed to start). It produces the same
six files as the MCP workflow in `SKILL.md`, from the deterministic briefs
instead of open questions, and it cannot record **findings** (confirmed or
rejected calls, matched requests): those need `answer_question`.

Everything else in `SKILL.md` (audience, non-negotiables) still applies.

1. **Index and emit the analysis pack.**
   ```
   codemap scan            # only if there is no index yet
   codemap explore --emit-brief
   ```
   `.codemap/briefs/00-overview.md` now carries a **Dependency reference**
   (every import + every module, with importing files), a **Folder reference**
   (every folder candidate, with file counts, direct dependencies, and an
   advisory "nothing imports this" flag where applicable), a **Scenario
   index** (every entry point with a suggested workflow group + order), and an
   **Architecture** section (every derived component by layer, with its
   evidence, best guesses and wrong-way imports flagged).
   Per-module briefs carry verbatim pre-extracted snippets and each symbol's
   `key:`. Derived packs sit alongside: `libraries-derived.json`,
   `scenarios-derived.json`, `folders-derived.json`,
   `architecture-derived.json`.

2. **Read `00-overview.md`**, then the per-module briefs, plus
   `references/content-philosophy.md` and `references/gotchas.md` (always),
   `references/grounding-tools.md` (when you'll trace scenarios or make
   "only X does Y" claims), and the schema files for whatever you're about to write: `references/walkthrough-schema.md`,
   `references/glossary-schema.md`, `references/libraries-schema.md`,
   `references/explanations-schema.md`, `references/scenarios-schema.md`,
   `references/architecture-schema.md`.

3. **Write `.codemap/walkthrough.json`** — the Learn tab, now a project
   walkthrough. Follow `references/walkthrough-schema.md`.
   - `intro` — what the project is, plus a `sides` entry for each top-level
     part actually rooted somewhere in the repo (skip `sides` for a
     single-part repo). Add `seam` only when 2+ sides genuinely connect
     somewhere in the code.
   - `categories` — invent this repo's own "what this app is made of" map.
     Outcomes an engineer new to the repo would recognize, never bare technology labels —
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
   `references/explanations-schema.md`. One precise `what` line for every
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
   reconstruct a call tree by hand. Mark the single best hero `"start": true`:
   it is pinned above the rail as "If you follow only one", so pick the richest
   end-to-end thread, not startup just because it has the lowest `order`. Confirm each one with
   `codemap calls <root key> --out --depth 4 --no-guesses`: every `call` step
   must be an edge in that output, and a hop the graph can't show (a framework
   calling your handler, a branch) says so in its `code` line. For real
   branch/loop/output fidelity record an actual run:
   `codemap trace --name "<title>" -- <command>` (never edit its output by hand).

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
   the app actually connect. When the overview has a **Route links** section
   (codemap pairs JS/TS and Python requests with their routes itself),
   it names each web request, the code that sends it and the code that serves
   it; the derived trees already cross those hops with a `note` step carrying a
   `route` field, so narrate that step instead of inventing the connection (it
   is the one hop `codemap calls` won't show).

   **Point at a safe first edit.** codemap never edits code — it only reads
   and explains it — but a `screen`/`layout` scenario's `summary` can still
   name one harmless, literal string visible in that screen's source (a
   button label, a heading, a placeholder) worth changing and re-running. That
   moment — a string in a file becoming a thing on screen — is when "this is
   all just text a person typed" actually clicks. Only ever point at a plain
   string literal already sitting in the excerpt; never suggest touching logic.

7. **Correct `.codemap/architecture.json`** — the Architecture tab, following
   `references/architecture-schema.md`. The tab already draws the whole diagram
   from folder names, entry points and imports; write only corrections, from
   the brief's **Architecture** section:
   - every box marked **⚠ best guess** — confirm it with a `title` (and its
     `layer`) or move it;
   - every **wrong-way import** — fix whichever side is in the wrong layer, or
     note the shortcut in a `body`;
   - a human `title` for boxes still named after a bare folder (`services`,
     `utils`) where the folder name alone wouldn't tell a newcomer what it does;
   - any database, queue or outside service the code reaches without importing
     a client for it (an ORM URL, a plain HTTP call).
   Skip the file entirely when nothing needs correcting.

8. **Check what you wrote against the graph.** The renderer drops a bad entry
   without a word, so this is the only place you learn about it.
   ```
   codemap check --json
   ```
   Fix every `error` (an entry that was dropped, or a key that matches no symbol;
   each names the file, the path inside it, and often the key you probably meant)
   and re-run until `"errors": 0`. Read each `warning` and either fix it or
   decide it is deliberate: a key cut by the `max_symbols` budget, a walkthrough
   `see` that points at a file codemap doesn't index, a glossary term nothing
   uses. Never hand the work over with errors outstanding.

9. **Bake and review.**
   ```
   codemap explore --open
   ```
   First, verify every exclusivity claim you wrote across all the files —
   `"only X does Y"`, `"the only place that…"`, `"nothing else touches this"` — before
   baking: `codemap calls <Y's key> --in` lists the callers the graph found, and a
   real grep covers what it can't see. That phrasing reads as fact once it's on
   screen; an unverified one is worse than no claim at all (see
   `references/scenarios-schema.md`'s Rules for the exact check, and don't repeat the
   mistake this whole skill exists to fix: written prose the underlying data doesn't
   back up).
   Learn tab: `intro` renders top to bottom (the `what` line, each side's
   `body`, the `seam` note if there is one); the category map's groups read as
   things an engineer new to the repo would recognize, not bare tech labels; every folder the
   brief flagged either has authored prose or reads sensibly with none.
   Packages tab: every third-party package and every repo module still has a
   real `general` *and* `here` — no "add a line to libraries.json"
   placeholders left on anything that matters; `see` links land on the right
   symbol. Graph tab: focus a hotspot and confirm its inspector shows the
   explanation, and hover a glossed term to confirm the tooltip fires.
   Simulate tab: the rail groups your scenarios in workflow order —
   spot-check the grouping, then play each hero scenario through once.
   Architecture tab: no dashed "best guess" box left that you meant to
   confirm, every box name reads as what that part does, and the wrong-way
   import list holds only shortcuts you've explained.
