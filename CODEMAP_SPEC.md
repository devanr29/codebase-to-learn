# codemap — Change Explainer for AI-Assisted Codebases

**Spec version:** 2.0 (supersedes 1.0 — architecture changed, do not follow v1)
**Status:** Not started
**Audience:** Claude Code (implementation agent)

---

## 1. Problem and product

The user writes code largely through AI assistants and has lost track of what happens in
their own repositories. Reading every diff is not viable; reading a full generated
architecture document is not either.

`codemap` is a git-triggered CLI that answers one question after each commit:

> What changed, why, what does it affect, and which forty lines should I read myself?

It maintains a symbol-level graph of the repository across commits, computes a **semantic
diff** between consecutive graphs, classifies each change by severity, attaches the stated
intent behind the change, and appends a short markdown entry.

The graph is not the deliverable. The graph exists so that a diff can be explained.

---

## 2. Two artifacts

| Artifact | Content | Regenerated | Read frequency |
|---|---|---|---|
| **Delta** | Per-commit change entries — what changed, why, impact | Every commit | Often. This is the point of the tool. |
| **Snapshot** | Current architecture, entry points, module map | On demand | Rarely |

Build the delta first. The snapshot is M9 and optional.

---

## 3. Non-goals

Do not implement these, even partially, even if they look easy:

- File watcher, daemon, background service, or any always-running process
- No server, no daemon, no IDE plugin. A statically generated, self-contained
  HTML artifact **is** in scope (M10–M14, §12); it is rendered from the DB by
  deterministic code and never runs a process.
- Full type inference or a compiler-grade index (see §5 on resolution tiers)
- Vector search / embeddings / RAG
- Multi-repo or cross-repo analysis
- Automatic code review, linting, or quality scoring — this tool explains, it does not judge

---

## 4. Constraints

- Python 3.11+, CLI only, installed via `uv` / `pipx`
- Parsing: `tree-sitter` with per-language `tags.scm` queries. **Not** language-specific ASTs, **not** regex
- Storage: SQLite at `.codemap/index.db`. Single file, no server
- Git access via `subprocess`. No `GitPython`
- Allowed deps: `tree-sitter` + a grammar bundle, `pathspec`, `networkx`, `pytest`, `anthropic` (M8 only)
- Must never block or fail a commit. The post-commit hook wraps everything and exits 0 unconditionally
- Deterministic: same commit range in, same deterministic output out (M0–M7). Only M8 is non-deterministic

---

## 5. Locked design decisions

Do not revisit these. If one appears to be causing a problem, stop and ask.

**5.1 — Tree-sitter core, tiered resolution.**
Compiler-grade indexers (SCIP) are accurate but require a full rebuild per update and drag in
each language's toolchain. Tree-sitter parses one file at a time with no toolchain, which is
what makes incremental indexing possible. Accuracy is recovered selectively via tiers:

| Tier | Provides | Cost |
|---|---|---|
| **T1** universal | Declarations, raw import strings, references by name | Free for any language with a grammar |
| **T2** per-language | Imports resolved to files, same-file and same-class call edges | ~200 lines per language |
| **T3** external | True cross-file typed call graph | Out of scope for v1; keep the interface open |

Every language records its tier in the DB, and every report states it. "No callers found" must
be distinguishable from "callers not resolvable at this tier".

**5.2 — Stable symbol keys.**
`relative/path.ext::Qualified.Name`. Every edge and change record references keys, never bare
names.

**5.3 — Intent is captured, not inferred.**
Reverse-engineering why a change was made produces confident, plausible, sometimes wrong
narratives. Since the AI session that produced the change already stated the intent, capture
it at commit time. Inference is the last resort and must be labelled as such. See M6.

**5.4 — SQLite with symbol-level history.**
A semantic diff is a statement about two graphs. The DB stores versions per commit, not a
single current state.

---

## 6. Project layout

