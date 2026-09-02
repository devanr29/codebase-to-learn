"""Resolve raw import statements to in-repo file paths — a query-time T2 pass.

The indexer stores ``imports.resolved_file_id`` as NULL (see
``indexer._write_parsed``). This module recovers file -> file import edges on
demand for the explorer, **without touching the incremental index path**, so the
from-scratch / incremental equality contract in ``tests/test_incremental.py`` is
unaffected. It can later be promoted into ``_write_parsed`` to fill the column.

Resolution is best-effort and structural only: dynamic imports, path aliases
(``@/components/...``) and re-export barrels are blind spots, same as the rest
of codemap's graph.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath

from . import discovery
from .indexer import (
    _internal_names,
    classify_import,
    is_csharp_stdlib,
    is_go_stdlib,
    is_java_stdlib,
    is_rust_stdlib,
    is_ruby_stdlib,
    is_stdlib,
)
from .languages.registry import spec_for_path

_PY_EXTS = (".py", ".pyi")
_TS_EXTS = (".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs")

# Node's core modules — classify_import has no stdlib awareness for JS/TS, so a
# bare `import fs from "fs"` would otherwise read as a third-party package.
_NODE_BUILTINS = frozenset(
    """assert async_hooks buffer child_process cluster console constants crypto
    dgram diagnostics_channel dns domain events fs http http2 https inspector
    module net os path perf_hooks process punycode querystring readline repl
    stream string_decoder sys timers tls trace_events tty url util v8 vm
    wasi worker_threads zlib""".split()
)


@dataclass(frozen=True)
class ResolvedImport:
    importer: str            # repo-relative posix path of the importing file
    raw: str                 # the normalized import statement
    target: str | None       # repo-relative path it resolves to, else None
    external: bool            # a genuine third-party dependency
    kind: str = "internal"   # "internal" | "stdlib" | "third_party"
    module: str = ""          # the classified module string (e.g. "os.path", "./loader")


def _entries(paths: set[str]) -> list[discovery.FileEntry]:
    out: list[discovery.FileEntry] = []
    for p in paths:
        spec = spec_for_path(p)
        out.append(discovery.FileEntry(path=p, lang=spec.name if spec else "", tier=0))
    return out


def _py_candidates(mod: str, importer: str, raw: str) -> list[str]:
    importer_pkg = PurePosixPath(importer).parent
    ups = len(mod) - len(mod.lstrip("."))
    if ups:
        rest = mod[ups:]
        if not rest:
            # `from . import name[, ...]` — take the first imported name
            after = raw.split(" import ", 1)
            if len(after) == 2:
                rest = after[1].split(",")[0].strip().split(" as ")[0].strip().lstrip("(")
        base = importer_pkg
        for _ in range(ups - 1):
            base = base.parent
        parts = [p for p in rest.split(".") if p]
        stems = ["/".join([*str(base).split("/"), *parts])] if parts else [str(base)]
    else:
        parts = mod.split(".")
        stems = ["/".join(parts)]
        if len(parts) > 1:
            stems.append("/".join(parts[:-1]))  # `from pkg.mod import name`

    out: list[str] = []
    for stem in stems:
        stem = stem.lstrip("/")
        if not stem or stem == ".":
            continue
        for ext in _PY_EXTS:
            out.append(f"{stem}{ext}")
        out.append(f"{stem}/__init__.py")
    return out


def _ts_candidates(mod: str, importer: str) -> list[str]:
    if not mod.startswith("."):
        return []
    target = PurePosixPath(importer).parent
    for part in mod.split("/"):
        if part in ("", "."):
            continue
        target = target.parent if part == ".." else target / part
    stem = str(target)
    if stem in ("", "."):
        return []
    return [f"{stem}{ext}" for ext in _TS_EXTS] + [f"{stem}/index{ext}" for ext in _TS_EXTS]


def resolve_imports(
    pairs, file_paths: set[str] | frozenset[str]
) -> list[ResolvedImport]:
    """``pairs``: iterable of ``(importer_path, raw)``. First candidate that
    exists among ``file_paths`` wins, mirroring how the rest of codemap resolves
    ambiguity (first match, deterministic order)."""
    paths = set(file_paths)
    internal = _internal_names(_entries(paths))
    out: list[ResolvedImport] = []
    for importer, raw in pairs:
        spec = spec_for_path(importer)
        lang = spec.name if spec else "python"
        mod, external = classify_import(raw, lang, internal)
        kind = _kind_of(mod, lang, external, internal)
        if external or not mod:
            out.append(ResolvedImport(importer, raw, None, bool(external), kind, mod))
            continue
        cands = (
            _py_candidates(mod, importer, raw)
            if lang == "python"
            else _ts_candidates(mod, importer)
        )
        target = next((c for c in cands if c in paths and c != importer), None)
        out.append(ResolvedImport(importer, raw, target, False, kind, mod))
    return out


def _kind_of(
    mod: str, lang: str, external: bool, internal_names: set[str] | None = None
) -> str:
    """Bucket a classified import: relative and in-repo -> internal, the
    language's own standard library -> stdlib, everything else -> third_party.

    ``internal_names`` (optional, defaults to none matching) disambiguates a
    same-repo import from a real stdlib one where the two heuristics could
    otherwise collide — chiefly Go, whose "no dot in the first path segment"
    stdlib signal also matches an internal package path under a module name
    that isn't itself domain-shaped (``module myrepo`` rather than
    ``module github.com/user/myrepo``, both legal ``go.mod`` forms).
    """
    if not mod or mod.startswith("."):
        return "internal"
    names = internal_names or set()
    top = mod.lstrip("@").split("/")[0].split(".")[0]
    if lang == "python":
        return "third_party" if external else ("stdlib" if is_stdlib(mod) else "internal")
    if lang in ("typescript", "javascript", "tsx"):
        if top in _NODE_BUILTINS or mod.startswith("node:"):
            return "stdlib"
        return "third_party" if external else "internal"
    if lang == "go":
        if mod.rsplit("/", 1)[-1] in names:
            return "internal"
        return "stdlib" if is_go_stdlib(mod) else ("third_party" if external else "internal")
    if lang == "rust":
        if mod.split("::", 1)[0] in names:
            return "internal"
        return "stdlib" if is_rust_stdlib(mod) else ("third_party" if external else "internal")
    if lang == "java":
        return "stdlib" if is_java_stdlib(mod) else ("third_party" if external else "internal")
    if lang == "csharp":
        return "stdlib" if is_csharp_stdlib(mod) else ("third_party" if external else "internal")
    if lang == "ruby":
        return "stdlib" if is_ruby_stdlib(mod) else ("third_party" if external else "internal")
    if lang in ("c", "cpp"):
        return "internal"  # classify_import never marks a C/C++ include external — see there
    # php and anything else unlisted: no stdlib concept modeled
    return "third_party" if external else "internal"
