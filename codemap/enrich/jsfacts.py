"""JS/TS facts the call graph can't see: web requests, server routes, config
reads and browser storage — and the links between a request and its handler.

The detection rules are ported from REA ("Reverse Engineer Anything",
https://github.com/morluto/rea, MIT licence, Copyright (c) 2026 morluto):

- request URLs: ``endpointArgument`` / ``isRequestUrlLiteral`` /
  ``isKeyedCollectionReceiver`` in ``javascriptStaticAnalysisHelpers.js``;
- browser storage: ``storageKind`` + ``inspectStorageCall`` in
  ``javascriptStaticAnalysisHelpers.js`` / ``javascriptStaticAnalysisCalls.js``;
- config reads: ``processConfigurationMember`` + ``collectDefaults`` in
  ``javascriptSemanticDataEffects.js``.

REA reads Babel's tree; this reads tree-sitter's, so the rules are re-expressed
node by node rather than copied. Where REA stops, this goes further, because
codemap needs a *link* and not just an inventory:

- a URL built with ``${...}`` is kept as a pattern (``/api/orders/:param``)
  instead of being dropped — REA only keeps exact literals;
- a request has to *look* like one (``/x``, ``./x``, ``http(s)://``,
  ``ws(s)://``) for every method, not just ``get``/``delete``: REA's broader
  rule is fine for an inventory, but ``cache.get('key')`` would pollute links;
- ``router.METHOD(path, ...)`` / ``router.route(path).get().put()`` on a
  receiver created by ``express()`` / ``Router()`` is a *server route*, with
  its method and handler, rather than another request;
- ``app.use('/prefix', router)`` mounts are followed across imports, so a
  route reads as ``/api/orders/:id`` and not ``/:id``;
- ``axios.create({ baseURL })`` instances carry their base into each call;
- each request is paired with the route(s) that serve it (``link``).

Never raises on a file it can't make sense of: that file just contributes
nothing. Everything here is static syntax — nothing is executed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import PurePosixPath

from tree_sitter import Parser

from ..languages.registry import get_ts_language, spec_for_path

JS_LANGS = frozenset({"javascript", "typescript", "tsx"})

HTTP_METHODS = ("get", "post", "put", "patch", "delete", "options", "head")
_CLIENT_METHODS = frozenset(HTTP_METHODS) | {"request"}
_SERVER_METHODS = frozenset(HTTP_METHODS) | {"all"}

# REA isRequestUrlLiteral
_URL_LIKE = re.compile(r"^(?:https?|wss?)://", re.I)
# REA isKeyedCollectionReceiver
_KEYED_COLLECTIONS = frozenset({"FormData", "Headers", "Map", "Set", "URLSearchParams", "WeakMap", "WeakSet"})
_STORAGE_OPS = frozenset({"getItem", "setItem", "removeItem", "clear", "key"})
PARAM = ":param"
_HOLE = "\x00"


# --------------------------------------------------------------------------- data


@dataclass
class Request:
    """A web request the code sends (client side)."""
    method: str          # GET/POST/... or ANY when it can't be told
    url: str             # normalized path pattern, e.g. /api/orders/:param
    raw: str             # the URL expression as written
    line: int
    via: str             # callee as written: axios.get, fetch, api.post
    base: str | None = None   # an axios instance name, resolved in link()


@dataclass
class Route:
    """A route the code serves (server side)."""
    method: str          # GET/... or ANY for .all / .use-less shapes
    path: str            # as written on its router, e.g. /:id
    receiver: str        # router/app variable it was declared on
    handler: str | None  # last handler argument's name, if a plain name
    middleware: list[str]
    line: int


@dataclass
class Mount:
    receiver: str        # app/router variable doing the mounting
    prefix: str
    target: str          # local name of the mounted router
    line: int


@dataclass
class ConfigRead:
    kind: str            # environment | argv | vite-env
    key: str | None
    default: str | None
    line: int


@dataclass
class StorageUse:
    kind: str            # local-storage | session-storage | indexed-db | cache-storage | sqlite
    key: str | None
    op: str              # getItem / setItem / removeItem / open / ...
    line: int


@dataclass
class FileFacts:
    path: str
    requests: list[Request] = field(default_factory=list)
    routes: list[Route] = field(default_factory=list)
    mounts: list[Mount] = field(default_factory=list)
    config: list[ConfigRead] = field(default_factory=list)
    storage: list[StorageUse] = field(default_factory=list)
    # name -> (specifier, imported name | "default" | "*")
    imports: dict[str, tuple[str, str]] = field(default_factory=dict)
    default_export: str | None = None
    servers: set[str] = field(default_factory=set)    # express() apps
    routers: set[str] = field(default_factory=set)    # Router() instances
    instances: dict[str, str] = field(default_factory=dict)   # axios.create name -> baseURL


@dataclass
class RouteLink:
    """Same shape as ``cbm.RouteLink``: keys of the symbols that send a request
    and of the symbols that serve it."""
    url: str
    method: str
    callers: list[str] = field(default_factory=list)
    handlers: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- tree helpers


@lru_cache(maxsize=None)
def _parser(grammar: str) -> Parser:
    return Parser(get_ts_language(grammar))


def _t(src: bytes, node) -> str:
    return src[node.start_byte:node.end_byte].decode("utf-8", "replace")


def _line(node) -> int:
    return node.start_point[0] + 1


def _args(call) -> list:
    a = call.child_by_field_name("arguments")
    return [] if a is None else [c for c in a.named_children if c.type != "comment"]


def _string(src: bytes, node) -> str | None:
    """Exact string value of a literal, or None (REA ``stringValue``): a plain
    string, or a template with no substitutions."""
    if node is None:
        return None
    if node.type == "string":
        return "".join(_t(src, c) for c in node.named_children
                       if c.type in ("string_fragment", "escape_sequence"))
    if node.type == "template_string":
        if any(c.type == "template_substitution" for c in node.named_children):
            return None
        return "".join(_t(src, c) for c in node.named_children)
    return None


def _template(src: bytes, node) -> str | None:
    """A URL expression with every non-literal part as a hole: string, template
    (substitutions -> hole), or ``a + b`` concatenation of those / names."""
    if node is None:
        return None
    if node.type in ("string", "template_string"):
        if node.type == "string":
            return _string(src, node)
        out = []
        for c in node.named_children:
            out.append(_HOLE if c.type == "template_substitution" else _t(src, c))
        return "".join(out)
    if node.type == "binary_expression" and _op(src, node) == "+":
        left = _template(src, node.child_by_field_name("left"))
        right = _template(src, node.child_by_field_name("right"))
        if left is None and right is None:
            return None
        return (left if left is not None else _HOLE) + (right if right is not None else _HOLE)
    if node.type == "parenthesized_expression" and node.named_children:
        return _template(src, node.named_children[0])
    return None


def _op(src: bytes, node) -> str:
    op = node.child_by_field_name("operator")
    return _t(src, op) if op is not None else ""


def _dotted(src: bytes, node) -> str:
    """``a.b.c`` for a member chain over a plain name; '' otherwise (REA ``calleeName``)."""
    parts = []
    while node is not None and node.type == "member_expression":
        prop = node.child_by_field_name("property")
        parts.append(_t(src, prop) if prop is not None else "")
        node = node.child_by_field_name("object")
    if node is None:
        return ""
    if node.type == "identifier":
        parts.append(_t(src, node))
    elif node.type == "this":
        parts.append("this")
    else:
        return ""
    return ".".join(reversed(parts))


def _ident(src: bytes, node) -> str | None:
    return _t(src, node) if node is not None and node.type == "identifier" else None


def _walk(node):
    stack = [node]
    while stack:
        n = stack.pop()
        yield n
        stack.extend(reversed(n.children))


# --------------------------------------------------------------------------- URL normalization


def normalize_url(template: str) -> str | None:
    """``/api/products/<hole>/reviews?x=<hole>`` -> ``/api/products/:param/reviews``.
    Origin and query/fragment dropped; a hole anywhere in a segment makes that
    segment a parameter. ``None`` when the result isn't a request path.
    A leading hole followed by ``/`` is a base-URL variable (``${API}/users``):
    dropped, since the path after it is what routes match on."""
    s = template.strip()
    if s.startswith(_HOLE + "/"):
        s = s[1:]
    s = _URL_LIKE.sub("", s, count=1)
    if _URL_LIKE.match(template.strip()):
        s = "/" + s.split("/", 1)[1] if "/" in s else "/"
    s = re.split(r"[?#]", s, maxsplit=1)[0]
    if not s.startswith(("/", "./", "../")):
        return None
    s = s.lstrip(".")
    segs = []
    for seg in s.split("/"):
        if seg in ("", "."):
            continue
        segs.append(PARAM if _HOLE in seg else seg)
    return "/" + "/".join(segs)


def _route_segments(path: str) -> list[str]:
    return [PARAM if (seg.startswith(":") or (seg.startswith("{") and seg.endswith("}")) or seg == "*")
            else seg for seg in path.split("/") if seg]


def join_path(prefix: str, path: str) -> str:
    segs = [s for s in (prefix or "").split("/") if s] + [s for s in (path or "").split("/") if s]
    return "/" + "/".join(segs)


# --------------------------------------------------------------------------- extraction


def extract(path: str, source: bytes, lang: str | None = None) -> FileFacts:
    """Facts for one JS/TS file. Never raises."""
    ff = FileFacts(path)
    spec = spec_for_path(path)
    if spec is None or (lang or spec.name) not in JS_LANGS:
        return ff
    try:
        root = _parser(spec.grammar).parse(source).root_node
        _collect(ff, source, root)
    except Exception:  # a file this can't read contributes nothing, never breaks explore
        return FileFacts(path)
    return ff


def _collect(ff: FileFacts, src: bytes, root) -> None:
    calls = []
    for n in _walk(root):
        t = n.type
        if t == "import_statement":
            _import(ff, src, n)
        elif t == "variable_declarator":
            _declarator(ff, src, n)
        elif t == "export_statement":
            _export(ff, src, n)
        elif t == "assignment_expression":
            _module_exports(ff, src, n)
        elif t in ("call_expression", "new_expression"):
            calls.append(n)
        elif t in ("member_expression", "subscript_expression"):
            _config(ff, src, n)
    # calls last: receivers (routers, apps, axios instances) are known by then
    for n in calls:
        if n.type == "new_expression":
            _new_storage(ff, src, n)
        else:
            _call(ff, src, n)


def _import(ff: FileFacts, src: bytes, n) -> None:
    source = n.child_by_field_name("source")
    spec = _string(src, source)
    if not spec:
        return
    for clause in n.named_children:
        if clause.type != "import_clause":
            continue
        for part in clause.named_children:
            if part.type == "identifier":
                ff.imports[_t(src, part)] = (spec, "default")
            elif part.type == "namespace_import":
                name = next((c for c in part.named_children if c.type == "identifier"), None)
                if name is not None:
                    ff.imports[_t(src, name)] = (spec, "*")
            elif part.type == "named_imports":
                for s in part.named_children:
                    if s.type != "import_specifier":
                        continue
                    orig = s.child_by_field_name("name")
                    alias = s.child_by_field_name("alias")
                    if orig is not None:
                        ff.imports[_t(src, alias or orig)] = (spec, _t(src, orig))


def _require_spec(src: bytes, node) -> str | None:
    """``require('x')`` -> 'x'."""
    if node is None or node.type != "call_expression":
        return None
    fn = node.child_by_field_name("function")
    if _ident(src, fn) != "require":
        return None
    a = _args(node)
    return _string(src, a[0]) if a else None


def _declarator(ff: FileFacts, src: bytes, n) -> None:
    name_node = n.child_by_field_name("name")
    value = n.child_by_field_name("value")
    if value is None:
        return
    req = _require_spec(src, value)
    if req is not None:
        if name_node is not None and name_node.type == "identifier":
            ff.imports[_t(src, name_node)] = (req, "default")
        elif name_node is not None and name_node.type == "object_pattern":
            for p in name_node.named_children:
                if p.type == "shorthand_property_identifier_pattern":
                    ff.imports[_t(src, p)] = (req, _t(src, p))
                elif p.type == "pair_pattern":
                    k, v = p.child_by_field_name("key"), p.child_by_field_name("value")
                    if k is not None and v is not None and v.type == "identifier":
                        ff.imports[_t(src, v)] = (req, _t(src, k))
        return
    name = _ident(src, name_node)
    if name is None:
        return
    if value.type == "new_expression":
        ctor = value.child_by_field_name("constructor")
        if _dotted(src, ctor).split(".")[-1] == "Router":
            ff.routers.add(name)
        return
    if value.type != "call_expression":
        return
    fn = value.child_by_field_name("function")
    callee = _dotted(src, fn)
    # `require('express')()` / `require('express').Router()`
    if fn is not None and fn.type == "call_expression" and _require_spec(src, fn) == "express":
        ff.servers.add(name)
        return
    if fn is not None and fn.type == "member_expression":
        obj = fn.child_by_field_name("object")
        prop = _t(src, fn.child_by_field_name("property"))
        if _require_spec(src, obj) == "express" and prop == "Router":
            ff.routers.add(name)
            return
    imported = ff.imports.get(callee.split(".")[0]) if callee else None
    root_pkg = imported[0] if imported else None
    if callee == "Router" or callee.endswith(".Router"):
        ff.routers.add(name)
    elif root_pkg == "express" and "." not in callee:
        ff.servers.add(name)
    elif callee in ("express", "fastify", "Koa") or callee.endswith(".create") and root_pkg in ("fastify",):
        ff.servers.add(name)
    elif callee.endswith(".create") and (root_pkg == "axios" or callee.split(".")[0] == "axios"):
        base = None
        a = _args(value)
        if a and a[0].type == "object":
            for pair in a[0].named_children:
                if pair.type == "pair" and _t(src, pair.child_by_field_name("key")).strip("'\"") == "baseURL":
                    tpl = _template(src, pair.child_by_field_name("value"))
                    base = normalize_url(tpl) if tpl is not None else None
        ff.instances[name] = base or ""


def _export(ff: FileFacts, src: bytes, n) -> None:
    if not any(c.type == "default" for c in n.children):
        return
    val = n.child_by_field_name("value") or n.child_by_field_name("declaration")
    if val is None:
        return
    if val.type == "identifier":
        ff.default_export = _t(src, val)
    else:
        name = val.child_by_field_name("name")
        if name is not None:
            ff.default_export = _t(src, name)


def _module_exports(ff: FileFacts, src: bytes, n) -> None:
    left = n.child_by_field_name("left")
    right = n.child_by_field_name("right")
    if _dotted(src, left) in ("module.exports", "exports.default") and _ident(src, right):
        ff.default_export = _t(src, right)


# -- config reads (REA processConfigurationMember + collectDefaults) ----------


def _config(ff: FileFacts, src: bytes, n) -> None:
    obj = n.child_by_field_name("object")
    family = _dotted(src, obj)
    if family not in ("process.env", "process.argv", "import.meta.env"):
        if not (obj is not None and obj.type == "member_expression" and _t(src, obj) == "import.meta.env"):
            return
        family = "import.meta.env"
    # skip `process.env` itself appearing as the object of a longer chain we already handle
    key: str | None
    if n.type == "member_expression":
        key = _t(src, n.child_by_field_name("property"))
    else:
        idx = n.child_by_field_name("index")
        key = _string(src, idx) if idx is not None and idx.type != "number" else (_t(src, idx) if idx is not None else None)
    if family == "process.argv":
        if key is None or not key.isdigit():
            return
        kind = "argv"
    elif family == "import.meta.env":
        kind = "vite-env"
    else:
        kind = "environment"
    ff.config.append(ConfigRead(kind, key, _default_of(src, n), _line(n)))


def _default_of(src: bytes, n) -> str | None:
    node = n
    parent = node.parent
    while parent is not None and parent.type == "parenthesized_expression":
        node, parent = parent, parent.parent
    if (parent is not None and parent.type == "binary_expression"
            and _op(src, parent) in ("||", "??") and parent.child_by_field_name("left") == node):
        right = parent.child_by_field_name("right")
        if right is not None and right.type in ("number", "true", "false", "null"):
            return _t(src, right)
        val = _string(src, right)
        if val is not None:
            return val
    return None


# -- calls: requests, server routes, mounts, storage --------------------------


def _call(ff: FileFacts, src: bytes, n) -> None:
    fn = n.child_by_field_name("function")
    if fn is None:
        return
    args = _args(n)
    if fn.type == "member_expression":
        prop = _t(src, fn.child_by_field_name("property"))
        obj = fn.child_by_field_name("object")
        recv = _ident(src, obj)
        if recv is not None and (recv in ff.servers or recv in ff.routers):
            _server_call(ff, src, n, recv, prop, args)
            return
        base_route = _route_chain(src, obj, ff)
        if base_route is not None and prop in _SERVER_METHODS:
            recv_name, path = base_route
            _add_route(ff, src, prop, path, recv_name, args, n)
            return
    name = _dotted(src, fn)
    if not name:
        return
    if _storage(ff, src, n, name, args):
        return
    _request(ff, src, n, name, fn, args)


def _route_chain(src: bytes, obj, ff: FileFacts) -> tuple[str, str] | None:
    """``router.route('/x')`` possibly followed by ``.get(..)`` links -> (router, '/x')."""
    while obj is not None and obj.type == "call_expression":
        fn = obj.child_by_field_name("function")
        if fn is None or fn.type != "member_expression":
            return None
        prop = _t(src, fn.child_by_field_name("property"))
        inner = fn.child_by_field_name("object")
        if prop == "route":
            recv = _ident(src, inner)
            a = _args(obj)
            path = _string(src, a[0]) if a else None
            if recv is not None and path is not None and (recv in ff.servers or recv in ff.routers):
                return recv, path
            return None
        if prop not in _SERVER_METHODS:
            return None
        obj = inner
    return None


def _server_call(ff: FileFacts, src: bytes, n, recv: str, prop: str, args: list) -> None:
    if prop == "use":
        if len(args) >= 2:
            prefix = _string(src, args[0])
            target = args[-1]
            tname = _ident(src, target)
            if tname is None:
                spec = _require_spec(src, target)
                if spec is not None:
                    tname = f"require:{spec}"
                    ff.imports[tname] = (spec, "default")
            if prefix is not None and tname is not None:
                ff.mounts.append(Mount(recv, prefix, tname, _line(n)))
        return
    if prop in _SERVER_METHODS and args:
        path = _string(src, args[0])
        if path is not None:
            _add_route(ff, src, prop, path, recv, args[1:], n)


def _add_route(ff: FileFacts, src: bytes, method: str, path: str, recv: str, handlers: list, n) -> None:
    names = []
    for h in handlers:
        if h.type == "identifier":
            names.append(_t(src, h))
        elif h.type == "member_expression":
            names.append(_dotted(src, h) or "")
        else:
            names.append("")
    handler = names[-1] if names and names[-1] else None
    ff.routes.append(Route("ANY" if method == "all" else method.upper(), path, recv, handler,
                           [x for x in names[:-1] if x], _line(n)))


def _storage(ff: FileFacts, src: bytes, n, name: str, args: list) -> bool:
    """REA storageKind + inspectStorageCall."""
    kind = None
    if "localStorage." in name or name.startswith("localStorage."):
        kind = "local-storage"
    elif "sessionStorage." in name:
        kind = "session-storage"
    elif name.endswith("indexedDB.open"):
        kind = "indexed-db"
    elif name.endswith("caches.open"):
        kind = "cache-storage"
    if kind is None:
        return False
    op = name.rsplit(".", 1)[-1]
    if kind in ("local-storage", "session-storage") and op not in _STORAGE_OPS:
        return False
    ff.storage.append(StorageUse(kind, _string(src, args[0]) if args else None, op, _line(n)))
    return True


def _new_storage(ff: FileFacts, src: bytes, n) -> None:
    ctor = n.child_by_field_name("constructor")
    name = _dotted(src, ctor)
    if name == "Database" or name.endswith(".Database"):
        a = _args(n)
        ff.storage.append(StorageUse("sqlite", _string(src, a[0]) if a else None, "open", _line(n)))
    elif name == "WebSocket":
        a = _args(n)
        tpl = _template(src, a[0]) if a else None
        url = normalize_url(tpl) if tpl is not None else None
        if url is not None:
            ff.requests.append(Request("WS", url, _t(src, a[0]), _line(n), "WebSocket"))


def _keyed_receiver(src: bytes, fn) -> bool:
    """REA isKeyedCollectionReceiver: ``new Map().get``, ``x.headers.get``."""
    if fn is None or fn.type != "member_expression":
        return False
    recv = fn.child_by_field_name("object")
    if recv is not None and recv.type == "new_expression":
        return _dotted(src, recv.child_by_field_name("constructor")) in _KEYED_COLLECTIONS
    if recv is not None and recv.type == "member_expression":
        return _t(src, recv.child_by_field_name("property")) in ("headers", "searchParams")
    return False


def _request(ff: FileFacts, src: bytes, n, name: str, fn, args: list) -> None:
    """REA endpointArgument, minus XHR ``open``/Electron ``loadURL`` (not web
    requests a learner traces), plus methods and URL patterns."""
    if not args:
        return
    last = name.rsplit(".", 1)[-1]
    if name == "fetch" or name.endswith(".fetch"):
        method = "GET"
        if len(args) > 1 and args[1].type == "object":
            for pair in args[1].named_children:
                if pair.type == "pair" and _t(src, pair.child_by_field_name("key")).strip("'\"") == "method":
                    m = _string(src, pair.child_by_field_name("value"))
                    method = m.upper() if m else "ANY"
    elif last in _CLIENT_METHODS and "." in name:
        if _keyed_receiver(src, fn):
            return
        method = "ANY" if last == "request" else last.upper()
    else:
        return
    tpl = _template(src, args[0])
    if tpl is None:
        return
    url = normalize_url(tpl)
    if url is None:
        return
    recv = name.split(".")[0]
    base = recv if (recv in ff.instances or recv in ff.imports) and recv not in ("axios", "window", "globalThis") else None
    ff.requests.append(Request(method, url, _t(src, args[0]), _line(n), name, base))


# --------------------------------------------------------------------------- linking


@dataclass
class SymSpan:
    key: str
    name: str
    file: str
    start: int
    end: int


@dataclass
class Linked:
    routes: list[RouteLink] = field(default_factory=list)
    config: list[dict] = field(default_factory=list)
    storage: list[dict] = field(default_factory=list)
    requests: int = 0
    paired: int = 0
    server_routes: int = 0


_EXTS = (".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx")


def _resolve(spec: str, importer: str, paths: set[str]) -> str | None:
    if not spec.startswith("."):
        return None
    target = PurePosixPath(importer).parent
    for part in spec.split("/"):
        if part in ("", "."):
            continue
        target = target.parent if part == ".." else target / part
    stem = str(target)
    if stem in paths:
        return stem
    for ext in _EXTS:
        if f"{stem}{ext}" in paths:
            return f"{stem}{ext}"
    for ext in _EXTS:
        if f"{stem}/index{ext}" in paths:
            return f"{stem}/index{ext}"
    return None


def _owner(spans: list[SymSpan], line: int) -> str | None:
    best = None
    for s in spans:
        if s.start <= line <= s.end and (best is None or s.end - s.start < best.end - best.start):
            best = s
    return best.key if best else None


def _target_of(ff: FileFacts, local: str, paths: set[str], by_path: dict[str, FileFacts]) -> tuple[str, str] | None:
    """(file, name in that file) a local name points at — following one import."""
    if local in ff.imports:
        spec, imported = ff.imports[local]
        dest = _resolve(spec, ff.path, paths)
        if dest is None:
            return None
        if imported == "default":
            other = by_path.get(dest)
            name = other.default_export if other else None
            return (dest, name) if name else None
        if imported == "*":
            return None
        return dest, imported
    return ff.path, local


@dataclass
class Served:
    """A server route at its full address, with the symbol that handles it."""
    method: str
    path: str            # mounts applied, e.g. /api/orders/:id
    handler: str | None  # symbol key, when the handler is a named function in this repo
    file: str            # where the route is declared
    line: int


def served_routes(facts: list[FileFacts], keys: set[str]) -> list[Served]:
    """Every server route in ``facts``, its path prefixed through ``app.use`` /
    ``router.use`` mounts and its handler resolved to a key in ``keys``."""
    paths = {f.path for f in facts}
    by_path = {f.path: f for f in facts}

    # -- every router's full prefix, through app.use / router.use mounts ------
    mounted: dict[tuple[str, str], list[tuple[str, str, str]]] = {}   # (file, router) -> [(mount file, mount receiver, prefix)]
    for ff in facts:
        for m in ff.mounts:
            tgt = _target_of(ff, m.target, paths, by_path)
            if tgt is not None:
                mounted.setdefault(tgt, []).append((ff.path, m.receiver, m.prefix))

    def prefixes(file: str, recv: str, depth: int = 0) -> list[str]:
        ff = by_path.get(file)
        if ff is not None and recv in ff.servers:
            return [""]
        if depth > 6:
            return []
        out = []
        for mfile, mrecv, prefix in mounted.get((file, recv), ()):
            for up in prefixes(mfile, mrecv, depth + 1):
                out.append(join_path(up, prefix))
        return out

    def handler_key(ff: FileFacts, name: str | None) -> str | None:
        if not name:
            return None
        if "." in name:   # ctrl.getX through `import * as ctrl` / `const ctrl = require(..)`
            head, tail = name.split(".", 1)
            if head in ff.imports:
                dest = _resolve(ff.imports[head][0], ff.path, paths)
                key = f"{dest}::{tail}" if dest else None
                return key if key in keys else None
            return None
        tgt = _target_of(ff, name, paths, by_path)
        if tgt is None:
            return None
        key = f"{tgt[0]}::{tgt[1]}"
        return key if key in keys else None

    served: list[Served] = []
    for ff in facts:
        for r in ff.routes:
            for p in prefixes(ff.path, r.receiver) or [""]:
                served.append(Served(r.method, join_path(p, r.path), handler_key(ff, r.handler), ff.path, r.line))
    return served


def link(facts: list[FileFacts], symbols: list[SymSpan]) -> Linked:
    """Join every file's facts into route links, config reads and storage uses,
    each tied to the codemap symbol that owns it."""
    paths = {f.path for f in facts}
    by_path = {f.path: f for f in facts}
    keys = {s.key for s in symbols}
    spans_by_file: dict[str, list[SymSpan]] = {}
    for s in symbols:
        spans_by_file.setdefault(s.file, []).append(s)

    def target_of(ff: FileFacts, local: str) -> tuple[str, str] | None:
        return _target_of(ff, local, paths, by_path)

    served = [(s.method, _route_segments(s.path), s.handler, s.path) for s in served_routes(facts, keys)]

    def base_of(ff: FileFacts, name: str | None) -> str:
        if not name:
            return ""
        if name in ff.instances:
            return ff.instances[name]
        tgt = target_of(ff, name)
        if tgt is None:
            return ""
        other = by_path.get(tgt[0])
        return other.instances.get(tgt[1], "") if other else ""

    out = Linked(server_routes=len(served))
    links: dict[tuple[str, str], RouteLink] = {}
    for ff in facts:
        spans = spans_by_file.get(ff.path, [])
        for q in ff.requests:
            out.requests += 1
            url = join_path(base_of(ff, q.base), q.url) if q.base else q.url
            caller = _owner(spans, q.line)
            segs = _route_segments(url)
            best, best_score = [], None
            for method, rsegs, hkey, full in served:
                if len(rsegs) != len(segs):
                    continue
                if q.method not in ("ANY",) and method not in ("ANY", q.method):
                    continue
                score = 0
                for a, b in zip(segs, rsegs):
                    if a == b:
                        score += 2 if a != PARAM else 1
                    elif a == PARAM or b == PARAM:
                        score += 0
                    else:
                        break
                else:
                    if best_score is None or score > best_score:
                        best, best_score = [(method, hkey, full)], score
                    elif score == best_score:
                        best.append((method, hkey, full))
            if best:
                out.paired += 1
            for method, hkey, full in best or [(q.method, None, url)]:
                label_method = q.method if q.method != "ANY" else method
                rl = links.setdefault((label_method, full), RouteLink(url=full, method=label_method))
                if caller and caller not in rl.callers:
                    rl.callers.append(caller)
                if hkey and hkey not in rl.handlers:
                    rl.handlers.append(hkey)
        for c in ff.config:
            out.config.append({"kind": c.kind, "key": c.key, "default": c.default, "file": ff.path,
                               "line": c.line, "symbol": _owner(spans, c.line)})
        for s in ff.storage:
            out.storage.append({"kind": s.kind, "key": s.key, "op": s.op, "file": ff.path,
                                "line": s.line, "symbol": _owner(spans, s.line)})
    # routes nobody in this repo calls still teach "this address exists"
    for method, _segs, hkey, full in served:
        if hkey and (method, full) not in links and not any(k[1] == full for k in links):
            links[(method, full)] = RouteLink(url=full, method=method, handlers=[hkey])
    out.routes = sorted(links.values(), key=lambda r: (r.url, r.method))
    return out


# --------------------------------------------------------------------------- index-time storage

# Words a file must contain to declare or mount a server route at all (see
# _declarator: express() / Router() / fastify / Koa). A cheap byte test so the
# indexer only re-parses the few files that can matter.
_SERVER_HINTS = (b"express", b"Router", b"fastify", b"Koa")


def might_serve(source: bytes) -> bool:
    return any(h in source for h in _SERVER_HINTS)


def route_facts_json(ff: FileFacts) -> str | None:
    """The part of a file's facts :func:`served_routes` reads, as JSON for the
    indexer's ``server_route_facts`` table; None when the file holds no server
    route, mount or app."""
    import json

    if not (ff.routes or ff.mounts or ff.servers):
        return None
    return json.dumps({
        "routes": [[r.method, r.path, r.receiver, r.handler, r.middleware, r.line] for r in ff.routes],
        "mounts": [[m.receiver, m.prefix, m.target, m.line] for m in ff.mounts],
        "imports": {k: list(v) for k, v in ff.imports.items()},
        "default_export": ff.default_export,
        "servers": sorted(ff.servers),
        "routers": sorted(ff.routers),
    }, sort_keys=True)


def route_facts_from_json(path: str, text: str | None, default_export: str | None = None) -> FileFacts:
    """Inverse of :func:`route_facts_json`. A file stored without route facts
    still contributes its ``default_export`` (a default-imported handler)."""
    import json

    ff = FileFacts(path, default_export=default_export)
    if not text:
        return ff
    d = json.loads(text)
    ff.routes = [Route(m, p, recv, h, list(mw), ln) for m, p, recv, h, mw, ln in d["routes"]]
    ff.mounts = [Mount(recv, pre, tgt, ln) for recv, pre, tgt, ln in d["mounts"]]
    ff.imports = {k: (v[0], v[1]) for k, v in d["imports"].items()}
    ff.default_export = d.get("default_export") or default_export
    ff.servers = set(d["servers"])
    ff.routers = set(d["routers"])
    return ff

