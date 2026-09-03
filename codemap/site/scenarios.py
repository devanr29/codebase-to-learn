"""Load and validate ``.codemap/scenarios.json`` — authored Simulate-tab
narration (Lane 2 of the Simulate tab; see ``SKILL.md`` / the
``codebase-to-course`` skill).

Written by the course-authoring skill, never by codemap itself. Absent or
malformed content is not an error: the Simulate tab falls back to a
derived-from-the-call-graph scenario computed entirely client-side in
``explore.js`` (Lane 1), the same way ``learn.py`` falls back to a
graph-derived Orientation module. This loader only ever *overrides* or *adds*
scenarios — a malformed file never blocks the derived lane.

Shape (see ``references/scenarios-schema.md``):

    {
      "scenarios": [
        {
          "id": "explore-run",
          "title": "Running `codemap explore`",
          "trigger": {"surface": "terminal", "text": "codemap explore"},
          "root": "codemap/cli.py::cmd_explore",   # a data.nodes[].key
          "steps": [
            {"node": "codemap/cli.py::cmd_explore", "t": "call",
             "user": "Nothing on screen yet.", "code": "cmd_explore starts."}
          ]
        }
      ]
    }

``root``/``node`` reference symbol *keys*; ``explore.js`` resolves them to
graph node indices at render time (so this file never has to know the
node-index numbering `model.build()` assigns).
"""

from __future__ import annotations

import json

from ..config import Config

SCENARIOS_FILE = "scenarios.json"

_VALID_SURFACES = {"terminal", "browser", "api", "file"}
_VALID_STEP_TYPES = {"call", "return", "emit", "note", "branch"}


def load(cfg: Config) -> list[dict]:
    """Return a list of authored scenario dicts (Lane 2). Always a list — empty
    when the file is absent, unreadable, or malformed. Never raises."""
    path = cfg.codemap_dir / SCENARIOS_FILE
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    raw = data.get("scenarios") if isinstance(data, dict) else None
    if not isinstance(raw, list):
        return []

    out: list[dict] = []
    for sc in raw:
        if not isinstance(sc, dict):
            continue
        sid, title = sc.get("id"), sc.get("title")
        if not isinstance(sid, str) or not sid.strip() or not isinstance(title, str) or not title.strip():
            continue
        steps = sc.get("steps")
        if not isinstance(steps, list) or not steps:
            continue
        clean_steps = [s for s in (_clean_step(st) for st in steps) if s is not None]
        if not clean_steps:
            continue
        trigger = sc.get("trigger") if isinstance(sc.get("trigger"), dict) else {}
        surface = trigger.get("surface")
        entry: dict = {
            "id": sid.strip(),
            "title": title.strip(),
            "trigger": {
                "surface": surface if surface in _VALID_SURFACES else "terminal",
                "text": str(trigger.get("text") or "").strip(),
            },
            "steps": clean_steps,
            "source": "authored",
        }
        root = sc.get("root")
        if isinstance(root, str) and root.strip():
            entry["root"] = root.strip()
        out.append(entry)
    return out


def _clean_step(st: object) -> dict | None:
    if not isinstance(st, dict):
        return None
    node = st.get("node")
    if not isinstance(node, str) or not node.strip():
        return None
    t = st.get("t")
    entry: dict = {
        "node": node.strip(),
        "t": t if t in _VALID_STEP_TYPES else "call",
    }
    for key in ("user", "code"):
        v = st.get(key)
        if isinstance(v, str) and v.strip():
            entry[key] = v.strip()
    emit = st.get("emit")
    if isinstance(emit, dict):
        surface = emit.get("surface")
        text = emit.get("text")
        if isinstance(text, str) and text.strip():
            entry["emit"] = {
                "surface": surface if surface in _VALID_SURFACES else "terminal",
                "text": text.strip(),
            }
    cond = st.get("cond")
    if isinstance(cond, dict) and isinstance(cond.get("text"), str) and cond["text"].strip():
        kind = cond.get("kind")
        entry["cond"] = {
            "kind": kind if kind in ("if", "for", "while", "try") else "if",
            "text": cond["text"].strip(),
        }
    frm = st.get("from")
    if isinstance(frm, str) and frm.strip():
        entry["from"] = frm.strip()
    return entry
