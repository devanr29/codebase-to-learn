# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project aims
to follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.6.0] — 2026-10-15

codemap is now standalone: the call-graph engine and the MCP server that used
to come from other tools are built in.

### Added
- **Type-aware call resolution** for Python, JavaScript and TypeScript
  (`codemap/enrich/typeflow.py`), a port of codebase-memory-mcp's "Hybrid LSP"
  resolver (MIT). It works out what each call's receiver is (annotations,
  constructors, `self.x` / class fields / TS constructor parameter properties,
  return types, imports through `__init__.py` and barrel re-exports, base
  classes, `super()`, `await`, `as` casts) and then adds the calls a name
  match missed, confirms `AMBIGUOUS` guesses and removes the wrong ones, and
  rules out calls on builtin or third-party values. A method call also reaches
  every override in a repo subclass. Unsure stays unknown and leaves codemap's
  own edges alone. Used by `explore`, `calls` and `mcp`; such links carry a
  `typed` tag in the Graph inspector, and the topbar shows a `+ types` badge.
  `[engine] types = "off"` turns it off.
- **Python web requests** (`codemap/enrich/pyfacts.py`): `requests` / `httpx` /
  `aiohttp` / test-client / `urlopen` calls are paired with the Python routes
  that serve them, and with Express routes; a JS frontend's requests pair with
  Python (Flask, FastAPI) routes too. They appear in Simulate, on the
  Architecture tab and in the brief like the JS/TS links.

### Changed
- **`codemap mcp` no longer needs the MCP SDK.** The server speaks MCP's
  JSON-RPC 2.0 over stdio itself (`codemap/mcp_server.py`), so a plain
  `pipx install` is enough; `pipx inject codemap mcp` is no longer needed. The
  `mcp` extra stays, empty, so existing install commands still work.
- `codemap calls` and the MCP tools read the same type-resolved graph as
  `explore`, so a call settled by types is no longer an open question.

### Removed
- Reading a codebase-memory-mcp index (`codemap/enrich/cbm.py`). Its
  `[engine] codebase_memory` and `cache_dir` settings are ignored if present.

## [0.5.1] — 2026-10-14

A frontend pass over `explore.html`: layout bugs found on a real repo,
phone layouts, contrast and touch sizing. No change to the data model or CLI.

### Fixed
- **Graph:** files in a folder bubble are spread on an even spiral (most-used
  in the middle) instead of at random, so nodes and their names no longer pile
  on top of each other.
- **Graph:** fit-to-view uses the area the canvas actually shows, not the
  fixed 1040×700 drawing area, and keeps folder titles on screen. A near-square
  canvas is no longer a third empty.
- **Graph on a phone:** the bottom-corner controls now only reserve a bottom
  strip; reserving their width at the sides too shrank the graph to a
  thumbnail.
- **Architecture:** a web-request label (`POST /api/contact …`) looks for a gap
  along its arrow that no box covers, and is drawn above the boxes. It used to
  sit under the nearest card.
- **Map → Mass:** tiles near the light end of the churn ramp get dark text
  (about 9:1); white labels there were about 2:1.
- **Learn and Packages on a phone:** below 900px the side menu stacks above the
  text. The fixed 220px menu left the text one word wide.
- **Timeline:** git trailers (`Co-Authored-By:`, `Signed-off-by:` …) at the end
  of a commit message are dropped, so they no longer show as the summary. The
  summary is the body's whole first paragraph (capped at two lines), not its
  first hard-wrapped line, which stopped mid-sentence.
- The "Skip to inspector" link is hidden until it gets keyboard focus. It sat
  on top of the logo.
- Simulate's legend row "recorded — a real `codemap trace` run" no longer
  breaks into three columns.
- The page height uses `100dvh`, so mobile browser bars don't cover the bottom
  controls.

### Changed
- Small grey labels use a new `--color-text-faint` token that meets 4.5:1 on
  every panel. Native controls render dark (`color-scheme: dark`).
- Buttons, tabs and pills shrink slightly while pressed (off under reduced
  motion). On touch screens, controls are 32–42px tall.
- Three panels lost their thick left-edge stripe in favour of a thin accent
  border with a light tint.

## [0.5.0] — 2026-10-14

