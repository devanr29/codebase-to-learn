# Interactive elements

Everything here is rendered by the frozen `codemap/site/assets/explore.js` from
fields in `learn.json`. You never write HTML, CSS, or JS — you write JSON. This
file describes what each field produces so you can choose well.

## 1. Code-to-English translation — `screen.translation`

```json
"translation": {
  "code": "<5–10 lines, verbatim from a brief snippet>",
  "lines": ["plain line 1", "plain line 2", "..."]
}
```

Two-column block: syntax-plain code left, one English line per row right. Each
English line should cover 1–2 code lines. Stacks to one column on narrow
screens. **At least one per module.**

## 2. Quiz — `module.quiz`

```json
"quiz": [
  { "q": "...", "options": ["...", "..."], "answer": 1,
    "right": "why that's correct — reinforces a principle",
    "wrong": "why the picked answer misleads — teaches something new" }
]
```

Rendered at the end of the module. Clicking an option locks the question,
reveals the correct one, and shows `right` or `wrong`. **One quiz block (3–5
questions) per module.** Application questions only (see content-philosophy).

## 3. Glossary tooltips — `module.glossary`

```json
"glossary": { "idempotent": "Safe to run twice — the second run changes nothing. ..." }
```

Every whole-word match of a key in any screen `body` (first occurrence) becomes
a dashed-underline term; hover/focus shows the definition in a tooltip that is
`position: fixed` on `document.body`, so it is never clipped. Define a term the
first time it appears in the module.

## 4. Callout — `screen.callout`

```json
"callout": { "kind": "accent", "title": "aha!", "text": "one punchy insight" }
```

`kind`: `accent` (insight), `info`, `warning`. Only `accent` has distinct
styling today; the others render the same. **Max two per module.**

## 5. Graph links — `screen.nodes`

```json
"nodes": [12, 44, 61]
```

A list of graph node indices (from the briefs / the `--json` model). Rendered as
a short list of clickable rows; each jumps to that symbol in the **Graph** tab
(neuron view). Use this instead of describing a call path in prose — it *is*
the "message flow" element, backed by real edges.

## 6. Simulate — `screen.sim`

```json
"sim": "explore-run"
```

A scenario id from `.codemap/scenarios.json` (`references/scenarios-schema.md`).
Renders a small link-out card ("Open in Simulate — watch it run, step by
step") instead of an embedded player — clicking it jumps to the **Simulate**
tab, a transport-controlled animation of one call-by-call run: the user's
world above (a terminal/browser/API/file stage) and the code's world below
(an animated call-flow graph, a scrollable trace log you can click back
through, the source line executing), with two narration lines per step — 👤
what the user perceives, ⚙ what the code is doing. The Simulate rail lists the
whole `scenarios.json` curriculum, grouped by `group` and in workflow `order`.

Use this instead of the old "describe a call path in prose" advice: point a
screen at a real scenario rather than narrating a trace by hand. `sim` can
name any curriculum entry — including a `root`-only one whose steps the
renderer derives; you don't need to hand-author `steps` for a scenario just to
link it. With no `scenarios.json` at all, Simulate still offers a derived
scenario (⚡, computed from the call graph) for any busy symbol.

## What is intentionally NOT here

Dropped from the upstream skill because they have no honest mapping to a call
graph or were broken as documented: group-chat animation, drag-and-drop
matching, the HTML/CSS/JS layer toggle, and `data-steps` flow JSON — Simulate
(§6) is the real, graph-backed version of what `data-steps` was gesturing at.
Point a screen at a scenario via `sim`, or open a symbol directly in the
Graph tab, rather than trying to reproduce a call path in prose.
