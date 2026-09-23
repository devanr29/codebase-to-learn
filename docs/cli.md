# CLI reference

Every command accepts `--path <repo>` to run against a repository other than
the current directory. Run `codemap --version` to check the install, or
`codemap <command> --help` for the flags below straight from argparse.

## Progress output

`scan`, `explore`, and `trace` can take a while on a large repo, so they show
live progress on **stderr** (stdout stays parseable — `explore --json`,
`snapshot`'s markdown, and so on are never touched). It adapts to where it's
running:

- **A real terminal:** an animated bar per phase (`history`, `worktree`,
  `graph`, `symbols`, …) that settles into a one-line summary when the phase
  finishes. ASCII/Unicode blocks only, redrawn with `\r` — no ANSI escapes, so
  it renders correctly in Windows conhost, Git Bash/MINTTY, and Windows
  Terminal alike.
- **Redirected or piped** (the post-commit hook's `codemap scan >log 2>&1`,
  Claude Code capturing output, `codemap scan | cat`): plain `\n`-terminated
  lines at each phase's start, every 25% boundary, and its end. No animation.
- **Silenced:** pass `--no-progress`, set `CODEMAP_NO_PROGRESS=1`, or pass
  `explore --quiet` — nothing is written at all.

`CI=1`, `TERM=dumb`, or `NO_COLOR` set also downgrade a real terminal to the
plain-line form.

## `codemap status`

Prints the index state — commit/file/symbol/ref/change counts, and which
`graph:` head `explore`/`snapshot` are currently reading, and the `engine:`
line: `codemap only` with the reason (no codebase-memory-mcp index of this repo,
an index format codemap doesn't know, or `engine.codebase_memory = "off"`), or
`codemap + codebase-memory` when its index will be merged in by `explore`. On
first run in a repo, creates `.codemap/` and a default `config.toml`.

```
codemap status
```

## `codemap scan`

Indexes the repository into `.codemap/index.db`. Walks every commit since the
last one it saw (or the whole history, on first run), parses changed files
with tree-sitter, and always syncs the **live worktree graph** — the code as
it is on disk right now, uncommitted changes included. Run this after every
commit you want reflected, or any time before `explore`/`snapshot`.

```
codemap scan
codemap scan --since <commit>   # index from this commit forward instead of the last indexed one
codemap scan --no-progress      # plain output only -- see "Progress output" above
```

## `codemap explain [<rev>]`

Prints the deterministic markdown change entry for a commit — what changed,
why (stated intent), what it affects, and a "read this first" pointer.

```
codemap explain              # HEAD
codemap explain HEAD~3
codemap explain worktree     # uncommitted changes, not yet a commit
codemap explain HEAD --breakdown   # also print the raw change list
```

## `codemap catchup`

One digest of every change entry since the last `codemap reviewed` marker —
for catching up after being away, instead of reading commits one at a time.

```
codemap catchup
```

## `codemap reviewed [<rev>]`

Advances the "last reviewed" marker `catchup` reads from.

```
codemap reviewed          # HEAD
codemap reviewed HEAD~1
```

## `codemap snapshot`

Renders a markdown architecture snapshot of the current (live) graph — no
history, just "what does this codebase look like right now".

```
codemap snapshot
```

## `codemap explore`

Renders `.codemap/explore.html` — the self-contained, seven-tab browsable
surface (Graph / Architecture / Map / Simulate / Learn / Packages / Timeline).
See [`explore-guide.md`](explore-guide.md) for what's in it.

```
codemap explore --open
```

| Flag | Does |
|---|---|
| `--out <path>` | write somewhere other than `.codemap/explore.html` |
| `--json` | print the graph model as JSON, write nothing (without the embedded file text the page carries for its source viewer) |
| `--emit-brief` | write the analysis pack in `.codemap/briefs/` that the `codebase-to-course` skill authors from — `00-overview.md` plus one brief per module, and `folders-`, `libraries-`, `scenarios-` and `architecture-derived.json` |
| `--max-symbols N` | cap graph nodes (default: from config) |
| `--open` | open the result in a browser |
| `--quiet` | suppress the summary line and progress output |
| `--if-enabled` | no-op unless `[explore] rebuild_on_commit` is true — this is what the post-commit hook calls, so it never fights a repo that's turned auto-rebuild off |
| `--no-progress` | plain output only — see "Progress output" above |

## `codemap check`

Compares the skill-authored `.codemap/*.json` (`explanations`, `scenarios`,
`walkthrough`, `libraries`, `architecture`, `glossary`) with the real graph and
reports everything the renderer would drop without a word: a symbol key that
matches nothing (with a "did you mean"), a scenario left with fewer than two
resolvable steps, a library name with no Packages page, an architecture
component that isn't a folder codemap groups by, an unparseable file. An
**error** means the entry is lost or points at nothing; a **warning** means it
still renders but probably isn't what was meant.

```
codemap check
codemap check --json      # {"ok", "errors", "warnings", "issues": [{file, path, severity, message}], "authored"}
codemap check --strict    # exit 1 when there are errors (default exit is 0)
```

`codemap explore` prints a one-line stderr note ("3 problems in the authored
.codemap/*.json … run `codemap check`") whenever it finds errors, unless
`--quiet`.

## `codemap calls <symbol>`

What a symbol calls, or what calls it, read off the same resolved call graph the
Graph tab draws — every line is an edge codemap actually found, with the file
and line of the call and its confidence (`EXTRACTED` for a same-file or
class-owned call, `INFERRED` through an import, `AMBIGUOUS` for a name-only
match that may be a false link). `<symbol>` is a `path::Name` key, a qualified
name (`Class.method`) or a bare name; a bare name that matches several symbols
lists the candidate keys and exits 1.

```
codemap calls cmd_explore                 # what it calls, 3 hops deep
codemap calls build_report --in           # what calls it
codemap calls svc/cli.py::main --both --depth 4 --no-guesses
codemap calls build_report --json         # nodes + edges, for scripts and the course skill
```

| Flag | Does |
|---|---|
| `--out` / `--in` / `--both` | direction: callees (default), callers, or both |
| `--depth N` | hops to follow (default 3); a `... more beyond depth N` line says when the cap hid more |
| `--no-guesses` | leave out `AMBIGUOUS` edges, and whatever only they reach |
| `--json` | `{"target", "depth", "out": {"nodes", "edges", "truncated"}, "in": …}` |

A node already shown higher in the tree is marked `(*)` and not expanded again,
so every edge appears exactly once and a cycle ends.

## `codemap trace -- <command>`

Records a **real run** of your program as a call/return trace for the
Simulate tab's recorded lane (⏺) — real branches, real loop counts, real
output, not a derived guess. Python only; writes to
`.codemap/traces/<slug>.json` (git-ignored), read back by `codemap explore`.

```
codemap trace -- -m codemap explore
codemap trace --name "Full request cycle" -- -m myapp.server
codemap trace --values -- -m myapp.cli process input.csv
```

| Flag | Does |
|---|---|
| `--name <title>` | scenario title (default: the command itself) |
| `--values` | capture call-argument reprs, truncated; secret-named args redacted |
| `--no-progress` | plain output only — see "Progress output" above |

`cmd` is everything after `--`, passed through as-is (e.g. `-m mymodule
--flag`). If `cmd` is itself `codemap` (as in the first example above), its
progress output is silenced automatically — otherwise its animation would
fight the recorder's own, and since it isn't writing to a real terminal by the
time it runs, it would end up captured as text in the trace instead.

## `codemap note "<text>"`

Records the stated intent for the *next* commit — the highest-priority source
in codemap's intent resolution order (see [`spec.md`](spec.md) §"Intent").

```
codemap note "fix the race condition in the retry queue"
```

## `codemap install-hook`

Installs a post-commit hook (`scan → explain → explore`, via `--if-enabled`)
that keeps the index and `explore.html` current after every commit. Designed
to never block or fail a commit — see `codemap/hooks/post-commit`.

```
codemap install-hook
```

Turn the explore-rebuild half off per-repo without uninstalling the hook:

```toml
# .codemap/config.toml
[explore]
rebuild_on_commit = false
```

## Optional engine (`[engine]`)

If a [codebase-memory-mcp](https://github.com/DeusData/codebase-memory-mcp) index
of the repo exists, `explore` merges its call links into the graph (see
`docs/spec.md` §20). Nothing to enable; to control it:

```toml
# .codemap/config.toml
[engine]
codebase_memory = "auto"   # "off" never looks
cache_dir = ""             # empty = $CBM_CACHE_DIR, then ~/.cache/codebase-memory-mcp
```

Index the repo with `codebase-memory-mcp cli index_repository --repo-path .`, then
`codemap status` shows whether it was found.
