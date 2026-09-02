"""Load and validate ``.codemap/explanations.json`` — per-symbol plain-English
blurbs shown in the Graph tab's inspector.

Written by the ``codebase-to-course`` skill (see repo-root ``SKILL.md``), never
by codemap itself. Absent or malformed content is **not** an error: the
inspector simply shows no blurb. Companion to ``learn.py`` (the Learn-tab
course); both fall back silently.

Shape (see ``references/explanations-schema.md``):

    {
      "symbols": {
        "codemap/report.py::render_commit": {
          "what": "One sentence: what this function does, in plain English.",
          "why":  "Optional: why it matters / when a vibe coder would touch it.",
          "terms": {"blast radius": "how many callers an edit could break"}
        },
        ...
      }
    }

Keys must match ``data.nodes[].key`` exactly; unknown keys are ignored by the
renderer (they simply never attach to a node).
"""

from __future__ import annotations

import json

from ..config import Config

EXPLAIN_FILE = "explanations.json"

_MAX_WHAT = 400
_MAX_WHY = 400


def load(cfg: Config) -> dict:
    """Return ``{symbol key: {"what": str, "why"?: str, "terms"?: {str: str}}}``.
    Always a dict — empty when the file is absent, unreadable, or malformed."""
    path = cfg.codemap_dir / EXPLAIN_FILE
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    syms = data.get("symbols") if isinstance(data, dict) else None
    if not isinstance(syms, dict):
        return {}

    out: dict[str, dict] = {}
    for key, v in syms.items():
        if not isinstance(key, str) or not isinstance(v, dict):
            continue
        what = v.get("what")
        if not isinstance(what, str) or not what.strip():
            continue
        entry: dict = {"what": what.strip()[:_MAX_WHAT]}
        why = v.get("why")
        if isinstance(why, str) and why.strip():
            entry["why"] = why.strip()[:_MAX_WHY]
        terms = v.get("terms")
        if isinstance(terms, dict):
            clean = {
                t: str(d).strip()
                for t, d in terms.items()
                if isinstance(t, str) and t.strip() and str(d).strip()
            }
            if clean:
                entry["terms"] = clean
        out[key] = entry
    return out
