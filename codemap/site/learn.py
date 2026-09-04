"""Load and validate ``.codemap/learn.json`` — legacy authored course content.

**Vestigial.** The Learn tab now renders ``.codemap/libraries.json`` (a library
/ module reference — see ``libraries.py``); ``explore.js`` no longer reads
``DATA.learn``. This loader and the ``DATA.learn`` payload key are kept only so
an old ``learn.json`` on disk is still parsed without error. Nothing renders it.

Historic shape:

    {
      "title": "…",
      "accent": "#9184d9",              # optional override
      "modules": [
        {
          "id": "m1", "title": "…", "summary": "…",
          "metaphor": "…",
          "screens": [
            {"heading": "…", "body": "…",
             "translation": {"code": "…", "lines": ["…", …]},
             "callout": {"kind": "accent|info|warning", "title": "…", "text": "…"},
             "nodes": [12, 44]},          # graph node indices this screen is about
            …
          ],
          "quiz": [
            {"q": "…", "options": ["…", …], "answer": 1,
             "right": "…", "wrong": "…"}
          ],
          "glossary": {"term": "definition", …}
        }
      ]
    }
"""

from __future__ import annotations

import json

from ..config import Config

LEARN_FILE = "learn.json"


def load(cfg: Config) -> dict | None:
    path = cfg.codemap_dir / LEARN_FILE
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("modules"), list):
        return None
    mods = []
    for m in data["modules"]:
        if not isinstance(m, dict) or not m.get("title"):
            continue
        m.setdefault("id", f"m{len(mods) + 1}")
        m.setdefault("screens", [])
        m.setdefault("quiz", [])
        m.setdefault("glossary", {})
        mods.append(m)
    if not mods:
        return None
    data["modules"] = mods
    data.setdefault("title", "Course")
    return data
