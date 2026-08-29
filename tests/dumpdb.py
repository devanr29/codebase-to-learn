"""Natural-key dump of the versioned tables, for from-scratch vs incremental
equality assertions. Insensitive to autoincrement id churn and wall-clock time.
"""

from __future__ import annotations

import sqlite3


def dump_index(conn: sqlite3.Connection) -> dict:
    sid_to_key = {
        r["id"]: r["key"] for r in conn.execute("SELECT id, key FROM symbols")
    }
    fid_to_path = {
        r["id"]: r["path"] for r in conn.execute("SELECT id, path FROM files")
    }

    commits = {
        r["sha"]: (r["parent_sha"], r["ts"], r["message"])
        for r in conn.execute("SELECT sha, parent_sha, ts, message FROM commits")
    }
    files = {
        r["path"]: (r["lang"], r["tier"])
        for r in conn.execute("SELECT path, lang, tier FROM files")
    }
    file_versions = {
        (fid_to_path[r["file_id"]], r["commit_sha"]): (r["content_hash"], r["loc"], r["status"])
        for r in conn.execute(
            "SELECT file_id, commit_sha, content_hash, loc, status FROM file_versions"
        )
    }
    symbols = {
        r["key"]: (r["kind"], r["name"], r["qualified_name"])
        for r in conn.execute("SELECT key, kind, name, qualified_name FROM symbols")
    }
    symbol_versions = {
        (sid_to_key[r["symbol_id"]], r["commit_sha"]): (
            r["signature"],
            r["start_line"],
            r["end_line"],
            r["body_hash"],
            r["raw_hash"],
            r["decorators"],
            r["docstring"],
        )
        for r in conn.execute(
            "SELECT symbol_id, commit_sha, signature, start_line, end_line, body_hash, "
            "raw_hash, decorators, docstring FROM symbol_versions"
        )
    }
    refs = sorted(
        (
            r["commit_sha"],
            sid_to_key.get(r["from_symbol_id"]),
            r["target_name"],
            r["line"],
            r["resolved"],
        )
        for r in conn.execute(
            "SELECT commit_sha, from_symbol_id, target_name, line, resolved FROM refs"
        )
    )
    imports = sorted(
        (r["commit_sha"], fid_to_path[r["file_id"]], r["raw"], r["external"], r["line"])
        for r in conn.execute(
            "SELECT commit_sha, file_id, raw, external, line FROM imports"
        )
    )
    return {
        "commits": commits,
        "files": files,
        "file_versions": file_versions,
        "symbols": symbols,
        "symbol_versions": symbol_versions,
        "refs": refs,
        "imports": imports,
    }


def restrict_to_commits(dump: dict, shas: set[str]) -> dict:
    """Keep only the rows scoped to ``shas`` (plus the global files/symbols maps)."""
    return {
        "commits": {k: v for k, v in dump["commits"].items() if k in shas},
        "files": dump["files"],
        "symbols": dump["symbols"],
        "file_versions": {k: v for k, v in dump["file_versions"].items() if k[1] in shas},
        "symbol_versions": {k: v for k, v in dump["symbol_versions"].items() if k[1] in shas},
        "refs": [r for r in dump["refs"] if r[0] in shas],
        "imports": [r for r in dump["imports"] if r[0] in shas],
    }
