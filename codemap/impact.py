"""Impact analysis (spec M5).

For each changed symbol, at the N-1 graph:
  * direct callers, then transitive callers out to ``config.impact_depth``
  * which of those callers were themselves modified in this commit — this drives
    the "2 updated, 2 unchanged (worth checking)" line
  * the shortest path from an entry point, e.g.
    ``GET /report -> report_view -> build_report -> fetch``

At T1 caller lookup is name-based, so results can be over-broad; every Impact
records its ``tier`` and whether "no callers" is real or just unresolvable here.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field

import networkx as nx

from . import resolve
from .config import Config
from .languages.registry import spec_for_path

EXTRACTED, INFERRED, AMBIGUOUS = "EXTRACTED", "INFERRED", "AMBIGUOUS"

# change types whose impact is worth computing — a change to something that
# already had dependents. A brand-new symbol has no N-1 graph node, so
# symbol_added is deliberately excluded (the report just lists it).
IMPACTFUL = {
    "body_changed", "signature_changed", "symbol_removed",
    "renamed", "symbol_moved",
}
_MODIFYING = {"body_changed", "signature_changed", "renamed", "symbol_moved"}


@dataclass
class Caller:
    key: str
    qualified_name: str
    depth: int
    modified: bool


@dataclass
class Impact:
    symbol_key: str
    callers: list[Caller] = field(default_factory=list)
    entry_paths: list[str] = field(default_factory=list)
    tier: int = 1
    resolvable: bool = True

    @property
    def direct(self) -> list[Caller]:
        return [c for c in self.callers if c.depth == 1]

    @property
    def transitive(self) -> list[Caller]:
        return [c for c in self.callers if c.depth > 1]

    @property
    def n_modified(self) -> int:
        return sum(1 for c in self.callers if c.modified)

    def summary(self) -> dict:
        return {
            "callers": len(self.callers),
            "direct": len(self.direct),
            "modified": self.n_modified,
            "unmodified": len(self.callers) - self.n_modified,
            "entry_path": self.entry_paths[0] if self.entry_paths else None,
            "tier": self.tier,
            "resolvable": self.resolvable,
        }


def _qualname(key: str) -> str:
    return key.split("::", 1)[1] if "::" in key else key


def _path_of(key: str) -> str:
    return key.split("::", 1)[0]


# --------------------------------------------------------------------------- graph


def _imports_by_file(conn: sqlite3.Connection, sha: str) -> dict[str, set[str]]:
    """``file path -> resolved file paths it imports``, at ``sha`` — the
    evidence a call needs to earn ``INFERRED`` rather than falling all the way
    to a global, over-broad name match (spec M15 resolution uplift)."""
    paths = {
        r["path"]
        for r in conn.execute(
            "SELECT f.path FROM file_versions fv JOIN files f ON f.id = fv.file_id "
            "WHERE fv.commit_sha = ? AND fv.status != 'deleted'",
            (sha,),
        )
    }
    pairs = [
        (r["path"], r["raw"])
        for r in conn.execute(
            "SELECT f.path AS path, i.raw AS raw FROM imports i "
            "JOIN files f ON f.id = i.file_id WHERE i.commit_sha = ?",
            (sha,),
        )
    ]
    out: dict[str, set[str]] = {}
    for ri in resolve.resolve_imports(pairs, paths):
        if ri.target:
            out.setdefault(ri.importer, set()).add(ri.target)
    return out


def call_graph(conn: sqlite3.Connection, sha: str) -> nx.DiGraph:
    """Directed graph of symbol keys; an edge ``caller -> callee`` for every
    reference, resolved in three tiers (spec M15) — each edge carries a
    ``confidence`` attribute recording which one won, but which edges exist
    is unchanged from before this tier was added: confidence is metadata, not
    a filter.

    1. **EXTRACTED** — a same-named symbol in the caller's own file. The T2
       "same-file call edge" rule; removes most T1 false positives from
       common helper names.
    2. **INFERRED** — nothing in the caller's own file, but exactly one
       same-named symbol lives in a file the caller actually imports
       (``resolve.resolve_imports``, the same machinery the explorer's file
       graph already uses).
    3. **AMBIGUOUS** — the T1 fallback: every same-named symbol repo-wide,
       whether that's one candidate with no import evidence connecting it or
       several genuinely competing ones.
    """
    g = nx.DiGraph()
    id_to_key: dict[int, str] = {}
    name_to_keys: dict[str, list[str]] = {}
    for r in conn.execute(
        "SELECT s.id, s.key, s.name FROM symbol_versions sv JOIN symbols s "
        "ON s.id = sv.symbol_id WHERE sv.commit_sha = ?",
        (sha,),
    ):
        id_to_key[r["id"]] = r["key"]
        name_to_keys.setdefault(r["name"], []).append(r["key"])
        g.add_node(r["key"])

    imports_by_file = _imports_by_file(conn, sha)

    for r in conn.execute(
        "SELECT from_symbol_id, target_name FROM refs "
        "WHERE commit_sha = ? AND from_symbol_id IS NOT NULL",
        (sha,),
    ):
        src = id_to_key.get(r["from_symbol_id"])
        if src is None:
            continue
        candidates = [k for k in name_to_keys.get(r["target_name"], ()) if k != src]
        if not candidates:
            continue

        same_file = [k for k in candidates if _path_of(k) == _path_of(src)]
        if same_file:
            for dst in same_file:
                g.add_edge(src, dst, confidence=EXTRACTED)
            continue

        imported = imports_by_file.get(_path_of(src), ())
        via_import = [k for k in candidates if _path_of(k) in imported]
        if via_import:
            confidence = INFERRED if len(via_import) == 1 else AMBIGUOUS
            for dst in via_import:
                g.add_edge(src, dst, confidence=confidence)
            continue

        for dst in candidates:
            g.add_edge(src, dst, confidence=AMBIGUOUS)
    return g


def entry_points(conn: sqlite3.Connection, sha: str) -> dict[str, list[tuple[str, str]]]:
    out: dict[str, list[tuple[str, str]]] = {}
    for r in conn.execute(
        "SELECT s.key, e.kind, e.detail FROM entry_points e "
        "JOIN symbols s ON s.id = e.symbol_id WHERE e.commit_sha = ? AND e.symbol_id IS NOT NULL",
        (sha,),
    ):
        out.setdefault(r["key"], []).append((r["kind"], r["detail"]))
    return out


# ------------------------------------------------------------------------ analyze


def analyze(
    conn: sqlite3.Connection,
    cfg: Config,
    prev_sha: str | None,
    curr_sha: str,
    changes,
) -> dict[str, Impact]:
    depth = max(1, cfg.impact_depth)
    keys = [c.symbol_key for c in changes if c.symbol_key and c.change_type in IMPACTFUL]
    if not keys:
        return {}

    modified_now = {
        c.symbol_key for c in changes if c.symbol_key and c.change_type in _MODIFYING
    }

    g_prev = call_graph(conn, prev_sha) if prev_sha else nx.DiGraph()
    g_curr = call_graph(conn, curr_sha)
    ep_prev = entry_points(conn, prev_sha) if prev_sha else {}
    ep_curr = entry_points(conn, curr_sha)

    out: dict[str, Impact] = {}
    for key in keys:
        in_prev = key in g_prev
        g = g_prev if in_prev else g_curr
        eps = ep_prev if in_prev else ep_curr
        spec = spec_for_path(_path_of(key))
        tier = spec.tier if spec else 1

        imp = Impact(symbol_key=key, tier=tier)
        if key in g:
            reach = nx.single_target_shortest_path_length(g, key, cutoff=depth)
            pairs = reach.items() if hasattr(reach, "items") else reach
            for src, dist in pairs:
                if dist == 0:
                    continue
                imp.callers.append(
                    Caller(src, _qualname(src), dist, src in modified_now)
                )
            imp.callers.sort(key=lambda c: (c.depth, c.qualified_name))
            imp.entry_paths = _entry_paths(g, eps, key)

        imp.resolvable = tier >= 2 or bool(imp.callers)
        out[key] = imp
    return out


def _entry_paths(g: nx.DiGraph, eps: dict[str, list[tuple[str, str]]], key: str) -> list[str]:
    candidates: list[tuple[int, str]] = []
    for ep_key, kinds in sorted(eps.items()):
        if ep_key not in g or not nx.has_path(g, ep_key, key):
            continue
        path = nx.shortest_path(g, ep_key, key)
        label = kinds[0][1] or kinds[0][0]
        rendered = f"{label} -> " + " -> ".join(_qualname(p) for p in path)
        candidates.append((len(path), rendered))
    candidates.sort()
    return [candidates[0][1]] if candidates else []


# ------------------------------------------------------------------- annotation


def annotate(
    conn: sqlite3.Connection, cfg: Config, prev_sha: str | None, curr_sha: str, changes
) -> dict[str, Impact]:
    """Fold an impact summary into each change's ``details_json`` so old commits
    keep the headline after their full graph is pruned."""
    impacts = analyze(conn, cfg, prev_sha, curr_sha, changes)
    for c in changes:
        imp = impacts.get(c.symbol_key or "")
        if imp is None:
            continue
        c.details["impact"] = imp.summary()
        row = conn.execute(
            "SELECT id, details_json FROM changes WHERE commit_sha = ? AND change_type = ? "
            "AND symbol_id = (SELECT id FROM symbols WHERE key = ?)",
            (curr_sha, c.change_type, c.symbol_key),
        ).fetchone()
        if row:
            payload = json.loads(row["details_json"]) if row["details_json"] else {}
            payload["impact"] = imp.summary()
            conn.execute(
                "UPDATE changes SET details_json = ? WHERE id = ?",
                (json.dumps(payload, sort_keys=True), row["id"]),
            )
    conn.commit()
    return impacts


def render_lines(imp: Impact) -> list[str]:
    """Human-readable impact lines for the breakdown / report."""
    lines: list[str] = []
    if not imp.callers:
        if imp.resolvable:
            lines.append("Impact: no callers found")
        else:
            lines.append(f"Impact: callers not resolvable at tier {imp.tier}")
    else:
        m, u = imp.n_modified, len(imp.callers) - imp.n_modified
        tail = f" — {m} updated, {u} unchanged (worth checking)" if u else f" — all {m} updated"
        lines.append(f"Impact: {len(imp.callers)} caller(s){tail}")
    if imp.entry_paths:
        lines.append(f"On path from: {imp.entry_paths[0]}")
    return lines
