"""Python web requests and served routes, for the same request -> handler links
``jsfacts`` makes for JavaScript.

The call graph has no edge across an HTTP request. For JS/TS, ``jsfacts`` finds
both ends; this module adds Python's: the requests a Python client sends
(``requests`` / ``httpx`` / ``aiohttp`` / a test client / ``urlopen``) and the
routes a Python server declares, which codemap already records as ``route``
entry points (Flask, FastAPI, Starlette... -- see ``entrypoints.py``). Both go
through ``jsfacts.link``, so a React screen calling a FastAPI handler pairs as
readily as two Python files do. codebase-memory-mcp's ``HTTP_CALLS`` /
``HANDLES`` edges were the reference for what to link.

A call counts as a request only when its URL *looks* like one (``/x``,
``http(s)://``, an f-string or ``BASE + "/x"`` that becomes one), the same rule
``jsfacts`` follows, so ``cache.get("key")`` never becomes a request.
"""

from __future__ import annotations

import re
from typing import Iterable

from tree_sitter import Parser

from ..languages.registry import get_ts_language
from .jsfacts import _HOLE, FileFacts, Request, Served, normalize_url

_CLIENT_METHODS = ("get", "post", "put", "patch", "delete", "head", "options")
_SERVED_METHODS = frozenset(m.upper() for m in _CLIENT_METHODS)
_FLASK_PARAM = re.compile(r"<[^>]+>")
_ROUTE_DETAIL = re.compile(r"^\s*(?P<methods>[A-Za-z][A-Za-z,|\s]*?)\s+(?P<path>/\S*)\s*$")


def _t(src: bytes, node) -> str:
    return src[node.start_byte:node.end_byte].decode("utf-8", "replace")


def _template(src: bytes, node) -> str | None:
    """A URL expression with every non-literal part as a hole: a string,
    an f-string (``{...}`` -> hole) or a ``+`` concatenation of those / names."""
    if node is None:
        return None
    if node.type == "string":
        out = []
        for c in node.named_children:
            if c.type == "string_content":
                out.append(_t(src, c))
            elif c.type == "interpolation":
                out.append(_HOLE)
            elif c.type == "escape_sequence":
                out.append(_t(src, c))
        return "".join(out)
    if node.type == "concatenated_string":
        parts = [_template(src, c) for c in node.named_children]
        return None if any(p is None for p in parts) else "".join(parts)
    if node.type == "binary_operator":
        op = node.child_by_field_name("operator")
        if op is None or _t(src, op) != "+":
            return None
        left = _template(src, node.child_by_field_name("left"))
        right = _template(src, node.child_by_field_name("right"))
        if left is None and right is None:
            return None
        return (left if left is not None else _HOLE) + (right if right is not None else _HOLE)
    if node.type == "parenthesized_expression" and node.named_children:
        return _template(src, node.named_children[0])
    return None


def _args(call) -> tuple[list, dict]:
    args = call.child_by_field_name("arguments")
    pos, kw = [], {}
    for a in (args.named_children if args is not None else ()):
        if a.type == "keyword_argument":
            name = a.child_by_field_name("name")
            if name is not None:
                kw[name.text.decode("utf-8", "replace")] = a.child_by_field_name("value")
        elif a.type not in ("comment", "list_splat", "dictionary_splat"):
            pos.append(a)
    return pos, kw


def _string(src: bytes, node) -> str | None:
    t = _template(src, node)
    return None if t is None or _HOLE in t else t


def extract(path: str, source: bytes) -> FileFacts:
    """The web requests a Python file sends. Never raises."""
    ff = FileFacts(path)
    if not any(w in source for w in (b"get", b"post", b"put", b"patch", b"delete", b"request", b"urlopen")):
        return ff
    try:
        root = Parser(get_ts_language("python")).parse(source).root_node
    except Exception:  # noqa: BLE001 -- an unparsable file contributes nothing
        return ff
    stack = [root]
    while stack:
        n = stack.pop()
        stack.extend(reversed(n.named_children))
        if n.type != "call":
            continue
        fn = n.child_by_field_name("function")
        if fn is None:
            continue
        name = _t(source, fn).replace(" ", "").replace("\n", "")
        last = name.rsplit(".", 1)[-1]
        pos, kw = _args(n)
        if last in _CLIENT_METHODS and "." in name:
            method, url_node = last.upper(), pos[0] if pos else kw.get("url")
        elif last == "request" and "." in name:
            m = _string(source, pos[0]) if pos else _string(source, kw.get("method"))
            method = m.upper() if m and m.isalpha() else "ANY"
            url_node = pos[1] if len(pos) > 1 else kw.get("url")
        elif last == "urlopen":
            method, url_node = "GET", pos[0] if pos else kw.get("url")
        else:
            continue
        tpl = _template(source, url_node)
        if tpl is None:
            continue
        url = normalize_url(tpl)
        # `f"{BASE}/{path}"` is a request to *somewhere*, but with no literal
        # segment there is nothing a route could be matched on
        if url is None or all(seg == ":param" for seg in url.split("/") if seg):
            continue
        ff.requests.append(Request(method, url, _t(source, url_node), n.start_point[0] + 1, name))
    return ff


def served_from_entry_points(ep_map: dict[str, list[tuple[str, str]]], keys: set[str]) -> list[Served]:
    """Python ``route`` entry points (``{key: [(kind, "GET /report"), ...]}``,
    from ``impact.entry_points``) as served routes. JS routes are left to
    ``jsfacts``, which also follows their ``app.use`` mounts."""
    out: list[Served] = []
    for key, eps in ep_map.items():
        file = key.split("::", 1)[0]
        if not file.endswith((".py", ".pyi")) or key not in keys:
            continue
        for kind, detail in eps:
            # "GET /report", "GET, POST /users/<int:id>" (entrypoints._py_route_methods)
            m = _ROUTE_DETAIL.match(detail or "") if kind == "route" else None
            if m is None:
                continue
            path = _FLASK_PARAM.sub(":param", m.group("path"))
            for verb in re.split(r"[,|\s]+", m.group("methods").upper()):
                if verb and verb != "WS":
                    out.append(Served(verb if verb in _SERVED_METHODS else "ANY", path, key, file, 0))
    return out


def python_files(file_rows: Iterable) -> list[str]:
    return [r["path"] for r in file_rows if r["lang"] == "python"]