```
codemap/
  __init__.py
  cli.py                # scan, explain, catchup, reviewed, note, install-hook, status
  config.py             # .codemap/config.toml
  db.py                 # connection, migrations, SCHEMA_VERSION
  schema.sql
  discovery.py          # M1 — file walking, language detection
  languages/
    registry.py         # extension -> grammar + queries + optional T2 resolver
    queries/<lang>/tags.scm
    python.py           # T2 resolver
    typescript.py       # T2 resolver
  parsing.py            # M1 — tree-sitter -> symbols, refs, imports
  normalize.py          # M4 — comment/whitespace-stripped body hashing
  indexer.py            # M2/M3 — incremental index, per-commit snapshots
  semdiff.py            # M4 — change detection and classification
  impact.py             # M5 — callers, entry-point reachability
  intent.py             # M6 — intent capture and fallback chain
  report.py             # M7 — deterministic markdown entries
  narrate.py            # M8 — optional LLM stage (isolated)
  hooks/post-commit
tests/
  fixtures/build_repo.py   # constructs a real git repo with scripted commits
  test_parsing.py
  test_semdiff.py
  test_impact.py
  test_incremental.py
```

---

## 7. Database schema

`SCHEMA_VERSION` starts at 1. Every table that varies over time is keyed by `commit_sha`.

```sql
meta(key TEXT PRIMARY KEY, value TEXT)
  -- schema_version, last_indexed_commit, last_reviewed_commit

commits(sha TEXT PRIMARY KEY, parent_sha TEXT, ts INTEGER,
        author TEXT, message TEXT, indexed_at INTEGER)

files(id INTEGER PRIMARY KEY, path TEXT UNIQUE, lang TEXT, tier INTEGER)

file_versions(file_id INTEGER, commit_sha TEXT, content_hash TEXT,
              loc INTEGER, status TEXT,   -- added|modified|deleted|unchanged
              PRIMARY KEY(file_id, commit_sha))

symbols(id INTEGER PRIMARY KEY, file_id INTEGER, key TEXT UNIQUE,
        kind TEXT, name TEXT, qualified_name TEXT)

symbol_versions(symbol_id INTEGER, commit_sha TEXT,
                signature TEXT, start_line INTEGER, end_line INTEGER,
                body_hash TEXT,          -- normalized, see M4
                decorators TEXT, docstring TEXT,
                PRIMARY KEY(symbol_id, commit_sha))

refs(id INTEGER PRIMARY KEY, commit_sha TEXT,
     from_symbol_id INTEGER, target_name TEXT,
     target_symbol_id INTEGER,          -- NULL when unresolved
     resolved INTEGER, tier INTEGER, line INTEGER)

imports(id INTEGER PRIMARY KEY, commit_sha TEXT, file_id INTEGER,
        raw TEXT, resolved_file_id INTEGER, external INTEGER, line INTEGER)

entry_points(id INTEGER PRIMARY KEY, commit_sha TEXT, symbol_id INTEGER,
             kind TEXT, detail TEXT)

changes(id INTEGER PRIMARY KEY, commit_sha TEXT, symbol_id INTEGER, file_id INTEGER,
        change_type TEXT, severity TEXT, details_json TEXT)

intents(commit_sha TEXT PRIMARY KEY, source TEXT, text TEXT)
  -- source: session|note|env|commit_message|inferred
```

Index `refs(target_symbol_id, commit_sha)` and `refs(target_name, commit_sha)` — impact
analysis is a hot path.

---

## 8. Milestones

Build in order. Commit after each with the milestone ID in the message. **Stop at the M4
checkpoint and report before continuing.**

### M0 — Skeleton
CLI scaffold, `.codemap/` creation, SQLite migrations, `config.toml` (ignore patterns,
languages, impact depth, LLM on/off). `codemap status` prints DB state.

*Acceptance:* `codemap status` runs on a fresh repo and reports an empty index.

### M1 — Parsing (T1, universal)
Walk files honoring `.gitignore` plus hard exclusions (`.venv`, `node_modules`, `vendor`,
`dist`, `build`, `target`, `__pycache__`, `.git`). Map extension → grammar via the registry.
Parse with tree-sitter and run `tags.scm` to extract per file:

