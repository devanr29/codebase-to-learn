"""``.codemap/findings.json`` — facts an AI reviewer settled that static
analysis could not, each one pinned to evidence in the code.

The indexer leaves gaps it is honest about: a call matched by name alone
(``AMBIGUOUS``), a web request no route in the repo serves, a call made
through a dispatch table. ``codemap mcp`` hands those gaps to an AI model as
open questions (``codemap/query.py``); an answer that survives verification is
stored here and folded into the graph by ``model.build``:

    {"version": 1, "items": [
      {"id": "call:app/api.py::save->validate", "kind": "call",
       "from": "app/api.py::save", "to": "app/rules.py::validate",
       "note": "validate is imported from app.rules at the top of the file",
       "evidence": [{"file": "app/api.py", "line": 3, "text": "from app.rules import validate"}],
       "index_sha": "…", "at": "2026-10-13"}
    ]}

Kinds:

- ``call`` — ``from`` really calls ``to``. An existing ``AMBIGUOUS`` edge is
  upgraded to ``INFERRED``; a missing edge (dynamic dispatch) is added. Either
  way the edge is tagged ``via: "review"`` so the page can say who found it.
- ``not_call`` — an ``AMBIGUOUS`` edge that is *not* real is removed. A
  confident edge is never removed.
- ``request`` — the web request ``method url`` is served by ``handler`` (a
  symbol key), or goes to an outside service (``external``: its name).
- ``unknown`` — the reviewer looked and could not tell. Kept so the same
  question isn't asked again; changes nothing in the graph.
- ``note`` — a question settled in words alone ("called by the framework",
  "a deliberate shortcut"); changes nothing in the graph either.

Every finding carries ``evidence``: ``file`` + ``line`` + the ``text`` of that
line when the finding was written. A finding whose evidence line no longer
reads the same, or whose symbols no longer exist, is **stale**: it is not
applied, and ``codemap check`` reports it. Loading fails soft like every other
authored file.
"""

from __future__ import annotations

import json
import re
from typing import Callable

from ..config import Config
from ..impact import AMBIGUOUS, INFERRED

FINDINGS_FILE = "findings.json"
KINDS = ("call", "not_call", "request", "unknown", "note")
VIA = "review"

LineText = Callable[[str, int], "str | None"]

_WS = re.compile(r"\s+")


def _s(v: object) -> str:
    return v.strip() if isinstance(v, str) else ""


def norm(text: str) -> str:
    return _WS.sub(" ", text).strip()


def clean_evidence(raw: object) -> list[dict]:
    out = []
    for ev in raw if isinstance(raw, list) else []:
        if not isinstance(ev, dict):
            continue
        f, line = _s(ev.get("file")).replace("\\", "/"), ev.get("line")
        if not f or not isinstance(line, int) or isinstance(line, bool) or line < 1:
            continue
        item = {"file": f, "line": line}
        if _s(ev.get("text")):
            item["text"] = _s(ev["text"])
        if _s(ev.get("note")):
            item["note"] = _s(ev["note"])
        out.append(item)
    return out


def clean_item(raw: object) -> dict | None:
    """One finding with only the fields its kind uses, or ``None``."""
    if not isinstance(raw, dict) or raw.get("kind") not in KINDS:
        return None
    kind = raw["kind"]
    item: dict = {"id": _s(raw.get("id")), "kind": kind}
    if kind in ("call", "not_call"):
        frm, to = _s(raw.get("from")), _s(raw.get("to"))
        if not frm or not to or frm == to:
            return None
        item.update({"from": frm, "to": to})
        item["id"] = item["id"] or f"call:{frm}->{to}"
    elif kind == "request":
        method, url = _s(raw.get("method")).upper() or "ANY", _s(raw.get("url"))
        handler, external = _s(raw.get("handler")), _s(raw.get("external"))
        if not url or not (handler or external):
            return None
        item.update({"method": method, "url": url})
        if handler:
            item["handler"] = handler
        if external:
            item["external"] = external
        item["id"] = item["id"] or f"request:{method} {url}"
    else:  # unknown / note
        if not item["id"] or (kind == "note" and not _s(raw.get("note"))):
            return None
    for k in ("note", "index_sha", "at"):
        if _s(raw.get(k)):
            item[k] = _s(raw[k])
    item["evidence"] = clean_evidence(raw.get("evidence"))
    if kind not in ("unknown", "note") and not item["evidence"]:
        return None
    return item


