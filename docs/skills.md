# The tool ↔ skill contract

`codemap explore` renders `.codemap/explore.html` from **only** the index —
every tab works with zero authored content, falling back to what can be
derived from the code itself. The `codebase-to-course` skill exists to make
several of those tabs better by *authoring prose the renderer can't derive*:
it writes JSON files under `.codemap/`, and the renderer bakes them in
verbatim on the next `codemap explore`.

```
codemap scan → index.db → codemap explore --emit-brief → .codemap/briefs/*
                                                                  │
                                              codebase-to-course skill reads
                                                                  │
                                                                  ▼
                      .codemap/{libraries,explanations,scenarios}.json
                                                                  │
                                              codemap explore (bakes them in)
                                                                  ▼
                                                    .codemap/explore.html
```

The renderer never calls the skill, never calls an LLM, and never blocks on
these files existing — this is the one rule that makes the split safe:

> **All of these files are optional. Missing or malformed content is not an
> error.** Each tab has a deterministic, derived-from-the-graph fallback.

| File | Tab it fills | Fallback when absent |
|---|---|---|
| `.codemap/libraries.json` | **Packages** | a bundled table of well-known-library one-liners + the import graph |
| `.codemap/explanations.json` | **Graph** inspector | no blurb shown; everything else in the inspector (fan-in/out, blast radius, source) is unaffected |
| `.codemap/scenarios.json` | **Simulate** | a derived scenario computed client-side in `explore.js` for the busiest few functions |
| `.codemap/walkthrough.json` | **Learn** | a plain-language intro, an optional category map, and per-folder prose — falls back to a derived folder tree with no authored content |
| `.codemap/glossary.json` | *(cross-cutting — every tab's prose)* | the two dictionaries bundled with `codemap` (well-known packages, common concepts) still power tooltips alone |

The Map and Timeline tabs have **no** authored input at all — they're built
entirely from the graph.

## Why a separate skill instead of building this into the CLI

Writing good prose — "what does `networkx` do", "what's this repo's own
`retention.py` for", "what's the third scenario a new contributor should
watch" — needs a language model reading real code and forming a judgment.
the `explore` renderer itself stays deterministic and never calls an LLM (see
[`spec.md`](spec.md) §5 — this is a locked design decision, not an oversight).
The one optional LLM stage the CLI has (`narrate.py`, gated by `[llm] enabled`
in `config.toml`, off by default) feeds `codemap explain`'s report and is
unrelated and untouched by any of this.
The skill is where the judgment for Learn/Packages/Graph/Simulate content happens, and
it happens in whatever agent session you're already using, not behind an API
key the CLI would have to manage.

## Authoring by hand, without Claude Code

The skill is a convenience, not a requirement. These are all plain,
documented JSON — write them yourself, or with any other tool:

- [`skills/codebase-to-course/references/libraries-schema.md`](../skills/codebase-to-course/references/libraries-schema.md)
- [`skills/codebase-to-course/references/explanations-schema.md`](../skills/codebase-to-course/references/explanations-schema.md)
- [`skills/codebase-to-course/references/scenarios-schema.md`](../skills/codebase-to-course/references/scenarios-schema.md)
- [`skills/codebase-to-course/references/walkthrough-schema.md`](../skills/codebase-to-course/references/walkthrough-schema.md)
- [`skills/codebase-to-course/references/glossary-schema.md`](../skills/codebase-to-course/references/glossary-schema.md)

`codemap explore --emit-brief` is useful either way — it writes
`.codemap/briefs/` with every symbol's `key:`, pre-extracted snippets, and (for
scenarios) real call trees to narrate from, so you're not reconstructing
anything by hand even if you skip the skill entirely.

## Where the skill lives once installed

The skill's own instructions (`SKILL.md`) reference its `references/*.md`
files with plain relative paths. That keeps resolving correctly wherever the
skill ends up — installed as a plugin (`~/.claude/plugins/…`), copied loose
into `~/.claude/skills/codebase-to-course/`, or run straight from this repo's
`skills/codebase-to-course/` — because the skill and its references always
move together. See [`install.md`](install.md) for the install paths.

## More on the skill itself

- [`skills/codebase-to-course/SKILL.md`](../skills/codebase-to-course/SKILL.md) — the authoring workflow
- [`skills/codebase-to-course/references/content-philosophy.md`](../skills/codebase-to-course/references/content-philosophy.md) — audience and tone rules
- [`skills/codebase-to-course/references/gotchas.md`](../skills/codebase-to-course/references/gotchas.md) — common authoring mistakes
- [`skills/codebase-to-course/references/interactive-elements.md`](../skills/codebase-to-course/references/interactive-elements.md) — how the rendered tabs use this content
