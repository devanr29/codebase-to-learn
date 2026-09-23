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
import re
import sqlite3
from dataclasses import dataclass, field

import networkx as nx

from . import resolve
from .config import Config
from .languages.registry import spec_for_path

EXTRACTED, INFERRED, AMBIGUOUS = "EXTRACTED", "INFERRED", "AMBIGUOUS"

# Same two patterns codemap/site/architecture.py and folders.py use to keep a
# test file out of the Architecture tests layer / "Hide tests" — duplicated
# here (rather than imported) because impact.py is core and those two live
# under site/, which depends on core, not the other way around.
_TEST_LIKE_RE = re.compile(r"(^|/)(tests?|__tests__|spec|e2e)(/|$)", re.IGNORECASE)
_TEST_STEM_RE = re.compile(r"^(test_.*|.*_test|conftest)$|\.(test|spec)$")

# Grammars grouped so a call can resolve across file extensions that are
# really the same language (a .ts calling into a .js helper), but never
# across genuinely different ones (Python calling "into" TypeScript just
# because both happen to define a method of the same name).
_LANG_FAMILY = {
    "javascript": "js", "typescript": "js", "tsx": "js",
    "python": "py", "go": "go", "rust": "rust", "java": "java",
    "csharp": "csharp", "ruby": "ruby", "php": "php", "c": "c", "cpp": "cpp",
}

# Built-in container/IO/collection method names that exist on countless
# unrelated types (dict.get, list.append, a Promise.then, str.split, a
# response object's .json()...). Without type inference there's no way to
# tell a call to one of these apart from a genuine same-named repo method, so
# a receiver that isn't self/a resolved class name never matches one of
# these — even with import evidence. A real hit stays reachable through a
# direct self/Class.method reference; a missed one is far cheaper than the
# false "everyone calls WalletClient.get" edge this list exists to prevent.
_STOP_METHOD_NAMES = frozenset(
    """get set add remove update items keys values pop popitem append extend
    insert clear copy count index sort reverse read write open close send
    recv post put patch delete json text encode decode split join strip
    lstrip rstrip format replace find match search run start stop next
    value wait done result cancel then catch finally push shift unshift
    slice splice map filter reduce forEach toString valueOf
    hasOwnProperty""".split()
)

_PY_ALIAS_RE = re.compile(
    r"^from\s+[\w.]+\s+import\s+(?P<name>\w+)\s+as\s+(?P<alias>\w+)\s*$"
)

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


def _imports_by_file(
    conn: sqlite3.Connection, sha: str, aliases: list[resolve.TsAlias] | None = None
) -> dict[str, set[str]]:
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
    for ri in resolve.resolve_imports(pairs, paths, aliases=aliases):
        if ri.target:
            out.setdefault(ri.importer, set()).add(ri.target)
    return out


