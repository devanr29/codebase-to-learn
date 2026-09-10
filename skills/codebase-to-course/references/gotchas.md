# Gotchas

Check these before you call the reference done.

1. **Under-glossing.** The most common failure. Sweep every `general` / `here` /
   `what` / folder `purpose` line for REPL, CLI, SDK, flag, PATH, WSGI, ASGI,
   decorator, AST, subprocess, PR, E2E — and every acronym on first use. Check
   the two bundled dictionaries first: if one already covers the term, leave
   it bare and let the tooltip carry it — don't write an inline gloss anymore.
   If neither covers it, it isn't done until it's a `.codemap/glossary.json`
   entry, not a parenthetical bolted onto the sentence.

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

9. **Category titles that are technology labels.** "CSS" or "React state"
   names the tech, not the outcome. The whole point of `walkthrough.json`'s
   `categories` is that a vibe coder reads the title and understands *why*
   that code exists — "Website design" or "Keeping things in sync," not the
   tool that happens to implement it.

10. **A folder `purpose` that's really a file listing.** "Contains Button,
    Card, and Input" restates the folder name with more words. A real
    `purpose` says what the app would lose without it — see
    `references/content-philosophy.md`.

11. **Asserting an orphan flag instead of checking it.** The folder brief's
    "nothing imports this" flag is advisory, not a verdict. Writing `note:
    "this folder is unused"` without opening a single file is a guess wearing
    a fact's clothes — go look, then write what you actually found: a
    leftover nobody deleted, a barrel re-export, or a route table loaded
    dynamically instead of imported.
