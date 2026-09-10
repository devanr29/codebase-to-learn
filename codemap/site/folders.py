"""Files -> a folded folder tree for the Folders surface of the explorer.

Pure function over pieces ``model.py::build()`` already assembled — no git, no
SQLite, nothing re-derived from the index. Given ``files[]``, ``file_edges[]``
and ``entry_points[]`` (see those fields' shapes in ``model.py``), :func:`build`
groups files by directory, folds directories that would otherwise clutter the
tree (single-child chains, near-empty leaves, a runaway monorepo width), and
annotates each surviving folder with the same rollups a file already carries
(loc, symbols, langs, deps) plus folder-to-folder import edges.

The tree-shaping rules (depth clamp, single-child collapse, sparse-node fold,
the 40-node cap) are spec'd in detail on each helper below; the short version
is: every real top-level folder (depth 1) and the synthetic ``"(root)"``
bucket for slash-less paths always survive, everything deeper only survives
if it carries enough of its own weight to be worth a row.

``reach`` is an **advisory** signal, not a verdict — "orphan" means "nothing
in the file-level import graph points here", which can just as easily mean
"loaded a way the graph can't see" (a config string, a dynamic import, a
router wired up outside the code) as it can mean genuinely dead. Callers
(the skill, a human) are the ones who get to call something dead code.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

ROOT = "(root)"
MAX_DEPTH = 4
CAP = 40

_KIND_RANK = {"third_party": 0, "stdlib": 1, "internal": 2}
_TEST_LIKE_RE = re.compile(r"(^|/)(tests?|__tests__|spec|e2e)(/|$)", re.IGNORECASE)

_ORPHAN_REASON = (
    "Nothing in this codebase's import graph points at this folder. That can mean "
    "it's unused, or that it's loaded in a way the graph can't see (a config string, "
    "a dynamic import, routing set up outside the code)."
)


@dataclass
class _Node:
    path: str
    depth: int
    parent: str | None
    children: set[str] = field(default_factory=set)
    files: list[int] = field(default_factory=list)  # direct file `fi`s, post-fold


def build(
    files: list[dict],
    file_edges: list[dict],
    entry_points: list[dict],
) -> list[dict]:
    """``files``/``file_edges``/``entry_points`` (see ``model.py::build()`` for
    their exact shape) -> one dict per surviving folder, sorted by ``path``
    with ``"(root)"`` first. See the module docstring for what "surviving"
    and ``reach`` mean."""
    nodes = _build_tree(files)
    _fold(nodes)
    _apply_cap(nodes, CAP)
    return _summarize(nodes, files, file_edges, entry_points)


# --------------------------------------------------------------------- tree


def _raw_dir(path: str) -> str:
    return path.rsplit("/", 1)[0] if "/" in path else ROOT


def _clamp(dir_path: str) -> str:
    """Step 2 — a directory deeper than ``MAX_DEPTH`` segments is treated as
    its ``MAX_DEPTH``-segment ancestor for every purpose (identity, file
    membership, edges)."""
    if dir_path == ROOT:
        return dir_path
    parts = dir_path.split("/")
    return "/".join(parts[:MAX_DEPTH])


def _chain(dir_path: str) -> list[str]:
    """``dir_path`` and every ancestor down to (and including) its depth-1
    segment, shallowest first. ``"(root)"`` is its own one-element chain."""
    if dir_path == ROOT:
        return [ROOT]
    parts = dir_path.split("/")
    return ["/".join(parts[:i]) for i in range(1, len(parts) + 1)]


def _build_tree(files: list[dict]) -> dict[str, _Node]:
    """Step 3 — every clamped directory plus its ancestors becomes a
    candidate node; each file is attached to its own (clamped) directory
    only, not to every ancestor along the way."""
    nodes: dict[str, _Node] = {}

    def ensure(path: str, depth: int, parent: str | None) -> _Node:
        n = nodes.get(path)
        if n is None:
            n = _Node(path=path, depth=depth, parent=parent)
            nodes[path] = n
        return n

    for fi, f in enumerate(files):
        clamped = _clamp(_raw_dir(f["path"]))
        chain = _chain(clamped)
        prev: str | None = None
        for depth, p in enumerate(chain, start=1):
            parent = None if depth == 1 else prev
            ensure(p, depth, parent)
            if prev is not None:
                nodes[prev].children.add(p)
            prev = p
        nodes[clamped].files.append(fi)

    return nodes


def _total_files(nodes: dict[str, _Node], path: str) -> int:
    n = nodes[path]
    return len(n.files) + sum(_total_files(nodes, c) for c in n.children)


def _fold(nodes: dict[str, _Node]) -> None:
    """Step 4 — bottom-up, to a fixed point: single-child collapse, then
    sparse-node fold. ``"(root)"`` and every depth-1 node are permanent and
    never considered. A node kept only because it has >=2 surviving
    children (a pure grouping folder) is left alone by both rules — neither
    one ever fires for it."""
    changed = True
    while changed:
        changed = False
        for path in sorted(nodes, key=lambda p: -nodes[p].depth):
            node = nodes.get(path)
            if node is None or path == ROOT or node.depth <= 1:
                continue
            parent_path = node.parent
            if parent_path is None:
                continue  # unreachable for depth > 1, but keep the tree safe

            if len(node.children) == 1 and not node.files:
                (child_path,) = node.children
                nodes[child_path].parent = parent_path
                parent = nodes[parent_path]
                parent.children.discard(path)
                parent.children.add(child_path)
                del nodes[path]
                changed = True
                continue

            if _total_files(nodes, path) < 2:
                parent = nodes[parent_path]
                parent.files.extend(node.files)
                for child_path in node.children:
                    nodes[child_path].parent = parent_path
                    parent.children.add(child_path)
                parent.children.discard(path)
                del nodes[path]
                changed = True


def _apply_cap(nodes: dict[str, _Node], cap: int) -> None:
    """Step 5 — a monorepo guard: fold the smallest surviving leaf into its
    parent (ties broken by path) until at most ``cap`` nodes remain, or
    nothing foldable (only depth-1 nodes) is left."""
    while len(nodes) > cap:
        leaves = [p for p, n in nodes.items() if p != ROOT and n.depth > 1 and not n.children]
        if not leaves:
            break
        victim = min(leaves, key=lambda p: (_total_files(nodes, p), p))
        node = nodes.pop(victim)
        parent = nodes[node.parent]  # type: ignore[index]
        parent.files.extend(node.files)
        parent.children.discard(victim)


# ------------------------------------------------------------------ summary


def _entry_owning_files(files: list[dict], entry_points: list[dict]) -> set[int]:
    node_to_file: dict[int, int] = {}
    for fi, f in enumerate(files):
        for ni in f.get("symbols", []):
            node_to_file[ni] = fi
    owning: set[int] = set()
    for ep in entry_points:
        ni = ep.get("node")
        if ni is None:
            continue
        fi = node_to_file.get(ni)
        if fi is not None:
            owning.add(fi)
    return owning


def _folder_deps(files: list[dict], member_fis: list[int]) -> list[dict]:
    agg: dict[str, dict] = {}
    for fi in member_fis:
        for d in files[fi].get("deps", []):
            entry = agg.setdefault(d["name"], {"kind": d["kind"], "count": 0})
            entry["count"] += 1
    return [
        {"name": name, "kind": e["kind"], "count": e["count"]}
        for name, e in sorted(agg.items(), key=lambda p: (_KIND_RANK[p[1]["kind"]], p[0]))
    ]


def _summarize(
    nodes: dict[str, _Node],
    files: list[dict],
    file_edges: list[dict],
    entry_points: list[dict],
) -> list[dict]:
    # Step 6 — the final file -> folder assignment, read straight off the
    # post-fold tree (never re-derived by string-matching paths).
    fi_to_folder: dict[int, str] = {
        fi: path for path, node in nodes.items() for fi in node.files
    }

    folder_imports: dict[str, set[str]] = {}
    folder_imported_by: dict[str, set[str]] = {}
    has_inbound: dict[str, bool] = {}
    for e in file_edges:
        fs, ft = fi_to_folder.get(e["s"]), fi_to_folder.get(e["t"])
        if fs is None or ft is None or fs == ft:
            continue
        folder_imports.setdefault(fs, set()).add(ft)
        folder_imported_by.setdefault(ft, set()).add(fs)
        has_inbound[ft] = True

    entry_owning_files = _entry_owning_files(files, entry_points)

    out: list[dict] = []
    for path, node in nodes.items():
        member_fis = sorted(node.files)
        symbols = sorted({s for fi in member_fis for s in files[fi].get("symbols", [])})
        langs = sorted({files[fi]["lang"] for fi in member_fis})

        has_entry = any(fi in entry_owning_files for fi in member_fis)
        is_test_like = bool(_TEST_LIKE_RE.search(path))
        if has_entry or is_test_like:
            reach, orphan_reason = "root", None
        elif has_inbound.get(path, False):
            reach, orphan_reason = "reached", None
        else:
            reach, orphan_reason = "orphan", _ORPHAN_REASON

        out.append(
            {
                "path": path,
                "name": ROOT if path == ROOT else path.rsplit("/", 1)[-1],
                "parent": node.parent if node.depth > 1 else None,
                "depth": node.depth,
                "files": member_fis,
                "file_count": len(member_fis),
                "total_files": _total_files(nodes, path),
                "loc": sum(files[fi]["loc"] for fi in member_fis),
                "symbols": symbols,
                "symbol_count": len(symbols),
                "langs": langs,
                "deps": _folder_deps(files, member_fis),
                "imports": sorted(folder_imports.get(path, ())),
                "imported_by": sorted(folder_imported_by.get(path, ())),
                "reach": reach,
                "orphan_reason": orphan_reason,
            }
        )

    out.sort(key=lambda r: (0 if r["path"] == ROOT else 1, r["path"]))
    return out
