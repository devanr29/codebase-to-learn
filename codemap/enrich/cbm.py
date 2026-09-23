"""codebase-memory-mcp (CBM) as an optional call-graph engine.

CBM keeps one SQLite file per project under ``$CBM_CACHE_DIR`` (default
``~/.cache/codebase-memory-mcp``). Its documented interface is the MCP tools;
the schema is *not* promised stable, so this module is deliberately defensive:

* it only ever opens the file read-only;
* it refuses any ``PRAGMA user_version`` or column layout it was not written
  against, and says why instead of raising;
* it trusts an edge only when both files still hash the way they did when CBM
  indexed them (``file_hashes.sha256`` is a SHA-256 of the raw bytes).

Every failure comes back as a human-readable reason for ``codemap status``; a
broken or missing CBM index must never break ``codemap explore``.
"""

from __future__ import annotations

import json
import os
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

SUPPORTED_FORMATS = {1}
DEFAULT_CACHE_DIR = "~/.cache/codebase-memory-mcp"

# CBM's own "high" confidence band starts at 0.70 (registry.c); below it is a
# fuzzy or suffix guess, which is exactly what codemap already calls AMBIGUOUS.
MIN_SCORE = 0.70
# import_map (0.95) and same_module (0.90) are resolved from imports / scope
EXTRACTED_SCORE = 0.90

_CODE_LABELS = ("Function", "Method", "Class")
_EXPECTED_COLUMNS = {
    "projects": {"name", "root_path"},
    "file_hashes": {"project", "rel_path", "sha256"},
    "nodes": {"id", "project", "label", "name", "qualified_name", "file_path", "start_line", "properties"},
    "edges": {"project", "source_id", "target_id", "type", "properties"},
}


@dataclass
class Found:
    conn: sqlite3.Connection
    project: str
    db_path: Path

    def close(self) -> None:
        self.conn.close()


@dataclass(frozen=True)
class SymRef:
    """The bits of a codemap symbol needed to line a CBM node up with it."""
    key: str
    name: str
    file: str
    start: int


@dataclass(frozen=True)
class CbmEdge:
    src: str
    dst: str
    score: float
    strategy: str
    line: int | None = None


@dataclass
class RouteLink:
    url: str
    method: str
    callers: list[str] = field(default_factory=list)    # symbols that make the HTTP call
    handlers: list[str] = field(default_factory=list)   # symbols that serve it


@dataclass
class Loaded:
    edges: list[CbmEdge] = field(default_factory=list)
    routes: list[RouteLink] = field(default_factory=list)
    stale_files: int = 0
    skipped: int = 0   # edges with an end that is not a codemap symbol, or is in a stale file


# --------------------------------------------------------------------------- locating


def cache_dirs(configured: str = "") -> list[Path]:
    """Where CBM may keep its index, most specific first."""
    out: list[Path] = []
    for raw in (configured, os.environ.get("CBM_CACHE_DIR", ""), DEFAULT_CACHE_DIR):
        if raw and raw.strip():
            p = Path(raw.strip()).expanduser()
            if p not in out:
                out.append(p)
    return out


def _norm(path: str | Path) -> str:
    """A path in one comparable spelling: separators unified, symlinks and
    Windows 8.3 short names expanded, ``..`` folded, case ignored on Windows.
    CBM records the root the way it was given, so both sides go through this."""
    text = str(path).replace("\\", "/")
    try:
        text = str(Path(text).resolve())
    except (OSError, RuntimeError):
        pass
    return os.path.normcase(os.path.normpath(text))


def _open_ro(path: Path) -> sqlite3.Connection | None:
    """Read-only, never creating a file. CBM's WAL mode can defeat `mode=ro`
    when the -shm file is unwritable, so fall back to `immutable=1` as CBM's own
    query path does."""
    uri = path.resolve().as_uri()
    for flags in ("mode=ro", "immutable=1"):
        try:
            conn = sqlite3.connect(f"{uri}?{flags}", uri=True, timeout=1.0)
            conn.row_factory = sqlite3.Row
            conn.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchone()
            return conn
        except sqlite3.Error:
            continue
    return None


