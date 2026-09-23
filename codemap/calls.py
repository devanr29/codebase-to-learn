"""``codemap calls`` -- what a symbol calls, or what calls it, from the resolved
call graph.

The course-authoring skill narrates Simulate scenarios and "only X does Y"
claims; without this it can only *guess* which function calls which. Every hop
printed here is an edge ``impact.call_graph()`` really resolved, with the same
``confidence`` the Graph tab draws, so a narrated step is either in this output
or is flagged as narration.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

import networkx as nx

from . import resolve
from .config import Config
from .db import get_meta
from .impact import AMBIGUOUS, call_graph


@dataclass(frozen=True)
class Sym:
    sid: int
    key: str
    kind: str
    name: str
    qual: str
    file: str
    start: int
    end: int

    def where(self) -> str:
        return f"{self.file}:{self.start}"


@dataclass(frozen=True)
class Edge:
    src: str          # the caller
    dst: str          # the callee
    depth: int        # hops from the target (1 = direct)
    confidence: str


def graph_sha(conn: sqlite3.Connection) -> str | None:
    return get_meta(conn, "graph_head") or get_meta(conn, "last_indexed_commit")


def load_symbols(conn: sqlite3.Connection, sha: str) -> dict[str, Sym]:
    rows = conn.execute(
        "SELECT s.id AS sid, s.key, s.kind, s.name, s.qualified_name AS qual, f.path AS file, "
        "sv.start_line AS start, sv.end_line AS end "
        "FROM symbol_versions sv JOIN symbols s ON s.id = sv.symbol_id "
        "JOIN files f ON f.id = s.file_id WHERE sv.commit_sha = ?",
        (sha,),
    )
    return {
        r["key"]: Sym(r["sid"], r["key"], r["kind"], r["name"], r["qual"] or r["name"],
                      r["file"], r["start"], r["end"])
        for r in rows
    }


def find(symbols: dict[str, Sym], target: str) -> list[Sym]:
    """Symbols ``target`` names, most specific reading first: the exact key, a
    qualified name (``Class.method``), a key ending in that path (``cli.py::main``),
    then a bare name. Only the first reading that matches anything is returned,
    so a full key is never reported ambiguous."""
    target = target.strip().replace("\\", "/")
    readings = (
        lambda s: s.key == target,
        # a bare name is not a qualified one: `run` must also find `Job.run`
        lambda s: "." in target and s.qual == target,
        lambda s: s.key.endswith("/" + target),
        lambda s: s.name == target,
    )
    for match in readings:
        hits = sorted((s for s in symbols.values() if match(s)), key=lambda s: s.key)
        if hits:
            return hits
    return []


def walk(
    g: nx.DiGraph, root: str, *, direction: str, depth: int, guesses: bool = True
) -> tuple[list[Edge], bool]:
    """Every call edge within ``depth`` hops of ``root``, breadth first.
    ``direction`` is ``"out"`` (what it calls) or ``"in"`` (what calls it).
    Each node is expanded once, so every edge appears once and a cycle ends.
    Returns ``(edges, truncated)``; ``truncated`` means the depth cap hid more."""
    out = direction == "out"

    def neighbours(n: str) -> list[tuple[str, str]]:
        if n not in g:
            return []
        pairs = sorted(g.successors(n)) if out else sorted(g.predecessors(n))
        found = []
        for m in pairs:
            src, dst = (n, m) if out else (m, n)
            conf = g.edges[src, dst].get("confidence", AMBIGUOUS)
            if guesses or conf != AMBIGUOUS:
                found.append((m, conf))
        return found

    seen = {root}
    edges: list[Edge] = []
    frontier = [root]
    for level in range(1, depth + 1):
        nxt: list[str] = []
        for n in frontier:
            for m, conf in neighbours(n):
                edges.append(Edge(n, m, level, conf) if out else Edge(m, n, level, conf))
                if m not in seen:
                    seen.add(m)
                    nxt.append(m)
        frontier = nxt
    return edges, any(neighbours(n) for n in frontier)


def call_lines(conn: sqlite3.Connection, sha: str) -> dict[tuple[int, str], int]:
    """``(caller symbol id, callee name) -> first line of the call`` -- the same
    key ``site/model.py`` uses for an edge's ``line``."""
    return {
        (r["from_symbol_id"], r["target_name"]): r["line"]
        for r in conn.execute(
            "SELECT from_symbol_id, target_name, MIN(line) AS line FROM refs "
            "WHERE commit_sha = ? AND from_symbol_id IS NOT NULL "
            "GROUP BY from_symbol_id, target_name",
            (sha,),
        )
    }


def edge_lines(
    edges: list[Edge], symbols: dict[str, Sym], raw: dict[tuple[int, str], int]
) -> dict[tuple[str, str], int | None]:
    """The call-site line, in the caller, of each edge (``None`` when the ref
    was resolved through an import alias and so isn't under the callee's name)."""
    return {(e.src, e.dst): raw.get((symbols[e.src].sid, symbols[e.dst].name)) for e in edges}


def build_graph(conn: sqlite3.Connection, cfg: Config, sha: str) -> nx.DiGraph:
    return call_graph(conn, sha, aliases=resolve.load_ts_aliases(cfg.root, sha))


# --------------------------------------------------------------------------- render


def as_dict(
    symbols: dict[str, Sym], root: Sym, edges: list[Edge], truncated: bool, *, direction: str,
    lines: dict[tuple[str, str], int | None],
) -> dict:
    depth_of = {root.key: 0}
    for e in edges:  # breadth-first order, so the first sighting is the shortest
        depth_of.setdefault(e.dst if direction == "out" else e.src, e.depth)
    return {
        "nodes": [
            {"key": k, "kind": symbols[k].kind, "file": symbols[k].file,
             "line": [symbols[k].start, symbols[k].end], "depth": d}
            for k, d in sorted(depth_of.items(), key=lambda kv: (kv[1], kv[0]))
        ],
        "edges": [
            {"from": e.src, "to": e.dst, "confidence": e.confidence, "depth": e.depth,
             "call_line": lines.get((e.src, e.dst))}
            for e in edges
        ],
        "truncated": truncated,
    }


def render_tree(
    symbols: dict[str, Sym], root: Sym, edges: list[Edge], *, direction: str,
    lines: dict[tuple[str, str], int | None],
) -> list[str]:
    """Indented tree; a node already shown above is marked ``(*)`` and not
    expanded again. Every edge in ``edges`` is printed exactly once."""
    out = direction == "out"
    arrow = "->" if out else "<-"
    children: dict[str, list[Edge]] = {}
    for e in edges:
        children.setdefault(e.src if out else e.dst, []).append(e)

    text = [f"{root.key}   {root.where()}"]
    shown = {root.key}

    def visit(key: str, indent: int) -> None:
        for e in children.get(key, ()):
            child = e.dst if out else e.src
            s = symbols[child]
            ln = lines.get((e.src, e.dst))
            site = f"   call at {symbols[e.src].file}:{ln}" if ln else ""
            repeat = child in shown
            text.append(
                f"{'  ' * indent}{arrow} {child}   {s.where()}   {e.confidence}{site}"
                + ("   (*)" if repeat else "")
            )
            if not repeat:
                shown.add(child)
                visit(child, indent + 1)

    visit(root.key, 1)
    return text
