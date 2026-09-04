# Gotchas

Check these before you call the reference done.

1. **Under-glossing.** The most common failure. Sweep every `general` / `here` /
   `what` line for REPL, CLI, SDK, flag, entry point, PATH, namespace, WSGI,
   ASGI, ORM, decorator, AST, subprocess, PR, E2E — and every acronym on first
   use. If a term wouldn't come up talking to a non-technical friend, gloss it
   inline ("a **decorator** — a line starting `@` that wraps the function below").

2. **`here` lines that aren't about this repo.** "networkx builds graphs and
   runs algorithms on them" is a `general` line wearing a `here` costume. A real
   `here` names files and functions: "impact.py's `call_graph` builds a
   `DiGraph`; `model.build` reads cycles off it." If it's generic, move it to
   `general` or delete it.

3. **Skipped entries.** The Dependency reference and Scenario index are the
   scope. Every third-party package and every repo module needs at least a
   `general`; don't stop at "the interesting ones".

4. **Invented symbols.** A `see` key, a function name, or a file path you didn't
   copy from a brief. The reader will open it and check. Out-of-graph `see` keys
   are dropped silently, so a bad key quietly loses the link.

5. **Stale keys after a re-scan.** Keys come from the model at a specific commit.
   If you re-run `codemap scan` after drafting, re-check `see` /
   `explanations.json` / `scenarios.json` keys against a fresh
   `codemap explore --json`.

6. **Invalid JSON = silent fallback.** A trailing comma doesn't error — the
   loader returns `None` and the tab falls back to the graph-only view. If your
   content "isn't showing up", validate the JSON first.

7. **Walls of text.** Over ~3 sentences in a `general` / `here` reads like a
   textbook. Cut to the one thing the reader needs.

8. **Writing it all in one pass.** Later entries come out rushed. Do the
   external packages, re-read against this list, then the modules, then the
   scenario index.