def locate(repo_root: Path | str, configured_dir: str = "") -> tuple[Found | None, str]:
    """The CBM index whose recorded root is ``repo_root``, or ``(None, reason)``.
    Matching on the recorded root (not on CBM's project-naming rules) keeps this
    independent of how CBM derives a project name."""
    dirs = cache_dirs(configured_dir)
    existing = [d for d in dirs if d.is_dir()]
    if not existing:
        return None, f"codebase-memory-mcp not detected (no index cache at {dirs[0]})"
    want = _norm(repo_root)
    best: tuple[float, Found] | None = None
    for d in existing:
        for db_path in sorted(d.glob("*.db")):
            conn = _open_ro(db_path)
            if conn is None:
                continue
            try:
                rows = conn.execute("SELECT name, root_path FROM projects").fetchall()
            except sqlite3.Error:
                conn.close()
                continue
            match = next((r["name"] for r in rows if _norm(r["root_path"]) == want), None)
            if match is None:
                conn.close()
                continue
            mtime = db_path.stat().st_mtime
            if best is None or mtime > best[0]:
                if best is not None:
                    best[1].close()
                best = (mtime, Found(conn, match, db_path))
            else:
                conn.close()
    if best is None:
        return None, (
            "codebase-memory-mcp has no index for this repo "
            f"(run: codebase-memory-mcp cli index_repository --repo-path \"{Path(repo_root)}\")"
        )
    return best[1], ""


def guard(conn: sqlite3.Connection) -> str | None:
    """``None`` when this index has the format and columns codemap reads, else why not."""
    try:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version not in SUPPORTED_FORMATS:
            return (f"codebase-memory index format v{version} is not one codemap understands "
                    f"(supports v{', v'.join(map(str, sorted(SUPPORTED_FORMATS)))}); "
                    "update codemap, or set engine.codebase_memory = \"off\"")
        for table, want in _EXPECTED_COLUMNS.items():
            have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
            if not want <= have:
                return f"codebase-memory index is missing {table}.{', '.join(sorted(want - have))}"
    except sqlite3.Error as e:
        return f"codebase-memory index could not be read ({e})"
    return None


# --------------------------------------------------------------------------- reading


def _props(raw: str | None) -> dict:
    try:
        v = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return v if isinstance(v, dict) else {}


def _mapper(syms: Iterable[SymRef]) -> Callable[[str, int, str], str | None]:
    by_line: dict[tuple[str, int], str] = {}
    by_name: dict[tuple[str, str], list[str]] = defaultdict(list)
    for s in syms:
        by_line[(s.file, s.start)] = s.key
        by_name[(s.file, s.name)].append(s.key)

    def to_key(file: str, line: int, name: str) -> str | None:
        hit = by_line.get((file, line))
        if hit:
            return hit
        same = by_name.get((file, name), ())
        return same[0] if len(same) == 1 else None

    return to_key