def _is_test_path(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    stem = name.rsplit(".", 1)[0] if "." in name else name
    parent = path.rsplit("/", 1)[0] if "/" in path else ""
    return bool(_TEST_LIKE_RE.search(parent) or _TEST_STEM_RE.match(stem.lower()))


def _owner_of(key: str) -> str | None:
    """The class/namespace a method key belongs to — ``"WalletClient"`` for
    ``file.py::WalletClient.get``, ``None`` for a bare top-level function."""
    q = _qualname(key)
    return q.rsplit(".", 1)[0] if "." in q else None


def _py_import_aliases(conn: sqlite3.Connection, sha: str) -> dict[str, dict[str, str]]:
    """Python ``from X import Y as Z`` aliases at ``sha``, as
    ``{importer file path: {local alias Z: original name Y}}`` — lets a bare
    call to ``_ok(...)`` (``from api_common import ok as _ok``) redirect to
    the symbol actually named ``ok``, instead of never matching anything
    (and, on the explorer's Simulate tab, being mistaken for dynamic
    dispatch). Module aliases (``import numpy as np``) aren't included —
    there's no single symbol to redirect *to*, only a file, which the
    ordinary import-evidence path already covers via the bare name itself."""
    out: dict[str, dict[str, str]] = {}
    for r in conn.execute(
        "SELECT f.path AS path, i.raw AS raw FROM imports i "
        "JOIN files f ON f.id = i.file_id WHERE i.commit_sha = ? AND f.lang = 'python'",
        (sha,),
    ):
        m = _PY_ALIAS_RE.match(r["raw"])
        if m and m.group("name") != m.group("alias"):
            out.setdefault(r["path"], {})[m.group("alias")] = m.group("name")
    return out


def call_graph(
    conn: sqlite3.Connection, sha: str, aliases: list[resolve.TsAlias] | None = None
) -> nx.DiGraph:
    """Directed graph of symbol keys; an edge ``caller -> callee`` for every
    reference that resolves, each carrying a ``confidence`` (spec M15,
    receiver-aware resolution): unlike the tier a symbol's *language* gets
    (``languages/registry.py``), this is a hint for the UI, not a filter —
    Graph shows a low-confidence edge dashed, blast-radius / Simulate leave
    it out, but it still counts as "reachable" for someone reading the raw
    graph. What determines whether an edge exists **at all** is
    ``refs.receiver`` (``parsing._receiver_of`` — what the call was made
    *on*), which is the actual fix for the false-positive problem tier alone
    never solved: two same-named methods on unrelated classes, or a
    dict/response/string method matched against a same-named repo function,
    used to both resolve as confidently as a real same-file call.

    - **receiver "self"/cls/this/...**: only a method of the *same class*,
      in the same file, ever resolves — ``EXTRACTED``. No class -> no edge.
    - **receiver "N:X"** (a capitalized name — a class, or a module alias):
      only a symbol literally owned by ``X`` (``X.<name>``), reached either
      in the same file (``EXTRACTED``) or through this file's own import
      evidence (``INFERRED``/``AMBIGUOUS``). No such match falls through to
      the next rule instead of guessing further.
    - **receiver "v:x" / "x"** (a local variable, or any other expression —
      no type information to go on): import evidence only, **never** a
      repo-wide guess, and not at all for a name on ``_STOP_METHOD_NAMES``
      (``.get``, `.then`, `.json`, ...) — those exist on too many unrelated
      types to trust without knowing what the receiver actually is.
    - **receiver "-" (bare call, or a pre-schema-v3 row with no receiver
      recorded yet)**: the original three tiers, unchanged — same-file
      ``EXTRACTED``, then import-evidence ``INFERRED``/``AMBIGUOUS``, then a
      repo-wide ``AMBIGUOUS`` guess. A bare name has no object to be precise
      about, so this stays the permissive fallback it always was.

    Every rule above also requires: the candidate is in the same *language
    family* as the caller (``_LANG_FAMILY`` — a .ts calling a .js helper is
    fine, a .py "calling" a .ts method by coincidence of name is not), and
    non-test code never resolves into a test file (test code may still call
    into production or other test code freely).
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

    imports_by_file = _imports_by_file(conn, sha, aliases=aliases)
    py_aliases = _py_import_aliases(conn, sha)

    _fam_cache: dict[str, str | None] = {}
    _test_cache: dict[str, bool] = {}

    def fam(path: str) -> str | None:
        if path not in _fam_cache:
            spec = spec_for_path(path)
            _fam_cache[path] = _LANG_FAMILY.get(spec.name) if spec else None
        return _fam_cache[path]

    def is_test(path: str) -> bool:
        if path not in _test_cache:
            _test_cache[path] = _is_test_path(path)
        return _test_cache[path]

    for r in conn.execute(
        "SELECT from_symbol_id, target_name, receiver FROM refs "
        "WHERE commit_sha = ? AND from_symbol_id IS NOT NULL",
        (sha,),
    ):
        src = id_to_key.get(r["from_symbol_id"])
        if src is None:
            continue
        target_name = r["target_name"]
        receiver = r["receiver"] or "-"  # NULL = a pre-v3 row, resolved the old way
        src_path = _path_of(src)

        candidates_all = [k for k in name_to_keys.get(target_name, ()) if k != src]
        alias_only = False
        if not candidates_all and receiver == "-":
            original = py_aliases.get(src_path, {}).get(target_name)
            if original:
                candidates_all = [k for k in name_to_keys.get(original, ()) if k != src]
                alias_only = True
        if not candidates_all:
            continue

        src_fam = fam(src_path)
        src_is_test = is_test(src_path)
        candidates = [
            k for k in candidates_all
            if fam(_path_of(k)) == src_fam and (src_is_test or not is_test(_path_of(k)))
        ]
        if not candidates:
            continue

        if alias_only:
            # only valid through the one import that named it — no same-file
            # guess (if it were same-file, the plain name would've matched
            # already) and no repo-wide fallback.
            imported = imports_by_file.get(src_path, ())
            via_import = [k for k in candidates if _path_of(k) in imported]
            if len(via_import) == 1:
                g.add_edge(src, via_import[0], confidence=INFERRED)
            continue

        if receiver == "self":
            owner = _owner_of(src)
            if owner is not None:
                for dst in candidates:
                    if _path_of(dst) == src_path and _owner_of(dst) == owner:
                        g.add_edge(src, dst, confidence=EXTRACTED)
            continue

        if receiver.startswith("N:"):
            obj = receiver[2:]
            direct = [k for k in candidates if _owner_of(k) == obj]
            same_file_direct = [k for k in direct if _path_of(k) == src_path]
            if same_file_direct:
                for dst in same_file_direct:
                    g.add_edge(src, dst, confidence=EXTRACTED)
                continue
            imported = imports_by_file.get(src_path, ())
            via_import_direct = [k for k in direct if _path_of(k) in imported]
            if via_import_direct:
                confidence = INFERRED if len(via_import_direct) == 1 else AMBIGUOUS
                for dst in via_import_direct:
                    g.add_edge(src, dst, confidence=confidence)
                continue
            receiver = "x"  # no "<Name>.<method>" match — fall through below

        if receiver == "-":
            same_file = [k for k in candidates if _path_of(k) == src_path]
            if same_file:
                for dst in same_file:
                    g.add_edge(src, dst, confidence=EXTRACTED)
                continue
            imported = imports_by_file.get(src_path, ())
            via_import = [k for k in candidates if _path_of(k) in imported]
            if via_import:
                confidence = INFERRED if len(via_import) == 1 else AMBIGUOUS
                for dst in via_import:
                    g.add_edge(src, dst, confidence=confidence)
                continue
            for dst in candidates:
                g.add_edge(src, dst, confidence=AMBIGUOUS)
            continue

        # "v:x" / "x" / an "N:X" with no direct match: import evidence only,
        # never a repo-wide guess, and never at all for a stoplisted name —
        # see the docstring above.
        if target_name in _STOP_METHOD_NAMES:
            continue
        imported = imports_by_file.get(src_path, ())
        via_import = [k for k in candidates if _path_of(k) in imported]
        if via_import:
            confidence = INFERRED if len(via_import) == 1 else AMBIGUOUS
            for dst in via_import:
                g.add_edge(src, dst, confidence=confidence)
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
