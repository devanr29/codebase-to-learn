# `.codemap/walkthrough.json` schema

Authored input for the **Learn** tab, which is now a **project walkthrough**: what
the project is, how its top-level parts relate, a repo-wide category map of
"what this app is made of," and one page per folder worth explaining.

`codemap/site/walkthrough.py` loads this file; `codemap/site/assets/explore.js`
renders it. A missing or malformed file is **not an error** — the tab falls back
to a bare folder tree derived from the index, no intro and no categories. This
file only ever *fills in* the prose; it never changes what folders exist.

```json
{
  "intro": {
    "what": "A budgeting app: a React Native mobile client and a FastAPI backend that share one Postgres database.",
    "sides": {
      "mobile": {
        "root": "mobile",
        "title": "What the user taps",
        "body": "Everything under mobile/ runs on the phone — every screen, button and animation lives here."
      },
      "backend": {
        "root": "backend",
        "title": "Where the money actually lives",
        "body": "Everything under backend/ runs on a server the user never sees — it owns the database and the rules for touching it."
      }
    },
    "seam": {
      "note": "The only place the two sides meet is one HTTP call: the app asks the API to sync a wallet.",
      "see": ["mobile/src/api/wallets.ts::syncWallet", "backend/app/routers/wallets.py::sync_wallet"]
    }
  },

  "categories": [
    {
      "title": "Money movement",
      "body": "The code that actually changes a balance.",
      "groups": [
        { "title": "Syncing", "see": ["backend/app/services/ledger.py::apply_sync"] },
        { "title": "Styling the ledger screen", "see": { "CSS": ["mobile/src/screens/ledger.css"] } }
      ]
    }
  ],

  "folders": {
    "backend/app/services": {
      "title": "The rules for touching money",
      "purpose": "Without this folder there's nothing stopping a route from writing a bad balance straight to the database.",
      "read_first": "backend/app/services/ledger.py",
      "categories": ["Money movement"],
      "see": ["backend/app/services/ledger.py::apply_sync"]
    },
    "backend/app/schemas": {
      "title": "Shapes the API promises",
      "purpose": "Nothing imports this folder directly, but FastAPI reads every module here to build request/response validation — remove it and every route loses its type checking.",
      "note": "Flagged as an orphan by the folder brief; it's a dynamically-loaded schema registry, not dead code."
    }
  },

  "glossary": {
    "wallet sync": "The moment the app asks the server to reconcile what it has stored locally against the real ledger."
  }
}
```

## Fields

**`intro`** — optional, but almost always worth writing:

| field | meaning |
|---|---|
| `what` | one or two plain sentences: what this project *is* |
| `sides` | object keyed by an invented name for each top-level part of the app (`frontend`/`backend`, `mobile`/`api`, or a single key for a one-part repo). 0 or more. |
| `sides.<key>.root` | **required** — a repo-relative path this side is rooted at |
| `sides.<key>.title` | optional short label |
| `sides.<key>.body` | optional, one or two sentences on what lives under `root` and why |
| `seam` | optional — write it only when 2+ sides actually connect |
| `seam.note` | one sentence on where and how the sides meet |
| `seam.see` | the call sites that show the connection, same `see` shape as everywhere else |

**`categories`** — array, the repo-wide "what this app is made of" map:

| field | meaning |
|---|---|
| `title` | **required** — invented per project, e.g. "Website design," "Money movement." Never hard-coded, never the same list twice across projects. |
| `body` | optional, one sentence |
| `groups[].title` | a sub-heading under the category |
| `groups[].see` | the references for that group — flat array or grouped object, see **`see` polymorphism** below |

**`folders`** — object keyed by repo-relative folder path, one entry per folder worth explaining:

| field | meaning |
|---|---|
| `title` | optional short label |
| `purpose` | **required** — the one thing every folder entry must have |
| `read_first` | optional — one path, the first file worth opening here |
| `note` | optional — a caveat, correction, or context the reader needs |
| `categories` | optional — which `categories[].title` values this folder participates in |
| `see` | optional — same `see` shape as `groups[].see` |

**`glossary`** — optional, a flat `term: "definition"` map. A shorthand for
defining a term right where you coined it while writing the walkthrough,
instead of switching files. It's the same pool as `.codemap/glossary.json`'s
`items` (see `references/glossary-schema.md`) with `display` implicitly equal
to the key — the standalone file is still where this skill's authoring step
actually writes, so treat this field as a convenience, not a second workflow.

## Rules

- `sides.<key>.root` is the only required field inside `sides` — everything
  else in `intro` is prose you can skip if it doesn't earn its place.
- Write `seam` only when there genuinely are 2+ `sides` and they connect
  somewhere in the code. A single-sided project (a CLI, a script) has no
  `seam` and often no `sides` at all — `intro.what` alone is enough.
- **`see` polymorphism.** Wherever `see` appears (`seam.see`, `groups[].see`,
  `folders[<path>].see`), it accepts either a flat array of
  `data.nodes[].key` strings, or an object of `{"sub-label": [keys...]}` to
  group references further within that group or folder. Same rule as
  `libraries.json`.
- **Keys must be verbatim, but a bare file path is also valid here.** Every
  `data.nodes[].key` must be copied from a brief, never invented, and an
  unresolvable key is dropped silently — same as `libraries.json`. Unlike
  `libraries.json`, a `see` entry may *also* be a plain repo-relative file
  path for something codemap doesn't index at all (a `.css` file, a config
  file). It renders as plain text pointing at that file instead of a
  clickable graph link. This matters because styling code is almost always
  CSS, which codemap never parses — without this exception, a "Styling"
  group would have nothing to point at.
- **`folders` is not exhaustive by requirement.** Unlike `libraries.json`'s
  dependency coverage, you don't owe every folder an entry — only the ones
  that earn one: a folder whose purpose isn't obvious from its name alone, or
  one flagged by `.codemap/briefs/` as an orphan/leftover worth confirming or
  explaining. It's fine for most folders to have no entry and just render as
  a bare row in the tree.
- **An orphan flag is a question, not a fact.** The folder brief lists every
  folder candidate with file counts, direct dependencies, and an advisory
  "nothing imports this" flag where applicable. Investigate before writing
  anything — if the flag is a false positive (a barrel re-export, a
  dynamically-loaded route or schema table), the `note` says what the folder
  is actually for. Never assert "this folder is unused" without checking; if
  you did check and it really is dead, say that plainly instead.
- **`categories` is authored once.** The list in top-level `categories[]` is
  the whole taxonomy for this project. A folder's own `categories` field only
  ever points back into that same list by title — never invent a second,
  slightly-different category name on a folder page. If a folder needs a
  category that doesn't exist yet, add it to `categories[]` first.
- `purpose` answers "what would I lose if this folder were deleted," never
  "what files live in here" — see `references/content-philosophy.md`.
- No HTML in any string.

## Where this fits

See `SKILL.md` for the authoring workflow. `codemap explore --emit-brief`
writes a **folder brief** into `.codemap/briefs/00-overview.md` listing every
folder candidate with its file count, direct dependencies, and an advisory
orphan flag — start from that, don't rediscover the folder list by hand. See
`references/glossary-schema.md` for the standalone `.codemap/glossary.json`
this file's own `glossary` shorthand feeds into.
