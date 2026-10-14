"""Glue between the call graph and codemap's own enrichment passes.

``apply`` folds ``typeflow``'s type-resolved calls into a call graph in place
and returns what changed; ``apply_from_db`` does the same for a graph built
straight from the index (``calls.build_graph``: the CLI and ``codemap mcp``);
``native`` is the JS/TS facts pass (``jsfacts``); ``describe`` is the line
``codemap status`` prints. None of them raises for a problem in a source file:
codemap carries on with its own name-based graph.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from typing import Callable, Iterable

from ..config import Config
from ..impact import AMBIGUOUS, EXTRACTED, INFERRED
from . import typeflow

OFF_REASON = 'off (engine.types = "off")'

# One analysis per (repo, commit, index state): `codemap mcp` builds the explore
# model and its own call graph from the same index, and both want this result.
_cache: dict[tuple, typeflow.Result] = {}


def _path_of(key: str) -> str:
    return key.split("::", 1)[0]


def file_bytes(cfg: Config, sha: str, path: str) -> bytes | None:
    """A file as codemap indexed it at ``sha`` (the worktree pseudo-commit reads disk)."""
    from .. import gitio
    from ..indexer import WORKTREE_SHA

    if sha == WORKTREE_SHA:
        from ..discovery import read_worktree_bytes

        return read_worktree_bytes(cfg.root, path)
    return gitio.show_bytes(cfg.root, sha, path)


def _cache_key(cfg: Config, sha: str) -> tuple:
    try:
        st = cfg.db_path.stat()
        state = (st.st_mtime_ns, st.st_size)
    except OSError:
        state = None
    return (str(cfg.root), sha, state)


def analyze(cfg: Config, sha: str, file_rows: Iterable, sym_rows: Iterable,
            read: Callable[[str], bytes | None], aliases: list | None = None) -> typeflow.Result:
    key = _cache_key(cfg, sha)
    hit = _cache.get(key)
    if hit is not None:
        return hit
    res = typeflow.analyze(file_rows, sym_rows, read, aliases)
    _cache.clear()
    _cache[key] = res
    return res


def merge(g, sites: Iterable[typeflow.Site], name_of: dict[str, str]) -> dict[str, int]:
    """Fold call-site outcomes into ``g`` (a networkx DiGraph whose edges carry
    ``confidence``). Returns counts of what changed.

    * a resolved link codemap did not have is added, tagged ``via="types"``;
    * an AMBIGUOUS codemap edge a resolution confirms is upgraded and tagged;
    * when *every* call to a name from one symbol is decided (resolved, or a
      builtin / third-party target), codemap's AMBIGUOUS edges from it to the
      other same-named symbols are dropped: they were guesses this settles;
    * a confident codemap edge is never touched.
    """
    added = upgraded = dropped = 0
    groups: dict[tuple[str, str], list[typeflow.Site]] = defaultdict(list)
    for s in sites:
        if s.src in g:
            groups[(s.src, s.name)].append(s)

    for (src, name), ss in sorted(groups.items()):
        allowed: set[str] = set()
        for s in ss:
            if s.outcome != typeflow.RESOLVED:
                continue
            for dst in s.targets:
                if dst not in g or dst == src:
                    continue
                allowed.add(dst)
                confidence = EXTRACTED if _path_of(dst) == _path_of(src) else INFERRED
                note = {"via": "types", "type_strategy": s.strategy, "line": s.line}
                if g.has_edge(src, dst):
                    if g.edges[src, dst].get("confidence") == AMBIGUOUS:
                        g.edges[src, dst].update(confidence=confidence, **note)
                        upgraded += 1
                else:
                    g.add_edge(src, dst, confidence=confidence, **note)
                    added += 1
        if any(s.outcome == typeflow.UNKNOWN for s in ss):
            continue
        for other in list(g.successors(src)):
            data = g.edges[src, other]
            if (other not in allowed and name_of.get(other) == name
                    and data.get("confidence") == AMBIGUOUS and not data.get("via")):
                g.remove_edge(src, other)
                dropped += 1
    return {"added": added, "upgraded": upgraded, "dropped": dropped}


def apply(
    cfg: Config,
    g,
    sha: str,
    file_rows: Iterable,
    sym_rows: Iterable,
    read: Callable[[str], bytes | None],
    aliases: list | None = None,
) -> dict:
    """Resolve calls by type and fold them into ``g``. ``file_rows`` need
    ``path`` and ``lang``; ``sym_rows`` need ``key``, ``name``, ``file``,
    ``start_line`` and ``end_line``; ``read(path)`` is the file as indexed.
    Returns ``{"used": False, "reason"}`` or ``{"used": True, ...}`` with counts."""
    if cfg.engine.types == "off":
        return {"used": False, "reason": OFF_REASON}
    rows = list(sym_rows)
    try:
        res = analyze(cfg, sha, file_rows, rows, read, aliases)
    except (OSError, ValueError, RecursionError) as e:
        return {"used": False, "reason": f"type resolution failed ({type(e).__name__}: {e})"}
    if not res.files:
        return {"used": False, "reason": "no Python / JavaScript / TypeScript files"}
    changes = merge(g, res.sites, {r["key"]: r["name"] for r in rows})
    counts = {o: 0 for o in (typeflow.RESOLVED, typeflow.EXTERNAL, typeflow.UNKNOWN)}
    for s in res.sites:
        counts[s.outcome] += 1
    return {"used": True, "files": res.files, **changes, "sites": counts, "failed": res.failed}


def apply_from_db(conn: sqlite3.Connection, cfg: Config, sha: str, g, aliases: list | None = None) -> dict:
    """``apply`` for a graph built straight from the index at ``sha``."""
    if cfg.engine.types == "off":
        return {"used": False, "reason": OFF_REASON}
    file_rows = conn.execute(
        "SELECT f.path, f.lang FROM file_versions fv JOIN files f ON f.id = fv.file_id "
        "WHERE fv.commit_sha = ? AND fv.status != 'deleted' ORDER BY f.path",
        (sha,),
    ).fetchall()
    sym_rows = conn.execute(
        "SELECT s.key, s.name, f.path AS file, sv.start_line, sv.end_line "
        "FROM symbol_versions sv JOIN symbols s ON s.id = sv.symbol_id "
        "JOIN files f ON f.id = s.file_id WHERE sv.commit_sha = ?",
        (sha,),
    ).fetchall()
    return apply(cfg, g, sha, file_rows, sym_rows, lambda p: file_bytes(cfg, sha, p), aliases)


def native(
    cfg: Config,
    file_rows: Iterable,
    sym_rows: Iterable,
    read: Callable[[str], bytes | None],
    entry_points: dict[str, list[tuple[str, str]]] | None = None,
) -> dict:
    """codemap's own request/route facts: web requests (JS/TS through
    ``jsfacts.py``, Python through ``pyfacts.py``) paired with the routes that
    serve them (Express through ``jsfacts``, Python's ``route`` entry points
    from ``entry_points``), plus JS config reads and browser storage.
    ``file_rows`` need ``path`` and ``lang``; ``sym_rows`` need ``key``,
    ``name``, ``file``, ``start_line`` and ``end_line``. Returns ``{"used":
    False, "reason"}`` or ``{"used": True, "routes", "config", "storage",
    "requests", "paired"}``."""
    if cfg.engine.native == "off":
        return {"used": False, "reason": 'off (engine.native = "off")'}
    from . import jsfacts, pyfacts

    sym_rows = list(sym_rows)
    facts = []
    for r in file_rows:
        if r["lang"] in jsfacts.JS_LANGS:
            blob = read(r["path"])
            if blob:
                facts.append(jsfacts.extract(r["path"], blob, r["lang"]))
        elif r["lang"] == "python":
            blob = read(r["path"])
            ff = pyfacts.extract(r["path"], blob) if blob else None
            if ff is not None and ff.requests:
                facts.append(ff)
    served = pyfacts.served_from_entry_points(entry_points or {}, {s["key"] for s in sym_rows})
    if not facts and not served:
        return {"used": False, "reason": "no web requests or routes found"}
    spans = [jsfacts.SymSpan(s["key"], s["name"], s["file"], s["start_line"], s["end_line"] or s["start_line"])
             for s in sym_rows]
    linked = jsfacts.link(facts, spans, served)
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
    if cfg.engine.types == "off":
        return f"name-based only — type resolution {OFF_REASON}"
    return ("name-based + type resolution (Python, JavaScript, TypeScript: receiver types, "
            "imports, inheritance; applied by `codemap explore` and `codemap mcp`)")
