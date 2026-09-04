"""Load and validate ``.codemap/libraries.json`` — authored descriptions for the
Learn tab, which is a **library / module reference**: for every external package
the code imports (and every top-level module of the repo itself) it shows what
that thing does in general and how *this* codebase uses it.

Written by the ``codebase-to-course`` skill (repo-root ``SKILL.md``), never by
codemap itself. Absent or malformed content is not an error: the Learn tab falls
back to a deterministic reference built from the import graph plus a small
bundled table of well-known-library one-liners in ``explore.js``. This loader
only ever *fills in* or *overrides* the prose for a given name.

Shape (see ``references/libraries-schema.md``):

    {
      "items": {
        "networkx": {
          "general": "Graph data structures and algorithms — build a graph, ask "
                     "it questions (shortest path, cycles, centrality).",
          "here": "impact.py builds the call graph as a DiGraph and walks it for "
                  "reachability; model.py reads cycles and SCCs off the same graph.",
          "see": ["codemap/impact.py::call_graph", "codemap/site/model.py::build"]
        },
        "codemap/site": { "general": "...", "here": "...", "see": [...] }
      }
    }

Keys are the package token as the UI shows it (``networkx``, ``tree_sitter``,
``os``) or a repo module name (``codemap/site``). ``see`` entries are
``data.nodes[].key`` strings — ``explore.js`` resolves them to Graph-tab links
and silently drops any that no longer exist.
"""

from __future__ import annotations

import json

from ..config import Config

LIBRARIES_FILE = "libraries.json"


def load(cfg: Config) -> dict | None:
    """Return ``{"items": {name: {general?, here?, see?}}}`` or ``None`` when the
    file is absent, unreadable, malformed, or leaves nothing usable. Never raises."""
    path = cfg.codemap_dir / LIBRARIES_FILE
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    raw = data.get("items") if isinstance(data, dict) else None
    if not isinstance(raw, dict):
        return None

    items: dict[str, dict] = {}
    for name, entry in raw.items():
        if not isinstance(name, str) or not name.strip() or not isinstance(entry, dict):
            continue
        clean: dict = {}
        for key in ("general", "here"):
            v = entry.get(key)
            if isinstance(v, str) and v.strip():
                clean[key] = v.strip()
        see = entry.get("see")
        if isinstance(see, list):
            keys = [s.strip() for s in see if isinstance(s, str) and s.strip()]
            if keys:
                clean["see"] = keys
        if clean:
            items[name.strip()] = clean
    return {"items": items} if items else None
