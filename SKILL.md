---
name: codebase-to-course
description: >-
  Turn an indexed codebase into an interactive HTML course inside codemap's
  explore.html, teaching how the code works to a non-technical "vibe coder".
  Use when someone wants to "turn this codebase into a course", "explain this
  codebase interactively", "teach this code", "make a walkthrough / tutorial
  from this project", "explain the functions", or asks to fill in the Learn tab
  or the inspector explanations of the Codegraph Explorer. Produces
  .codemap/learn.json (module narratives, code-to-English translations, quizzes,
  glossary tooltips) and .codemap/explanations.json (a one-line plain-English
  blurb per symbol, shown in the Graph inspector) — both baked into the page by
  `codemap explore`.
---

# codebase-to-course

Adapted from the open-source `codebase-to-course` skill. Instead of generating a
standalone site, this writes `.codemap/learn.json`, which the deterministic
`codemap explore` renderer turns into the **Learn** tab of `explore.html`.

## Audience

A **vibe coder**: builds software with AI tools, no CS background. Goals — steer
AI coding tools, catch when the AI is wrong, escape bug loops, make
build-vs-buy calls, and **acquire the vocabulary of software**. They are *not*
becoming software engineers. Build understanding by tracing something they
already did ("you clicked Analyze — here is the journey your data takes").

## Workflow

1. **Index and emit the analysis pack.**
   ```
   codemap scan            # only if there is no index yet
   codemap explore --emit-brief
   ```
   This writes `.codemap/briefs/00-overview.md` (entry points, module map,
   hotspots) and one `NN-<module>.md` per top-level module, each with **verbatim
   pre-extracted code snippets** — you will not need to re-read the repo.

2. **Read `00-overview.md`.** Then design **4–6 modules** (7–8 only if the
   codebase genuinely has that many distinct concepts). Fewer, deeper modules
   beat more, thinner ones. Menu of module positions, not a checklist:

   | Position | Teaches | Why a vibe coder cares |
   |---|---|---|
   | 1 | What the app does + one core action traced into the code | orientation |
   | 2 | The actors (modules / services) and their jobs | "put this in X, not Y" |
   | 3 | How the pieces talk (data flow, call paths) | debug "it's not showing up" |
   | 4 | The outside world (APIs, DB, deps) | cost, rate limits, failure modes |
   | 5 | The clever tricks (caching, retries, dispatch) | request them from AI |
   | 6 | When it breaks + the big picture | escape AI bug loops, decide next |

   Every module must connect to a practical skill. If it doesn't help the
   learner *do* something better, cut it. **Do not present the curriculum for
   approval — just build it.**

3. **Read the per-module briefs** for the modules you designed, plus
   `references/content-philosophy.md` and `references/gotchas.md` (always), the
   sections of `references/interactive-elements.md` you use, and the two schema
   files `references/learn-schema.md` + `references/explanations-schema.md`.

4. **Write `.codemap/learn.json`** following `references/learn-schema.md`. Per
   module: a metaphor (never reuse one; never "restaurant"), an opening hook,
   3–6 screens, ≥1 code-to-English translation, ≥1 quiz (3–5 questions,
   application not recall), and glossary entries for every technical term.
   Reference real graph nodes by index (`"nodes": [12, 44]`) so screens link
   into the Graph tab. Use the snippets from the briefs **verbatim**.

5. **Write `.codemap/explanations.json`** following
   `references/explanations-schema.md`. One plain-English `what` line for every
   snippet in the per-module briefs (they print each symbol's `key:` — key the
   JSON by that). Optional `why` and `terms` per symbol. This populates the
   "What this does" blurb in the Graph inspector. Do both files — the request
   was for both.

6. **Bake and review.**
   ```
   codemap explore --open
   ```
   Open the Learn tab: every technical term tooltipped, no quiz question
   answerable by scrolling up, each screen more visual than prose, node links
   jump to the right symbol. Then focus a hotspot symbol in the Graph tab and
   confirm its inspector shows the explanation.

## Non-negotiables

- `.codemap/learn.json` and `.codemap/explanations.json` are the only files you
  write. Never edit `explore.css` / `explore.js` / the renderer.
- `explanations.json` keys must equal a `data.nodes[].key` verbatim (the briefs
  print each one). No HTML in any string in either file.
- Use code snippets **exactly as-is** — choose naturally short ones (5–10 lines)
  rather than trimming.
- Max 2–3 sentences per text block; every screen ≥50% visual.
- One concept per screen. Metaphor first, then "in this code, that looks like…".
- If there is a 1% chance a non-technical person doesn't know a word, add a
  glossary entry — the vocabulary *is* the learning.

See `references/` for the full rules; read each file only when you reach the
step that needs it.
