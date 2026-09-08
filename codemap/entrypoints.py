"""Entry-point heuristics (spec M5).

An entry point is where execution enters the codebase from outside: an HTTP
route, a CLI command, a task-queue handler, a ``__main__`` guard, a
``[project.scripts]`` console script, a frontend screen a user navigates to.
Detection is heuristic and per-language; adding a framework means adding a
pattern here.

Every detected entry point is tied to a symbol so it can anchor a reachability
path in ``impact.py``.

Frontend detection (``frontend_roots`` / ``from_route_file`` / ``from_source``)
covers the JS/TS ecosystem broadly rather than one framework: file-based
routing (expo-router, Next.js app + pages router, Remix / React Router v7 flat
routes) is path-based and deterministic — the reliable half. Component
*registration* (React Navigation ``<Stack.Screen>``, an app root like
``AppRegistry.registerComponent``) is regex-over-source and best-effort: a
screen assembled in a loop, or imported from another file, is invisible to it.
Both are gated on a matching dependency in the nearest ``package.json``
(``frontend_roots``) — an ``app/`` directory alone proves nothing, since it's
also a common backend folder name.
"""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass
from pathlib import PurePosixPath

from . import gitio

# decorator text (already whitespace-normalized) -> (kind, method-or-None)
_HTTP_VERBS = ("get", "post", "put", "patch", "delete", "options", "head")

_PY_ROUTE = re.compile(
    r"@(?P<obj>[\w.]+)\.(?P<attr>route|" + "|".join(_HTTP_VERBS) + r")\b\s*\(?\s*(?P<arg>['\"][^'\"]*['\"])?"
)
_PY_CLI = re.compile(r"@(?P<obj>[\w.]+)\.(command|group)\b")
_PY_TASK = re.compile(r"@(?P<obj>[\w.]+\.)?(shared_task|task)\b")
_PY_FASTAPI_DEP = re.compile(r"@(?P<obj>[\w.]+)\.(websocket)\b")

_TS_ROUTE = re.compile(
    r"@(Get|Post|Put|Patch|Delete|Options|Head|All)\s*\(\s*(?P<arg>['\"][^'\"]*['\"])?"
)
_TS_CONTROLLER = re.compile(r"@Controller\s*\(\s*(?P<arg>['\"][^'\"]*['\"])?")


def from_decorators(decorators: str | None, lang: str) -> tuple[str, str] | None:
    """Return ``(kind, detail)`` if any decorator marks an entry point."""
    if not decorators:
        return None
    for line in decorators.splitlines():
        line = line.strip()
        if lang == "python":
            m = _PY_ROUTE.match(line)
            if m:
                verb = m.group("attr")
                method = "ANY" if verb == "route" else verb.upper()
                path = (m.group("arg") or "").strip("'\"") or "/"
                return "route", f"{method} {path}"
            if _PY_CLI.match(line):
                return "cli", line
            if _PY_TASK.match(line):
                return "task", line
            if _PY_FASTAPI_DEP.match(line):
                return "route", f"WS {line}"
        elif lang in ("typescript", "tsx", "javascript"):
            m = _TS_ROUTE.match(line)
            if m:
                verb = line[1 : line.index("(")].upper()
                path = (m.group("arg") or "").strip("'\"")
                return "route", f"{verb} {path}".strip()
            if _TS_CONTROLLER.match(line):
                return "controller", line
    return None


_SCRIPTS_RE = re.compile(r"(?P<name>[\w.-]+)\s*=\s*['\"](?P<target>[\w.]+):(?P<func>[\w.]+)['\"]")
_DOCKER_RE = re.compile(r"^\s*(CMD|ENTRYPOINT)\s+(.+)$", re.MULTILINE)


def repo_scripts(root, sha: str) -> list[tuple[str, str, str]]:
    """``[project.scripts]`` entries as ``(script_name, module, func)``."""
    blob = gitio.show_bytes(root, sha, "pyproject.toml")
    if not blob:
        return []
    try:
        data = tomllib.loads(blob.decode("utf-8", "replace"))
    except tomllib.TOMLDecodeError:
        return []
    scripts = data.get("project", {}).get("scripts", {})
    out = []
    for name, target in scripts.items():
        if isinstance(target, str) and ":" in target:
            module, func = target.split(":", 1)
            out.append((name, module.strip(), func.strip()))
    return out


