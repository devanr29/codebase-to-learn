# How the files connect

`codemap explore` bakes `walkthrough.json`, `libraries.json`,
`explanations.json`, `scenarios.json` and `glossary.json` into `explore.html`.
They are rendered by the frozen `codemap/site/assets/explore.js` — you write
JSON, never HTML/CSS/JS. Each file fills one tab; the glue between them is the
symbol **key**.

## The key is the join

Every `data.nodes[].key` is printed in the per-module briefs. It is the id that
links content across tabs:

- `explanations.json` is keyed by it — the Graph inspector shows that `what`
  line when you focus the symbol.
- `libraries.json` `see: [...]` is a list of them — each becomes a clickable row
  under **See in the graph** that jumps to the Graph tab.
- `scenarios.json` `root` (and every `steps[].node`) is one — the Simulate
  player resolves it to a node in the animated call flow.

A key that no longer resolves is dropped silently, never an error.

## Packages tab — `libraries.json`

One entry per imported package and per repo module. The renderer shows, in
order: a **subtitle** it derives (`third-party · imported by N files`, or
`internal module · N files · M symbols`), your **In general** line (or the
bundled blurb, or a "add one" placeholder), your **In this codebase** line (or,
if you omit `here`, the list of importing files it derives), and **See in the
graph** (`see` keys, or derived call sites — the busiest symbols in the
importing files).

## Learn tab — `walkthrough.json`

The renderer shows, in order: the **intro** (`what`, then each `sides` entry's
`title`/`body`, then the `seam` note if present), the **category map**
(`categories[]`, each with its `body` and `groups[]`), and the **folder
tree** (every indexed folder, with a `folders[<path>]` entry's `title`,
`purpose`, `read_first`, `note` and `see` shown where authored, and a bare row
where it isn't). Its `see`/`folders[<path>].see` keys resolve exactly like
`libraries.json`'s `see` does — same "the key is the join" mechanism above —
except a `walkthrough.json` `see` entry may also be a bare repo-relative file
path for something codemap doesn't index at all (a `.css` file, most often),
which renders as plain text pointing at that file instead of a clickable graph
row. `glossary.json` powers hover/tap tooltips on every tab, not just this one.

## Graph inspector — `explanations.json`

`what` (required), optional `why`, optional `terms` (`{word: definition}` — each
becomes a dashed-underline tooltip inside `what`/`why`). Shown in the right-hand
inspector when a symbol is focused.

## Simulate tab — `scenarios.json`

See `references/scenarios-schema.md`. The rail lists the whole curriculum,
grouped by `group` and ordered by `order`. A `root`-only entry has its call tree
derived; author `steps` only for hero scenarios.