- definitions (functions, methods, classes, types) with name, kind, line span
- references (call sites, identifier uses) with the raw target name
- import statements as **raw strings** — no resolution at this tier
- decorators/annotations as raw source text (M5 needs them)

Ship Python and TypeScript/JavaScript grammars first. Adding a language must require only a
registry entry and a `tags.scm` — if it requires Python code, the abstraction is wrong.

*Acceptance:* on `tests/fixtures`, extracted symbol keys match a hand-written golden list for
both languages.

### M2 — Incremental index
```
resolve range: meta.last_indexed_commit .. HEAD
git diff --name-status <last>..HEAD
  A/M -> reparse; DELETE rows WHERE file_id=? AND commit_sha=?; insert
  D   -> mark file_version deleted
  R   -> treat as D + A, but preserve file identity for history
```
Also compare content hashes so a dirty tree or a non-git directory still indexes.
First run with no `last_indexed_commit`: full index of HEAD.

*Acceptance:* full index, then touch one file and re-run — only that file is reparsed
(assert via a parse counter), and the resulting DB equals a from-scratch index of the same
commit.

### M3 — Per-commit snapshots
Index each commit in a range, not just HEAD, so consecutive graphs exist to diff.
`codemap scan --since <sha>` walks commits in order.

Retention: keep full graphs for the last N commits (default 50, configurable); older commits
keep their `changes` rows but drop `symbol_versions`. Do not let the DB grow unbounded.

### M4 — Semantic diff

Normalized body hashing first (`normalize.py`): strip comments and normalize whitespace using
tree-sitter node types before hashing. This is what makes cosmetic-change detection
deterministic rather than heuristic.

Change types, computed between commit N-1 and N:

| change_type | Detection |
|---|---|
| `symbol_added` / `symbol_removed` | key present in one graph only |
| `signature_changed` | same key, different signature |
| `body_changed` | same signature, different normalized body_hash |
| `symbol_moved` | same body_hash + name, different file |
| `renamed` | same body_hash + file, different name |
| `file_added` / `file_removed` | — |
| `import_added` / `import_removed` | — |
| `dependency_added` / `dependency_removed` | external import appearing/disappearing repo-wide |
| `entry_point_added` / `entry_point_removed` | — |

Severity:
- **structural** — signature change, add/remove of a symbol with callers, dependency change, entry point change
- **behavioral** — body change on a symbol that has callers
- **cosmetic** — normalized hash unchanged, or rename with all references updated in the same commit

*Acceptance:* the fixture repo's scripted commits each produce the expected change set,
including one commit that is purely cosmetic and must classify as such.

**→ Checkpoint. Run against one of the user's real repos and report the change breakdown
before continuing.**

### M5 — Impact analysis
For each changed symbol, at the N-1 graph:
- direct callers (inbound refs), then transitive to `impact_depth` (default 3)
- whether each caller was also modified in this commit — this drives the
  "2 updated, 2 unchanged (worth checking)" signal, which is the most actionable line in the report
- reachability from entry points: report the shortest path, e.g. `GET /api/users -> UserView.get -> get_user`

Entry point detection by decorator/annotation heuristics, per language, in the language pack:
`main` guards, HTTP route decorators, CLI decorators, task queues, `[project.scripts]`,
Dockerfile `CMD`/`ENTRYPOINT`.

At T1, caller lookup is name-based and will include false positives. That is acceptable here —
a slightly over-broad list of affected callers is useful, where it would not be in an
architecture document. Mark such results `tier: 1` in the output.

### M6 — Intent capture
Resolution chain, first hit wins, source recorded:

1. `.codemap/pending-intent` file — an AI agent or the user writes the goal before committing; consumed and deleted by the hook
2. `CODEMAP_INTENT` environment variable
3. `codemap note "<text>"` run before the commit
4. Commit message body
5. `inferred` — no stated intent; the report says so explicitly

