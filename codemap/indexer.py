"""Incremental, per-commit indexing (spec M2/M3).

Each indexed commit gets a *complete* snapshot at its ``commit_sha``: unchanged
files are carried forward from the parent snapshot without reparsing, changed
files are reparsed. A from-scratch index of commit X and an incremental index
that arrives at X therefore hold identical rows (M2 acceptance).

A non-git directory (or a dirty tree) is indexed under the pseudo-sha
``worktree`` using content-hash comparison against the last snapshot.
"""

from __future__ import annotations

import hashlib
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import discovery, entrypoints, gitio
from .config import Config
from .db import get_meta, set_meta
from .languages.registry import spec_for_path
from .parsing import ParsedFile, parse_source

WORKTREE_SHA = "worktree"


@dataclass
class ScanStats:
    commits_indexed: int = 0
    files_parsed: int = 0
    files_skipped: int = 0
    errors: list[tuple[str, str]] = field(default_factory=list)


# --------------------------------------------------------------------------- utils


def resolve_sha(root: Path | str, rev: str) -> str:
    return gitio.rev_parse(root, rev)


def parent_sha(conn: sqlite3.Connection, sha: str) -> str | None:
    row = conn.execute("SELECT parent_sha FROM commits WHERE sha=?", (sha,)).fetchone()
    return row["parent_sha"] if row else None


