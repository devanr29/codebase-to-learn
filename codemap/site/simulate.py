"""Assemble ``data.sim`` -- the Simulate tab's authored (Lane 2) and recorded
(Lane 3) scenarios, resolved against the current graph.

Lane 1 (derived from the call graph) is deliberately **not** built here: like
every other Map-tab view (``layerCake``/``runTrace``/``massMap`` in
``explore.js``), it is computed entirely client-side from ``data.nodes`` /
``data.edges`` -- the payload already carries everything it needs (including
the call-site ``line`` on each edge, see ``model.py``), so it stays available
even when a repo has neither ``scenarios.json`` nor a recorded trace, and it
never goes stale relative to whatever ``max_symbols`` budget cut the graph.

This module only handles the two lanes that come from outside the graph:
authored JSON (``scenarios.py``) and recorded runs (``tracer.py``). Both are
optional and fail soft, same as ``learn.py`` / ``explain.py``.
"""

from __future__ import annotations

from . import scenarios as _scenarios
from .. import tracer as _tracer
from ..config import Config


def build(cfg: Config, key_to_i: dict[str, int]) -> dict:
    """``{"scenarios": [...]}`` with every ``node``/``root``/``from`` symbol
    key resolved to a graph node index. A scenario referencing a key that
    doesn't resolve to a *currently kept* node (pruned by the ``max_symbols``
    budget, renamed, deleted) drops just that step; a scenario left with
    fewer than 2 steps is dropped entirely rather than shown broken."""
    raw = _scenarios.load(cfg) + _tracer.load_all(cfg)
    out: list[dict] = []
    for sc in raw:
        resolved = _resolve_scenario(sc, key_to_i)
        if resolved is not None:
            out.append(resolved)
    return {"scenarios": out}


def _resolve_scenario(sc: dict, key_to_i: dict[str, int]) -> dict | None:
    steps = []
    for st in sc.get("steps", ()):
        i = key_to_i.get(st.get("node") if isinstance(st, dict) else None)
        if i is None:
            continue
        rs = dict(st)
        rs["node"] = i
        if isinstance(rs.get("from"), str):
            fi = key_to_i.get(rs["from"])
            rs["from"] = fi if fi is not None else None
        steps.append(rs)
    if len(steps) < 2:
        return None
    out = {
        "id": sc["id"], "title": sc["title"], "trigger": sc.get("trigger") or {"surface": "terminal", "text": ""},
        "source": sc.get("source", "authored"), "steps": steps,
    }
    root_key = sc.get("root")
    if isinstance(root_key, str) and root_key in key_to_i:
        out["root"] = key_to_i[root_key]
    if sc.get("truncated"):
        out["truncated"] = True
    if sc.get("crashed"):
        out["crashed"] = sc["crashed"]
    return out
