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
          "group": "A request comes in",           # Simulate-rail section header
          "order": 20,                              # position in the app's workflow
          "summary": "what you'll learn watching this",
          "steps": [                               # OPTIONAL — omit to let
            {"node": "codemap/cli.py::cmd_explore", "t": "call",   # explore.js
             "user": "Nothing on screen yet.", "code": "cmd_explore starts."}  # derive
          ]                                         # the steps from `root`
        }
      ]
    }

``root``/``node`` reference symbol *keys*; ``explore.js`` resolves them to
graph node indices at render time (so this file never has to know the
node-index numbering `model.build()` assigns).

A scenario needs *either* a non-empty ``steps`` list *or* a ``root`` — a
"curriculum" entry can carry just ``root`` (+ ``group``/``order``/``summary``)
and let the renderer's Lane-1 ``deriveSteps`` fill in the call tree. This keeps
the skill's authoring cost flat: one pass to write the ordered, grouped index,
hand-authored ``steps`` only for the few hero scenarios a Learn screen links to.
"""

from __future__ import annotations

import json

from ..config import Config

SCENARIOS_FILE = "scenarios.json"

_VALID_SURFACES = {"terminal", "browser", "api", "file", "ui", "job", "db"}
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
        clean_steps = (
            [s for s in (_clean_step(st) for st in steps) if s is not None]
            if isinstance(steps, list) else []
        )
        root = sc.get("root")
        has_root = isinstance(root, str) and bool(root.strip())
        # keepable with usable steps OR a root the renderer can derive from
        if not clean_steps and not has_root:
            continue
        trigger = sc.get("trigger") if isinstance(sc.get("trigger"), dict) else {}
        surface = trigger.get("surface")
        trigger_out: dict = {"text": str(trigger.get("text") or "").strip()}
        # an explicit, valid surface always wins; leaving it unset (rather than
        # defaulting to "terminal") lets the renderer's resolveSurface() infer
        # one from the scenario's own evidence instead of guessing wrong
        if surface in _VALID_SURFACES:
            trigger_out["surface"] = surface
        entry: dict = {
            "id": sid.strip(),
            "title": title.strip(),
            "trigger": trigger_out,
            "steps": clean_steps,
            "source": "authored",
        }
        if has_root:
            entry["root"] = root.strip()
        # curriculum metadata — the Simulate rail groups by `group` and orders
        # ascending by `order`; `summary` is the one-line "what you'll learn"
        group = sc.get("group")
        if isinstance(group, str) and group.strip():
            entry["group"] = group.strip()
        order = sc.get("order")
        if isinstance(order, bool):
            order = None  # bool is an int subclass — never a position
        if isinstance(order, (int, float)):
            entry["order"] = int(order)
        summary = sc.get("summary")
        if isinstance(summary, str) and summary.strip():
            entry["summary"] = summary.strip()
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
            # same rule as the scenario-level trigger: an unset/invalid surface
            # is left off rather than coerced, so the client falls back to the
            # scenario's own resolved surface instead of a wrong hardcoded one
            clean_emit: dict = {"text": text.strip()}
            if surface in _VALID_SURFACES:
                clean_emit["surface"] = surface
            # parity with a recorded (Lane 3) trace's emit shape (tracer.py's
            # _merge()) so an authored terminal scenario can mark a line as
            # stderr the same way a real run does
            stream = emit.get("stream")
            if stream in ("stdout", "stderr"):
                clean_emit["stream"] = stream
            entry["emit"] = clean_emit
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
