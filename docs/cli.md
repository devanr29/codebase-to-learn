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
`graph:` head `explore`/`snapshot` are currently reading. On first run in a
repo, creates `.codemap/` and a default `config.toml`.

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

Renders `.codemap/explore.html` — the self-contained, five-tab browsable
surface (Graph / Map / Simulate / Learn / Timeline). See
[`explore-guide.md`](explore-guide.md) for what's in it.

```
codemap explore --open
```

| Flag | Does |
|---|---|
| `--out <path>` | write somewhere other than `.codemap/explore.html` |
| `--json` | print the graph model as JSON, write nothing |
| `--emit-brief` | write module briefs for the `codebase-to-course` skill |
| `--max-symbols N` | cap graph nodes (default: from config) |
| `--open` | open the result in a browser |
| `--quiet` | suppress the summary line and progress output |
| `--if-enabled` | no-op unless `[explore] rebuild_on_commit` is true — this is what the post-commit hook calls, so it never fights a repo that's turned auto-rebuild off |
| `--no-progress` | plain output only — see "Progress output" above |

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
