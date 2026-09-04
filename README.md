# codemap — change explainer for AI-assisted codebases

`codemap` keeps a symbol-level graph rooted in **the worktree** — the code as it
is on disk right now, uncommitted changes included. `codemap scan` (with no
flags — every real invocation) always syncs that live graph, so `codemap
explore` / `codemap status` / `codemap snapshot` show the current code even
before you commit.

Git is the overlay on top of that graph, not its root. It drives two things:
incremental *updates* (a post-commit hook re-syncs only what changed) and
*history* — the question this tool was built to answer, after each commit:

> What changed, why, what does it affect, and which forty lines should I read myself?

For that, `codemap` also walks git history, computes a **semantic diff**
between consecutive commits, classifies each change by severity, attaches the
stated intent behind it, and appends a short markdown entry. `codemap explain
worktree` runs that same machinery against the live graph, to explain what
you've changed but haven't committed yet.

The graph is not the deliverable. It exists so a diff — historical or
uncommitted — can be explained, and so the current architecture can be browsed
without needing a commit first.

## Install

```
pipx install codemap        # or: uv tool install codemap
```

For a checkout without an install, `python -m codemap …` works from the repo
root, and every command takes `--path <repo>` to run against another repository.

## Use

```
codemap scan                 # sync the live worktree graph; index any new commits
codemap explain HEAD         # print the markdown entry for a commit
codemap explain worktree     # …what you've changed but haven't committed yet
codemap explain HEAD --breakdown   # …plus the raw change list
codemap catchup              # one digest of everything since `codemap reviewed`
codemap reviewed HEAD        # advance the reviewed marker
codemap snapshot             # architecture view of the current (live) graph
codemap explore              # render .codemap/explore.html — the browsable surface
codemap explore --open       # …and open it
codemap explore --emit-brief # analysis pack for the codebase-to-course skill (Learn + Simulate content)
codemap trace -- -m codemap explore   # record a real run for the Simulate tab
codemap note "why I'm about to commit"   # record intent for the next commit
codemap install-hook         # post-commit hook: scan + explain + explore, always exits 0
codemap status                # index state — `graph:` shows what explore/snapshot are reading
```

`codemap explore` / `codemap status` / `codemap snapshot` always read the live
worktree graph once one exists (`.codemap`'s `meta.graph_head`) — this is the
graph a real `codemap scan` produces. Commit-scoped queries (`codemap explain
<sha>`, `codemap catchup`) are unaffected: they read the git history walk,
exactly as before. Change entries land in `.codemap/changes/<ts>-<sha7>.md`.
Configuration is `.codemap/config.toml` (ignore patterns, languages,
`impact_depth`, `retention`, an off-by-default LLM narrative stage, and an
`[explore]` block).

### Explore

`codemap explore` renders one self-contained `.codemap/explore.html` from the
index — no server, no build step, opens with a double-click. Five tabs:

- **Graph** — the whole file/function structure as a neural-style code graph:
  source tree, module lobes wired by organic dendritic edges, a neuron view
  (callers as afferent dendrites, callees as efferent) when a symbol is focused,
  and an inspector with fan-in/out, blast radius, entry path, source, the file's
  third-party / built-in / internal imports, a plain-English "what this does"
  blurb from `.codemap/explanations.json` when present, and a file-anatomy strip
  (every symbol drawn at its true line span, so you see how much of a file is
  symbols vs. module-level code).
- **Map** — three static, laid-out-once views that answer questions the call
  graph alone doesn't: **Layers** topologically levels the file-import graph into
  dependency strata (foundation at the bottom, entry points at the top) and draws
  each import cycle as one merged block; **Run trace** is a subway map of one
  call chain, depth left-to-right, with a step control that walks the cascade one
  hop at a time and an explicit marker where a call is resolved at runtime
  (`args.func(...)`, dynamic dispatch) rather than statically; **Mass** is a
  treemap where area is lines of code and fill is how often the file changes,
  hatched when every function in it is unreachable.
- **Simulate** — a transport-controlled animation of one call-by-call run: the
  user's world above (a terminal/browser/API/file stage) and the code's world
  below (animated call flow, a **trace log** you can scroll and click any past
  step to jump to, the source line executing), with two narration lines per
  step — what the user perceives, what the code is doing. The left rail is a
  **curriculum**: `.codemap/scenarios.json` entries grouped by `group` and
  ordered by `order`, so they read the way the app runs (startup → an inbound
  request → background jobs → when it breaks). Three lanes feed it, increasing
  in fidelity: derived (⚡, computed from the call graph client-side — fills the
  rail only when nothing is authored), authored (✏, `scenarios.json`, written by
  the `codebase-to-course` skill; most entries carry just a `root` and the
  renderer derives the call tree), and recorded (⏺, `codemap trace -- <command>`
  — a real run, real branches, real output, real timing). See
  `references/scenarios-schema.md`.
- **Learn** — a **library / module reference**: every imported package
  (third-party + standard library) and every top-level repo module, each with
  *what it does in general*, *its job in this codebase*, and clickable call
  sites into the Graph tab. Prose comes from `.codemap/libraries.json` (authored
  by the repo-root `SKILL.md` skill) layered over a bundled table of
  common-library one-liners; the import graph supplies the fallback. See
  `references/libraries-schema.md`. `libraries.json`, `explanations.json` and
  `scenarios.json` are all optional and fall back silently.
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

Languages shipped: Python and TypeScript/TSX/JavaScript/JSX at tier 2 (same-file
call resolution is validated for these); Go, Rust, Java, C#, Ruby, PHP, C and
C++ at tier 1 (capture only, freshly added, no per-language resolver yet).
Adding one is a registry entry plus a `tags.scm` — no Python code — except
where the language's import syntax needs teaching to `indexer.classify_import`,
which is genuine per-language logic and the one place that claim doesn't fully
hold.

## Known blind spots

- Dynamic imports, `getattr`/dictionary dispatch, string-based routing, dependency injection,
  metaclass-generated methods, and wrapping decorators are invisible or distorted in the graph
  (the Map tab's Run trace marks where a chain hits one of these instead of silently stopping);
  a CLI whose subcommands dispatch through argparse `set_defaults(func=…)` is the common case —
  `main` looks like it reaches almost nothing, and the symbols past that call read as unreachable
- Caller resolution is name-based, narrowed by same-file and then by import evidence
  (`EXTRACTED`/`INFERRED`/`AMBIGUOUS`, shown per edge) — an `AMBIGUOUS` call, or any call at
  all outside Python/TS/JS, can still be a false positive; false negatives remain possible
  across aliased imports
- Go and Rust methods (receiver- and `impl`-declared, not nested in their type) show up with
  a flat name, not a `Type.method` one — see `CODEMAP_SPEC.md` §14
- Impact analysis is structural only. It cannot tell you whether a change is *correct*
- Intent marked `inferred` is a guess, not a record

## Development

```
python -m venv .venv && .venv/Scripts/python -m pip install -e ".[dev]"
python -m pytest
```

`tests/fixtures/build_repo.py` builds a real git repository with a scripted
commit sequence; that suite is the differ's contract.
