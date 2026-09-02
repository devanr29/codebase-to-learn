"""Semantic diff between two consecutive commit graphs (spec M4).

A change is a statement about two graphs: what symbols/files/imports differ, and
how much it matters. Classification is deterministic — no LLM, no fuzzy scoring.

Severity (spec section 8):
  structural  — signature change, add/remove of a symbol with callers, a
                move, a dependency change, a file add/remove
  behavioral  — a body change (normalized hash differs); also the fallback for
                an add/remove with no callers
  cosmetic    — normalized hash unchanged (whitespace / comments only), or a
                rename whose references were all updated in the same commit, or
                a body change fully explained by such a rename
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from . import gitio, normalize
from .config import Config
from .indexer import WORKTREE_SHA, classify_import
from .languages.registry import spec_for_path

STRUCTURAL, BEHAVIORAL, COSMETIC = "structural", "behavioral", "cosmetic"


@dataclass
class Change:
    change_type: str
    severity: str
    subject: str                 # symbol key, "old -> new", file path, or module
    file_path: str | None = None
    symbol_key: str | None = None
    details: dict = field(default_factory=dict)

    def as_tuple(self) -> tuple[str, str, str]:
        return (self.change_type, self.subject, self.severity)


# --------------------------------------------------------------------------- state


@dataclass
class _Sym:
    key: str
    name: str
    kind: str
    signature: str
    body_hash: str
    raw_hash: str
    decorators: str | None
    start_line: int
    end_line: int


@dataclass
class _State:
    symbols: dict[str, _Sym]
    files: set[str]
    imports: set[tuple[str, str]]          # (path, raw)
    deps: set[str]
    callers_by_name: dict[str, int]


def _path_of(key: str) -> str:
    return key.split("::", 1)[0]


def _dep_name(raw: str, path: str, roots: set[str]) -> str | None:
    spec = spec_for_path(path)
    lang = spec.name if spec else "python"
    mod, external = classify_import(raw, lang, roots)
    if not external or not mod:
        return None
    if mod.startswith("@"):
        parts = mod.split("/")
        return "/".join(parts[:2]) if len(parts) >= 2 else mod
    if "/" in mod:                       # ts bare specifier with subpath
        return mod.split("/")[0]
    return mod.split(".")[0]             # python dotted


def _load_state(conn: sqlite3.Connection, sha: str | None) -> _State:
    if sha is None:
        return _State({}, set(), set(), set(), {})

    symbols = {
        r["key"]: _Sym(
            key=r["key"],
            name=r["name"],
            kind=r["kind"],
            signature=r["signature"],
            body_hash=r["body_hash"],
            raw_hash=r["raw_hash"],
            decorators=r["decorators"],
            start_line=r["start_line"],
            end_line=r["end_line"],
        )
        for r in conn.execute(
            "SELECT s.key, s.name, s.kind, sv.signature, sv.body_hash, sv.raw_hash, "
            "sv.decorators, sv.start_line, sv.end_line "
            "FROM symbol_versions sv JOIN symbols s ON s.id = sv.symbol_id "
            "WHERE sv.commit_sha = ?",
            (sha,),
        )
    }
    files = {
        r["path"]
        for r in conn.execute(
            "SELECT f.path FROM file_versions fv JOIN files f ON f.id = fv.file_id "
            "WHERE fv.commit_sha = ? AND fv.status != 'deleted'",
            (sha,),
        )
    }
    roots: set[str] = set()
    for p in files:
        parts = p.split("/")
        roots.add(parts[0] if len(parts) > 1 else Path(parts[0]).stem)

    imports: set[tuple[str, str]] = set()
    deps: set[str] = set()
    for r in conn.execute(
        "SELECT f.path AS path, i.raw AS raw, i.external AS external "
        "FROM imports i JOIN files f ON f.id = i.file_id WHERE i.commit_sha = ?",
        (sha,),
    ):
        imports.add((r["path"], r["raw"]))
        if r["external"]:
            name = _dep_name(r["raw"], r["path"], roots)
            if name:
                deps.add(name)

    callers = {
        r["target_name"]: r["n"]
        for r in conn.execute(
            "SELECT target_name, COUNT(*) AS n FROM refs WHERE commit_sha = ? "
            "GROUP BY target_name",
            (sha,),
        )
    }
    return _State(symbols, files, imports, deps, callers)


# ----------------------------------------------------------------------- diffing


def _lines(src: bytes, start: int, end: int) -> bytes:
    return b"\n".join(src.split(b"\n")[max(start - 1, 0) : end])


def _read_source(cfg: Config, sha: str, path: str) -> bytes | None:
    """``gitio.show_bytes`` for a real commit; a worktree read for the live
    pseudo-commit (spec M15) — ``git show worktree:<path>`` is not a thing."""
    if sha == WORKTREE_SHA:
        from .discovery import read_worktree_bytes

        return read_worktree_bytes(cfg.root, path)
    return gitio.show_bytes(cfg.root, sha, path)


def _is_rename_fallout(
    cfg: Config, prev_sha: str, curr_sha: str, ps: _Sym, cs: _Sym, renames: dict[str, str]
) -> bool:
    path = _path_of(cs.key)
    spec = spec_for_path(path)
    if spec is None:
        return False
    prev_src = _read_source(cfg, prev_sha, _path_of(ps.key))
    curr_src = _read_source(cfg, curr_sha, path)
    if not prev_src or not curr_src:
        return False
    pt = normalize.tokens_from_bytes(_lines(prev_src, ps.start_line, ps.end_line), spec)
    ct = normalize.tokens_from_bytes(_lines(curr_src, cs.start_line, cs.end_line), spec)
    return [renames.get(t, t) for t in pt] == ct


def diff_commits(
    conn: sqlite3.Connection,
    cfg: Config,
    prev_sha: str | None,
    curr_sha: str,
    *,
    persist: bool = True,
) -> list[Change]:
    prev = _load_state(conn, prev_sha)
    curr = _load_state(conn, curr_sha)
    changes: list[Change] = []

    # --- files --------------------------------------------------------------
    for p in sorted(curr.files - prev.files):
        changes.append(Change("file_added", STRUCTURAL, p, file_path=p))
    for p in sorted(prev.files - curr.files):
        changes.append(Change("file_removed", STRUCTURAL, p, file_path=p))

    added = set(curr.symbols) - set(prev.symbols)
    removed = set(prev.symbols) - set(curr.symbols)
    common = set(curr.symbols) & set(prev.symbols)
    used_add: set[str] = set()
    used_rem: set[str] = set()
    renames: dict[str, str] = {}

    # --- moves: same name + normalized body, different file --------------
    for r in sorted(removed):
        rs = prev.symbols[r]
        for a in sorted(added - used_add):
            cs = curr.symbols[a]
            if cs.name == rs.name and cs.body_hash == rs.body_hash and _path_of(a) != _path_of(r):
                changes.append(
                    Change(
                        "symbol_moved", STRUCTURAL, f"{r} -> {a}",
                        file_path=_path_of(a), symbol_key=a,
                        details={"from": r, "to": a},
                    )
                )
                used_add.add(a)
                used_rem.add(r)
                break

    # --- renames: same file + normalized body, different name -----------
    for r in sorted(removed - used_rem):
        rs = prev.symbols[r]
        for a in sorted(added - used_add):
            cs = curr.symbols[a]
            if _path_of(a) == _path_of(r) and cs.body_hash == rs.body_hash and cs.name != rs.name:
                refs_left = curr.callers_by_name.get(rs.name, 0)
                sev = COSMETIC if refs_left == 0 else STRUCTURAL
                changes.append(
                    Change(
                        "renamed", sev, f"{r} -> {cs.name}",
                        file_path=_path_of(a), symbol_key=a,
                        details={"from_name": rs.name, "to_name": cs.name,
                                 "from_key": r, "to_key": a, "dangling_refs": refs_left},
                    )
                )
                renames[rs.name] = cs.name
                used_add.add(a)
                used_rem.add(r)
                break

    # --- plain adds / removes -----------------------------------------
    for a in sorted(added - used_add):
        cs = curr.symbols[a]
        has_callers = curr.callers_by_name.get(cs.name, 0) > 0
        changes.append(
            Change(
                "symbol_added", STRUCTURAL if has_callers else BEHAVIORAL, a,
                file_path=_path_of(a), symbol_key=a,
                details={"kind": cs.kind, "signature": cs.signature, "has_callers": has_callers},
            )
        )
    for r in sorted(removed - used_rem):
        rs = prev.symbols[r]
        has_callers = prev.callers_by_name.get(rs.name, 0) > 0
        changes.append(
            Change(
                "symbol_removed", STRUCTURAL if has_callers else BEHAVIORAL, r,
                file_path=_path_of(r), symbol_key=r,
                details={"kind": rs.kind, "has_callers": has_callers},
            )
        )

    # --- modified symbols present in both graphs ----------------------
    for k in sorted(common):
        ps, cs = prev.symbols[k], curr.symbols[k]
        if ps.signature != cs.signature:
            changes.append(
                Change("signature_changed", STRUCTURAL, k, file_path=_path_of(k), symbol_key=k,
                       details={"from": ps.signature, "to": cs.signature})
            )
            continue
        if ps.body_hash != cs.body_hash:
            if renames and _is_rename_fallout(cfg, prev_sha, curr_sha, ps, cs, renames):
                sev = COSMETIC
                details = {"rename_fallout": True}
            else:
                has_callers = (
                    curr.callers_by_name.get(cs.name, 0) > 0
                    or prev.callers_by_name.get(ps.name, 0) > 0
                )
                sev = BEHAVIORAL
                details = {"has_callers": has_callers}
            changes.append(
                Change("body_changed", sev, k, file_path=_path_of(k), symbol_key=k, details=details)
            )
            continue
        if ps.raw_hash != cs.raw_hash:
            changes.append(
                Change("body_changed", COSMETIC, k, file_path=_path_of(k), symbol_key=k,
                       details={"whitespace_or_comments": True})
            )
            continue
        if (ps.decorators or "") != (cs.decorators or ""):
            changes.append(
                Change("signature_changed", STRUCTURAL, k, file_path=_path_of(k), symbol_key=k,
                       details={"decorators_from": ps.decorators, "decorators_to": cs.decorators})
            )

    # --- imports & dependencies -------------------------------------
    for path, raw in sorted(curr.imports - prev.imports):
        changes.append(Change("import_added", COSMETIC, path, file_path=path, details={"raw": raw}))
    for path, raw in sorted(prev.imports - curr.imports):
        changes.append(Change("import_removed", COSMETIC, path, file_path=path, details={"raw": raw}))
    for m in sorted(curr.deps - prev.deps):
        changes.append(Change("dependency_added", STRUCTURAL, m, details={"module": m}))
    for m in sorted(prev.deps - curr.deps):
        changes.append(Change("dependency_removed", STRUCTURAL, m, details={"module": m}))

    if persist:
        _persist(conn, curr_sha, changes)
    return changes


# ---------------------------------------------------------------------- storage


def _persist(conn: sqlite3.Connection, sha: str, changes: list[Change]) -> None:
    conn.execute("DELETE FROM changes WHERE commit_sha = ?", (sha,))
    for c in changes:
        sid = None
        if c.symbol_key:
            row = conn.execute("SELECT id FROM symbols WHERE key = ?", (c.symbol_key,)).fetchone()
            sid = row["id"] if row else None
        fid = None
        if c.file_path:
            row = conn.execute("SELECT id FROM files WHERE path = ?", (c.file_path,)).fetchone()
            fid = row["id"] if row else None
        payload = dict(c.details)
        payload["subject"] = c.subject
        conn.execute(
            "INSERT INTO changes(commit_sha, symbol_id, file_id, change_type, severity, details_json) "
            "VALUES(?,?,?,?,?,?)",
            (sha, sid, fid, c.change_type, c.severity, json.dumps(payload, sort_keys=True)),
        )
    conn.commit()


def load_changes(conn: sqlite3.Connection, sha: str) -> list[Change]:
    out: list[Change] = []
    for r in conn.execute(
        "SELECT c.change_type, c.severity, c.details_json, s.key AS skey, f.path AS fpath "
        "FROM changes c LEFT JOIN symbols s ON s.id = c.symbol_id "
        "LEFT JOIN files f ON f.id = c.file_id WHERE c.commit_sha = ? ORDER BY c.id",
        (sha,),
    ):
        details = json.loads(r["details_json"]) if r["details_json"] else {}
        subject = details.pop("subject", r["skey"] or r["fpath"] or "")
        out.append(
            Change(
                change_type=r["change_type"],
                severity=r["severity"],
                subject=subject,
                file_path=r["fpath"],
                symbol_key=r["skey"],
                details=details,
            )
        )
    return out


# --------------------------------------------------------------------- rendering


_SEV_ORDER = {STRUCTURAL: 0, BEHAVIORAL: 1, COSMETIC: 2}


def _impact_lines(change: Change, impacts: dict | None) -> list[str]:
    if impacts is not None and change.symbol_key in impacts:
        from .impact import render_lines

        return render_lines(impacts[change.symbol_key])
    summ = change.details.get("impact")
    if not summ:
        return []
    n = summ.get("callers", 0)
    if not n:
        if summ.get("resolvable", True):
            return ["Impact: no callers found"]
        return [f"Impact: callers not resolvable at tier {summ.get('tier', 1)}"]
    u = summ.get("unmodified", 0)
    tail = f" — {summ.get('modified', 0)} updated, {u} unchanged (worth checking)" if u else ""
    out = [f"Impact: {n} caller(s){tail}"]
    if summ.get("entry_path"):
        out.append(f"On path from: {summ['entry_path']}")
    return out


def print_breakdown(sha: str, changes: list[Change], impacts: dict | None = None) -> None:
    by_sev: dict[str, int] = {STRUCTURAL: 0, BEHAVIORAL: 0, COSMETIC: 0}
    for c in changes:
        by_sev[c.severity] = by_sev.get(c.severity, 0) + 1

    print(f"{sha[:7]} — {len(changes)} change(s)")
    print(f"  structural {by_sev[STRUCTURAL]}   behavioral {by_sev[BEHAVIORAL]}   cosmetic {by_sev[COSMETIC]}")
    for c in sorted(changes, key=lambda x: (_SEV_ORDER.get(x.severity, 9), x.change_type, x.subject)):
        if c.severity == COSMETIC:
            continue
        extra = ""
        if c.change_type == "signature_changed":
            extra = f"  ({c.details.get('from')}  ->  {c.details.get('to')})"
        elif c.change_type == "dependency_added":
            extra = f"  ({c.details.get('module')})"
        print(f"  [{c.severity}] {c.change_type}  {c.subject}{extra}")
        for line in _impact_lines(c, impacts):
            print(f"      {line}")
    if by_sev[COSMETIC]:
        print(f"  cosmetic: {by_sev[COSMETIC]} change(s) (not expanded)")