Never fabricate an intent at this layer. M8 may summarize a stated intent; it must not invent
one where `source = inferred` without labelling it.

`codemap install-hook` writes `.git/hooks/post-commit`, chaining any existing hook. The hook
runs `codemap explain HEAD` in the background, redirects output to a log, and **always exits 0**.

### M7 — Report rendering (deterministic)
Append one markdown file per commit to `.codemap/changes/<ts>-<sha7>.md`:

```markdown
## a3f9c21 — 2026-08-30
**Intent:** [from session] Fix duplicate emails when retrying failed sends.
**Structural:** `EmailQueue.send()` signature changed, added `idempotency_key`.
**Impact:** 4 callers — 2 updated, 2 unchanged (worth checking).
**On path from:** POST /api/notify
**New dependency:** none.
**Read this first:** app/queue/email.py:88-140
```

Cosmetic changes are counted in a single line, never expanded. The tool's value depends on
this output staying short enough to actually read.

`Read this first` selects the highest-severity changed symbol's line span, ranked by
structural > behavioral, then caller count.

This milestone must work with the LLM disabled.

### M8 — LLM narrative (optional, isolated)
One function, one call site, behind `narrate.py`. Input is the structured change record from
M4–M6 — never the raw repository. Output is prose that replaces the bullet lines.

Rules: if `intent.source == inferred`, the output must be hedged and marked. The model may
summarize and connect the structured facts; it may not introduce facts absent from the record.
Disabled by default in config.

### M9 — Catch-up and snapshot
`codemap catchup` — aggregate `meta.last_reviewed_commit .. HEAD` into one digest. Collapse
repeated changes to the same symbol into a net change across the range. `codemap reviewed`
advances the marker.

`codemap snapshot` — the architecture document from spec v1 (module map, entry points,
dependencies, hotspots), rendered from the current graph.

---

## 12. Explorer — `codemap explore` (M10–M14)

A second-order artifact: one self-contained `.codemap/explore.html`, rendered
from the same DB by deterministic code (no LLM in the render path), that becomes
the primary way to *read* the repository. Three tabs sharing one hash router:

- **Graph** — the whole file/function structure as a neural-style graph: a source
  tree in the left rail, module "lobes" with organic dendritic edges on the
  canvas, a soma-and-dendrites neuron view when a symbol is focused, and an
  inspector (fan-in/out, blast radius, entry path, source excerpt, the file's
  third-party / built-in / internal imports, and a "what this does" blurb from
  `.codemap/explanations.json` when present).
- **Learn** — renders `.codemap/learn.json` when present (authored by the
  repo-root `SKILL.md` skill), else a deterministic Orientation built from the
  graph alone. Both `learn.json` and `explanations.json` are optional and fall
  back silently.
- **Timeline** — newest-first commits with intent source, severity counts, the
  `report._pick_headline` change, its impact line, and a read-this-first jump
  into the Graph tab.

Milestones:

- **M10** — `resolve.py` (raw import → file id, query-time, does **not** touch
  the incremental index path), `site/model.py` (DB → one JSON-serializable
  dict), `codemap explore --json`.
- **M11** — `site/render.py` + the frozen `site/assets/{shell.html,explore.css,
  explore.js}`, the `explore` subcommand. Assets are hand-authored once and
  never regenerated; all variation flows through the inlined JSON.
- **M12** — the Timeline tab; fold the impact/headline data into the model.
- **M13** — `site/brief.py` (`--emit-brief`), `site/learn.py`, the Orientation
  fallback, and the ported interactive components (translation blocks, quizzes,
  glossary tooltips, call-path replay, trace exercise).
- **M14** — repo-root `SKILL.md` + `references/*`, hook auto-rebuild
  (`codemap explore --quiet --if-enabled`, gated by
  `[explore] rebuild_on_commit`), these spec/README amendments.