def dockerfile_commands(root, sha: str) -> list[str]:
    blob = gitio.show_bytes(root, sha, "Dockerfile")
    if not blob:
        return []
    return [m.group(0).strip() for m in _DOCKER_RE.finditer(blob.decode("utf-8", "replace"))]


# --------------------------------------------------------------- frontend roots


# package.json dependency name -> the routing convention it signals. Presence
# alone is the gate; version is not inspected.
_FRONTEND_MARKERS = {
    "expo-router": "expo-router",
    "next": "next",
    "react-router-dom": "react-router",
    "react-router": "react-router",
    "@remix-run/react": "remix",
    "@react-navigation/native": "react-navigation",
    "react-native": "react-native",
    "react-dom": "react-dom",
    "react": "react",
}

# Same hard-excludes discovery.py applies to source files — a package.json
# under node_modules/vendor/build/dist is never a project root.
_EXCLUDE_DIR_NAMES = frozenset(
    {".git", ".venv", "venv", "node_modules", "vendor", "dist", "build", "target", "__pycache__", ".codemap"}
)


@dataclass(frozen=True)
class FrontendRoot:
    dir: str                     # posix dir containing package.json ("" = repo root)
    conventions: frozenset[str]  # subset of _FRONTEND_MARKERS.values()


def frontend_roots(all_paths: list[str], read_bytes) -> list[FrontendRoot]:
    """One entry per ``package.json`` whose dependencies name a known
    frontend routing convention. ``all_paths`` is every path in the tree
    (unfiltered by language — ``package.json`` isn't a registered source
    language, so the usual indexed file list never contains it);
    ``read_bytes(path) -> bytes | None`` reads one file's content, letting the
    caller supply either a git-commit or a worktree reader."""
    out: list[FrontendRoot] = []
    for path in all_paths:
        if PurePosixPath(path).name != "package.json":
            continue
        if any(part in _EXCLUDE_DIR_NAMES for part in path.split("/")):
            continue
        blob = read_bytes(path)
        if not blob:
            continue
        try:
            data = json.loads(blob.decode("utf-8", "replace"))
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        deps = {**(data.get("dependencies") or {}), **(data.get("devDependencies") or {})}
        found = {tag for name, tag in _FRONTEND_MARKERS.items() if name in deps}
        if not found:
            continue
        d = str(PurePosixPath(path).parent)
        out.append(FrontendRoot(dir="" if d == "." else d, conventions=frozenset(found)))
    return out


def _relative_to(path: str, base_dir: str) -> str | None:
    if not base_dir:
        return path
    prefix = base_dir + "/"
    return path[len(prefix):] if path.startswith(prefix) else None


# --------------------------------------------------------- file-based routing


_FRONTEND_EXTS = (".tsx", ".jsx", ".ts", ".js")
_GROUP_RE = re.compile(r"^\([^)]*\)$")                       # (group) — invisible in the URL
_DYNAMIC_RE = re.compile(r"^\[(?P<catch>\.\.\.)?(?P<name>[\w-]+)\]$")  # [id] / [...rest]

# Next.js app-router special filenames -> entry kind. Anything else under
# app/ is either an expo-router screen or not a route at all in Next.js.
_NEXT_APP_SPECIAL = {
    "page": "screen", "route": "route", "layout": "layout", "template": "layout",
    "loading": "layout", "error": "layout", "not-found": "layout", "default": "layout",
}
_NEXT_PAGES_SKIP = frozenset({"_app", "_document", "_error", "_middleware"})


def _path_from_segments(parts: list[str]) -> str:
    out: list[str] = []
    for seg in parts:
        if _GROUP_RE.match(seg):
            continue
        m = _DYNAMIC_RE.match(seg)
        if m:
            out.append("*" if m.group("catch") else f":{m.group('name')}")
            continue
        out.append(seg)
    if out and out[-1] in ("index", "page"):
        out.pop()
    return "/" + "/".join(out) if out else "/"