The flow is now **index → investigate → write → bake**. An AI assistant
queries the graph over MCP, settles what static analysis couldn't with
`file:line` evidence, and only verified answers reach the page.

### Added
- **`codemap mcp`**, an MCP server over stdio (optional `mcp` extra:
  `pipx inject codemap mcp`). Its logic lives in `codemap/query.py`, free of
  the SDK. The tools:
  - read tools: `overview`, `find_symbol`, `explain_symbol`, `calls`,
    `impact`, `read_source`, `search_code`, `get_authored`, `check`;
  - `open_questions`: nine ranked kinds of gap. They are ambiguous calls,
    unmatched web requests, orphans, best-guess architecture boxes, wrong-way
    imports, settings that hint at a database or service, entry points with no
    scenario, and unexplained symbols and packages;
  - `answer_question` and `submit`: verified writes. Evidence lines must
    exist, keys must resolve, and an entry must not add a `codemap check`
    error, or nothing is written and the reason comes back.
- **`.codemap/findings.json`**, the facts a reviewer settled:
  - `call` confirms an `AMBIGUOUS` edge, or adds a dispatch edge;
  - `not_call` removes a false guess;
  - `request` names a request's handler or outside service;
  - `unknown` and `note` close a question without changing the graph.

  `model.build` applies findings after both engines. A finding whose evidence
  line changed, or whose symbol is gone, goes stale: it is not applied, and
  `codemap check` warns.
- The page marks reviewed facts. The Graph inspector shows a dashed
  **reviewed** tag with the note and evidence. The Architecture tab labels
  request links "(confirmed by review)". `explore --json` carries
  `edges[].review`, `routes[].review` and a `review` summary.
- The plugin starts the server (`mcpServers` in `plugin.json`).
- `docs/mcp.md`.

### Changed
- **The audience is engineers onboarding to a repo**, not only non-technical
  "vibe coders". The `codebase-to-course` skill and
  `references/content-philosophy.md` now ask for responsibilities, inputs →
  outputs, side effects, callers and gotchas, each tied to real keys.
- The skill investigates over MCP first, then writes through `submit`. The
  old briefs-and-hand-written-JSON path moved to
  `references/file-mode.md` as the fallback.
