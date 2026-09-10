# `.codemap/glossary.json` schema

Authored input for the **tooltip** layer: hover or tap on a term anywhere the
skill's own prose is rendered (Learn, Packages, the Graph inspector, Simulate)
and this file supplies the definition. `codemap/site/glossary.py` loads it.

The tool already bundles two dictionaries — `codemap/site/data/glossary-concepts.json`
(~75 core programming concepts: component, hook, endpoint, middleware, ORM,
migration...) and `codemap/site/data/glossary-packages.json` (~200 package/tool
names: React, Vite, FastAPI, axios, PostgreSQL, Docker...). Together they cover
most everyday dev vocabulary. `.codemap/glossary.json` exists for the gap: a
term specific to *this* project's own domain, or a term neither bundled file
happens to cover. A missing or malformed file is not an error — tooltips just
fall back to the two bundled dictionaries alone.

```json
{
  "items": {
    "wallet sync": {
      "display": "wallet sync",
      "definition": "The moment the app asks the server to reconcile what it has stored locally against the real ledger."
    },
    "ledger": {
      "definition": "This app's running record of every balance change, kept in Postgres."
    }
  }
}
```

## Fields

`items` is an object keyed by the **term** exactly as it should be matched in
prose (a word or short phrase, case-sensitive):

| field | meaning |
|---|---|
| `display` | optional — the label shown in the tooltip header. Defaults to the key. |
| `definition` | **required** — one sentence, same tone as everything else this skill writes. |

## Rules

- **Check the bundled files before writing an entry.** Skim
  `codemap/site/data/glossary-concepts.json` and `glossary-packages.json`
  once before authoring. A duplicate entry isn't wrong — a project entry
  always takes precedence over a bundled one when both define the same
  term — but it's wasted effort. Only add a term that's genuinely missing
  from both.
- Scope is **this project's own domain vocabulary** — a name or phrase this
  codebase invented or uses in a specific way ("wallet sync," "side quest,"
  whatever this repo calls its own concepts) — plus any everyday dev term
  that happens to fall outside both bundled dictionaries.
- One sentence per `definition`. No jargon inside the definition that itself
  needs a gloss — if you can't define a term in one plain sentence, the term
  is probably two terms.
- No HTML in any string.

## Where this fits

See `SKILL.md` for the authoring workflow: author `.codemap/walkthrough.json`
first, then check the two bundled dictionaries, then write only the gaps here.
Every term that appears in this skill's own authored prose — `walkthrough.json`
and `libraries.json`'s `general`/`here` lines — and isn't already covered
belongs in this file. See `references/content-philosophy.md` for what counts
as jargon needing a definition at all.
