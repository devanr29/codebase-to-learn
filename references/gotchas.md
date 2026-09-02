# Gotchas

Check these before you call a course done.

1. **Under-tooltipping.** The most common failure. Sweep every screen `body` for
   REPL, JSON, flag, entry point, PATH, namespace, function, class, module, CLI,
   SDK, PR, E2E, tree-sitter, AST, SQLite, subprocess — and every acronym on
   first use. If a term wouldn't come up talking to a non-technical friend,
   it needs a `glossary` entry.

2. **Walls of text.** A `body` over ~3 sentences reads like a textbook. Split
   the screen, or convert the extra into a `translation`, `callout`, or `nodes`
   list. Every screen at least half visual.

3. **Recycled or lazy metaphors.** One metaphor per module, and never
   restaurant/kitchen. If the metaphor doesn't feel inevitable for the concept,
   it's decoration — cut it.

4. **Modified code snippets.** Trimming, renaming, "simplifying", removing
   comments — all forbidden. The learner should open the real file and see the
   same lines. Pick a shorter symbol instead; the briefs give you line spans.

5. **Memory-test quiz questions.** "What does API stand for?", "Which file
   handles routing?", anything answerable by scrolling up. Every question must
   put a *new* situation in front of the learner.

6. **Stale `nodes` indices.** Node indices come from the model built at a
   specific commit. If you re-run `codemap scan` after drafting, re-check them
   against a fresh `codemap explore --json` — out-of-range indices are dropped
   silently, so a screen can quietly lose its links.

7. **Invalid JSON = silent fallback.** A trailing comma or a bad `answer` index
   doesn't error — `learn.py` just returns `None` and the Learn tab shows the
   generated Orientation instead. If your course "isn't showing up", validate
   the JSON first.

8. **Too many thin modules.** 4–6 deep modules beat 8 shallow ones. If a module
   doesn't tie back to a concrete skill (steering AI, debugging, deciding),
   merge or cut it.

9. **Writing all modules in one pass.** Later modules come out rushed. Draft one
   module, re-read it against this list, then move on.