- `/codemap:course` follows the new flow.
- `validate.check()` takes `overrides` (check content before it's written)
  and `only` (limit the check to some files).

## [0.4.0] — 2026-10-11

### Added
- **JS/TS web requests are tied to the route that serves them, with no other
  engine.** New `codemap/enrich/jsfacts.py` reads `fetch` / `axios`-style calls
  and Express routes (`router.get`, `router.route().get().put()`, `app.use`
  mounts followed across imports, `axios.create({ baseURL })`), turns `${id}` in a
  URL into a parameter, and pairs each request with its handler. The links feed
  the existing route pipeline: Simulate's request hop, the Architecture tab's
  request connectors and the brief's **Route links**, merged with any
  codebase-memory links. On a MERN sample app: 23 of 23 requests paired, 20
  reaching a named handler (the other two are inline arrow functions).
- **Settings and browser storage.** Every `process.env` / `import.meta.env` /
  `process.argv` read (with its `||` / `??` default) and every `localStorage` /
  `sessionStorage` / IndexedDB / Cache Storage key, with the symbol that touches
  it. Storage shows as a store on the Architecture tab; both are listed in a new
  brief section. `codemap status` reports the feature; `[engine] native = "off"`
  turns it off.
- The request, storage and config detection rules are ported from REA
  (https://github.com/morluto/rea, MIT, Copyright (c) 2026 morluto).

### Fixed
- **A function handed straight to a wrapper is a symbol.** `const getX =
  asyncHandler(async (req, res) => ...)`, `memo(() => ...)`, `forwardRef(...)`
  were invisible, so a whole Express controllers folder could have zero symbols.
  Timers and value hooks (`setTimeout`, `useMemo`, ...) are still not symbols.
  Schema v4: the next `codemap scan` reparses every worktree file once.
- **React Router screens defined in another file are entry points.** `<Route
  component={X} />` (v4/v5) is now read, and a routed component imported from
  another file (`import X from './screens/X'`, a named import, or
  `lazy(() => import(...))`) resolves to that file's symbol instead of being
  dropped — a typical `App.js` route table used to yield no screens at all. A
  render root wrapped in `<Provider>`, `<BrowserRouter>`, `<Suspense>` and similar
  is unwrapped to the real root component. New `route_registrations` table
  (schema v4); the entry points are rebuilt for the whole snapshot on every
  index, so editing only the screen or only the router stays correct.
- **Express routes are entry points.** A backend route used to reach the
  Simulate scenario index only if some screen in the repo called it. Now every
  route with a named handler is a `route` entry point on that handler, at its
  full mounted path (`app.use('/api/orders', r)` + `r.get('/:id', getOrderById)`
  -> `GET /api/orders/:id`), so it shows up in Simulate, on the Architecture tab
  and in the brief's scenario candidates. New `server_route_facts` table (schema
  v5, one more worktree reparse); like the screens above, the entry points are
  rebuilt for the whole snapshot on every index. On the MERN sample app: 21
  routes, none before. Routes whose handler is an inline arrow function are
  still skipped, since there is no symbol to land on.
- **"If you follow only one" pins the hero, not the startup scenario.** The
  Simulate card above the rail took the lowest `order`, which the skill reserves
  for startup, usually a two-step tree. A scenario can now say `"start": true`
  in `scenarios.json` (the skill marks its best hero); without one, the card
  takes the first scenario with steps of its own (hand-authored or recorded),
  and only then the lowest `order`. `codemap check` warns when more than one
  scenario is marked, or when `start` isn't true/false.

## [0.3.1] — 2026-09-24

Three defects found by running `codebase-to-course` on codemap's own source.

### Fixed
- **Calls through an imported module now resolve.** `from . import a, b, c` only
  ever looked at `a`, and `from .site import model as _model` resolved to the
  package instead of `model.py`, so `_model.build(...)` had no caller in the Graph
  and `config.load(...)` was linked to an unrelated `intent.load`. A call on a
  module name (`config.load()`, `_model.build()`) now lands in exactly that module,
  `INFERRED`, even for a name on the method stop-list (`config.get()`). File-to-file
  import edges and the Graph's dependency lists now include every module named in
  a multi-name import. Nothing to rebuild; the call graph is computed on read.
- **A glossary term with no `display` keeps the case it was written in** (it was
  title-cased, so `"cosmetic"` never matched lowercase prose and its tooltip never
  appeared). This is what `glossary-schema.md` already documented.
- **Docstrings and string literals are no longer "background jobs".** A mention of
  `add_job(` in a docstring or a test's sample source counted as a scheduler
  registration. Run `codemap scan --rebuild` to clear ones already indexed.

## [0.3.0] — 2026-09-23

Aimed at the person reading the explorer, not at the graph engine. The prose the
`codebase-to-course` skill writes was never checked against the code (a wrong key
or a dropped scenario simply vanished), and the call graph was the ceiling for
anything the skill could claim. This release checks the prose, gives the skill a
way to ground it, and lets a codebase-memory-mcp index, when you have one, raise
that ceiling. Nothing new is required.

### Added
- **`codemap check`** — compares the skill-authored `.codemap/*.json` with the
  real graph and reports everything the renderer used to drop silently: a symbol
  key that matches nothing (with a "did you mean"), a scenario left with fewer
  than two resolvable steps, an unparseable file, a library name with no Packages
  page, an architecture component that isn't a folder codemap groups by.
  `--json` and `--strict` for scripts; `codemap explore` prints a one-line note
  when it finds errors. The skill now finishes with it.
- **`codemap calls <symbol>`** — what a symbol calls or what calls it, with
  file:line and confidence, from the same call graph the Graph tab draws
  (`--in/--out/--both`, `--depth`, `--no-guesses`, `--json`). The skill uses it
  to check scenario steps and "only X does Y" claims.
- **Optional codebase-memory-mcp engine** — when an index of the repo exists,
  its resolved call links are merged into the graph (tagged `cbm`), its
  `AMBIGUOUS`-settling resolutions replace codemap's name-only guesses, and its
  HTTP route links become **web requests**: Simulate's derived scenarios cross
  from a client call into the handler, the Architecture tab draws a labelled
  dashed connector, and the analysis pack lists a **Route links** section.
  Read-only, guarded against schema changes, skipped per file when stale, and
  invisible when absent (the payload is identical). `[engine]` in
  `.codemap/config.toml`; the topbar shows a "+ codebase-memory" badge and
  `codemap status` an `engine:` line. See `docs/spec.md` §20.
- Skill: a "Grounding tools" section and `references/grounding-tools.md` for
  using the `codebase-memory` MCP tools (when present) alongside `codemap calls`,
  and how to turn their names into codemap keys.

### Changed
- `docs/spec.md` §20 records the scope: codemap keeps its small tree-sitter
  indexer and adds no language without a request; coverage comes from the
  optional engine.
- The Packages docs no longer offer a nested path such as `codemap/site` as a
  module name (it never had a page; `codemap check` now says so).

## [0.2.5] — 2026-09-23

A user test on three real builds found the Graph, Timeline and most of
Simulate couldn't be trusted (name-based call links inflating fan-in and
inventing scenarios that ran production code into test fakes), the Graph was
the slowest tab, a single click didn't focus, and the legend covered nodes.
This release fixes all of it, by phase.

