---
description: Index the repo and open codemap's explore.html (Graph / Map / Simulate / Learn / Timeline).
---

Run codemap's explore workflow against the current repo.

1. Check the CLI is installed: run `codemap --version`. If that fails, tell the
   user to install it first — `pipx install git+https://github.com/OWNER/codemap`
   (or `uv tool install git+https://github.com/OWNER/codemap`) — and stop.
2. Run `codemap scan` to sync the live worktree graph (safe to run even if
   already up to date).
3. Run `codemap explore --open` to render `.codemap/explore.html` and open it
   in the browser.
4. Report the output path and a one-line summary of what changed since the
   last render (new/changed symbols, if `codemap scan` printed any).

If `$ARGUMENTS` is given, treat it as `--path <ARGUMENTS>` on both commands
(run against that repo instead of the current directory).
