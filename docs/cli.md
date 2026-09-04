# CLI reference

Every command accepts `--path <repo>` to run against a repository other than
the current directory. Run `codemap --version` to check the install, or
`codemap <command> --help` for the flags below straight from argparse.

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
| `--quiet` | suppress the summary line |
| `--if-enabled` | no-op unless `[explore] rebuild_on_commit` is true — this is what the post-commit hook calls, so it never fights a repo that's turned auto-rebuild off |

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

`cmd` is everything after `--`, passed through as-is (e.g. `-m mymodule
--flag`).

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
