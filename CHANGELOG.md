# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project aims
to follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

[0.2.2]: https://github.com/devanr29/codebase-to-learn/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/devanr29/codebase-to-learn/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/devanr29/codebase-to-learn/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/devanr29/codebase-to-learn/releases/tag/v0.1.0
