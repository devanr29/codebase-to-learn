# `.codemap/explanations.json` schema

`codemap/site/explain.py` loads and validates this file. It is the **inspector**
companion to `libraries.json`: where `libraries.json` fills the Learn tab's
library / module reference, `explanations.json` attaches a one-line
blurb to individual symbols in the **Graph** tab's right-hand inspector.

A missing or malformed file is **not an error** — the inspector just shows no
blurb for that symbol. Invalid JSON, `symbols` not an object, or an entry with no
non-empty `what` string → that entry is silently dropped.

```jsonc
{
  "symbols": {
    // key MUST equal a value of data.nodes[].key exactly — "<path>::<qualified name>".
    // The per-module briefs print the key for every snippet; copy it verbatim.
    "codemap/report.py::render_commit": {
      "what": "Turns one commit's semantic diff into the markdown block that codemap explain prints.",
      "why":  "Read this when a change entry looks wrong — the wording is decided here.",   // optional
      "terms": {                                                                            // optional
        "semantic diff": "the structural/behavioral/cosmetic classification of what a commit changed",
        "markdown block": "the plain-text report you see under .codemap/changes/"
      }
    },

    "codemap/indexer.py::scan": {
      "what": "Walks every new commit and rebuilds the symbol graph for it, reusing unchanged files from the parent snapshot."
    }
  }
}
```

## Rules the renderer assumes

- `what` is one or two precise sentences for an engineer new to the repo:
  responsibility, inputs → outputs, side effects. Project-specific jargon goes
  in `terms` or `glossary.json`. Trimmed to 400 chars.
- `why` is optional, same limit; render as a quieter second line.
- `terms` is a `word -> definition` map. Matched as whole words, first occurrence,
  in both `what` and `why`, and shown as a hover tooltip (same mechanism as the
  Learn tab glossary). Keep definitions to one sentence.
- No HTML in any string — every value is inserted as text.
- Keys that don't match any indexed symbol are ignored, so it is safe to leave
  stale entries when the graph shrinks — but prefer to prune them.
- Cover **at least** the `unexplained_symbol` questions (hot spots and entry
  points; in file mode, every snippet in the per-module briefs). Extra entries
  are welcome.
- `evidence` (optional, a list of `file:line`) is written by `submit` /
  `answer_question` when you pass evidence; the renderer ignores it and
  `codemap check` leaves it alone.