### Added
- **Receiver-aware call resolution** (schema v3) — a call now records what it
  was made *on* (`self`, a class name, a local variable, or bare), so
  `body.get(...)` no longer links to an unrelated `WalletClient.get` just
  because the names match. A `self.x()` only resolves to that class's own
  method; a variable's method call needs import evidence to resolve at all.
  Fan-in/fan-out, blast radius, the entry path, and Simulate's derived
  scenarios now count confident edges only; a same-name-only guess is drawn
  dashed in the Graph and never silently folded into a count. Opening an
  index built before this release triggers one forced re-parse to backfill
  the new data. See `docs/spec.md` §19.
- **Terminal and Scheduler actors** on the Architecture tab — every
  `__main__` guard now gets a Terminal entry (wherever its file landed), and
  APScheduler/`schedule` registrations (`add_job`, `BackgroundScheduler`,
  `schedule.every`) are detected as Scheduler tasks.

### Changed
- **Graph tab** — a single click now focuses a symbol (it used to pin);
  <kbd>Shift</kbd>+click pins instead. The graph fits itself to the canvas
  after every layout and on resize; the legend starts collapsed to its
  header so it no longer covers nodes. The "Traffic" call-flow animation
  defaults off on a graph with more than ~150 visible edges (it was
  unconditionally on, which dropped a big repo to single-digit fps) and
  pauses while the tab is hidden; panning/zooming now composites instead of
  re-laying out. Labels get a greedy collision pass instead of overlapping
  freely. The toolbar no longer overflows at narrow widths, and the left
  rail keeps its scroll position and expands to the focused symbol.
- **Simulate tab** — opens on a real default scenario instead of an empty
  state; the narration/flow/source panes no longer resize on every step; the
  source pane shows the whole file, not just a short excerpt; the call-stack
  snapshot pane is replaced by a scrollable trace log with a breadcrumb of
  the live stack. The ⚡ dynamic-dispatch marker now only flags real dynamic
  patterns (`getattr`, subscript calls, calling a variable) instead of any
  short call chain.
- Rail scroll position is now preserved across tab switches (Simulate, Learn,
  Packages, Timeline); Map's "Back" resets to the default view instead of
  staying on whatever was last opened; routing corrects an unresolved tab or
  argument with a small "link not found — showing X" note instead of just
  showing the wrong thing silently.
- Route entry-point labels now show the actual HTTP methods and blueprint
  prefix (`GET, POST /api/chat`) instead of always `ANY /chat`.
- Config files (`tsconfig.json`, `.eslintrc`, …) now land in the Architecture
  tab's Shared layer instead of being misplaced by a `pathlib.Path.stem`
  bug that also missed bare dotfiles.

### Fixed
- Timeline: a commit-message intent no longer repeats the commit subject, and
  a changed function signature no longer garbles destructured parameters
  into a headline.
- File anatomy could read over 100% when a file had overlapping symbol
  ranges; the top bar's "cycles" count now matches the Map tab's (file-import
  cycles, not call-graph cycles).
