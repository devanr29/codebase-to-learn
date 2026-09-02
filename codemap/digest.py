"""Catch-up digest and on-demand snapshot (spec M9).

``catchup`` collapses every change since ``last_reviewed_commit`` into one
net-change summary. ``snapshot`` is the rarely-read architecture view rendered
from the current graph (spec v1's document, kept optional).
"""

from __future__ import annotations

import sqlite3
from collections import Counter, defaultdict

from . import gitio, intent, semdiff
from .config import Config
from .db import get_meta
from .indexer import WORKTREE_SHA


def _capped(items: list[str], limit: int = 15) -> list[str]:
    if not items:
        return ["- none"]
    if len(items) > limit:
        return [f"- {s}" for s in items[:limit]] + [f"- …and {len(items) - limit} more"]
    return [f"- {s}" for s in items]


# --------------------------------------------------------------------------- catchup


def _range_shas(conn: sqlite3.Connection, cfg: Config) -> list[str]:
    last_reviewed = get_meta(conn, "last_reviewed_commit")
    last_indexed = get_meta(conn, "last_indexed_commit")
    if not last_indexed:
        return []
    try:
        shas = gitio.rev_list(cfg.root, last_reviewed, last_indexed)
    except gitio.GitError:
        shas = []
    indexed = {
        r["sha"]
        for r in conn.execute("SELECT sha FROM commits WHERE sha != ?", (WORKTREE_SHA,))
    }
    return [s for s in shas if s in indexed]


def catchup(conn: sqlite3.Connection, cfg: Config) -> str:
    shas = _range_shas(conn, cfg)
    if not shas:
        return "Nothing to catch up on — the reviewed marker is at HEAD."

    per_symbol: dict[str, list[semdiff.Change]] = defaultdict(list)
    file_events: Counter[str] = Counter()
    deps_added: set[str] = set()
    deps_removed: set[str] = set()
    intents: list[tuple[str, str, str]] = []

    for sha in shas:
        src, text = intent.load(conn, sha)
        first = (text or "").splitlines()[0] if text else ""
        intents.append((sha[:7], src, first))
        for c in semdiff.load_changes(conn, sha):
            if c.change_type == "dependency_added":
                deps_added.add(c.details.get("module", c.subject))
            elif c.change_type == "dependency_removed":
                deps_removed.add(c.details.get("module", c.subject))
            elif c.change_type in ("file_added", "file_removed"):
                file_events[c.change_type] += 1
            if c.symbol_key:
                per_symbol[c.symbol_key].append(c)

    structural_net: list[str] = []
    behavioral_net: list[str] = []
    for key, cs in sorted(per_symbol.items()):
        types = [c.change_type for c in cs]
        name = key.split("::")[-1]
        if "symbol_added" in types and "symbol_removed" in types:
            continue  # appeared and disappeared within the window
        if "symbol_removed" in types:
            structural_net.append(f"`{name}()` removed")
        elif "symbol_added" in types:
            structural_net.append(f"`{name}()` added")
        elif "signature_changed" in types:
            frm = next(c.details.get("from") for c in cs if c.change_type == "signature_changed")
            to = next(c.details.get("to") for c in reversed(cs) if c.change_type == "signature_changed")
            structural_net.append(f"`{name}()` signature: `{frm}` -> `{to}`")
        elif "symbol_moved" in types:
            structural_net.append(f"`{name}()` moved")
        else:
            n = sum(1 for t in types if t == "body_changed")
            worst = min((c.severity for c in cs), key=lambda s: {"structural": 0, "behavioral": 1, "cosmetic": 2}[s])
            if worst != semdiff.COSMETIC:
                behavioral_net.append(f"`{name}()` body changed" + (f" ({n}x)" if n > 1 else ""))

    caller_totals = Counter()
    for key, cs in per_symbol.items():
        best = max((c.details.get("impact", {}).get("callers", 0) for c in cs), default=0)
        if best:
            caller_totals[key.split("::")[-1]] = best

    first7, last7 = shas[0][:7], shas[-1][:7]
    out = [f"# Catch-up — {len(shas)} commit(s) ({first7}..{last7})", ""]
    out.append("## Stated intent, commit by commit")
    for sha7, src, first in intents:
        out.append(f"- `{sha7}` [{src}] {first}" if first else f"- `{sha7}` [{src}]")
    out.append("")
    out.append(f"**Files:** +{file_events['file_added']} / -{file_events['file_removed']}")
    out.append("")
    out.append("## Net structural")
    out.extend(_capped(structural_net))
    out.append("")
    out.append("## Net behavioral")
    out.extend(_capped(behavioral_net))
    out.append("")
    deps_line = ", ".join(f"`{d}`" for d in sorted(deps_added - deps_removed)) or "none"
    out.append(f"## New dependencies\n{deps_line}")
    if caller_totals:
        out.append("\n## Most-affected (by caller count)")
        for name, n in caller_totals.most_common(5):
            out.append(f"- `{name}()` — {n} caller(s)")
    return "\n".join(out)


