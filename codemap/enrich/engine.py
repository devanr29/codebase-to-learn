"""Glue between ``site/model.py`` and the optional codebase-memory index.

``apply`` mutates codemap's call graph in place and returns what happened;
``describe`` is the one-line answer ``codemap status`` prints. Neither ever
raises for a problem in the other engine's files: the reason comes back as
text and codemap carries on with its own graph.
"""

from __future__ import annotations

import hashlib
import sqlite3
from typing import Callable, Iterable

from ..config import Config
from ..impact import AMBIGUOUS
from . import cbm

OFF_REASON = 'off (engine.codebase_memory = "off")'


def open_index(cfg: Config) -> tuple[cbm.Found | None, str]:
    """The usable CBM index for this repo, or ``(None, why not)``."""
    if cfg.engine.codebase_memory == "off":
        return None, OFF_REASON
    found, why = cbm.locate(cfg.root, cfg.engine.cache_dir)
    if found is None:
        return None, why
    problem = cbm.guard(found.conn)
    if problem:
        found.close()
        return None, problem
    return found, ""


def apply(
    cfg: Config,
    g,
    sym_rows: Iterable,
    file_bytes: Callable[[str], bytes | None],
) -> dict:
    """Fold CBM's resolved calls into ``g``. ``sym_rows`` need ``key``, ``name``,
    ``file`` and ``start_line``; ``file_bytes(path)`` is the file as codemap
    indexed it. Returns ``{"used": False, "reason"}`` or ``{"used": True, ...}``
    with the counts and a ``routes`` list of :class:`cbm.RouteLink`."""
    found, why = open_index(cfg)
    if found is None:
        return {"used": False, "reason": why}
    try:
        rows = list(sym_rows)
        syms = [cbm.SymRef(r["key"], r["name"], r["file"], r["start_line"]) for r in rows]

        def current_hash(path: str) -> str | None:
            blob = file_bytes(path)
            return None if blob is None else hashlib.sha256(blob).hexdigest()

        loaded = cbm.load_edges(found.conn, found.project, syms, current_hash)
        changes = cbm.merge_into_graph(g, loaded.edges, {r["key"]: r["name"] for r in rows}, AMBIGUOUS)
        return {
            "used": True,
            "project": found.project,
            **changes,
            "stale_files": loaded.stale_files,
            "skipped": loaded.skipped,
            "routes": loaded.routes,
        }
    except (sqlite3.Error, OSError, KeyError, TypeError, ValueError) as e:
        # a third-party file in any state must never break `codemap explore`
        return {"used": False, "reason": f"codebase-memory index could not be used ({type(e).__name__}: {e})"}
    finally:
        found.close()


def native(
    cfg: Config,
    file_rows: Iterable,
    sym_rows: Iterable,
    file_bytes: Callable[[str], bytes | None],
) -> dict:
    """codemap's own JS/TS facts (``jsfacts.py``): web requests paired with the
    routes that serve them, config reads, browser storage. ``file_rows`` need
    ``path`` and ``lang``; ``sym_rows`` need ``key``, ``name``, ``file``,
    ``start_line`` and ``end_line``. Returns ``{"used": False, "reason"}`` or
    ``{"used": True, "routes", "config", "storage", "requests", "paired"}``."""
    if cfg.engine.native == "off":
        return {"used": False, "reason": 'off (engine.native = "off")'}
    from . import jsfacts

    facts = []
    for r in file_rows:
        if r["lang"] not in jsfacts.JS_LANGS:
            continue
        blob = file_bytes(r["path"])
        if blob:
            facts.append(jsfacts.extract(r["path"], blob, r["lang"]))
    if not facts:
        return {"used": False, "reason": "no JavaScript/TypeScript files"}
    spans = [jsfacts.SymSpan(s["key"], s["name"], s["file"], s["start_line"], s["end_line"] or s["start_line"])
             for s in sym_rows]
    linked = jsfacts.link(facts, spans)
    return {
        "used": True,
        "routes": linked.routes,
        "config": linked.config,
        "storage": linked.storage,
        "requests": linked.requests,
        "paired": linked.paired,
        "server_routes": linked.server_routes,
    }


def describe(cfg: Config) -> str:
    """One line for ``codemap status``."""
    found, why = open_index(cfg)
    if found is None:
        return f"codemap only — {why}"
    try:
        n = found.conn.execute(
            "SELECT COUNT(*) FROM edges WHERE project = ? AND type = 'CALLS'", (found.project,)
        ).fetchone()[0]
        return (f"codemap + codebase-memory (project {found.project}, {n} call links indexed; "
                "extra links are merged in by `codemap explore`)")
    except sqlite3.Error as e:
        return f"codemap only — codebase-memory index could not be read ({e})"
    finally:
        found.close()
