---
description: Author the Learn/Packages/Graph/Simulate/Architecture content for this repo's explore.html, end to end.
---

Run the full `codebase-to-course` authoring workflow against the current repo.

1. Check the CLI is installed: run `codemap --version`. If that fails, tell the
   user to install it first — `pipx install git+https://github.com/devanr29/codebase-to-learn`
   (or `uv tool install git+https://github.com/devanr29/codebase-to-learn`) — and stop.
2. If there is no `.codemap/index.db` yet, run `codemap scan`.
3. Run `codemap explore --emit-brief` to write the analysis pack under
   `.codemap/briefs/`.
4. Invoke the `codebase-to-course` skill and follow its workflow exactly —
   it reads the briefs and writes `.codemap/walkthrough.json`,
   `.codemap/libraries.json`, `.codemap/explanations.json`,
   `.codemap/scenarios.json`, `.codemap/glossary.json`, and (corrections
   only, when needed) `.codemap/architecture.json`.
5. Run `codemap check --json` and fix every error it reports until
   `"errors": 0` — the renderer drops a bad entry silently, so this is the
   only place a typo'd key or a dropped scenario shows up.
6. Run `codemap explore --open` to bake those files into `explore.html`
   and review the result as the skill's own "Bake and review" step directs.

If `$ARGUMENTS` is given, treat it as `--path <ARGUMENTS>` on every `codemap`
command (run against that repo instead of the current directory).
