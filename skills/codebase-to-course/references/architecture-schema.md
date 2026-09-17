# `.codemap/architecture.json` schema

Authored **corrections** for the **Architecture** tab, which draws the repo as a
layered architecture diagram. The layers run top to bottom: routes & entry →
views / API → logic → data. Component boxes carry the tech they're built on,
data stores are cylinders, outside services and whoever drives the app are
clouds, and shared code sits in a side panel.

`codemap/site/architecture.py` derives the whole diagram with nothing authored.
It places every file in a layer by scoring folder names, file names, detected
entry points and imports, and it records the reason for each point as
**evidence**. A file with no signal at all lands in Logic, marked as a best
guess (a dashed box). This file does **not** describe the architecture from
scratch. It corrects what the derivation got wrong or couldn't know. A missing
or malformed file is not an error.

```json
{
  "summary": "A Flask web app: pages and a JSON API over one Postgres database, with Celery doing the slow work.",
  "layers": {
    "logic": { "title": "Business rules", "body": "Everything that decides what's allowed — no HTTP, no SQL." }
  },
  "components": {
    "app/services": {
      "title": "Catalog & billing rules",
      "body": "Decides what a dataset is and when a card gets charged."
    },
    "app/__init__.py": { "layer": "entry", "title": "App factory" },
    "codemap/db.py": { "title": "The index store" },
    "app/legacy": { "layer": "data", "tech": ["raw SQL"] }
  },
  "stores": [
    { "id": "postgresql", "label": "System database", "via": ["app/models"] },
    { "id": "uploads", "label": "Uploads bucket", "kind": "storage", "via": ["app/services/files.py"] }
  ],
  "services": [
    { "id": "mailgun", "label": "Mailgun", "kind": "email", "via": ["app/tasks"],
      "body": "Called over plain HTTP, so no SDK import shows up in the code." }
  ]
}
```

## Fields

| field | meaning |
|---|---|
| `summary` | optional. One or two plain sentences on the whole system. It replaces the derived stack line at the top of the tab's overview panel. |
| `layers` | optional. Object keyed by layer id (`entry`, `views`, `api`, `logic`, `data`, `shared`, `tests`). Each value takes `title` (renames the band) and/or `body` (hover text on the band). Unknown ids are dropped. |
| `components` | optional. Object keyed by a repo-relative **folder or file path**, exactly as `architecture-derived.json` prints a component's `path` or one of its `file_paths`. |
| `components.<path>.title` | a human name for the box ("Catalog & billing rules" instead of `services`). |
| `components.<path>.layer` | moves the component to that layer. The box stops being a best guess and its evidence starts with "placed here by architecture.json". |
| `components.<path>.body` | one or two plain sentences, shown in the side panel as "What this is". |
| `components.<path>.tech` | extra tech labels to show first on the box, for tech the imports can't reveal (raw SQL, a REST call made with plain `fetch`). |
| `stores` / `services` | optional. Lists of `{id, label, kind?, body?, via}`. `via` holds folder/file paths whose components talk to it. Reusing a derived `id` (`postgresql`, `redis`, `stripe` — see `architecture-derived.json`) renames and extends that item, while a new `id` adds a cylinder (`stores`) or a cloud (`services`). |

### Folder key vs file key

- **Folder key** (`"app/services"`): the whole folder becomes **one** box, even
  when the derivation had split it file by file because its files looked like
  different layers. Without a `layer`, the box takes the layer that holds most
  of the folder's lines of code.
- **File key** (`"app/__init__.py"`): that one file is pulled out into its own
  box, e.g. to move an app factory out of Logic without moving the rest of its
  folder.

Paths that don't match an indexed folder or file are dropped silently. No HTML
in any string.

## Rules

- **Corrections only.** Don't restate what the tab already shows correctly. An
  empty or absent file is a perfectly good outcome for a repo whose folders
  already say what they are.
- Start with the boxes the brief marks **⚠ best guess**. For each one, either
  confirm it by giving it a `title` (and a `layer`, even if it's the same one),
  or move it.
- Look at every **wrong-way import** the brief lists. Either one side is in the
  wrong layer (fix the `layer`) or it's a real shortcut (say so in a `body`).
- A `title` names what the part **does for the app**, in words a vibe coder
  would recognize: "Checkout rules", not "Service layer" or "BillingServiceImpl".
- Add a store or service only when the code really reaches it without an
  import that reveals it: an ORM URL, a plain HTTP call, a queue named in config.
  Don't invent infrastructure the code never touches.

## Where this fits

See `SKILL.md` for the authoring workflow. `codemap explore --emit-brief` writes
an **Architecture** section into `.codemap/briefs/00-overview.md` (every
component by layer, with its evidence, weak guesses and wrong-way imports
flagged) and `.codemap/briefs/architecture-derived.json` (the full derived model,
with each component's `file_paths`). Correct from those, don't rebuild the
diagram by hand.