def load_edges(
    conn: sqlite3.Connection,
    project: str,
    syms: Iterable[SymRef],
    current_hash: Callable[[str], str | None],
) -> Loaded:
    """CBM's resolved calls and route links, expressed in codemap symbol keys.

    ``current_hash(path)`` is the SHA-256 (hex) of the file as codemap indexed it,
    or ``None``. An edge whose either file differs from what CBM hashed is dropped:
    the two engines would be describing different code."""
    syms = list(syms)
    to_key = _mapper(syms)
    hashes = {r["rel_path"]: r["sha256"] for r in
              conn.execute("SELECT rel_path, sha256 FROM file_hashes WHERE project = ?", (project,))}
    fresh: dict[str, bool] = {}

    def is_fresh(path: str) -> bool:
        if path not in fresh:
            theirs, ours = hashes.get(path), current_hash(path)
            fresh[path] = theirs is not None and ours is not None and theirs.lower() == ours.lower()
        return fresh[path]

    out = Loaded()
    stale: set[str] = set()
    marks = ",".join("?" * len(_CODE_LABELS))
    rows = conn.execute(
        "SELECT e.type AS etype, e.properties AS eprops, "
        "s.label AS slabel, s.name AS sname, s.file_path AS sfile, s.start_line AS sline, "
        "t.label AS tlabel, t.name AS tname, t.file_path AS tfile, t.start_line AS tline, "
        "t.properties AS tprops "
        "FROM edges e JOIN nodes s ON s.id = e.source_id JOIN nodes t ON t.id = e.target_id "
        "WHERE e.project = ? AND e.type IN ('CALLS', 'HTTP_CALLS', 'HANDLES') AND s.label IN (" + marks + ")",
        (project, *_CODE_LABELS),
    )
    routes: dict[tuple[str, str], RouteLink] = {}
    seen: set[tuple[str, str]] = set()

    def endpoint(file: str, line: int, name: str) -> str | None:
        if not file:
            return None
        if not is_fresh(file):
            stale.add(file)
            return None
        return to_key(file, line or 0, name)

    for r in rows:
        src = endpoint(r["sfile"], r["sline"], r["sname"])
        if src is None:
            out.skipped += 1
            continue
        ep = _props(r["eprops"])
        if r["etype"] == "CALLS":
            if r["tlabel"] not in _CODE_LABELS:
                continue
            dst = endpoint(r["tfile"], r["tline"], r["tname"])
            score = ep.get("confidence")
            if dst is None or dst == src or not isinstance(score, (int, float)) or score < MIN_SCORE:
                out.skipped += dst is None
                continue
            if (src, dst) in seen:
                continue
            seen.add((src, dst))
            line = ep.get("line")
            out.edges.append(CbmEdge(src, dst, float(score), str(ep.get("strategy") or "cbm"),
                                     line if isinstance(line, int) else None))
        elif r["tlabel"] == "Route":
            method = str(_props(r["tprops"]).get("method") or ep.get("method") or "ANY").upper()
            link = routes.setdefault((method, r["tname"]), RouteLink(url=r["tname"], method=method))
            side = link.callers if r["etype"] == "HTTP_CALLS" else link.handlers
            if src not in side:
                side.append(src)
    out.stale_files = len(stale)
    out.routes = [rl for _, rl in sorted(routes.items()) if rl.callers or rl.handlers]
    return out


# --------------------------------------------------------------------------- merging


def merge_into_graph(g, edges: list[CbmEdge], name_of: dict[str, str], ambiguous: str) -> dict[str, int]:
    """Fold CBM's resolved calls into codemap's call graph ``g`` (a networkx
    DiGraph whose edges carry ``confidence``). Returns counts of what changed.

    * a link codemap did not have is added, tagged ``via="cbm"``;
    * an ``ambiguous`` codemap edge CBM confirms is upgraded and tagged;
    * when CBM resolves one call site to a single target, codemap's ambiguous
      edges to the *other* same-named symbols are dropped: they were guesses
      CBM has now settled;
    * a confident codemap edge is never touched.
    """
    added = upgraded = dropped = 0
    resolved: dict[tuple[str, str], list[CbmEdge]] = defaultdict(list)
    for e in edges:
        if e.src in g and e.dst in g:
            resolved[(e.src, name_of.get(e.dst, ""))].append(e)

    for (src, name), hits in sorted(resolved.items()):
        if len({h.dst for h in hits}) == 1:
            keep = hits[0].dst
            for other in list(g.successors(src)):
                if other != keep and name_of.get(other) == name and g.edges[src, other].get("confidence") == ambiguous:
                    g.remove_edge(src, other)
                    dropped += 1
        for h in hits:
            confidence = "EXTRACTED" if h.score >= EXTRACTED_SCORE else "INFERRED"
            note = {"via": "cbm", "cbm_score": round(h.score, 2), "cbm_strategy": h.strategy}
            if h.line is not None:
                note["line"] = h.line
            if g.has_edge(h.src, h.dst):
                if g.edges[h.src, h.dst].get("confidence") == ambiguous:
                    g.edges[h.src, h.dst].update(confidence=confidence, **note)
                    upgraded += 1
            else:
                g.add_edge(h.src, h.dst, confidence=confidence, **note)
                added += 1
    return {"added": added, "upgraded": upgraded, "dropped": dropped}