Constraints: every `explore` code path returns 0 (the hook must never fail a
commit); the render is deterministic apart from a `built_at` timestamp; the
graph never shows an edge absent from `impact.call_graph`, and labels a
name-based cross-file edge as tier 1. `anthropic` stays an M8-only optional dep —
the explorer does not use it.

---

## 13. Worktree-rooted graph (M15)

Through M14, the graph a user *reads* (`explore`/`status`/`snapshot`) was
always the last commit `scan()` had processed — the working tree only entered
the picture for a non-git directory. M15 makes the worktree the graph's root
for every repository, git included, so the explorer shows the code as it is
on disk right now, uncommitted changes included, without gating that view
behind a commit.

`scan()` gains a second, independent step: after the git-history walk (M2/M3,
unchanged), it syncs the `worktree` pseudo-commit (`indexer.WORKTREE_SHA`) —
the same content-hash-based incremental mechanism M2 already used for a
non-git directory, now exercised for a real repo too. This step runs whenever
a caller asks for the ordinary "everything up to now" scan (`until` at its
default, `"HEAD"` — every real invocation; the CLI never overrides it). A
caller that bounds `until` to a specific historical commit — the test suite
does this throughout, to inspect one point in history in isolation — leaves
the live graph untouched, so M2–M14's contracts hold exactly as written.

Two meta keys now do different jobs: `last_indexed_commit` still means "how
far the git-history walk has resumed from" (M2's resumption pointer,
unchanged). A new `graph_head` is what a *view* should read to find the graph
a user actually wants to see: `"worktree"` once a sync has run, else falling
back to `last_indexed_commit` (the bounded-`until` case above).
`site/model.py::build()` and `digest.snapshot()` read `graph_head` first.

`codemap explain worktree` reuses `semdiff.diff_commits`/`impact.analyze`
unchanged, diffing HEAD against the live pseudo-commit, to explain what's
changed but not yet committed — the same machinery M4/M5 already built,
pointed at a pseudo-commit instead of two real ones.

Non-goal, deliberately: M15 does not persist a diff for `worktree` into the
`changes` table (`codemap explain worktree` recomputes it on demand, the same
"unpersisted" path M4 already had for a pruned commit) — the timeline and
`catchup` continue to iterate only real commits.

---

## 14. Language breadth (M16)

Go, Rust, Java, C#, Ruby, PHP, C and C++ join Python/TS/TSX/JS — one
`LanguageSpec` + one `tags.scm` each, exactly the M1 registry contract, using
grammars `tree-sitter-language-pack` already ships. All eight land at **tier
1**: capture-only, no per-language resolver, matching javascript's existing
precedent for "works, isn't specially validated yet." Two real limitations,
both from grammar shape rather than a missed spot:

- **Go and Rust methods get a flat name, not a receiver-qualified one.** A Go
  method is declared via a receiver (`func (s *Store) Put(...)`), not nested
  inside the type it operates on; a Rust `impl` block is a separate top-level
  declaration from the `struct`/`trait` it implements. Neither byte-range-nests
  inside a captured parent the way Python/TS/Java/C#/Ruby/C++ methods do, so
  `parsing.py`'s generic function->method promotion (and the qualified-name
  prefix that comes with it) doesn't fire. Go's grammar at least distinguishes
  `method_declaration` from `function_declaration` at the node-type level, so
  it's captured directly as `kind="method"`; Rust's `function_item` is used for
  both, and a second, impl-scoped pattern to recover the distinction would
  double-capture every impl method (tree-sitter matches overlapping patterns
  independently, not exclusively) — left as plain `kind="function"` instead.
