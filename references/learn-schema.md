# `.codemap/learn.json` schema

`codemap/site/learn.py` loads and validates this file. A missing or malformed
file is **not an error** — the Learn tab falls back to a generated Orientation.
So: invalid JSON, or `modules` not a list, or every module missing a `title` →
silently ignored. Get it right and re-run `codemap explore`.

```jsonc
{
  "title": "Understanding <project>",        // string, shown nowhere critical
  "accent": "#9184d9",                        // optional; reserved, not yet applied

  "modules": [
    {
      "id": "m1",                             // stable slug; the hash route is #/learn/m1
      "title": "How a request becomes a session",
      "summary": "One sentence under the title.",
      "metaphor": "A session is a coat-check ticket: ...",   // one line, never reused

      "screens": [
        {
          "heading": "You click Log in — then what?",
          "body": "Two or three sentences. Glossary terms in this text are\n auto-linked from the module's `glossary` map below.",

          "translation": {                    // optional — the code-to-English block
            "code": "def create_session(user):\n    token = sign(user.id)\n    return Session(token)",
            "lines": [
              "Take the user we just authenticated.",
              "Sign their id into a token nobody can forge.",
              "Hand back a Session wrapped around that token."
            ]
          },

          "callout": {                        // optional — max one per screen, two per module
            "kind": "accent",                 // accent | info | warning (only accent styled today)
            "title": "aha!",
            "text": "The token is the session. There is no server-side list to look up."
          },

          "nodes": [12, 44]                   // optional — graph node indices this screen is about;
                                              //   rendered as clickable links into the Graph tab
        }
      ],

      "quiz": [
        {
          "q": "Your teammate says login is slow. Where do you look first?",
          "options": ["The token signer", "The HTML template", "The CSS", "The favicon"],
          "answer": 0,                        // 0-based index into options
          "right": "Signing is the only CPU-bound step on the login path.",
          "wrong": "Rendering happens after the session exists — it can't be the login cost."
        }
      ],

      "glossary": {                           // term -> definition; applied to every screen `body`
        "token": "A signed string that proves who you are without a database lookup. In an AI prompt you'd say 'issue a signed token'.",
        "sign": "To attach a cryptographic signature so the value can't be tampered with."
      }
    }
  ]
}
```

## Rules the renderer assumes

- `answer` is a valid 0-based index into that question's `options`.
- `nodes` entries are integers in `[0, len(data.nodes))`; out-of-range entries
  are skipped silently.
- `translation.lines` maps 1–2 code lines each; keep `code` short (5–10 lines)
  and **verbatim** from a brief snippet.
- `glossary` keys are matched as whole words, first occurrence per screen.
- No HTML in any string — everything is inserted as text.
