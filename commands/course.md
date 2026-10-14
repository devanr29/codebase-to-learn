---
description: Investigate this repo through codemap's MCP server, settle what static analysis couldn't, and author the explore.html content end to end.
---

Run the full `codebase-to-course` workflow against the current repo.

1. Check the CLI is installed: run `codemap --version`. If that fails, tell the
   user to install it first and stop. The install is
   `pipx install git+https://github.com/devanr29/codebase-to-learn` (the MCP
   server is built in), or the `uv tool install` equivalent.
2. If there is no `.codemap/index.db` yet, or `overview` reports the index is
   stale, run `codemap scan`.
3. Invoke the `codebase-to-course` skill and follow its workflow exactly:
   1. `overview`;
   2. work through `open_questions`, answering each with `answer_question` and
      file:line evidence;
   3. `submit` the walkthrough, package, glossary, architecture and hero
      scenario prose;
   4. `check` until there are zero errors.
   If the `codemap` MCP tools aren't available in this session, the skill's
   `references/file-mode.md` is the fallback (briefs + hand-written JSON +
   `codemap check --json`).
4. Run `codemap explore --open` to bake everything into `explore.html`, then
   review it as the skill's "Bake and review" step directs. Report what was
   settled, what is still unknown, and how many open questions remain.

If `$ARGUMENTS` is given, treat it as `--path <ARGUMENTS>` on every `codemap`
command (run against that repo instead of the current directory).
