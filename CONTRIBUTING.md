# Contributing

## Setup

```
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"   # Windows
# .venv/bin/python -m pip install -e ".[dev]"      # macOS/Linux
```

Requires Python 3.11+.

## Tests

```
.venv/Scripts/python -m pytest
```

`tests/fixtures/build_repo.py` builds a real git repository with a scripted
commit sequence — that suite is the differ's contract, and most feature work
should extend it rather than mock git out.

## Repo layout

See [`docs/spec.md`](docs/spec.md) §6 for the full tree and what each module
does. Short version: `codemap/` is the installable package (the tool),
`skills/` + `commands/` + `.claude-plugin/` are the Claude Code plugin (the
skills), `docs/` is documentation, `tests/` covers the tool only — there is no
automated test coverage for the skill's authored content, by nature (it's
prose written by a model reading a specific codebase).

## One generated asset: the vendored icon font

Every asset under `codemap/site/assets/` is hand-authored and never
regenerated, **except** `phosphor-icons.css`. That file is vendored (base64
woff2, trimmed to the glyphs `explore.js` actually uses) so icons render
without depending on `unpkg.com` being reachable — see the header comment in
`vendor_phosphor_icons.py` for why this matters (it was a real bug: the icon
stylesheet hung forever behind a blocked CDN, leaving every icon-only button
blank with no fallback).

If you add a new `ph-*` / `ph-fill ph-*` class to `explore.js`, regenerate:

```
.venv/Scripts/python codemap/site/assets/vendor_phosphor_icons.py
```

This needs network access to `raw.githubusercontent.com` (nothing else).
Never hand-edit `phosphor-icons.css`.

## Before opening a PR

1. `python -m pytest` — all green, no new failures.
2. If you touched `codemap/site/`, sanity-check the rendered page: `codemap
   explore --open` in a real repo (or this one) and click through the five
   tabs.
3. If you touched anything under `skills/codebase-to-course/`, keep
   `SKILL.md` and its `references/*.md` internally consistent — the skill has
   no automated test, so this is a manual read.

## Publishing (maintainer notes)

Every absolute GitHub URL in this repo currently points at the placeholder
`OWNER/codemap` — there's no real repo yet. Once you've created one, stamp the
real address everywhere in one shot:

```
python scripts/set-repo.py <your-username-or-org>/codemap
```

See `scripts/set-repo.py`'s docstring for the exact file list. It prints
every replacement it makes — review the diff, commit, push.
