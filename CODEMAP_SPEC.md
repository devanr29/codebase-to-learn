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
- Web UI, desktop app, IDE plugin
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
