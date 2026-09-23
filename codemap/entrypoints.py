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

import io
import json
import re
import tokenize
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
_PY_TASK = re.compile(r"@(?P<obj>[\w.]+\.)?(shared_task|task|scheduled_job)\b")
_PY_FASTAPI_DEP = re.compile(r"@(?P<obj>[\w.]+)\.(websocket)\b")
_PY_METHODS = re.compile(r"methods\s*=\s*\[(?P<list>[^\]]*)\]")

# APScheduler's job registration isn't a decorator (`@scheduled_job` above IS,
# and already covered) — `add_job(...)` / `BackgroundScheduler()` /
# `schedule.every(...)` are plain calls, matched by scheduler_symbol_ranges()
# below (byte offsets into the whole file, since it isn't decorator text).
_PY_SCHEDULER_CALL = re.compile(
    rb"\.add_job\(|BackgroundScheduler\(|BlockingScheduler\(|\bschedule\.every\("
)

_TS_ROUTE = re.compile(
    r"@(Get|Post|Put|Patch|Delete|Options|Head|All)\s*\(\s*(?P<arg>['\"][^'\"]*['\"])?"
)
_TS_CONTROLLER = re.compile(r"@Controller\s*\(\s*(?P<arg>['\"][^'\"]*['\"])?")


def _py_route_methods(line: str, verb: str) -> str:
    """``methods=[...]`` on a ``.route(...)`` decorator, else the verb itself
    for ``.get``/``.post``/etc., else Flask's own default (``GET`` — plus the
    HEAD/OPTIONS it adds silently, not worth spelling out here)."""
    if verb != "route":
        return verb.upper()
    m = _PY_METHODS.search(line)
    if not m:
        return "GET"
    names = [p.strip(" '\"") for p in m.group("list").split(",")]
    names = list(dict.fromkeys(n.upper() for n in names if n))
    return ", ".join(names) if names else "GET"


def from_decorators(
    decorators: str | None, lang: str, blueprint_prefixes: dict[str, str] | None = None
) -> tuple[str, str] | None:
    """Return ``(kind, detail)`` if any decorator marks an entry point.

    ``blueprint_prefixes`` (optional, see ``blueprint_prefixes()`` below):
    a Flask route's ``@bp.route(...)`` object name, looked up against
    ``app.register_blueprint(bp, url_prefix=...)`` calls found anywhere in
    the repo, so the label carries the real mount path instead of just the
    route's own suffix.
    """
    if not decorators:
        return None
    for line in decorators.splitlines():
        line = line.strip()
        if lang == "python":
            m = _PY_ROUTE.match(line)
            if m:
                verb = m.group("attr")
                method = _py_route_methods(line, verb)
                path = (m.group("arg") or "").strip("'\"") or "/"
                obj_name = m.group("obj").rsplit(".", 1)[-1]
                prefix = (blueprint_prefixes or {}).get(obj_name, "")
                if prefix and prefix != "/":
                    path = prefix.rstrip("/") + (path if path.startswith("/") else f"/{path}")
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

_BP_CALL_RE = re.compile(r"register_blueprint\(\s*(?P<args>[^)]*)\)")
_BP_PREFIX_RE = re.compile(r"url_prefix\s*=\s*(['\"])(?P<prefix>[^'\"]*)\1")


def blueprint_prefixes(all_paths: list[str], read_bytes) -> dict[str, str]:
    """Flask ``app.register_blueprint(bp, url_prefix="/api")`` calls,
    repo-wide, as ``{blueprint variable name: prefix}``. Best-effort (a
    single-line call, the common case; a value containing ``)`` defeats the
    non-nested paren match). Keyed by the blueprint's bare variable name
    rather than by file, so a route decorated ``@bp.route(...)`` in the file
    that *defines* the blueprint picks up the prefix from wherever it's
    actually *registered* (usually a different file, ``app.py``) — the same
    name is what makes the registration work in the first place.

    ``all_paths``/``read_bytes`` mirror ``frontend_roots()``'s signature so
    the same call works against a git commit or the live worktree."""
    out: dict[str, str] = {}
    for path in all_paths:
        if PurePosixPath(path).suffix != ".py":
            continue
        blob = read_bytes(path)
        if not blob:
            continue
        text = blob.decode("utf-8", "replace")
        if "register_blueprint" not in text:
            continue
        for m in _BP_CALL_RE.finditer(text):
            args = m.group("args")
            var = args.split(",", 1)[0].strip().rsplit(".", 1)[-1]
            if not var.isidentifier():
                continue
            pm = _BP_PREFIX_RE.search(args)
            out[var] = pm.group("prefix") if pm else ""
    return out


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


def scheduler_symbol_ranges(source: bytes) -> list[tuple[int, int]]:
    """Byte spans in ``source`` where a background job gets registered by a
    plain call — ``add_job(...)``, ``BackgroundScheduler()``,
    ``schedule.every(...)`` — matched directly against the file's own bytes
    so a caller (``indexer._write_parsed``) can tie a hit to whichever
    symbol's own ``[start_byte, end_byte)`` contains it, the same way
    ``parsing._main_guard_calls`` locates a ``__main__`` guard's callees.
    The decorator form (``@shared_task``/``@scheduled_job``) is already
    covered by ``from_decorators`` — this is only the plain-call cousin.
    Matched against the raw bytes (not a decoded string) so offsets line up
    exactly with ``Symbol.start_byte``/``end_byte``. A mention inside a string
    literal or comment (a docstring describing ``add_job(``, a test's sample
    source) is not a registration and is skipped."""
    hits = [(m.start(), m.end()) for m in _PY_SCHEDULER_CALL.finditer(source)]
    if not hits:
        return hits
    literals = _literal_byte_spans(source)
    if literals is None:
        return hits
    return [h for h in hits if not any(a <= h[0] < b for a, b in literals)]


def _literal_byte_spans(source: bytes) -> list[tuple[int, int]] | None:
    """Byte spans of every string literal and comment in Python ``source``, or
    ``None`` if it doesn't tokenize (broken file -- the caller keeps every hit)."""
    text = source.decode("utf-8", "replace")
    lines = text.splitlines(keepends=True)
    starts = [0]
    for line in lines:
        starts.append(starts[-1] + len(line.encode("utf-8")))

    def offset(pos: tuple[int, int]) -> int:
        row, col = pos
        return starts[row - 1] + len(lines[row - 1][:col].encode("utf-8"))

    skip = {tokenize.STRING, tokenize.COMMENT, getattr(tokenize, "FSTRING_MIDDLE", tokenize.STRING)}
    try:
        return [
            (offset(t.start), offset(t.end))
            for t in tokenize.generate_tokens(io.StringIO(text).readline)
            if t.type in skip
        ]
    except (tokenize.TokenError, SyntaxError, IndexError):
        return None


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