- The Packages filter now matches "stdlib"/"standard" against built-in
  packages; a monorepo subdirectory import path no longer produces a false
  "nothing imports this" marker.
- Glossary term matching no longer fires inside a path or package name
  (`a.b`, `@scope/pkg`); the inspector runs code-formatting before term
  matching instead of after.

### Accessibility
- Faint UI text (rail counts, keyboard-shortcut hints, section labels, pane
  titles, legend headers, the zoom box caption) is raised to at least a
  4.5:1 contrast ratio against its background, and section/pane label text
  is no longer smaller than 11px.
- Text that's clipped with an ellipsis (a package name in the Packages rail,
  a trace-log row's code and the live call-stack breadcrumb in Simulate) now
  shows the full text on hover via `title`.

## [0.2.4] — 2026-09-22

### Added
- **Graph tab source viewer** — a symbol's "View source" opens a panel over
  the graph with its whole file: line numbers, syntax colours, the symbol's
  own lines highlighted and scrolled into view, clickable definition markers
  and call-site chips that jump the graph to the callee, and a minimap strip.
  The inspector's preview is now numbered and syntax-coloured too. Files are
  embedded once (not per-symbol) up to a new `[explore] max_source_bytes`
  budget (default 4 MB, most relevant files first); the ⌘K palette can now
  find text anywhere in a long function, not just its first 40 lines.
- **Graph tab Folder | Layer colouring** — the legend can colour and isolate
  nodes by the architecture layer the Architecture tab inferred, as an
  alternative to folders (still the default). Switching only recolours; the
  layout doesn't move. Layers are labelled "inferred" since folders remain
  the ground truth.
- **Language support** — generated and vendored files (`*.min.js`,
  `*.bundle.js`, `*.d.ts`, `*_pb2.py`, `*.pb.go`, plus `vendored/`,
  `third_party/`, `generated/` directories) are excluded from indexing by
  default, regardless of `.gitignore`. A file with a syntax error in part of
  it is now parsed for whatever's still readable instead of being dropped
  entirely, unless the unparsed gap swallows most of the file; `codemap scan`
  reports these as "parsed with gaps", separate from files it couldn't use
  at all.
- **Architecture tab** — imports between Views & UI and API are drawn. The two
  layers sit side by side in one row, so their imports were tagged `same` and
  never rendered; they now get a sideways pair of block arrows in the gap
  between the two bands.

### Changed
- A worktree scan now commits its progress every 200 files instead of as one
  multi-thousand-file transaction, so an interrupted scan on a large repo
  doesn't lose everything and the WAL file can checkpoint along the way
  instead of ballooning to gigabytes.

### Fixed
- **Architecture tab** — an arrow between two layers is placed over the bands it
  joins and named for those two layers, with the import count and one example
  pair of parts (`Routes & entry → API · 3 imports` / `for example: app.py →
  api.py · 1`). It used to sit at the midpoint of two *boxes* and be titled with
  their names, which on a split row (Views beside API) put it in the seam between
  the bands, touching neither box. A cap of two arrows per gap also silently hid
  whole layer-to-layer relationships (API → Logic was never drawn on a
  client-plus-API repo).
- **Architecture tab** — the actor's arrow (Internet, User's screen…) reaches
  the band it drives, layer-skipping connectors name and leave from the layers
  they really join, and every band that uses shared code gets its own titled
  dashed line (the API band had none when Views also used shared code).

## [0.2.3] — 2026-09-18

### Added
- **Architecture tab** — a layered architecture diagram of the indexed repo,
  in the style of a hand-drawn system picture: who drives the app (Internet,
  Terminal, a user's screen, a scheduler) → **Routes & entry** → **Views & UI**
  / **API** → **Logic** → **Data & models**, with a side panel for shared code
  and tests. Each box names the tech it's built on (Flask, React, SQLAlchemy,
  …); databases, caches, search engines and queues are drawn as cylinders and
  outside services (Stripe, OpenAI, AWS, …) as clouds, detected from imports
  plus `docker-compose` images and `package.json` / `requirements.txt` /
  `pyproject.toml` / `go.mod` dependencies. Arrows show which layers import
  which; wrong-way and layer-skipping imports are flagged. Hover to light up an
  item's connections; click for why it was placed there (every scoring reason
  is shown), its files, entry points and links into Graph, Packages, Simulate
  and Learn. Derived by the new `codemap/site/architecture.py` from a bundled
  `architecture-catalog.json`; no authoring needed.
