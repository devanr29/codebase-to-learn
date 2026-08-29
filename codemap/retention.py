"""Snapshot retention (spec M3).

Full graphs are kept for the most recent ``config.retention`` commits. Older
commits keep their ``changes`` rows (the delta is the deliverable) but shed
``symbol_versions`` / ``refs`` / ``imports`` / ``file_versions`` so the DB does
not grow without bound. Pruning is idempotent — deleting already-dropped rows
is a no-op.
"""

from __future__ import annotations

import sqlite3

from .config import Config
from .indexer import WORKTREE_SHA

# Literal statements (no interpolation) — one per full-graph table.
_PRUNE_STATEMENTS = (
    "DELETE FROM symbol_versions WHERE commit_sha=?",
    "DELETE FROM refs WHERE commit_sha=?",
    "DELETE FROM imports WHERE commit_sha=?",
    "DELETE FROM file_versions WHERE commit_sha=?",
    "DELETE FROM entry_points WHERE commit_sha=?",
)


def _ordered_commits(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT sha FROM commits WHERE sha != ? ORDER BY ts DESC, indexed_at DESC",
        (WORKTREE_SHA,),
    ).fetchall()
    return [r["sha"] for r in rows]


def prune(conn: sqlite3.Connection, cfg: Config) -> list[str]:
    """Drop full-graph rows for commits older than the retention window.

    Returns the list of commit shas whose full graph was pruned in this call.
    """
    keep_n = max(1, cfg.retention)
    stale = _ordered_commits(conn)[keep_n:]
    if not stale:
        return []

    pruned: list[str] = []
    for sha in stale:
        touched = 0
        for statement in _PRUNE_STATEMENTS:
            cur = conn.execute(statement, (sha,))
            touched += max(cur.rowcount, 0)
        if touched:
            pruned.append(sha)
    conn.commit()
    return pruned