def _from_app_router(parts: list[str], conventions: frozenset[str]) -> tuple[str, str] | None:
    if "app" not in parts or not ({"next", "expo-router"} & conventions):
        return None
    idx = parts.index("app")
    tail = parts[idx + 1:]
    if not tail:
        return None
    stem = PurePosixPath(tail[-1]).stem
    dir_parts = tail[:-1]
    if "next" in conventions and stem in _NEXT_APP_SPECIAL:
        kind = _NEXT_APP_SPECIAL[stem]
        route = _path_from_segments(dir_parts)
        return kind, (f"ANY {route}" if kind == "route" else route)
    if "expo-router" not in conventions:
        return None  # a Next-only project with a non-special file under app/ isn't a page
    if stem.startswith("_layout"):
        return "layout", _path_from_segments(dir_parts)
    if stem.startswith("+"):
        return "screen", f"{_path_from_segments(dir_parts)} ({stem.lstrip('+')})"
    return "screen", _path_from_segments(dir_parts + [stem])


def _from_pages_router(parts: list[str], conventions: frozenset[str]) -> tuple[str, str] | None:
    if "pages" not in parts or "next" not in conventions:
        return None
    idx = parts.index("pages")
    tail = parts[idx + 1:]
    if not tail:
        return None
    stem = PurePosixPath(tail[-1]).stem
    if stem in _NEXT_PAGES_SKIP or stem.startswith("_"):
        return None
    dir_parts = tail[:-1]
    if dir_parts and dir_parts[0] == "api":
        return "route", f"ANY {_path_from_segments(dir_parts[1:] + [stem])}"
    return "screen", _path_from_segments(dir_parts + [stem])


def _from_flat_routes(parts: list[str], conventions: frozenset[str]) -> tuple[str, str] | None:
    """Remix / React Router v7's flat-file convention: ``app/routes/`` with
    dots as segment separators and ``$param`` for a dynamic segment."""
    if not ({"remix", "react-router"} & conventions):
        return None
    if len(parts) < 3 or parts[0] != "app" or parts[1] != "routes":
        return None
    stem = PurePosixPath(parts[-1]).stem
    if stem in ("_index", "index"):
        return "screen", "/"
    segs = [s for s in stem.split(".") if s and s != "_index"]
    route = "/" + "/".join(
        "*" if s == "$" else (f":{s[1:]}" if s.startswith("$") else s) for s in segs
    )
    return "screen", route or "/"


def from_route_file(path: str, roots: list[FrontendRoot]) -> tuple[str, str] | None:
    """File-based frontend routing: expo-router, Next.js (app + pages
    router), Remix / React Router v7 flat routes. Path-based and
    deterministic, gated on a matching ``package.json`` dependency
    (``roots``, from ``frontend_roots``) so an ``app/`` directory in a Node
    *backend* is never mistaken for a router."""
    if PurePosixPath(path).suffix not in _FRONTEND_EXTS:
        return None
    for root in roots:
        rel = _relative_to(path, root.dir)
        if rel is None:
            continue
        parts = rel.split("/")
        hit = (
            _from_app_router(parts, root.conventions)
            or _from_pages_router(parts, root.conventions)
            or _from_flat_routes(parts, root.conventions)
        )
        if hit:
            return hit
    return None


# ----------------------------------------------------- registration-based (JS)


@dataclass(frozen=True)
class SourceHit:
    kind: str
    detail: str
    component: str | None  # a name to resolve via *this file's* own symbols


_RN_SCREEN_RE = re.compile(r"<\s*(?:[\w$]+\.)?Screen\b(?P<attrs>[^>]*)/?>")
_ATTR_NAME_RE = re.compile(r"""\bname\s*=\s*(['"])(?P<val>[^'"]+)\1""")
_ATTR_COMPONENT_RE = re.compile(r"""\bcomponent\s*=\s*\{\s*(?P<val>[A-Za-z_$][\w$]*)\s*\}""")

# React Router (classic JSX form): <Route path="/x" element={<Y />} />. The
# attrs capture is loose on purpose — see the module-level note below the
# pattern list for why it still extracts correctly despite not bounding the
# outer tag exactly.
_RR_ROUTE_RE = re.compile(r"<\s*Route\b(?P<attrs>[^>]*)/?>")
_ATTR_ELEMENT_RE = re.compile(r"""\belement\s*=\s*\{\s*<\s*(?P<val>[A-Za-z_$][\w$]*)""")
_ATTR_PATH_RE = re.compile(r"""\bpath\s*=\s*(['"])(?P<val>[^'"]+)\1""")

