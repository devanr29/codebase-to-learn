---
description: Author the Learn/Graph/Simulate content for this repo's explore.html, end to end.
---

Run the full `codebase-to-course` authoring workflow against the current repo.

1. Check the CLI is installed: run `codemap --version`. If that fails, tell the
   user to install it first — `pipx install git+https://github.com/OWNER/codemap`
   (or `uv tool install git+https://github.com/OWNER/codemap`) — and stop.
2. If there is no `.codemap/index.db` yet, run `codemap scan`.
3. Run `codemap explore --emit-brief` to write the analysis pack under
   `.codemap/briefs/`.
4. Invoke the `codebase-to-course` skill and follow its workflow exactly —
   it reads the briefs and writes `.codemap/libraries.json`,
   `.codemap/explanations.json`, and `.codemap/scenarios.json`.
5. Run `codemap explore --open` to bake the three files into `explore.html`
   and review the result as the skill's own "Bake and review" step directs.

If `$ARGUMENTS` is given, treat it as `--path <ARGUMENTS>` on every `codemap`
command (run against that repo instead of the current directory).
