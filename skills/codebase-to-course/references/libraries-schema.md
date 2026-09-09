# `.codemap/libraries.json` schema

Authored input for the **Learn** tab, which is a **library / module reference**:
for every external package the code imports — third-party (`react`, `networkx`,
`flask`…) and standard library (`json`, `os`, `pathlib`…) — plus every
top-level module of the repo itself (`codemap/site`, `codemap/languages`…), it
shows two things:

1. **what that thing does in general** — the one-liner you'd give someone who
   has never heard of it;
2. **how *this* codebase uses it** — the concrete role it plays here, and the
   symbols to look at.

`codemap/site/libraries.py` loads this file; `codemap/site/assets/explore.js`
renders it. A missing or malformed file is **not an error**: the tab falls back
to a reference built from the import graph alone, with the "general" line filled
from a small table of well-known-library blurbs bundled in `explore.js`. This
file only ever *fills in* or *overrides* the prose for a given name.

```json
{
  "items": {
    "networkx": {
      "general": "Graph data structures and algorithms — build a graph, then ask it questions (shortest path, cycles, centrality).",
      "here": "impact.py builds the call graph as a DiGraph and walks it for reachability; model.py reads cycles and strongly-connected components off the same graph.",
      "see": ["codemap/impact.py::call_graph", "codemap/site/model.py::build"]
    },
    "os": {
      "general": "The operating-system bridge in Python's standard library — paths, environment variables, processes.",
      "here": "Only used for `os.environ` reads and path joins during discovery."
    },
    "codemap/site": {
      "general": "Everything that turns the indexed graph into the self-contained explore.html.",
      "here": "model.build() assembles the JSON payload; render.render() inlines the CSS/JS; brief.emit() writes the analysis pack for this skill.",
      "see": ["codemap/site/model.py::build", "codemap/site/render.py::render"]
    }
  }
}
```

## Fields

`items` is an object keyed by **name**:

- an **external package token** exactly as the UI shows it — the import root,
  not the pip/npm name (`tree_sitter`, not `tree-sitter`; `networkx`; `os`).
  `explore.js` also tries the `-`/`_` swap when matching, so either spelling of a
  hyphenated name resolves.
- or a **repo module name** — a top-level entry from the module list
  (`codemap/site`, `codemap`, `tests`).

Each value:

| field | meaning |
|---|---|
| `general` | one or two plain sentences — what this library/module is, for someone who's never used it. Overrides the bundled blurb. |
| `here` | one or two plain sentences — the specific job it does *in this repo*. No general theory; name the modules/functions that touch it. |
| `see` | optional list of `data.nodes[].key` strings — the call sites / owning symbols worth opening in the Graph tab. Unknown keys are dropped silently. Also drives the Simulate tab: a step landing on one of these keys shows this entry's blurb inline, the first time that library appears in a scenario — an empty `see` means a scenario that clearly calls into this library never gets that cross-link. |

Any of the three may be omitted; an entry with none of them is dropped. No HTML
in any string — everything is inserted as text.

## Rules

- Cover every third-party package and every repo module. Standard-library
  entries are optional — add one only when the *way this repo uses it* is
  non-obvious (skip a `general` for `json`; do write a `here` for a surprising
  `ctypes` call).
- `here` must be concrete and repo-specific. If it would read the same for any
  project, it belongs in `general` (or nowhere).
- `see` keys must be verbatim `data.nodes[].key` values (the per-module briefs
  print each one), same rule as `explanations.json` / `scenarios.json`.

## Where this fits

See `SKILL.md` for the authoring workflow. `codemap explore --emit-brief` writes
a **Dependency reference** section into `.codemap/briefs/00-overview.md` and a
`.codemap/briefs/libraries-derived.json` listing every dependency and module
with its importing files and call-site symbol keys — annotate that, don't
rebuild the list by hand.
