---
description: Explain a commit, uncommitted changes, or everything since you last reviewed. Usage&#58; /codemap:review [<sha>|worktree|catchup]
---

Run codemap's change-explainer against the current repo.

1. Check the CLI is installed: run `codemap --version`. If that fails, tell the
   user to install it first — `pipx install git+https://github.com/devanr29/codebase-to-learn`
   (or `uv tool install git+https://github.com/devanr29/codebase-to-learn`) — and stop.
2. Interpret `$ARGUMENTS`:
   - empty or `HEAD` → `codemap explain HEAD`
   - `worktree` → `codemap explain worktree` (uncommitted changes)
   - `catchup` → `codemap catchup` (everything since the last `codemap reviewed` marker)
   - anything else → treat it as a commit-ish and run `codemap explain <ARGUMENTS>`
3. Print the resulting markdown entry (or digest) to the user. If they want
   the raw change list too, re-run with `--breakdown` (not valid for `catchup`).
4. If the user confirms they've read it, offer to run `codemap reviewed HEAD`
   to advance the marker — only do this if they say yes.
