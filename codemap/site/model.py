"""DB -> one JSON-serializable dict describing the whole indexed graph.

Pure read path. Reuses ``impact.call_graph`` / ``impact.entry_points`` for the
call edges, ``resolve.resolve_imports`` for file->file import edges, and
``report`` / ``semdiff`` helpers for the timeline so the HTML tells the same
story as ``codemap explain``. Deterministic apart from ``built_at``.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import networkx as nx

from .. import __version__, gitio, intent, report, resolve, semdiff
from .. import progress as _progress
from ..config import Config
from ..db import get_meta
from ..impact import call_graph
from ..impact import entry_points as _entry_points
from ..indexer import WORKTREE_SHA


def _short(sha: str | None) -> str:
    if not sha:
        return ""
    return sha if sha == WORKTREE_SHA else sha[:7]


def _module_of(path: str) -> str:
    parts = path.split("/")
    return parts[0] if len(parts) > 1 else "(root)"


def _path_of(key: str) -> str:
    return key.split("::", 1)[0]


def _file_source(cfg: Config, sha: str, path: str) -> list[str] | None:
    if sha == WORKTREE_SHA:
        from ..discovery import read_worktree_bytes

        blob = read_worktree_bytes(cfg.root, path)
    else:
        blob = gitio.show_bytes(cfg.root, sha, path)
    if blob is None:
        return None
    return blob.decode("utf-8", "replace").splitlines()


# --------------------------------------------------------------------------- build


def build(
    conn: sqlite3.Connection,
    cfg: Config,
    *,
    max_symbols: int = 1500,
    max_snippet_lines: int = 40,
    progress: _progress.Reporter | None = None,
) -> dict:
    progress = progress or _progress.NULL
    # `graph_head` points at the live worktree graph whenever a normal scan
    # has run (spec M15); it falls back to `last_indexed_commit` for a graph
    # built by bounding `scan(until=...)` to a specific historical commit.
    sha = get_meta(conn, "graph_head") or get_meta(conn, "last_indexed_commit")
    if not sha:
        return {"empty": True, "generator": f"codemap {__version__}"}

    from . import explain as _explain

    explanations = _explain.load(cfg)  # {symbol key: {"what": ..., ...}} — may be {}

    # -- commits / staleness ------------------------------------------------
    # `sha` is the graph's base commit for a diff — but when it's the
    # worktree pseudo-commit, that base is never HEAD itself: it's whatever
    # HEAD was the last time `codemap scan` ran (stored as its parent_sha by
    # _index_worktree). Comparing worktree's OWN sha to head would always
    # read "0 behind" and permanently hide the stale-banner, even though the
    # graph can be many commits behind the real HEAD until rescanned.
    head = None
    behind = 0
    try:
        head = gitio.head(cfg.root)
        base = sha
        if sha == WORKTREE_SHA:
            row = conn.execute(
                "SELECT parent_sha FROM commits WHERE sha = ?", (sha,)
            ).fetchone()
            base = row["parent_sha"] if row else None
        if head and base and base != head:
            behind = len(gitio.rev_list(cfg.root, base, head))
    except gitio.GitError:
        pass

    with progress.phase("graph") as _p_graph:
        # -- files ----------------------------------------------------------
        file_rows = conn.execute(
            "SELECT f.id, f.path, f.lang, f.tier, fv.loc "
            "FROM file_versions fv JOIN files f ON f.id = fv.file_id "
            "WHERE fv.commit_sha = ? AND fv.status != 'deleted' ORDER BY f.path",
            (sha,),
        ).fetchall()
        all_paths = {r["path"] for r in file_rows}
        path_to_fi = {r["path"]: i for i, r in enumerate(file_rows)}

        # -- symbols ----------------------------------------------------
        sym_rows = conn.execute(
            "SELECT s.id AS sid, s.key, s.kind, s.name, s.qualified_name AS qual, f.path AS file, "
            "sv.signature, sv.start_line, sv.end_line, sv.docstring "
            "FROM symbol_versions sv JOIN symbols s ON s.id = sv.symbol_id "
            "JOIN files f ON f.id = s.file_id WHERE sv.commit_sha = ? "
            "ORDER BY f.path, sv.start_line, s.key",
            (sha,),
        ).fetchall()

        # tsconfig/jsconfig `paths` aliases (`@/components/...`) — loaded once
        # here (not in impact.py's per-commit history pass; see resolve.py's
        # module docstring) so both the call graph and the file-import graph
        # below treat an aliased import as internal, not a phantom package.
        ts_aliases = resolve.load_ts_aliases(cfg.root, sha)
        g = call_graph(conn, sha, aliases=ts_aliases)

        # call-site line numbers: `call_graph()` matches refs by (from_symbol_id,
        # target_name) but only keeps the edge, not where it happened — Simulate
        # needs a frame's calls in the order they appear in the source, which the
        # graph alone can't give. Re-derive that same key here rather than
        # touching the shared `impact.call_graph()` hot path.
        id_by_key = {r["key"]: r["sid"] for r in sym_rows}
        name_by_key = {r["key"]: r["name"] for r in sym_rows}
        line_by_ref: dict[tuple[int, str], int] = {
            (r["from_symbol_id"], r["target_name"]): r["line"]
            for r in conn.execute(
                "SELECT from_symbol_id, target_name, MIN(line) AS line FROM refs "
                "WHERE commit_sha = ? AND from_symbol_id IS NOT NULL "
                "GROUP BY from_symbol_id, target_name",
                (sha,),
            )
        }

        change_counts: dict[str, int] = {
            r["key"]: r["n"]
            for r in conn.execute(
                "SELECT s.key, COUNT(*) AS n FROM changes c JOIN symbols s ON s.id = c.symbol_id "
                "WHERE c.severity != 'cosmetic' GROUP BY s.key"
            )
        }
        # most recent non-cosmetic change per symbol, with that commit's
        # *captured* (not inferred) intent — "someone wrote a reason down"
        # (teacher's principle 6). One row per symbol: the newest commit wins
        # because the query is already ordered newest-first, same tie-break as
        # _timeline()'s own commit ordering.
        last_change_by_key: dict[str, sqlite3.Row] = {}
        for r in conn.execute(
            "SELECT s.key AS key, c.commit_sha AS sha, co.ts AS ts, "
            "i.source AS intent_source, i.text AS intent_text "
            "FROM changes c "
            "JOIN symbols s ON s.id = c.symbol_id "
            "JOIN commits co ON co.sha = c.commit_sha "
            "LEFT JOIN intents i ON i.commit_sha = c.commit_sha "
            "WHERE c.severity != 'cosmetic' "
            "ORDER BY co.ts DESC, co.indexed_at DESC"
        ):
            last_change_by_key.setdefault(r["key"], r)
        ep_map = _entry_points(conn, sha)  # {key: [(kind, detail), ...]}
        _p_graph.set_summary(f"{len(file_rows)} files, {len(sym_rows)} symbols")

    def fan_in(k: str) -> int:
        return g.in_degree(k) if k in g else 0

    def fan_out(k: str) -> int:
        return g.out_degree(k) if k in g else 0

    # rank for the symbol budget: entry points, then churn + connectivity
    ranked = sorted(
        sym_rows,
        key=lambda r: (
            0 if r["key"] in ep_map else 1,
            -(change_counts.get(r["key"], 0) * 3 + fan_in(r["key"]) + fan_out(r["key"])),
            r["file"],
            r["start_line"],
        ),
    )
    kept = ranked[:max_symbols] if len(ranked) > max_symbols else ranked
    kept_keys = {r["key"] for r in kept}

    # node list, ordered by file then line so the tree reads naturally
    ordered = sorted(kept, key=lambda r: (r["file"], r["start_line"], r["key"]))
    key_to_i = {r["key"]: i for i, r in enumerate(ordered)}

    src_cache: dict[str, list[str] | None] = {}

    def excerpt(path: str, start: int, end: int) -> str | None:
        if end - start + 1 > max_snippet_lines:
            return None
        if path not in src_cache:
            src_cache[path] = _file_source(cfg, sha, path)
        lines = src_cache[path]
        if not lines:
            return None
        return "\n".join(lines[max(start - 1, 0) : end])

    def history_of(k: str) -> dict | None:
        n = change_counts.get(k, 0)
        if not n:
            return None
        row = last_change_by_key.get(k)
        if row is None:  # counted above but the join found no commit row — stale index
            return {"commits": n, "last_sha": None, "last_short": None, "intent_source": "inferred", "intent_text": None}
        text = (row["intent_text"] or "").strip().splitlines()[0] if row["intent_text"] else None
        return {
            "commits": n,
            "last_sha": row["sha"],
            "last_short": _short(row["sha"]),
            "intent_source": row["intent_source"] or "inferred",
            "intent_text": text,
        }

    nodes: list[dict] = []
    with progress.phase("symbols", len(ordered), unit="symbols") as p:
        for i, r in enumerate(ordered):
            k = r["key"]
            p.advance(detail=r["file"])
            nodes.append(
                {
                    "i": i,
                    "key": k,
                    "kind": r["kind"],
                    "name": r["name"],
                    "qual": r["qual"],
                    "file": r["file"],
                    "module": _module_of(r["file"]),
                    "line": [r["start_line"], r["end_line"]],
                    "sig": r["signature"] or r["name"],
                    "doc": (r["docstring"] or None),
                    "fan_in": fan_in(k),
                    "fan_out": fan_out(k),
                    "churn": change_counts.get(k, 0),
                    "history": history_of(k),
                    "entry": [f"{kind}:{detail}" for kind, detail in ep_map.get(k, [])],
                    "excerpt": excerpt(r["file"], r["start_line"], r["end_line"]),
                    "explain": explanations.get(k) or None,
                }
            )

    # -- call edges (index pairs; tier 2 = same-file, tier 1 = name-based;
    #    confidence is the finer-grained M15 signal — EXTRACTED/INFERRED/
    #    AMBIGUOUS — kept alongside tier/namebased rather than replacing them,
    #    since existing consumers of this shape read those two fields) -------
    edges: list[dict] = []
    for src, dst, edata in g.edges(data=True):
        si, ti = key_to_i.get(src), key_to_i.get(dst)
        if si is None or ti is None:
            continue
        same_file = _path_of(src) == _path_of(dst)
        line = line_by_ref.get((id_by_key.get(src), name_by_key.get(dst)))
        edges.append(
            {
                "s": si,
                "t": ti,
                "tier": 2 if same_file else 1,
                "namebased": not same_file,
                "confidence": edata.get("confidence", "AMBIGUOUS"),
                "line": line,
            }
        )

    with progress.phase("imports") as _p_imports:
        # -- imports -> file edges ---------------------------------------
        imp_rows = conn.execute(
            "SELECT f.path AS path, i.raw AS raw FROM imports i "
            "JOIN files f ON f.id = i.file_id WHERE i.commit_sha = ? ORDER BY f.path, i.line",
            (sha,),
        ).fetchall()
        resolved = resolve.resolve_imports(
            [(r["path"], r["raw"]) for r in imp_rows], all_paths, aliases=ts_aliases
        )
        file_edges_set: set[tuple[int, int]] = set()
        external_by_file: dict[int, set[str]] = {}       # -> file["imports"] (name strings, ext only)
        deps_by_file: dict[int, dict[str, str]] = {}     # fi -> {token: kind}, all three kinds
        dep_importers: dict[tuple[str, str], set[str]] = {}  # (token, kind) -> importer paths
        all_external: set[str] = set()
        for ri in resolved:
            fi = path_to_fi.get(ri.importer)
            if fi is None:
                continue
            token = _pkg_token(ri.module) if ri.kind != "internal" else _internal_token(ri)
            if token and token != "__future__":  # `from __future__ import …` is pure boilerplate
                deps_by_file.setdefault(fi, {}).setdefault(token, ri.kind)
                if ri.kind in ("third_party", "stdlib"):
                    dep_importers.setdefault((token, ri.kind), set()).add(ri.importer)
            if ri.external:
                name = _dep_token(ri.raw, ri.importer)
                if name:
                    external_by_file.setdefault(fi, set()).add(name)
                    all_external.add(name)
            elif ri.target is not None:
                tj = path_to_fi.get(ri.target)
                if tj is not None and tj != fi:
                    file_edges_set.add((fi, tj))

        _KIND_RANK = {"third_party": 0, "stdlib": 1, "internal": 2}

        def _deps_list(fi: int) -> list[dict]:
            return [
                {"name": t, "kind": k}
                for t, k in sorted(
                    deps_by_file.get(fi, {}).items(), key=lambda p: (_KIND_RANK[p[1]], p[0])
                )
            ]

        dependencies = [
            {
                "name": token,
                "kind": kind,
                "count": len(importers),
                "importers": sorted(importers)[:40],
            }
            for (token, kind), importers in sorted(
                dep_importers.items(), key=lambda p: (_KIND_RANK[p[0][1]], p[0][0])
            )
        ]

        files: list[dict] = []
        sym_by_file: dict[int, list[int]] = {}
        for n in nodes:
            sym_by_file.setdefault(path_to_fi[n["file"]], []).append(n["i"])
        for fi, r in enumerate(file_rows):
            files.append(
                {
                    "fi": fi,
                    "path": r["path"],
                    "lang": r["lang"],
                    "tier": r["tier"],
                    "loc": r["loc"] or 0,
                    "module": _module_of(r["path"]),
                    "symbols": sym_by_file.get(fi, []),
                    "imports": sorted(external_by_file.get(fi, ())),
                    "deps": _deps_list(fi),
                }
            )
        file_edges = [{"s": s, "t": t} for s, t in sorted(file_edges_set)]
        _p_imports.set_summary(f"{len(files)} files, {len(dependencies)} deps")

    # -- modules --------------------------------------------------------
    mod_files: dict[str, list[int]] = {}
    for f in files:
        mod_files.setdefault(f["module"], []).append(f["fi"])
    modules = [
        {
            "name": name,
            "files": fis,
            "symbol_count": sum(len(files[fi]["symbols"]) for fi in fis),
        }
        for name, fis in sorted(mod_files.items())
    ]

    # -- entry points -------------------------------------------------
    entry_points: list[dict] = []
    for r in conn.execute(
        "SELECT e.kind, e.detail, s.key FROM entry_points e "
        "LEFT JOIN symbols s ON s.id = e.symbol_id WHERE e.commit_sha = ? "
        "ORDER BY e.kind, e.detail",
        (sha,),
    ):
        entry_points.append(
            {
                "kind": r["kind"],
                "detail": r["detail"],
                "node": key_to_i.get(r["key"]) if r["key"] else None,
            }
        )

    # -- stats -------------------------------------------------------
    n_cycles = sum(1 for c in nx.strongly_connected_components(g) if len(c) > 1)
    entry_keys = set(ep_map)
    unreachable = sum(
        1
        for n in nodes
        if n["kind"] in ("function", "method")
        and n["fan_in"] == 0
        and n["key"] not in entry_keys
    )

    # -- timeline --------------------------------------------------
    timeline = _timeline(conn, cfg, key_to_i, progress=progress)

    # -- authored course content (optional) --------------------------
    from . import learn as _learn

    course = _learn.load(cfg)

    # -- authored library / module descriptions (optional) — the Packages tab
    #    is a dependency reference; explore.js falls back to the import graph +
    #    a bundled table of well-known-library blurbs when this is absent -----
    from . import libraries as _libraries

    libs = _libraries.load(cfg)

    # -- derived folder tree for the Learn tab's walkthrough (always present;
    #    an empty repo with no files just yields an empty list) --------------
    from . import folders as _folders

    folder_list = _folders.build(files, file_edges, entry_points)

    # -- authored walkthrough content (optional) — Learn's actual content:
    #    a plain-language intro, the category map, and per-folder prose ------
    from . import walkthrough as _walkthrough

    wt = _walkthrough.load(cfg)

    # -- glossary/tooltip terms, filtered to what the authored prose actually
    #    uses. `extra_terms` folds in walkthrough.json's own optional inline
    #    `glossary` field -- a separate, smaller convenience next to the
    #    dedicated `.codemap/glossary.json` file (see glossary.py::build) ----
    from . import glossary as _glossary

    _prose: list[str] = []
    if wt:
        intro = wt.get("intro") or {}
        if intro.get("what"):
            _prose.append(intro["what"])
        for side in (intro.get("sides") or {}).values():
            _prose.append(side.get("title") or "")
            _prose.append(side.get("body") or "")
        seam = intro.get("seam") or {}
        if seam.get("note"):
            _prose.append(seam["note"])
        for cat in wt.get("categories") or []:
            _prose.append(cat.get("title") or "")
            _prose.append(cat.get("body") or "")
            for grp in cat.get("groups") or []:
                _prose.append(grp.get("title") or "")
        for entry in (wt.get("folders") or {}).values():
            _prose.append(entry.get("title") or "")
            _prose.append(entry.get("purpose") or "")
            _prose.append(entry.get("note") or "")
    if libs:
        for entry in (libs.get("items") or {}).values():
            _prose.append(entry.get("general") or "")
            _prose.append(entry.get("here") or "")
    _extra_terms = (wt.get("glossary") if wt else None) or {}
    gloss = _glossary.build(cfg, _prose, extra_terms=_extra_terms)

    # -- simulate scenarios (optional; Lane 1 is derived client-side) -----
    from . import simulate as _simulate

    sim = _simulate.build(cfg, key_to_i)

    out = {
        "generator": f"codemap {__version__}",
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "root": str(cfg.root),
        "commit": sha,
        "commit_short": _short(sha),
        "head": head,
        "head_short": _short(head),
        "behind": behind,
        "stats": {
            "files": len(files),
            "symbols": len(nodes),
            "symbols_total": len(sym_rows),
            "edges": len(edges),
            "unreachable": unreachable,
            "cycles": n_cycles,
        },
        "degraded": (
            {"shown": len(nodes), "total": len(sym_rows)}
            if len(nodes) < len(sym_rows)
            else None
        ),
        "nodes": nodes,
        "edges": edges,
        "files": files,
        "file_edges": file_edges,
        "modules": modules,
        "folders": folder_list,
        "entry_points": entry_points,
        "external": sorted(all_external),
        "dependencies": dependencies,
        "timeline": timeline,
        "learn": course,
        "libraries": libs,
        "walkthrough": wt,
        "glossary": gloss,
        "sim": sim,
    }
    return out


def _dep_token(raw: str, importer: str) -> str | None:
    from ..semdiff import _dep_name

    return _dep_name(raw, importer, set())


def _pkg_token(module: str) -> str:
    """Third-party / stdlib module string -> the package name shown in the UI.
    ``rich.console`` -> ``rich``; ``@scope/pkg/sub`` -> ``@scope/pkg``; ``os`` -> ``os``."""
    m = (module or "").strip().removeprefix("node:")
    if not m:
        return ""
    if m.startswith("@"):
        parts = m.split("/")
        return "/".join(parts[:2]) if len(parts) >= 2 else m
    return m.split("/")[0].split(".")[0]


def _internal_token(ri) -> str:
    """In-repo import -> a readable module name. Prefer the dotted module; fall
    back to the resolved file path for bare ``from . import x`` forms."""
    m = (ri.module or "").lstrip(".").strip()
    if m:
        return m
    if ri.target:
        return ri.target.rsplit(".", 1)[0].replace("/", ".")
    return ""


def _timeline(
    conn: sqlite3.Connection,
    cfg: Config,
    key_to_i: dict[str, int],
    *,
    progress: _progress.Reporter | None = None,
) -> list[dict]:
    progress = progress or _progress.NULL
    rows = conn.execute(
        "SELECT sha, ts, author, message FROM commits WHERE sha != ? "
        "ORDER BY ts DESC, indexed_at DESC",
        (WORKTREE_SHA,),
    ).fetchall()
    out: list[dict] = []
    with progress.phase("timeline", len(rows), unit="commits") as p:
        for r in rows:
            sha = r["sha"]
            p.advance(detail=sha[:7])
            changes = semdiff.load_changes(conn, sha)
            src, text = intent.load(conn, sha)
            counts = {"structural": 0, "behavioral": 0, "cosmetic": 0}
            for c in changes:
                counts[c.severity] = counts.get(c.severity, 0) + 1
            headline = report._pick_headline(changes, None)
            hl_text = report._describe(headline) if headline else None
            impact_line = on_path = None
            if headline is not None:
                for ln in semdiff._impact_lines(headline, None):
                    if ln.startswith("Impact:"):
                        impact_line = ln.split(": ", 1)[1]
                    elif ln.startswith("On path from:"):
                        on_path = ln.split(": ", 1)[1]
            deps = sorted(
                {
                    c.details.get("module", c.subject)
                    for c in changes
                    if c.change_type == "dependency_added"
                }
            )
            date = (
                datetime.fromtimestamp(r["ts"], tz=timezone.utc).strftime("%Y-%m-%d")
                if r["ts"]
                else "?"
            )
            touched = sorted(
                {
                    key_to_i[c.symbol_key]
                    for c in changes
                    if c.symbol_key and c.symbol_key in key_to_i
                }
            )
            out.append(
                {
                    "sha": sha,
                    "short": _short(sha),
                    "date": date,
                    "ts": r["ts"] or 0,
                    "author": r["author"] or "",
                    "subject": (r["message"] or "").strip().splitlines()[0] if r["message"] else "",
                    "intent": {"source": src, "text": (text or "").strip().splitlines()[0] if text else ""},
                    "counts": counts,
                    "headline": hl_text,
                    "headline_node": key_to_i.get(headline.symbol_key) if headline else None,
                    "impact": impact_line,
                    "on_path": on_path,
                    "read_first": report._read_this_first(conn, sha, headline, changes),
                    "new_deps": deps,
                    "touched": touched,
                }
            )
    return out