def _sha1(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def _file_id(conn: sqlite3.Connection, path: str, lang: str, tier: int) -> int:
    conn.execute(
        "INSERT INTO files(path, lang, tier) VALUES(?,?,?) "
        "ON CONFLICT(path) DO UPDATE SET lang=excluded.lang, tier=excluded.tier",
        (path, lang, tier),
    )
    return conn.execute("SELECT id FROM files WHERE path=?", (path,)).fetchone()["id"]


def _symbol_id(conn: sqlite3.Connection, file_id: int, sym) -> int:
    conn.execute(
        "INSERT INTO symbols(file_id, key, kind, name, qualified_name) VALUES(?,?,?,?,?) "
        "ON CONFLICT(key) DO UPDATE SET "
        "file_id=excluded.file_id, kind=excluded.kind, name=excluded.name, "
        "qualified_name=excluded.qualified_name",
        (file_id, sym.key, sym.kind, sym.name, sym.qualified_name),
    )
    return conn.execute("SELECT id FROM symbols WHERE key=?", (sym.key,)).fetchone()["id"]


_STDLIB = set(getattr(sys, "stdlib_module_names", ())) | {
    # builtins / common names not always in stdlib_module_names
    "__future__", "builtins",
}


def is_stdlib(module: str) -> bool:
    return module.split(".")[0] in _STDLIB


def _internal_names(entries: list[discovery.FileEntry]) -> set[str]:
    """Every token that could name an in-repo module: top-level dirs, package
    dirs, file stems, and dotted paths. Wider than a top-level-root check so a
    sibling module imported by bare name (``import load_cmdb``) is seen as
    internal, not a dependency."""
    names: set[str] = set()
    for e in entries:
        parts = e.path.split("/")
        stem = Path(parts[-1]).stem
        names.add(stem)
        dotted_parts = parts[:-1] + ([stem] if stem != "__init__" else [])
        for i in range(len(dotted_parts)):
            names.add(dotted_parts[i])
            names.add(".".join(dotted_parts[: i + 1]))
    names.discard("")
    return names


def classify_import(raw: str, lang: str, internal_names: set[str]) -> tuple[str, bool]:
    """Return ``(module, is_external)`` for a raw import statement.

    String-level classification only — not extraction, so no tree-sitter needed.
    ``is_external`` is True only for genuine third-party packages: relative
    imports, in-repo modules, and the standard library are all internal.
    """
    text = raw.strip()
    if lang in ("typescript", "tsx", "javascript"):
        mod = ""
        for quote in ('"', "'", "`"):
            if quote in text:
                a = text.index(quote)
                b = text.index(quote, a + 1)
                mod = text[a + 1 : b]
                break
        if mod.startswith("."):
            return mod, False
        top = mod.lstrip("@").split("/")[0]
        return mod, top not in internal_names and mod not in internal_names

    # python
    if text.startswith("from "):
        mod = text[5:].split(" import", 1)[0].strip()
    elif text.startswith("import "):
        mod = text[7:].split(",")[0].strip().split(" as ")[0].strip()
    else:
        mod = text
    if mod.startswith("."):
        return mod, False
    top = mod.split(".")[0]
    internal = top in internal_names or mod in internal_names or is_stdlib(mod)
    return mod, not internal


# ------------------------------------------------------------------ row writers


def _clear_file_rows(conn: sqlite3.Connection, file_id: int, sha: str) -> None:
    conn.execute("DELETE FROM file_versions WHERE file_id=? AND commit_sha=?", (file_id, sha))
    conn.execute(
        "DELETE FROM symbol_versions WHERE commit_sha=? AND symbol_id IN "
        "(SELECT id FROM symbols WHERE file_id=?)",
        (sha, file_id),
    )
    conn.execute(
        "DELETE FROM refs WHERE commit_sha=? AND from_symbol_id IN "
        "(SELECT id FROM symbols WHERE file_id=?)",
        (sha, file_id),
    )
    conn.execute("DELETE FROM imports WHERE commit_sha=? AND file_id=?", (sha, file_id))
    conn.execute(
        "DELETE FROM entry_points WHERE commit_sha=? AND symbol_id IN "
        "(SELECT id FROM symbols WHERE file_id=?)",
        (sha, file_id),
    )


def _write_parsed(
    conn: sqlite3.Connection,
    sha: str,
    file_id: int,
    pf: ParsedFile,
    source: bytes,
    status: str,
    internal_roots: set[str],
) -> None:
    _clear_file_rows(conn, file_id, sha)
    loc = source.count(b"\n") + (1 if source and not source.endswith(b"\n") else 0)
    conn.execute(
        "INSERT INTO file_versions(file_id, commit_sha, content_hash, loc, status) "
        "VALUES(?,?,?,?,?)",
        (file_id, sha, _sha1(source), loc, status),
    )

    key_to_id: dict[str, int] = {}
    for sym in pf.symbols:
        sid = _symbol_id(conn, file_id, sym)
        key_to_id[sym.key] = sid
        conn.execute(
            "INSERT INTO symbol_versions(symbol_id, commit_sha, signature, start_line, "
            "end_line, body_hash, raw_hash, decorators, docstring) VALUES(?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(symbol_id, commit_sha) DO UPDATE SET "
            "signature=excluded.signature, start_line=excluded.start_line, "
            "end_line=excluded.end_line, body_hash=excluded.body_hash, "
            "raw_hash=excluded.raw_hash, decorators=excluded.decorators, "
            "docstring=excluded.docstring",
            (
                sid,
                sha,
                sym.signature,
                sym.start_line,
                sym.end_line,
                sym.body_hash,
                sym.raw_hash,
                "\n".join(sym.decorators) or None,
                sym.docstring,
            ),
        )

    for ref in pf.refs:
        from_id = key_to_id.get(ref.from_key) if ref.from_key else None
        conn.execute(
            "INSERT INTO refs(commit_sha, from_symbol_id, target_name, target_symbol_id, "
            "resolved, tier, line) VALUES(?,?,?,?,?,?,?)",
            (sha, from_id, ref.target_name, None, 0, 1, ref.line),
        )

    for imp in pf.imports:
        _mod, external = classify_import(imp.raw, pf.lang, internal_roots)
        conn.execute(
            "INSERT INTO imports(commit_sha, file_id, raw, resolved_file_id, external, line) "
            "VALUES(?,?,?,?,?,?)",
            (sha, file_id, imp.raw, None, 1 if external else 0, imp.line),
        )

    # --- entry points (decorator- and __main__-based; always tied to a symbol)
    name_to_key = {s.name: s.key for s in pf.symbols}
    for sym in pf.symbols:
        hit = entrypoints.from_decorators("\n".join(sym.decorators), pf.lang)
        if hit:
            kind, detail = hit
            conn.execute(
                "INSERT INTO entry_points(commit_sha, symbol_id, kind, detail) VALUES(?,?,?,?)",
                (sha, key_to_id[sym.key], kind, detail),
            )
    for callee in pf.main_calls:
        key = name_to_key.get(callee)
        if key:
            conn.execute(
                "INSERT INTO entry_points(commit_sha, symbol_id, kind, detail) VALUES(?,?,?,?)",
                (sha, key_to_id[key], "main", f"__main__ @ {pf.path}"),
            )


def _carry_forward(conn: sqlite3.Connection, file_id: int, src_sha: str, dst_sha: str) -> None:
    _clear_file_rows(conn, file_id, dst_sha)
    conn.execute(
        "INSERT INTO file_versions(file_id, commit_sha, content_hash, loc, status) "
        "SELECT file_id, ?, content_hash, loc, 'unchanged' FROM file_versions "
        "WHERE file_id=? AND commit_sha=?",
        (dst_sha, file_id, src_sha),
    )
    conn.execute(
        "INSERT INTO symbol_versions(symbol_id, commit_sha, signature, start_line, end_line, "
        "body_hash, raw_hash, decorators, docstring) "
        "SELECT symbol_id, ?, signature, start_line, end_line, body_hash, raw_hash, "
        "decorators, docstring FROM symbol_versions WHERE commit_sha=? AND symbol_id IN "
        "(SELECT id FROM symbols WHERE file_id=?)",
        (dst_sha, src_sha, file_id),
    )
    conn.execute(
        "INSERT INTO refs(commit_sha, from_symbol_id, target_name, target_symbol_id, resolved, tier, line) "
        "SELECT ?, from_symbol_id, target_name, target_symbol_id, resolved, tier, line "
        "FROM refs WHERE commit_sha=? AND from_symbol_id IN "
        "(SELECT id FROM symbols WHERE file_id=?)",
        (dst_sha, src_sha, file_id),
    )
    conn.execute(
        "INSERT INTO imports(commit_sha, file_id, raw, resolved_file_id, external, line) "
        "SELECT ?, file_id, raw, resolved_file_id, external, line "
        "FROM imports WHERE commit_sha=? AND file_id=?",
        (dst_sha, src_sha, file_id),
    )
    conn.execute(
        "INSERT INTO entry_points(commit_sha, symbol_id, kind, detail) "
        "SELECT ?, symbol_id, kind, detail FROM entry_points "
        "WHERE commit_sha=? AND symbol_id IN (SELECT id FROM symbols WHERE file_id=?)",
        (dst_sha, src_sha, file_id),
    )


def _mark_deleted(conn: sqlite3.Connection, file_id: int, sha: str) -> None:
    _clear_file_rows(conn, file_id, sha)
    conn.execute(
        "INSERT INTO file_versions(file_id, commit_sha, content_hash, loc, status) "
        "VALUES(?,?,?,?,'deleted')",
        (file_id, sha, None, 0),
    )


# ----------------------------------------------------------------- commit indexing


def _commit_indexed(conn: sqlite3.Connection, sha: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM file_versions WHERE commit_sha=? LIMIT 1", (sha,)
    ).fetchone()
    return row is not None


def index_commit(
    conn: sqlite3.Connection,
    cfg: Config,
    sha: str,
    stats: ScanStats,
    *,
    reindex: bool = False,
) -> None:
    root = cfg.root
    meta = gitio.commit_meta(root, sha)
    conn.execute(
        "INSERT INTO commits(sha, parent_sha, ts, author, message, indexed_at) "
        "VALUES(?,?,?,?,?,?) ON CONFLICT(sha) DO UPDATE SET "
        "parent_sha=excluded.parent_sha, ts=excluded.ts, author=excluded.author, "
        "message=excluded.message, indexed_at=excluded.indexed_at",
        (meta.sha, meta.parent_sha, meta.ts, meta.author, meta.message, int(time.time())),
    )

    parent = meta.parent_sha
    incremental = (
        not reindex and parent is not None and _commit_indexed(conn, parent)
    )

    entries = discovery.iter_commit(root, sha, cfg)
    internal_roots = _internal_names(entries)
    present_paths = {e.path for e in entries}

    changed: dict[str, str] = {}   # path -> A|M
    renamed_from: dict[str, str] = {}
    if incremental:
        for status, path, old_path in gitio.name_status(root, parent, sha):
            if status == "R":
                renamed_from[path] = old_path
                changed[path] = "M"
            elif status in ("A", "C"):
                changed[path] = "A"
            elif status in ("M", "T"):
                changed[path] = "M"
            elif status == "D":
                pass  # handled via parent-snapshot diff below
    else:
        changed = {p: "A" for p in present_paths}

    for entry in entries:
        path = entry.path
        # rename: move the files-row identity from old path to new
        if path in renamed_from:
            conn.execute(
                "UPDATE OR IGNORE files SET path=? WHERE path=?",
                (path, renamed_from[path]),
            )
        fid = _file_id(conn, path, entry.lang, entry.tier)

        if incremental and path not in changed:
            _carry_forward(conn, fid, parent, sha)
            stats.files_skipped += 1
            continue

        blob = gitio.show_bytes(root, sha, path)
        if blob is None:
            continue
        pf = parse_source(path, blob, spec_for_path(path))
        _write_parsed(conn, sha, fid, pf, blob, changed.get(path, "A"), internal_roots)
        stats.files_parsed += 1
        if not pf.ok:
            stats.errors.append((path, pf.error or "parse error"))

    # files present in the parent snapshot but gone at this commit
    if incremental:
        gone = conn.execute(
            "SELECT f.id, f.path FROM file_versions fv JOIN files f ON f.id = fv.file_id "
            "WHERE fv.commit_sha=? AND fv.status != 'deleted'",
            (parent,),
        ).fetchall()
        for row in gone:
            if row["path"] not in present_paths and row["path"] not in renamed_from.values():
                _mark_deleted(conn, row["id"], sha)

    _index_repo_entry_points(conn, root, sha)

    set_meta(conn, "last_indexed_commit", sha)
    conn.commit()


def _index_repo_entry_points(conn: sqlite3.Connection, root, sha: str) -> None:
    """Repo-level entry points: ``[project.scripts]`` consoles and Dockerfile
    CMD/ENTRYPOINT. Scripts are resolved to a symbol; Dockerfile lines are
    detail-only (no symbol, so no reachability path)."""
    conn.execute(
        "DELETE FROM entry_points WHERE commit_sha=? AND kind IN ('script','docker')", (sha,)
    )
    for name, module, func in entrypoints.repo_scripts(root, sha):
        base = module.replace(".", "/")
        row = conn.execute(
            "SELECT s.id FROM symbols s JOIN files f ON f.id = s.file_id "
            "JOIN symbol_versions sv ON sv.symbol_id = s.id AND sv.commit_sha = ? "
            "WHERE f.path IN (?, ?) AND s.name = ? LIMIT 1",
            (sha, f"{base}.py", f"{base}/__init__.py", func),
        ).fetchone()
        if row:
            conn.execute(
                "INSERT INTO entry_points(commit_sha, symbol_id, kind, detail) VALUES(?,?,?,?)",
                (sha, row["id"], "script", f"{name} = {module}:{func}"),
            )
    for line in entrypoints.dockerfile_commands(root, sha):
        conn.execute(
            "INSERT INTO entry_points(commit_sha, symbol_id, kind, detail) VALUES(?,?,?,?)",
            (sha, None, "docker", line),
        )


# ------------------------------------------------------------------------ worktree


def _index_worktree(conn: sqlite3.Connection, cfg: Config, stats: ScanStats) -> None:
    sha = WORKTREE_SHA
    conn.execute(
        "INSERT INTO commits(sha, parent_sha, ts, author, message, indexed_at) "
        "VALUES(?,?,?,?,?,?) ON CONFLICT(sha) DO UPDATE SET indexed_at=excluded.indexed_at",
        (sha, get_meta(conn, "last_indexed_commit"), int(time.time()), "", "(working tree)", int(time.time())),
    )
    entries = discovery.iter_worktree(cfg)
    internal_roots = _internal_names(entries)
    present = {e.path for e in entries}

    prev_hashes = {
        row["path"]: row["content_hash"]
        for row in conn.execute(
            "SELECT f.path, fv.content_hash FROM file_versions fv "
            "JOIN files f ON f.id = fv.file_id WHERE fv.commit_sha=?",
            (sha,),
        )
    }
    for entry in entries:
        blob = discovery.read_worktree_bytes(cfg.root, entry.path)
        if blob is None:
            continue
        fid = _file_id(conn, entry.path, entry.lang, entry.tier)
        if prev_hashes.get(entry.path) == _sha1(blob):
            stats.files_skipped += 1
            continue
        pf = parse_source(entry.path, blob, spec_for_path(entry.path))
        status = "added" if entry.path not in prev_hashes else "modified"
        _write_parsed(conn, sha, fid, pf, blob, status, internal_roots)
        stats.files_parsed += 1
        if not pf.ok:
            stats.errors.append((entry.path, pf.error or "parse error"))

    for path in list(prev_hashes):
        if path not in present:
            row = conn.execute("SELECT id FROM files WHERE path=?", (path,)).fetchone()
            if row:
                _mark_deleted(conn, row["id"], sha)
    conn.commit()


# ---------------------------------------------------------------------------- scan


def scan(
    conn: sqlite3.Connection,
    cfg: Config,
    *,
    since: str | None = None,
    until: str = "HEAD",
) -> ScanStats:
    stats = ScanStats()
    root = cfg.root

    if not gitio.is_repo(root):
        _index_worktree(conn, cfg, stats)
        return stats

    if gitio.head(root) is None:
        return stats  # empty repo, nothing committed yet

    start = since or get_meta(conn, "last_indexed_commit")
    if start is not None:
        try:
            resolve_sha(root, start)
        except gitio.GitError:
            start = None  # stale/unknown ref — fall back to full history

    shas = gitio.rev_list(root, start, until)
    if not shas and start is None:
        shas = [resolve_sha(root, until)]

    from . import impact, intent, report, semdiff

    head_sha = resolve_sha(root, until)
    for sha in shas:
        index_commit(conn, cfg, sha, stats)
        stats.commits_indexed += 1
        meta = gitio.commit_meta(root, sha)
        intent.capture(conn, cfg, sha, meta.message, consume=(sha == head_sha))
        parent = parent_sha(conn, sha)
        if parent is None or _commit_indexed(conn, parent):
            changes = semdiff.diff_commits(conn, cfg, parent, sha, persist=True)
            impacts = impact.annotate(conn, cfg, parent, sha, changes)
            report.write_commit_file(conn, cfg, sha, impacts=impacts)
    conn.commit()

    from . import retention

    retention.prune(conn, cfg)
    return stats