- **`indexer.classify_import` needed genuine per-language code**, contradicting
  the "registry entry + tags.scm, no Python code" claim for extraction. Each
  language's import statement has different syntax (`"quoted"` Go paths,
  `use a::b;` Rust, `import a.b;` Java, `#include <x>` vs `"x"` C/C++, …), and
  the fallback for anything unlisted was silently assuming Python's syntax —
  correct for nothing new here. Each new language's stdlib-vs-third-party
  signal is a small, well-known set or a simple heuristic (Go: no dot in the
  first path segment, since every real module path is domain-shaped; Rust:
  `std`/`core`/`alloc`/`proc_macro`; Java/C#: `java./javax.` and
  `System/Microsoft` prefixes; Ruby: a short common-gems list), not an
  exhaustive package registry — the same "best effort" bar Python/JS already
  held to. `resolve.py::_kind_of` mirrors the same signals for the `internal
  |stdlib|third_party` bucket the explorer shows, and takes `internal_names` to
  disambiguate a same-repo path from a real stdlib one where a heuristic could
  otherwise collide (concretely: a Go module declared without a domain-shaped
  path, e.g. `module myrepo` rather than `module github.com/user/myrepo` —
  both legal).

Not shipped: Kotlin and Swift. Both grammars declared the definition/name
relationship without the named `field:` this whole capture contract leans on
(Kotlin's `class_declaration`/`function_declaration` hold their name as an
un-fielded child; Swift's parameter list isn't wrapped in any single node a
`@params` capture could point at), so a clean tags.scm for either needs either
a grammar-version-specific workaround or a change to the capture contract
itself — a real design decision, not a mechanical add, and out of scope here.

## 15. Resolution uplift (M17)

`impact.call_graph` gains a middle tier between "same file" and "any same-named
symbol repo-wide": before falling all the way to the T1 fallback, it now checks
whether a same-named symbol lives in a file the caller actually imports, using
`resolve.resolve_imports` — the same import-resolution machinery
`site/model.py`'s file graph already relies on. Every edge is tagged with which
tier won:

| Confidence | Won by |
|---|---|
| `EXTRACTED` | a same-named symbol in the caller's own file |
| `INFERRED` | nothing same-file, but exactly one same-named symbol in a file the caller imports |
| `AMBIGUOUS` | the T1 fallback — every same-named symbol repo-wide, one candidate with no import evidence or several genuinely competing ones |

**Confidence is metadata, not a filter**: which edges exist in the graph is
byte-for-byte unchanged from before this tier existed — `test_impact.py`'s
existing caller-set assertions pass with zero changes, and
`test_resolution_uplift.py::test_confidence_never_changes_which_edges_exist`
states that guarantee directly. `site/model.py`'s edges gain a `confidence`
field alongside the existing `tier`/`namebased` pair (kept, not replaced —
other consumers already read those two).

Deliberately not built: `refs.target_symbol_id` / `imports.resolved_file_id`
staying persisted (still always NULL, computed at query time as before) —
promoting them into the write path would need re-resolving on every commit
during the history walk, and the columns already exist unused in the schema
for whoever picks that up later. Also not built: the real cross-file
resolution graphify has for JS/TS specifically (tsconfig `paths`/`baseUrl`,
workspace packages, `index.*` barrels) — a substantial, JS/TS-specific
undertaking on its own, separate from the general three-tier mechanism above.

---

## 16. Simulate tab (M18)

Every explorer view through M17 is static and simultaneous — the whole
structure at once. M18 adds a fifth tab, **Simulate**: a transport-controlled
player that walks one *scenario* — one thing a person does with the software —
call by call, pairing the user's world (a terminal/browser/API/file stage)
with the code's world (an animated call-flow graph, a live call stack, the
source line executing) and two narration lines per step. Three lanes feed the
same normalized step shape, increasing in fidelity:

1. **Derived** (⚡) — computed entirely client-side in `explore.js` from the
   call graph, the same architectural pattern as the Map tab's own
   `layerCake`/`runTrace`/`massMap` (payload facts in, view derived in the
   browser). The one new fact it needed: `site/model.py` now attaches a
   call-site `line` to every edge (re-deriving `impact.call_graph`'s own
   `(from_symbol_id, target_name)` match against `refs.line`, without
   touching that shared hot path) — without it, a frame's calls are an
   unordered set. Works on any repo, authors nothing; the tab always shows
   the honesty banner that branches/loops are possibilities, not choices.
