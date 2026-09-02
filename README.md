# codemap — change explainer for AI-assisted codebases

`codemap` is a git-triggered CLI that answers one question after each commit:

> What changed, why, what does it affect, and which forty lines should I read myself?

It keeps a symbol-level graph of the repository across commits, computes a
**semantic diff** between consecutive graphs, classifies each change by severity,
attaches the stated intent behind it, and appends a short markdown entry.

The graph is not the deliverable. The graph exists so that a diff can be explained.

## Install

```
pipx install codemap        # or: uv tool install codemap
```

For a checkout without an install, `python -m codemap …` works from the repo
root, and every command takes `--path <repo>` to run against another repository.

## Use

```
codemap scan                 # index new commits, write change entries
codemap explain HEAD         # print the markdown entry for a commit
codemap explain HEAD --breakdown   # …plus the raw change list
codemap catchup              # one digest of everything since `codemap reviewed`
codemap reviewed HEAD        # advance the reviewed marker
codemap snapshot             # architecture view from the current graph
codemap explore              # render .codemap/explore.html — the browsable surface
codemap explore --open       # …and open it
codemap explore --emit-brief # analysis pack for the course-authoring skill
codemap note "why I'm about to commit"   # record intent for the next commit
codemap install-hook         # post-commit hook: scan + explain + explore, always exits 0
codemap status               # index state
```

Change entries land in `.codemap/changes/<ts>-<sha7>.md`. Configuration is
`.codemap/config.toml` (ignore patterns, languages, `impact_depth`, `retention`,
an off-by-default LLM narrative stage, and an `[explore]` block).

### Explore

`codemap explore` renders one self-contained `.codemap/explore.html` from the
index — no server, no build step, opens with a double-click. Three tabs:

- **Graph** — the whole file/function structure as a neural-style code graph:
  source tree, module lobes wired by organic dendritic edges, a neuron view
  (callers as afferent dendrites, callees as efferent) when a symbol is focused,
  and an inspector with fan-in/out, blast radius, entry path, source, the file's
  third-party / built-in / internal imports, and a plain-English "what this does"
  blurb from `.codemap/explanations.json` when present.
- **Learn** — an authored walkthrough from `.codemap/learn.json` when present
  (see the repo-root `SKILL.md`), else a generated Orientation. `learn.json` and
  `explanations.json` are both optional and fall back silently.
- **Timeline** — every commit with its stated intent, severity counts, headline
  change and blast radius, linking back into the graph.

Fonts (Inter) and icons (Phosphor) load from a CDN; offline, the page degrades
to a system font and keeps working. The `[explore]` config controls
`rebuild_on_commit` (default true — the hook refreshes the page after each
commit), `max_symbols` and `max_snippet_lines`.

### Intent

Intent is captured, not guessed. Resolution order, first hit wins, source recorded:

1. `.codemap/pending-intent` — an agent or you write the goal before committing
2. `CODEMAP_INTENT` environment variable
3. `codemap note "<text>"`
4. the commit message
5. `inferred` — nothing stated; the entry says so, and any narrative is hedged

## How it works

Tree-sitter parses one file at a time (`tags.scm` queries per language, no
language toolchains), which is what makes incremental indexing possible.
Accuracy is layered on in tiers:

| Tier | Provides |
|---|---|
| **T1** universal | declarations, raw imports, references by name — any language with a grammar |
| **T2** per-language | imports resolved to files, same-file / same-class call edges |
| **T3** external | true cross-file typed call graph — not in v1 |

Every language records its tier and every report states it, so "no callers
found" is always distinguishable from "callers not resolvable at this tier".
State lives in one SQLite file, `.codemap/index.db`, versioned per commit.

Languages shipped: Python, TypeScript/TSX, JavaScript/JSX. Adding one is a
registry entry plus a `tags.scm` — no Python code.

## Known blind spots

- Dynamic imports, `getattr`/dictionary dispatch, string-based routing, dependency injection,
  metaclass-generated methods, and wrapping decorators are invisible or distorted in the graph
- At T1, caller lists are name-based: expect false positives, and false negatives across
  aliased imports
- Impact analysis is structural only. It cannot tell you whether a change is *correct*
- Intent marked `inferred` is a guess, not a record

## Development

```
python -m venv .venv && .venv/Scripts/python -m pip install -e ".[dev]"
python -m pytest
```

`tests/fixtures/build_repo.py` builds a real git repository with a scripted
commit sequence; that suite is the differ's contract.