def read_raw(cfg: Config) -> dict:
    """The file as a ``{"version", "items"}`` dict, empty on any problem."""
    path = cfg.codemap_dir / FINDINGS_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"version": 1, "items": []}
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        return {"version": 1, "items": []}
    return data


def load(cfg: Config) -> list[dict]:
    """Every usable finding; later entries with the same id win."""
    by_id: dict[str, dict] = {}
    for raw in read_raw(cfg)["items"]:
        item = clean_item(raw)
        if item:
            by_id[item["id"]] = item
    return list(by_id.values())


def evidence_problem(ev: dict, line_text: LineText) -> str | None:
    """``None`` when the evidence line exists and still reads as recorded."""
    text = line_text(ev["file"], ev["line"])
    if text is None:
        return f'{ev["file"]}:{ev["line"]} does not exist in the index'
    if ev.get("text") and norm(ev["text"]) not in norm(text):
        return f'{ev["file"]}:{ev["line"]} has changed since this was written'
    return None


def stale_reason(item: dict, keys: set[str], line_text: LineText) -> str | None:
    """Why ``item`` can no longer be trusted, or ``None``."""
    for k in ("from", "to", "handler"):
        if item.get(k) and item[k] not in keys:
            return f'"{item[k]}" is no longer a symbol in the graph'
    for ev in item.get("evidence", ()):
        problem = evidence_problem(ev, line_text)
        if problem:
            return problem
    return None


def _review(item: dict) -> dict:
    out = {"evidence": [{k: ev[k] for k in ("file", "line", "note") if k in ev} for ev in item["evidence"]]}
    if item.get("note"):
        out["note"] = item["note"]
    return out


def _caller_line(item: dict) -> int | None:
    src_file = item["from"].split("::", 1)[0]
    for ev in item["evidence"]:
        if ev["file"] == src_file:
            return ev["line"]
    return None


def apply_calls(g, items: list[dict], line_text: LineText) -> dict:
    """Fold ``call`` / ``not_call`` findings into the call graph ``g`` in place.
    Returns ``{"applied": n, "stale": [{"id", "why"}]}``."""
    keys = set(g.nodes)
    applied, stale = 0, []
    for item in items:
        if item["kind"] not in ("call", "not_call"):
            continue
        why = stale_reason(item, keys, line_text)
        if why:
            stale.append({"id": item["id"], "why": why})
            continue
        src, dst = item["from"], item["to"]
        if item["kind"] == "not_call":
            if g.has_edge(src, dst) and g.edges[src, dst].get("confidence") == AMBIGUOUS:
                g.remove_edge(src, dst)
                applied += 1
            continue
        if g.has_edge(src, dst):
            ed = g.edges[src, dst]
            if ed.get("confidence") == AMBIGUOUS:
                ed["confidence"] = INFERRED
            ed.setdefault("via", VIA)
            ed["review"] = _review(item)
        else:
            g.add_edge(src, dst, confidence=INFERRED, via=VIA, review=_review(item), line=_caller_line(item))
        applied += 1
    return {"applied": applied, "stale": stale}


def apply_routes(routes: list[dict], items: list[dict], key_to_i: dict[str, int],
                 keys: set[str], line_text: LineText) -> list[dict]:
    """Fold ``request`` findings into ``model.build``'s route payload (node
    indexes): a handler the reviewer found is added, an outside service is
    named. Returns the stale ``request`` findings."""
    stale = []
    for item in items:
        if item["kind"] != "request":
            continue
        why = stale_reason(item, keys, line_text)
        if why:
            stale.append({"id": item["id"], "why": why})
            continue
        hits = [r for r in routes if r["url"] == item["url"]
                and item["method"] in ("ANY", r["method"])] or [
                r for r in routes if r["url"] == item["url"]]
        if not hits:
            continue
        for r in hits:
            if item.get("handler") in key_to_i and key_to_i[item["handler"]] not in r["handlers"]:
                r["handlers"].append(key_to_i[item["handler"]])
            r["review"] = {**_review(item), **({"external": item["external"]} if item.get("external") else {})}
    return stale
