# Content philosophy

Read this before writing any module. Condensed from the upstream
`codebase-to-course` skill, retargeted at `learn.json`.

## Show, don't tell — aggressively visual

- **Max 2–3 sentences per `body`.** A fourth sentence means you should have used
  a visual (a `translation` block, a `callout`, or a `nodes` link list).
- Every screen at least 50% visual weight.
- 3+ related items → don't list them in prose; make them separate screens or a
  `nodes` list.
- Explaining code → a `translation` block, never a paragraph *about* the code.

## Code-to-English translations

The single most valuable teaching tool. Left = real code, right = one plain
line per 1–2 code lines, conversational, explaining the *why* not the *what*.

- **Never modify the code.** No trimming, no simplifying, no "cleaning up". The
  learner must be able to open the real file and see the same lines. Instead,
  *choose* a naturally short, punchy 5–10 line snippet from the brief.
- Keep lines short — the panel wraps, and horizontal scrolling kills it.

## One concept per screen

If you need more room, add a screen. Never cram two ideas together.

## Metaphors first, then reality

Open with a metaphor, then "in this codebase, that looks like…". The metaphor
must feel *inevitable* for the concept, not decorative.

- **Never reuse a metaphor across modules.**
- **Never use the restaurant / kitchen metaphor.** It's overused.
- Good ones: a library card catalog (an index), a bouncer checking IDs (auth),
  an air-traffic controller (an event loop), a postal system (message passing),
  a nightclub at capacity (rate limiting).

## Learn by tracing

Anchor each module in something the learner already experienced by *using* the
software. "You know that button you click? Here's where the data goes after."

## Glossary — no term left behind

If there's a 1% chance a non-technical person doesn't know a word, add a
`glossary` entry. Tooltip: software names, everyday dev jargon (REPL, JSON,
flag, CLI, entry point, PATH, namespace), programming concepts (function,
class, module, dictionary), and every acronym on first use.

Each definition should teach the term well enough to *use it in an AI prompt*:
"A **flag** is an option on a command. You'd tell the AI: 'add a flag for
verbose output.'" **The vocabulary is the learning.**

Don't tooltip terms the learner already owns from their own domain (e.g. ML
terms for someone who does ML).

## Quizzes test application, not memory

Ranked best to worst: (1) "what would you do?" scenarios, (2) debugging
scenarios, (3) architecture-decision questions, (4) tracing exercises.

**Never quiz:** definitions, which-file-does-X, syntax, or anything answerable
by scrolling up. That tests scrolling, not understanding.

Tone: encouraging on wrong answers; the `wrong` text must teach something new,
not scold. No scores, no "you got 3/5".
