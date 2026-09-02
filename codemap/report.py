"""Deterministic per-commit markdown entry (spec M7).

Short by design — the tool's value depends on the output staying readable.
Cosmetic changes are one counted line, never expanded. Works fully with the LLM
disabled; when M8 is on, the narrative replaces the bullet lines only.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from . import intent, semdiff
from .config import Config
from .impact import Impact
from .indexer import WORKTREE_SHA

_INTENT_LABEL = {
    "session": "from session",
    "env": "from env",
    "note": "from note",
    "commit_message": "from commit message",
    "inferred": "inferred",
}

_HEADLINE_TYPES = (
    "signature_changed", "symbol_removed", "body_changed",
    "renamed", "symbol_moved", "symbol_added",
)
_TYPE_RANK = {t: i for i, t in enumerate(_HEADLINE_TYPES)}


def _span_at(conn: sqlite3.Connection, key: str, sha: str) -> tuple[str, int, int] | None:
    row = conn.execute(
        "SELECT f.path, sv.start_line, sv.end_line FROM symbol_versions sv "
        "JOIN symbols s ON s.id = sv.symbol_id JOIN files f ON f.id = s.file_id "
        "WHERE s.key = ? AND sv.commit_sha = ?",
        (key, sha),
    ).fetchone()
    return (row["path"], row["start_line"], row["end_line"]) if row else None


def _callers_of(change: semdiff.Change, impacts: dict[str, Impact] | None) -> int:
    if impacts and change.symbol_key in impacts:
        return len(impacts[change.symbol_key].callers)
    return int(change.details.get("impact", {}).get("callers", 0))


def _sig_delta(details: dict) -> str:
    frm, to = details.get("from", ""), details.get("to", "")

    def params(sig: str) -> list[str]:
        if "(" not in sig or ")" not in sig:
            return []
        inner = sig[sig.index("(") + 1 : sig.rindex(")")]
        return [p.split("=")[0].split(":")[0].strip() for p in inner.split(",") if p.strip()]

    added = [p for p in params(to) if p not in params(frm)]
    removed = [p for p in params(frm) if p not in params(to)]
    bits = []
    if added:
        bits.append("added " + ", ".join(f"`{p}`" for p in added))
    if removed:
        bits.append("removed " + ", ".join(f"`{p}`" for p in removed))
    return "; ".join(bits) if bits else f"`{frm}` -> `{to}`"


def _describe(c: semdiff.Change) -> str | None:
    name = c.subject.split("::")[-1] if "::" in c.subject else c.subject
    if c.change_type == "signature_changed":
        return f"`{name}()` signature changed, {_sig_delta(c.details)}"
    if c.change_type == "body_changed":
        return f"`{name}()` body changed"
    if c.change_type == "symbol_added":
        return f"new `{name}()`"
    if c.change_type == "symbol_removed":
        return f"removed `{name}()`"
    if c.change_type == "renamed":
        return f"renamed `{c.details.get('from_name')}` -> `{c.details.get('to_name')}`"
    if c.change_type == "symbol_moved":
        frm, to = c.details.get("from", ""), c.details.get("to", "")
        return f"moved `{name}()` {frm.split('::')[0]} -> {to.split('::')[0]}"
    if c.change_type == "file_added":
        return f"new file `{c.subject}`"
    if c.change_type == "file_removed":
        return f"removed file `{c.subject}`"
    if c.change_type == "dependency_added":
        return f"new dependency `{c.details.get('module', c.subject)}`"
    if c.change_type == "dependency_removed":
        return f"dropped dependency `{c.details.get('module', c.subject)}`"
    if c.change_type in ("entry_point_added", "entry_point_removed"):
        verb = "new" if c.change_type.endswith("added") else "removed"
        return f"{verb} entry point `{c.details.get('detail', c.subject)}`"
    return None


def _summarize(changes: list[semdiff.Change], limit: int = 6) -> str:
    parts = [d for c in changes if (d := _describe(c))]
    if not parts:
        return ""
    if len(parts) > limit:
        return "; ".join(parts[:limit]) + f"; +{len(parts) - limit} more"
    return "; ".join(parts)


def _pick_headline(
    changes: list[semdiff.Change], impacts: dict[str, Impact] | None
) -> semdiff.Change | None:
    cands = [
        c for c in changes
        if c.symbol_key and c.severity != semdiff.COSMETIC and c.change_type in _TYPE_RANK
    ]
    if not cands:
        return None
    sev_rank = {semdiff.STRUCTURAL: 0, semdiff.BEHAVIORAL: 1}
    return min(
        cands,
        key=lambda c: (
            sev_rank.get(c.severity, 9),
            -_callers_of(c, impacts),
            _TYPE_RANK[c.change_type],
            c.symbol_key,
        ),
    )


def render_commit(
    conn: sqlite3.Connection,
    cfg: Config,
    sha: str,
    *,
    changes: list[semdiff.Change] | None = None,
    impacts: dict[str, Impact] | None = None,
    narrative: str | None = None,
) -> str:
    meta = conn.execute(
        "SELECT ts, message FROM commits WHERE sha = ?", (sha,)
    ).fetchone()
    date = (
        datetime.fromtimestamp(meta["ts"], tz=timezone.utc).strftime("%Y-%m-%d")
        if meta and meta["ts"]
        else "?"
    )
    # `changes` is only ever passed by a caller that computed it without
    # persisting (spec M15's `explain worktree` — the live pseudo-commit's
    # diff is deliberately never written to the `changes` table). Every other
    # caller relies on the stored rows, which is the common, cheap path.
    if changes is None:
        changes = semdiff.load_changes(conn, sha)
    src, text = intent.load(conn, sha)

    structural = [c for c in changes if c.severity == semdiff.STRUCTURAL]
    behavioral = [c for c in changes if c.severity == semdiff.BEHAVIORAL]
    cosmetic_n = sum(1 for c in changes if c.severity == semdiff.COSMETIC)
    deps = sorted({c.details.get("module", c.subject) for c in changes if c.change_type == "dependency_added"})

    header_sha = sha if sha == WORKTREE_SHA else sha[:7]
    lines = [f"## {header_sha} — {date}"]

    if src == "inferred":
        lines.append("**Intent:** [inferred] no stated intent — treat any narrative as a guess.")
    else:
        first = (text or "").strip().splitlines()[0] if text else ""
        lines.append(f"**Intent:** [{_INTENT_LABEL.get(src, src)}] {first}")

    if not changes:
        lines.append("\nNo tracked changes.")
        return "\n".join(lines)

    headline = _pick_headline(changes, impacts)

    if narrative:
        lines.append("")
        lines.append(narrative.strip())
    else:
        s = _summarize(structural)
        if s:
            lines.append(f"**Structural:** {s}.")
        b = _summarize(behavioral)
        if b:
            lines.append(f"**Behavioral:** {b}.")

    if headline is not None:
        imp_summary = None
        if impacts and headline.symbol_key in impacts:
            from .impact import render_lines

            imp_lines = render_lines(impacts[headline.symbol_key])
            for ln in imp_lines:
                key = "**Impact:**" if ln.startswith("Impact:") else "**On path from:**"
                lines.append(f"{key} {ln.split(': ', 1)[1]}")
        else:
            summ = headline.details.get("impact")
            if summ and summ.get("callers"):
                u = summ.get("unmodified", 0)
                tail = f" — {summ.get('modified', 0)} updated, {u} unchanged (worth checking)" if u else ""
                lines.append(f"**Impact:** {summ['callers']} callers{tail}.")
                if summ.get("entry_path"):
                    lines.append(f"**On path from:** {summ['entry_path']}")
            elif summ and not summ.get("resolvable", True):
                lines.append(f"**Impact:** callers not resolvable at tier {summ.get('tier', 1)}.")

    lines.append(f"**New dependency:** {', '.join(f'`{d}`' for d in deps) if deps else 'none'}.")

    read_first = _read_this_first(conn, sha, headline, changes)
    if read_first:
        lines.append(f"**Read this first:** {read_first}")

    if cosmetic_n:
        lines.append(f"**Cosmetic:** {cosmetic_n} change(s).")

    return "\n".join(lines)


def _read_this_first(
    conn: sqlite3.Connection, sha: str, headline: semdiff.Change | None, changes: list[semdiff.Change]
) -> str | None:
    if headline is not None and headline.symbol_key:
        span = _span_at(conn, headline.symbol_key, sha)
        if span:
            return f"{span[0]}:{span[1]}-{span[2]}"
        parent = conn.execute("SELECT parent_sha FROM commits WHERE sha=?", (sha,)).fetchone()
        if parent and parent["parent_sha"]:
            span = _span_at(conn, headline.symbol_key, parent["parent_sha"])
            if span:
                return f"{span[0]}:{span[1]}-{span[2]} (removed)"
    # fall back to the file carrying the most changes
    from collections import Counter

    files = Counter(c.file_path for c in changes if c.file_path and c.severity != semdiff.COSMETIC)
    if files:
        return files.most_common(1)[0][0]
    return None


def write_commit_file(
    conn: sqlite3.Connection,
    cfg: Config,
    sha: str,
    *,
    impacts: dict[str, Impact] | None = None,
) -> str | None:
    changes = semdiff.load_changes(conn, sha)
    if not changes:
        return None
    row = conn.execute("SELECT ts FROM commits WHERE sha=?", (sha,)).fetchone()
    ts = row["ts"] if row and row["ts"] else 0
    cfg.changes_dir.mkdir(parents=True, exist_ok=True)
    path = cfg.changes_dir / f"{ts}-{sha[:7]}.md"
    narrative = None
    if cfg.llm.enabled:
        from . import narrate

        narrative = narrate.narrate(conn, cfg, sha)
    body = render_commit(conn, cfg, sha, impacts=impacts, narrative=narrative)
    path.write_text(body + "\n", encoding="utf-8")
    return str(path)