2. **Authored** (✏) — `.codemap/scenarios.json`, loaded by `site/scenarios.py`
   (absent/malformed fails soft, same contract as `learn.py`/`explain.py`),
   written by the course-authoring skill (`SKILL.md`, "Simulate" step;
   `references/scenarios-schema.md`). `--emit-brief` also writes
   `.codemap/briefs/scenarios-derived.json` — a Python mirror of the same
   derive algorithm — so authoring means narrating a real call tree, not
   reconstructing one by hand.
3. **Recorded** (⏺) — a new top-level command, `codemap trace -- <command>`
   (`tracer.py`): a `sys.setprofile` call/return recorder that runs the
   target **in-process** (the only way to see its frames at all), maps every
   frame to a symbol key via the existing index rather than re-parsing
   source, and tees stdout/stderr with real timing so the terminal stage
   replays actual output at the actual moments. Real branches taken, real
   loop counts, real values (`--values`, off by default, secret-named
   arguments redacted), real durations. Written to
   `.codemap/traces/<slug>.json`; never hand-authored.

`site/simulate.py` assembles `data.sim` from lanes 2 and 3 only (lane 1 stays
client-side on purpose, so it never goes stale relative to a `max_symbols`
budget cut and keeps working on a repo with neither `scenarios.json` nor a
trace) — resolving every step's symbol key to a graph node index, silently
dropping a step whose key doesn't resolve to a currently-kept node (same
fail-soft contract as `explanations.json`), and dropping a scenario left with
fewer than 2 resolvable steps.

`paintStep(i)` is a pure function of `i` — every normalized step carries its
own call-stack snapshot, computed once by `normalizeSteps()` (which also
back-fills `from` and default plain-fact narration for a recorded step that
carries neither, from the same stack). Scrubbing is therefore exact and
instant, and `#/sim/<scenario-id>/<step>` deep-links reproduce a frame
byte-for-byte identical to reaching it by playback. Layout is computed once
per scenario switch, never per step, so nodes never move while playing.

---

## 9. Testing

`tests/fixtures/build_repo.py` constructs a **real git repository** via `subprocess`, with a
scripted commit sequence. Build this at M1, before the differ exists.

Required commits in the fixture:
1. initial multi-file project (Python + TypeScript)
2. add a function with callers
3. change a signature (structural)
4. change a body only (behavioral)
5. reformat + add comments only (cosmetic — must not be reported as behavioral)
6. rename a symbol and update all references
7. move a symbol to another file unchanged
8. delete a file
9. add an external dependency
10. a commit with a syntax error in one file (must not crash the run)

Each commit asserts an exact expected change set. This suite is the contract; a differ change
that alters it must be deliberate.

---

## 10. Known blind spots

Put this in the generated `README.md` verbatim:

- Dynamic imports, `getattr`/dictionary dispatch, string-based routing, dependency injection,
  metaclass-generated methods, and wrapping decorators are invisible or distorted in the graph
- At T1, caller lists are name-based: expect false positives, and false negatives across
  aliased imports
- Impact analysis is structural only. It cannot tell you whether a change is *correct*
- Intent marked `inferred` is a guess, not a record

---

## 11. Rules for the implementation agent

1. Milestones in order. Do not start M4 before M2's incremental test passes.
2. Stop at the M4 checkpoint and report before continuing.
3. The post-commit hook must never fail a commit. Wrap everything; exit 0 unconditionally.
4. M0–M7 must be fully functional with the LLM disabled. If a feature only works with M8 on,
   it is in the wrong milestone.
5. Never fabricate a graph edge or an intent. Unresolved is a valid, useful value; a wrong
   edge is worse than a missing one.
6. Adding a language must mean adding a registry entry and a `tags.scm` — nothing else at T1.
7. No dependencies beyond §4 without asking. Nothing from §3 "Non-goals", even partially.
8. If a decision in §5 seems to be causing a problem, stop and ask rather than working around it.
