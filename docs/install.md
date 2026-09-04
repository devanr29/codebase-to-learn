# Install

codemap ships as two separate installs — the CLI (a normal Python tool) and
the skills (a Claude Code plugin). Install the CLI first; the plugin is
optional and depends on it.

## 1. The CLI

Not on PyPI yet — install straight from GitHub.

**Recommended: [pipx](https://pipx.pypa.io/)** — isolates the install in its
own virtualenv and puts `codemap` on `PATH`:

```
pipx install git+https://github.com/OWNER/codemap
```

**Or [uv](https://docs.astral.sh/uv/):**

```
uv tool install git+https://github.com/OWNER/codemap
```

Both need `git` on `PATH` (they clone the repo themselves — you don't need a
local checkout for this). Requires Python 3.11+.

### Verify

```
codemap --version
codemap status
```

`codemap status` should print `index: empty` the first time — that's correct,
it means the CLI is installed and looking for a repo to index. See
[`explore-guide.md`](explore-guide.md) to go from there.

### From a local checkout (contributing, or before it's pushed to GitHub)

```
git clone https://github.com/OWNER/codemap
cd codemap
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"   # Windows
# .venv/bin/python -m pip install -e ".[dev]"     # macOS/Linux
```

Then either run it as a module (`.venv/Scripts/python -m codemap …`) or
`pipx install --editable .` to also get a `codemap` command that tracks the
checkout live. See [`CONTRIBUTING.md`](../CONTRIBUTING.md).

### Windows notes

- `pipx` itself needs installing first if you don't have it:
  `python -m pip install --user pipx` then `python -m pipx ensurepath` (open a
  new terminal after).
- PowerShell and cmd.exe both work with the commands above unmodified.
- If `codemap` isn't found after install, it's a `PATH` issue from `pipx
  ensurepath` not having taken effect yet — open a new terminal, or check
  `pipx list` / `pipx environment` for where it put the shim.

### Uninstall

```
pipx uninstall codemap
# or: uv tool uninstall codemap
```

This doesn't touch any repo's `.codemap/` directory — delete that yourself if
you want to drop a repo's index too.

## 2. The skills (optional, Claude Code plugin)

The `codebase-to-course` skill writes the three JSON files (`libraries.json`,
`explanations.json`, `scenarios.json`) that `codemap explore` bakes into the
Learn, Graph, and Simulate tabs. You don't need it to use the CLI — every tab
works and falls back gracefully without it. See
[`skills.md`](skills.md) for what it actually does.

**Requires the CLI installed first** (§1 above) — the skill shells out to
`codemap` and has nothing to author without an index.

### Install as a plugin

In Claude Code:

```
/plugin marketplace add OWNER/codemap
/plugin install codemap@codemap
```

(Or, from a local checkout: `/plugin marketplace add /path/to/codemap`.)

This gives you the `codebase-to-course` skill plus three slash commands:

| Command | Does |
|---|---|
| `/codemap:explore` | scan + open `explore.html` |
| `/codemap:course` | run the full authoring workflow (emit briefs → invoke the skill → re-render) |
| `/codemap:review` | `codemap explain` / `catchup` for a commit or your uncommitted changes |

### Install the skill without the plugin system

If you don't want the slash commands, copy just the skill directory:

```
# from a codemap checkout
cp -r skills/codebase-to-course ~/.claude/skills/codebase-to-course
```

Claude Code picks up any skill under `~/.claude/skills/` automatically — no
manifest needed for this path.

### Verify

Ask Claude Code "explain this codebase" in a repo that has `codemap`
installed and indexed (`codemap scan` already run) — it should recognize and
invoke `codebase-to-course` per its description trigger.