# React Router (data-router / object form): createBrowserRouter([{ path,
# element }, ...]). A plain "does path: precede element: with a JSX value in
# between" scan — best-effort, not a JS object-literal parser.
_RR_ROUTER_ENTRY_RE = re.compile(
    r"""path\s*:\s*(['"])(?P<path>[^'"]+)\1\s*,\s*element\s*:\s*<\s*(?P<comp>[A-Za-z_$][\w$]*)"""
)

_APP_REGISTRY_RE = re.compile(
    r"AppRegistry\.registerComponent\([^,]+,\s*(?:\(\)\s*=>\s*)?(?P<comp>[A-Za-z_$][\w$]*)"
)
_REGISTER_ROOT_RE = re.compile(r"\bregisterRootComponent\(\s*(?P<comp>[A-Za-z_$][\w$]*)")
_RENDER_ROOT_RE = re.compile(
    r"(?:ReactDOM\.render|\.render)\(\s*(?:<\s*(?P<jsx>[A-Za-z_$][\w$.]*)|(?P<call>[A-Za-z_$][\w$]*)\()"
)
_JSX_TAG_RE = re.compile(r"<\s*(?P<name>[A-Z][\w$]*)")
# `<React.StrictMode><App/></React.StrictMode>` is the standard CRA/Vite
# scaffold around a render root — common enough to special-case: unwrap it to
# find the real root component instead of reporting the wrapper.
_KNOWN_WRAPPERS = frozenset({"StrictMode"})
_UNWRAP_WINDOW = 400


def _unwrap_known_wrapper(source_text: str, after: int, comp: str | None) -> str | None:
    name = comp.rsplit(".", 1)[-1] if comp else None
    if name not in _KNOWN_WRAPPERS:
        return comp
    window = source_text[after: after + _UNWRAP_WINDOW]
    for m in _JSX_TAG_RE.finditer(window):
        if m.group("name") not in _KNOWN_WRAPPERS:
            return m.group("name")
    return comp


def from_source(source_text: str, lang: str) -> list[SourceHit]:
    """Registration-based frontend entry points: content regexes, best-effort
    — the file-based detectors above carry the reliable path; this catches
    what they structurally can't (a screen registered by name rather than by
    file location, an app's render root). The biggest real gap: a routed
    component almost always comes from an ``import``, and resolution here is
    scoped to *this file's own* symbols — the same limitation the file-based
    detectors don't have, and the reason those are the reliable half."""
    if lang not in ("javascript", "typescript", "tsx"):
        return []
    out: list[SourceHit] = []
    for m in _RN_SCREEN_RE.finditer(source_text):
        attrs = m.group("attrs")
        name_m = _ATTR_NAME_RE.search(attrs)
        comp_m = _ATTR_COMPONENT_RE.search(attrs)
        if not name_m and not comp_m:
            continue
        detail = name_m.group("val") if name_m else comp_m.group("val")
        out.append(SourceHit("screen", detail, comp_m.group("val") if comp_m else None))
    for m in _RR_ROUTE_RE.finditer(source_text):
        attrs = m.group("attrs")
        path_m = _ATTR_PATH_RE.search(attrs)
        el_m = _ATTR_ELEMENT_RE.search(attrs)
        if not path_m and not el_m:
            continue
        detail = path_m.group("val") if path_m else "/"
        out.append(SourceHit("screen", detail, el_m.group("val") if el_m else None))
    for m in _RR_ROUTER_ENTRY_RE.finditer(source_text):
        out.append(SourceHit("screen", m.group("path"), m.group("comp")))
    for m in _APP_REGISTRY_RE.finditer(source_text):
        out.append(SourceHit("main", "AppRegistry.registerComponent", m.group("comp")))
    for m in _REGISTER_ROOT_RE.finditer(source_text):
        out.append(SourceHit("main", "registerRootComponent", m.group("comp")))
    for m in _RENDER_ROOT_RE.finditer(source_text):
        comp = m.group("jsx") or m.group("call")
        comp = _unwrap_known_wrapper(source_text, m.end(), comp)
        out.append(SourceHit("main", "render root", comp))
    return out