- Optional `.codemap/architecture.json` — corrections for that diagram (box
  names, misplaced parts, stores/services the imports don't reveal), authored
  by the `codebase-to-course` skill from a new **Architecture** section in
  `--emit-brief`'s overview and `architecture-derived.json`. Schema:
  `skills/codebase-to-course/references/architecture-schema.md`.

### Fixed
- Topbar no longer scrolls the page sideways between ~421px and ~650px wide:
  tabs collapse to icons below 780px, and the counters hide below 1100px.
- Simulate rail cards wrap a scenario's summary instead of cutting it off
  with "…", and hovering a card shows its full title.

## [0.2.2] — 2026-09-12

### Added
- `codemap init` now checks the project root for an existing `.gitignore` and,
  if present, appends `.codemap/` to it automatically (skipped, without
  creating one, when the project has no `.gitignore`).

## [0.2.1] — 2026-09-10

### Changed
- Plugin manifest description now names the current authored surfaces (Learn
  walkthrough, Packages reference, Simulate scenarios, glossary) instead of the
  older "Learn/Graph/Simulate content" phrasing.
- README: the Learn row of the six-tabs table points at
  [`docs/explore-guide.md`](docs/explore-guide.md) instead of carrying a
  screenshot-TODO placeholder.

### Added
- This changelog.

## [0.2.0]

### Added
- **Learn tab** — a guided, folder-by-folder walkthrough of the codebase
  (`.codemap/walkthrough.json`): what each part is, how the parts fit together,
  and a repo-wide category map.
- **Packages tab** — a per-dependency and per-repo-module reference
  (`.codemap/libraries.json`): what each thing does in general and in this code.
- **Glossary** — project-specific term definitions (concept + package) that
  power hover/tap tooltips across the whole page.
- **Simulate** — a scenario curriculum (`.codemap/scenarios.json`) with
  grouped, ordered entries; the call-stack pane is replaced by a scrollable
  trace log.
- New page loading animation.

### Changed
- The `codebase-to-course` skill now authors the walkthrough / libraries /
  scenarios / glossary JSON rather than a per-symbol code course.
- Graph rendering is capped to stay responsive on large repos, and skips a full
  rebuild on focus change.

### Fixed
- `pyproject.toml`: dropped the redundant wheel force-include that broke
  `pipx install`.
- CI test fixes.

## [0.1.0]

### Added
- Initial packaged release.
- `codemap` CLI (`scan`, `explore`, `trace`, `explain`) building a
  worktree-rooted, symbol-level graph and rendering a self-contained
  `explore.html` with the Graph, Map, Simulate, Learn, Packages and Timeline
  tabs.
- Claude Code plugin: the `codebase-to-course` skill plus the `/course`,
  `/explore` and `/review` slash commands.
- Post-commit hook for diff explanations.

[Unreleased]: https://github.com/devanr29/codebase-to-learn/compare/v0.6.0...HEAD
[0.6.0]: https://github.com/devanr29/codebase-to-learn/compare/v0.5.1...v0.6.0
[0.5.1]: https://github.com/devanr29/codebase-to-learn/compare/v0.5.0...v0.5.1
[0.5.0]: https://github.com/devanr29/codebase-to-learn/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/devanr29/codebase-to-learn/compare/v0.3.1...v0.4.0
[0.3.1]: https://github.com/devanr29/codebase-to-learn/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/devanr29/codebase-to-learn/compare/v0.2.4...v0.3.0
[0.2.4]: https://github.com/devanr29/codebase-to-learn/compare/v0.2.3...v0.2.4
[0.2.3]: https://github.com/devanr29/codebase-to-learn/compare/v0.2.2...v0.2.3
[0.2.2]: https://github.com/devanr29/codebase-to-learn/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/devanr29/codebase-to-learn/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/devanr29/codebase-to-learn/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/devanr29/codebase-to-learn/releases/tag/v0.1.0
