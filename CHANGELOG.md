# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project aims
to follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **Architecture tab** — imports between Views & UI and API are drawn. The two
  layers sit side by side in one row, so their imports were tagged `same` and
  never rendered; they now get a sideways pair of block arrows in the gap
  between the two bands.

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

[0.2.3]: https://github.com/devanr29/codebase-to-learn/compare/v0.2.2...v0.2.3
[0.2.2]: https://github.com/devanr29/codebase-to-learn/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/devanr29/codebase-to-learn/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/devanr29/codebase-to-learn/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/devanr29/codebase-to-learn/releases/tag/v0.1.0