# -------------------------------------------------------------------------- snapshot


def snapshot(conn: sqlite3.Connection, cfg: Config) -> str:
    # see site/model.py::build() for why graph_head is preferred (spec M15)
    sha = get_meta(conn, "graph_head") or get_meta(conn, "last_indexed_commit")
    if not sha:
        return "No index yet — run `codemap scan` first."

    files = conn.execute(
        "SELECT f.path, f.lang, f.tier, COUNT(sv.symbol_id) AS n "
        "FROM file_versions fv JOIN files f ON f.id = fv.file_id "
        "LEFT JOIN symbols s ON s.file_id = f.id "
        "LEFT JOIN symbol_versions sv ON sv.symbol_id = s.id AND sv.commit_sha = fv.commit_sha "
        "WHERE fv.commit_sha = ? AND fv.status != 'deleted' "
        "GROUP BY f.path ORDER BY f.path",
        (sha,),
    ).fetchall()

    eps = conn.execute(
        "SELECT e.kind, e.detail, s.key FROM entry_points e "
        "LEFT JOIN symbols s ON s.id = e.symbol_id WHERE e.commit_sha = ? ORDER BY e.kind, e.detail",
        (sha,),
    ).fetchall()

    deps = sorted(
        {
            r["raw"]
            for r in conn.execute(
                "SELECT raw FROM imports WHERE commit_sha = ? AND external = 1", (sha,)
            )
        }
    )

    hotspots = conn.execute(
        "SELECT s.key, COUNT(*) AS n FROM changes c JOIN symbols s ON s.id = c.symbol_id "
        "WHERE c.severity != 'cosmetic' GROUP BY s.key ORDER BY n DESC, s.key LIMIT 10"
    ).fetchall()

    tiers = Counter(r["tier"] for r in files)
    out = [
        f"# Snapshot @ {sha[:7]}",
        "",
        f"{len(files)} file(s) — " + ", ".join(f"T{t}: {n}" for t, n in sorted(tiers.items())),
        "",
        "## Modules",
    ]
    for r in files:
        out.append(f"- `{r['path']}` ({r['lang']}, T{r['tier']}) — {r['n']} symbol(s)")

    out.append("\n## Entry points")
    if eps:
        for r in eps:
            tgt = f" -> `{r['key'].split('::')[-1]}`" if r["key"] else ""
            out.append(f"- [{r['kind']}] {r['detail']}{tgt}")
    else:
        out.append("- none detected")

    out.append("\n## External dependencies")
    out.extend(f"- {d}" for d in deps) if deps else out.append("- none")

    out.append("\n## Hotspots (most non-cosmetic changes)")
    if hotspots:
        for r in hotspots:
            out.append(f"- `{r['key']}` — {r['n']} change(s)")
    else:
        out.append("- none recorded")

    return "\n".join(out)
