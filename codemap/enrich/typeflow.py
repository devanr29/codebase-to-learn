"""Type-aware call resolution for Python and TypeScript/JavaScript.

codemap's base call graph (``impact.call_graph``) matches calls by *name*, with
import evidence and a receiver hint deciding how much to trust each match. That
cannot tell ``self.repo.get()`` (a ``Repo``) from ``cache.get()`` (a dict), so the
first is left out and same-named methods on unrelated classes become AMBIGUOUS
guesses. This module adds the missing piece: it re-reads each file's syntax tree
and works out what each receiver *is*.

The design is a port of codebase-memory-mcp's "Hybrid LSP" layer (MIT, (c) 2025
DeusData; ``internal/cbm/lsp/py_lsp.c`` and ``ts_lsp.c``), cut down to what a
call graph needs and written in Python over codemap's own tree-sitter parse:

1. **bind** -- every scope's names: classes, functions, variables, parameters
   and imports (followed through re-exports, ``__init__.py`` and barrel files);
2. **type an expression** -- parameter annotations, ``x = Foo()``, ``x: Foo``,
   ``self.attr = ...`` / class fields / constructor parameter properties,
   return annotations or a function's agreeing ``return`` statements,
   ``with ... as``, ``for x in xs`` over ``list[Foo]``, ``await``, ``super()``,
   ``as Foo`` casts;
3. **look up an attribute** through the class and its repo base classes;
4. **decide each call site**: ``resolved`` to symbol keys (a method call also
   reaches every override in a repo subclass, since that is what can run),
   ``external`` (a builtin, stdlib or third-party target: no repo edge can
   exist), or ``unknown``.

Typing is flow-insensitive and conservative: a variable bound to two different
types anywhere in its scope is unknown, never a guess, and so is a call to an
abstract or ``Protocol`` method with no repo override. A wrong "resolved" would
be drawn as fact, so anything unsure stays ``unknown`` and codemap's own
name-based edges for it are left exactly as they were.

Nothing here raises for a problem in a file: a file that cannot be read or
parsed is skipped.
"""

from __future__ import annotations

import builtins as _py_builtins
import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Callable, Iterable

from .. import resolve
from ..languages.registry import get_ts_language, spec_for_path

PY, JS = "py", "js"
_FAMILY = {"python": PY, "javascript": JS, "typescript": JS, "tsx": JS}

# call-site outcomes
RESOLVED, EXTERNAL, UNKNOWN = "resolved", "external", "unknown"

# A type is a small tuple, or None for "unknown":
#   ("inst", ClassInfo)        an instance       ("cls", ClassInfo)  the class itself
#   ("fn", FuncInfo, recv)     a function value  ("mod", path)       a repo module
#   ("list", elem)             a sequence        ("await", inner)    a coroutine / Promise
#   ("ext",)                   a builtin or third-party value (never a repo symbol)
#   ("none",)                  None / null / undefined -- ignored when bindings are joined
#   ("self",)                  a return type of "the receiver's own class"
EXT: tuple = ("ext",)
NONE: tuple = ("none",)
SELF: tuple = ("self",)

_MAX_DEPTH = 48
_MAX_STEPS_PER_FILE = 60000
_MAX_EXPORT_HOPS = 8

_PY_BUILTINS = frozenset(dir(_py_builtins))
_JS_GLOBALS = frozenset(
    """console JSON Math Object Array Promise String Number Boolean Date RegExp Error
    TypeError RangeError Map Set WeakMap WeakSet Symbol BigInt Reflect Proxy Intl
    fetch setTimeout clearTimeout setInterval clearInterval queueMicrotask
    structuredClone parseInt parseFloat isNaN isFinite encodeURIComponent
    decodeURIComponent encodeURI decodeURI atob btoa window document navigator
    localStorage sessionStorage location history globalThis process require
    Buffer URL URLSearchParams FormData Headers Request Response AbortController
    Event CustomEvent alert confirm prompt performance crypto undefined NaN
    Infinity""".split()
)

# Wrappers whose first type argument is the type that matters.
_UNWRAP = frozenset(
    """Optional Annotated ClassVar Final Mapped Required NotRequired ReadOnly InitVar
    Partial Readonly NonNullable""".split()
)
_SEQ = frozenset(
    """list List Sequence MutableSequence Iterable Iterator Generator AsyncIterator
    AsyncIterable AsyncGenerator Collection Set set FrozenSet frozenset Deque deque
    tuple Tuple Array ReadonlyArray""".split()
)
_AWAITABLE = frozenset("Awaitable Coroutine Promise PromiseLike".split())
_CLASS_OF = frozenset("Type type".split())
_OPAQUE = frozenset("Any any unknown object never".split())
_EXT_TYPES = frozenset(
    """str int float complex bool bytes bytearray dict Dict Mapping MutableMapping
    DefaultDict defaultdict OrderedDict Counter memoryview range slice
    string number boolean bigint symbol Record Map WeakMap WeakSet Date RegExp
    Error Function""".split()
)

_PY_SCOPES = ("function_definition", "class_definition", "lambda")
_JS_FUNCS = ("function_declaration", "generator_function_declaration", "function_expression",
             "function", "arrow_function", "generator_function")
_JS_CLASSES = ("class_declaration", "abstract_class_declaration", "class")
_SCOPE_NODES = frozenset(_PY_SCOPES + _JS_FUNCS + _JS_CLASSES + ("method_definition",))


# --------------------------------------------------------------------------- data


@dataclass
class FuncInfo:
    key: str | None
    name: str
    file: "FileInfo"
    node: object
    owner: "ClassInfo | None" = None
    parent: "Ctx | None" = None      # the scope it is written in (closures)
    decorators: tuple[str, ...] = ()
    is_static: bool = False
    is_async: bool = False
    ctx: "Ctx | None" = None
    ret: object = "unset"

    def has_decorator(self, *names: str) -> bool:
        return any(d.rsplit(".", 1)[-1] in names for d in self.decorators)

    @property
    def is_property(self) -> bool:
        return self.has_decorator("property", "cached_property", "get")

    @property
    def is_classmethod(self) -> bool:
        return self.has_decorator("classmethod")


@dataclass
class ClassInfo:
    key: str | None
    name: str
    file: "FileInfo"
    node: object
    parent: "Ctx | None" = None
    base_nodes: list = field(default_factory=list)
    methods: dict[str, FuncInfo] = field(default_factory=dict)
    others: list[FuncInfo] = field(default_factory=list)   # property setters / deleters
    # attr -> [(kind, payload, ctx)]: "annot" (type text) or "assign" (expression node)
    fields: dict[str, list] = field(default_factory=dict)
    body_ctx: "Ctx | None" = None
    protocol: bool = False
    scanned: bool = False
    bases_cache: list | None = None
    field_memo: dict = field(default_factory=dict)


@dataclass
class FileInfo:
    path: str
    fam: str
    src: bytes
    root: object
    ctx: "Ctx | None" = None
    wildcard: bool = False
    exports: dict[str, tuple] = field(default_factory=dict)   # JS: name -> ("local", n) / ("from", spec, n) / ...
    star_from: list[str] = field(default_factory=list)        # JS: `export * from` specifiers
    spans: list = field(default_factory=list)                 # (start, end, key) of codemap symbols


@dataclass(frozen=True)
class Site:
    src: str                    # codemap key of the symbol the call is written in
    name: str                   # the called name, as written
    line: int
    outcome: str                # RESOLVED / EXTERNAL / UNKNOWN
    targets: tuple[str, ...] = ()
    strategy: str = ""          # how the target was found: local / import / module / self / type / super / new


@dataclass
class Result:
    sites: list[Site] = field(default_factory=list)
    files: int = 0
    failed: list[str] = field(default_factory=list)


class Ctx:
    """One scope: a module, a class body or a function. ``sites`` lists every
    binding of each local name (flow-insensitive); ``value`` joins them."""

    __slots__ = ("file", "parent", "owner", "func", "sites", "skip", "self_name",
                 "self_kind", "memo", "busy")

    def __init__(self, file: FileInfo, parent: "Ctx | None", owner: ClassInfo | None = None,
                 func: FuncInfo | None = None) -> None:
        self.file = file
        self.parent = parent
        self.owner = owner
        self.func = func
        self.sites: dict[str, list] = {}
        self.skip: set[str] = set()      # `global` / `nonlocal` names: bound elsewhere
        self.self_name: str | None = None
        self.self_kind = "inst"
        self.memo: dict[str, tuple | None] = {}
        self.busy: set[str] = set()

    def add(self, name: str, kind: str, payload=None) -> None:
        if name and name not in self.skip:
            self.sites.setdefault(name, []).append((kind, payload))


# --------------------------------------------------------------------------- helpers


def _text(f: FileInfo, node) -> str:
    return f.src[node.start_byte:node.end_byte].decode("utf-8", "replace")


def _field(node, name: str):
    if node is None:
        return None
    try:
        return node.child_by_field_name(name)
    except Exception:  # noqa: BLE001 -- a grammar without that field
        return None


def _named(node) -> list:
    return [node.named_child(i) for i in range(node.named_child_count)] if node is not None else []


def _same(a, b) -> bool:
    if a is None or b is None or a[0] != b[0]:
        return False
    if a[0] in ("inst", "cls", "fn"):
        return a[1] is b[1]
    if a[0] in ("list", "await"):
        return a[1] == b[1] or _same(a[1], b[1])
    return a == b


def _join(types: Iterable) -> tuple | None:
    """The one type every binding agrees on, ignoring None/null; else unknown."""
    seen: tuple | None = None
    for t in types:
        if t is None:
            return None
        if t == NONE:
            continue
        if seen is not None and not _same(seen, t):
            return None
        seen = t
    return seen if seen is not None else NONE


def _split_top(text: str, seps: str) -> list[str]:
    """Split on any char in ``seps`` outside brackets and quotes."""
    out, depth, cur, quote = [], 0, [], ""
    i = 0
    while i < len(text):
        ch = text[i]
        if quote:
            cur.append(ch)
            if ch == quote:
                quote = ""
        elif ch in "'\"`":
            quote = ch
            cur.append(ch)
        elif ch == "=" and text[i + 1:i + 2] == ">":     # an arrow, not a closing bracket
            cur.append("=>")
            i += 1
        elif ch in "[<({":
            depth += 1
            cur.append(ch)
        elif ch in "]>)}":
            depth = max(0, depth - 1)
            cur.append(ch)
        elif ch in seps and depth == 0:
            out.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
        i += 1
    out.append("".join(cur).strip())
    return [p for p in out if p]


def _decorators(f: FileInfo, decorated) -> tuple[str, ...]:
    return tuple(_text(f, d).lstrip("@").split("(")[0].strip()
                 for d in _named(decorated) if d.type == "decorator")


# --------------------------------------------------------------------------- engine


class TypeFlow:
    def __init__(self, paths: set[str], aliases: list | None = None) -> None:
        self.paths = paths
        self.aliases = aliases or []
        self.files: dict[str, FileInfo] = {}
        self.classes: list[ClassInfo] = []
        self.keys: set[str] = set()
        self._by_line: dict[tuple[str, int], list[tuple[str, str]]] = {}
        self._spans: dict[str, list] = {}
        self._wanted: dict[tuple[str, str], object] = {}
        self._pending: list[tuple[str, str]] = []
        self._py_mod_cache: dict[tuple[str, str], tuple] = {}
        self._mod_bases: dict[str, set[str]] | None = None
        self._dirs: set[str] | None = None
        self._subclasses: dict[int, list[ClassInfo]] | None = None
        self._depth = 0
        self._steps = 0

    # ------------------------------------------------------------- symbols

    def add_symbols(self, rows: Iterable) -> None:
        for r in rows:
            key, name, file = r["key"], r["name"], r["file"]
            start = int(r["start_line"] or 0)
            end = int(r["end_line"] or start)
            self.keys.add(key)
            self._by_line.setdefault((file, start), []).append((name, key))
            self._spans.setdefault(file, []).append((start, end, key))

    def _key_at(self, f: FileInfo, node, name: str) -> str | None:
        """The codemap key of the definition ``node`` (matched on file, first line
        and name -- a decorated definition is keyed by its ``def`` line)."""
        rows = [node.start_point[0] + 1]
        if node.parent is not None and node.parent.type in ("variable_declarator", "lexical_declaration",
                                                             "export_statement", "decorated_definition"):
            rows.append(node.parent.start_point[0] + 1)
            if node.parent.parent is not None:
                rows.append(node.parent.parent.start_point[0] + 1)
        for row in rows:
            for n, k in self._by_line.get((f.path, row), ()):
                if n == name:
                    return k
        return None

    def _enclosing(self, f: FileInfo, line: int) -> str | None:
        best: tuple[int, str] | None = None
        for start, end, key in f.spans:
            if start > line:
                break
            if line <= end and (best is None or end - start <= best[0]):
                best = (end - start, key)
        return best[1] if best else None

    # ------------------------------------------------------------- loading

    def load(self, path: str, lang: str, src: bytes) -> bool:
        fam = _FAMILY.get(lang)
        spec = spec_for_path(path)
        if fam is None or spec is None:
            return False
        try:
            from tree_sitter import Parser

            tree = Parser(get_ts_language(spec.grammar)).parse(src)
        except Exception:  # noqa: BLE001 -- an unparsable file is simply skipped
            return False
        f = FileInfo(path, fam, src, tree.root_node)
        f.spans = sorted(self._spans.get(path, ()))
        f.ctx = Ctx(f, None)
        self.files[path] = f
        if fam == PY:
            self._py_collect(f.ctx, tree.root_node)
        else:
            self._js_collect(f.ctx, tree.root_node)
        return True

    def finish_loading(self) -> None:
        """Classify every import the files named in one batch (the import
        resolver builds its package index once per call)."""
        if self._pending:
            for ri in resolve.resolve_imports(self._pending, self.paths, aliases=self.aliases):
                self._wanted[(ri.importer, ri.raw)] = ri
            self._pending = []

    def _want(self, importer: str, raw: str) -> None:
        if (importer, raw) not in self._wanted:
            self._wanted[(importer, raw)] = None
            self._pending.append((importer, raw))

    # ------------------------------------------------------------- module paths

    def _dir_set(self) -> set[str]:
        if self._dirs is None:
            dirs: set[str] = set()
            for p in self.paths:
                parts = p.split("/")
                for i in range(1, len(parts)):
                    dirs.add("/".join(parts[:i]))
            self._dirs = dirs
        return self._dirs

    def _py_file_for(self, stem: str) -> str | None:
        """``pkg/mod`` -> its ``.py`` / ``__init__.py``, or ``pkg/mod/`` for a
        namespace package, or None."""
        stem = stem.strip("/")
        if not stem:
            return "/"
        for cand in (f"{stem}.py", f"{stem}.pyi", f"{stem}/__init__.py"):
            if cand in self.paths:
                return cand
        return stem + "/" if stem in self._dir_set() else None

    def _bases_for(self, first: str, importer: str) -> list[str]:
        if self._mod_bases is None:
            idx: dict[str, set[str]] = {}
            for p in self.paths:
                segs = p.split("/")
                for i, seg in enumerate(segs):
                    name = seg.rsplit(".", 1)[0] if i == len(segs) - 1 else seg
                    idx.setdefault(name, set()).add("/".join(segs[:i]))
            self._mod_bases = idx
        here = str(PurePosixPath(importer).parent)
        return sorted(self._mod_bases.get(first, ()),
                      key=lambda b: (0 if b == "" else 1 if here == b or here.startswith(b + "/") else 2, b))

    def _py_module(self, f: FileInfo, mod: str) -> tuple:
        """The module ``mod`` names as seen from ``f``: ("mod", path), EXT or None."""
        ck = (f.path, mod)
        if ck in self._py_mod_cache:
            return self._py_mod_cache[ck]
        ups = len(mod) - len(mod.lstrip("."))
        parts = [p for p in mod[ups:].split(".") if p]
        out: tuple | None = None
        if ups:
            base = PurePosixPath(f.path).parent
            for _ in range(ups - 1):
                base = base.parent
            stem = "/".join([s for s in str(base).split("/") if s not in ("", ".")] + parts)
            hit = self._py_file_for(stem)
            out = ("mod", hit) if hit else None
        elif parts:
            for b in self._bases_for(parts[0], f.path):
                hit = self._py_file_for("/".join(([b] if b else []) + parts))
                if hit and hit != "/":
                    out = ("mod", hit)
                    break
            if out is None:
                ri = self._wanted.get((f.path, f"import {mod}"))
                if ri is not None and (ri.external or ri.kind in ("stdlib", "third_party")):
                    out = EXT
        self._py_mod_cache[ck] = out
        return out

    def _js_module(self, f: FileInfo, spec: str) -> tuple | None:
        ri = self._wanted.get((f.path, f"import x from '{spec}'"))
        if ri is None:
            return None
        if ri.external or ri.kind in ("stdlib", "third_party"):
            return EXT
        return ("mod", ri.target) if ri.target else None

    # ------------------------------------------------------------- python: collect

    def _py_collect(self, ctx: Ctx, node) -> None:
        """Record the bindings a scope makes, without entering nested scopes."""
        f = ctx.file
        for child in _named(node):
            t = child.type
            if t == "decorated_definition":
                inner = _field(child, "definition")
                if inner is not None:
                    self._py_define(ctx, inner, _decorators(f, child))
                continue
            if t in ("function_definition", "class_definition"):
                self._py_define(ctx, child, ())
                continue
            if t == "lambda":
                continue
            if t in ("import_statement", "import_from_statement"):
                self._py_import(ctx, child)
                continue
            if t in ("global_statement", "nonlocal_statement"):
                for n in _named(child):
                    if n.type == "identifier":
                        ctx.skip.add(_text(f, n))
                        ctx.sites.pop(_text(f, n), None)
                continue
            if t == "assignment":
                self._py_assign(ctx, child)
            elif t == "named_expression":
                name = _field(child, "name")
                if name is not None:
                    ctx.add(_text(f, name), "assign", _field(child, "value"))
            elif t in ("for_statement", "for_in_clause"):
                left, right = _field(child, "left"), _field(child, "right")
                if left is not None and left.type == "identifier":
                    ctx.add(_text(f, left), "for", right)
                else:
                    self._unknown_names(ctx, left)
            elif t == "as_pattern":
                alias = _field(child, "alias")
                expr = child.named_child(0) if child.named_child_count else None
                ident = alias.named_child(0) if alias is not None and alias.named_child_count else alias
                if child.parent is not None and child.parent.type == "with_item" \
                        and ident is not None and ident.type == "identifier":
                    ctx.add(_text(f, ident), "with", expr)
                else:
                    self._unknown_names(ctx, ident)
            elif t in ("case_pattern", "keyword_pattern", "pattern_list", "tuple_pattern", "list_pattern"):
                self._unknown_names(ctx, child)
            self._py_collect(ctx, child)

    def _unknown_names(self, ctx: Ctx, node) -> None:
        if node is None:
            return
        if node.type in ("identifier", "shorthand_property_identifier_pattern"):
            ctx.add(_text(ctx.file, node), "unknown")
            return
        for c in _named(node):
            self._unknown_names(ctx, c)

    def _py_assign(self, ctx: Ctx, node) -> None:
        f = ctx.file
        left, right, ann = _field(node, "left"), _field(node, "right"), _field(node, "type")
        if left is None:
            return
        value = right
        while value is not None and value.type == "assignment":     # a = b = Foo()
            value = _field(value, "right")
        if left.type == "identifier":
            name = _text(f, left)
            if ann is not None:
                ctx.add(name, "annot", _text(f, ann))
            elif value is not None:
                ctx.add(name, "assign", value)
            else:
                ctx.add(name, "unknown")
        elif left.type == "attribute":
            obj, attr = _field(left, "object"), _field(left, "attribute")
            if (ctx.func is not None and ctx.owner is not None and obj is not None and attr is not None
                    and obj.type == "identifier" and _text(f, obj) == ctx.self_name):
                entries = ctx.owner.fields.setdefault(_text(f, attr), [])
                if ann is not None:
                    entries.append(("annot", _text(f, ann), ctx))
                if value is not None:      # also kept beside an annotation: it can contradict it
                    entries.append(("assign", value, ctx))
        else:
            self._unknown_names(ctx, left)

    def _py_import(self, ctx: Ctx, node) -> None:
        f = ctx.file
        if node.type == "import_statement":
            for item in _named(node):
                if item.type == "aliased_import":
                    mod = _text(f, _field(item, "name"))
                    self._want(f.path, f"import {mod}")
                    ctx.add(_text(f, _field(item, "alias")), "import", ("pymod", mod))
                elif item.type == "dotted_name":
                    head = _text(f, item).split(".")[0]
                    self._want(f.path, f"import {head}")
                    ctx.add(head, "import", ("pymod", head))
            return
        mod_node = _field(node, "module_name")
        mod = _text(f, mod_node) if mod_node is not None else ""
        if mod and not mod.startswith("."):
            self._want(f.path, f"import {mod}")
        for item in _named(node):
            if mod_node is not None and item.start_byte == mod_node.start_byte:
                continue
            if item.type == "wildcard_import":
                f.wildcard = True
            elif item.type == "aliased_import":
                ctx.add(_text(f, _field(item, "alias")), "import", ("pyfrom", mod, _text(f, _field(item, "name"))))
            elif item.type == "dotted_name":
                name = _text(f, item)
                ctx.add(name.split(".")[0], "import", ("pyfrom", mod, name))

    def _py_define(self, ctx: Ctx, node, decos: tuple[str, ...]) -> None:
        f = ctx.file
        name_node = _field(node, "name")
        if name_node is None:
            return
        name = _text(f, name_node)
        key = self._key_at(f, node, name)
        in_class_body = ctx.owner is not None and ctx.func is None
        if node.type == "class_definition":
            ci = ClassInfo(key, name, f, node, parent=ctx)
            supers = _field(node, "superclasses")
            ci.base_nodes = [b for b in _named(supers) if b.type != "keyword_argument"]
            ci.protocol = any(_text(f, b).split("[")[0].rsplit(".", 1)[-1] == "Protocol" for b in ci.base_nodes)
            ci.body_ctx = Ctx(f, ctx, owner=ci)
            self._py_class_body(ci, _field(node, "body"))
            self.classes.append(ci)
            ctx.add(name, "cls", ci)
            return
        fi = FuncInfo(key, name, f, node, owner=ctx.owner if in_class_body else None,
                      parent=ctx.parent if in_class_body else ctx, decorators=decos)
        fi.is_async = _text(f, node).lstrip().startswith("async")
        fi.is_static = fi.has_decorator("staticmethod")
        if fi.owner is not None:
            if any(d.endswith((".setter", ".deleter")) for d in decos):
                fi.owner.others.append(fi)
                return
            fi.owner.methods[name] = fi    # last definition wins (an @overload stub before the real one)
        ctx.add(name, "fn", fi)

    def _py_class_body(self, ci: ClassInfo, body) -> None:
        f = ci.file
        bctx = ci.body_ctx
        for child in _named(body):
            node = child.named_child(0) if child.type == "expression_statement" and child.named_child_count else child
            if child.type == "decorated_definition":
                inner = _field(child, "definition")
                if inner is not None:
                    self._py_define(bctx, inner, _decorators(f, child))
            elif child.type in ("function_definition", "class_definition"):
                self._py_define(bctx, child, ())
            elif node.type == "assignment":
                left = _field(node, "left")
                if left is not None and left.type == "identifier":
                    ann, right = _field(node, "type"), _field(node, "right")
                    entries = ci.fields.setdefault(_text(f, left), [])
                    if ann is not None:
                        entries.append(("annot", _text(f, ann), bctx))
                    if right is not None:
                        entries.append(("assign", right, bctx))
                self._py_assign(bctx, node)

    def _py_func_ctx(self, fi: FuncInfo) -> Ctx:
        f = fi.file
        ctx = Ctx(f, fi.parent if fi.parent is not None else f.ctx, owner=fi.owner, func=fi)
        fi.ctx = ctx
        first = True
        for p in _named(_field(fi.node, "parameters")):
            pname, ann, default = None, None, None
            if p.type == "identifier":
                pname = _text(f, p)
            elif p.type in ("typed_parameter", "typed_default_parameter", "default_parameter"):
                nm = _field(p, "name") or (p.named_child(0) if p.named_child_count else None)
                pname = _text(f, nm) if nm is not None and nm.type == "identifier" else None
                t = _field(p, "type")
                ann = _text(f, t) if t is not None else None
                default = _field(p, "value")
            else:   # *args, **kwargs, a bare `*` / `/`
                self._unknown_names(ctx, p)
                first = False
                continue
            if pname is None:
                first = False
                continue
            if first and fi.owner is not None and not fi.is_static:
                ctx.self_name = pname
                ctx.self_kind = "cls" if fi.is_classmethod or fi.name in ("__new__", "__init_subclass__",
                                                                           "__class_getitem__") else "inst"
                ctx.add(pname, "type", (ctx.self_kind, fi.owner))
            elif ann is not None:
                ctx.add(pname, "annot", ann)
            elif default is not None:
                ctx.add(pname, "assign", default)
            else:
                ctx.add(pname, "unknown")
            first = False
        self._py_collect(ctx, _field(fi.node, "body"))
        return ctx

    # ------------------------------------------------------------- js/ts: collect

    def _js_collect(self, ctx: Ctx, node) -> None:
        f = ctx.file
        for child in _named(node):
            t = child.type
            if t in _JS_CLASSES:
                self._js_class(ctx, child)
                continue
            if t in ("function_declaration", "generator_function_declaration"):
                self._js_function(ctx, child, _field(child, "name"))
                continue
            if t in _JS_FUNCS:
                continue      # an anonymous function inside an expression: its own scope
            if t == "import_statement":
                self._js_import(ctx, child)
                continue
            if t == "export_statement":
                self._js_export(ctx, child)
                value = _field(child, "value")
                if value is not None and value.type in _JS_CLASSES:
                    self._js_class(ctx, value)
                elif value is not None and value.type in _JS_FUNCS:
                    self._js_function(ctx, value, _field(value, "name"))
                self._js_collect(ctx, child)
                continue
            if t == "variable_declarator":
                self._js_declarator(ctx, child)
                continue
            if t in ("interface_declaration", "type_alias_declaration", "enum_declaration"):
                nm = _field(child, "name")
                if nm is not None:
                    ctx.add(_text(f, nm), "unknown")
                continue
            if t == "assignment_expression":
                self._js_assign(ctx, child)
            elif t == "for_in_statement":
                left, right = _field(child, "left"), _field(child, "right")
                is_of = left is not None and right is not None and b"of" in f.src[left.end_byte:right.start_byte]
                if left is not None and left.type == "identifier" and right is not None:
                    ctx.add(_text(f, left), "for" if is_of else "unknown", right)
                else:
                    self._unknown_names(ctx, left)
            elif t == "catch_clause":
                self._unknown_names(ctx, _field(child, "parameter"))
            self._js_collect(ctx, child)

    def _js_declarator(self, ctx: Ctx, node) -> None:
        f = ctx.file
        name, value, typ = _field(node, "name"), _field(node, "value"), _field(node, "type")
        if name is None:
            return
        if name.type != "identifier":
            self._unknown_names(ctx, name)
            self._js_collect(ctx, node)
            return
        nm = _text(f, name)
        if value is not None and value.type in _JS_FUNCS:
            self._js_function(ctx, value, name)
            return
        if value is not None and value.type == "class":
            self._js_class(ctx, value, name_override=nm)
            return
        if value is not None and value.type == "call_expression":
            fn = _field(value, "function")
            if fn is not None and _text(f, fn) == "require":
                spec = self._js_string_arg(f, _field(value, "arguments"))
                if spec is not None:
                    self._want(f.path, f"import x from '{spec}'")
                    ctx.add(nm, "import", ("jsmod", spec))
                    return
        if typ is not None:
            ctx.add(nm, "annot", _text(f, typ).lstrip(":").strip())
        elif value is not None:
            ctx.add(nm, "assign", value)
        else:
            ctx.add(nm, "unknown")
        if value is not None:
            self._js_collect(ctx, node)

    @staticmethod
    def _js_string_arg(f: FileInfo, args) -> str | None:
        first = _named(args)[:1]
        if first and first[0].type == "string":
            return _text(f, first[0]).strip("'\"`")
        return None

    def _js_assign(self, ctx: Ctx, node) -> None:
        f = ctx.file
        left, right = _field(node, "left"), _field(node, "right")
        if left is None:
            return
        if left.type == "identifier":
            ctx.add(_text(f, left), "assign", right)
        elif left.type == "member_expression":
            obj, prop = _field(left, "object"), _field(left, "property")
            if obj is not None and obj.type == "this" and prop is not None and ctx.owner is not None \
                    and ctx.self_name == "this":
                ctx.owner.fields.setdefault(_text(f, prop), []).append(("assign", right, ctx))

    def _js_import(self, ctx: Ctx, node) -> None:
        f = ctx.file
        source = _field(node, "source")
        if source is None:
            return
        spec = _text(f, source).strip("'\"`")
        self._want(f.path, f"import x from '{spec}'")
        clause = next((c for c in _named(node) if c.type == "import_clause"), None)
        for c in _named(clause):
            if c.type == "identifier":
                ctx.add(_text(f, c), "import", ("jsfrom", spec, "default"))
            elif c.type == "namespace_import":
                ident = next((x for x in _named(c) if x.type == "identifier"), None)
                if ident is not None:
                    ctx.add(_text(f, ident), "import", ("jsmod", spec))
            elif c.type == "named_imports":
                for s in _named(c):
                    if s.type != "import_specifier":
                        continue
                    name, alias = _field(s, "name"), _field(s, "alias")
                    if name is not None:
                        ctx.add(_text(f, alias if alias is not None else name), "import",
                                ("jsfrom", spec, _text(f, name).strip("'\"")))

    def _js_export(self, ctx: Ctx, node) -> None:
        f = ctx.file
        source = _field(node, "source")
        spec = _text(f, source).strip("'\"`") if source is not None else None
        if spec is not None:
            self._want(f.path, f"import x from '{spec}'")
        text = _text(f, node)
        clause = next((c for c in _named(node) if c.type == "export_clause"), None)
        if clause is not None:
            for s in _named(clause):
                if s.type != "export_specifier":
                    continue
                name, alias = _field(s, "name"), _field(s, "alias")
                if name is None:
                    continue
                exported = _text(f, alias if alias is not None else name)
                f.exports[exported] = ("from", spec, _text(f, name)) if spec else ("local", _text(f, name))
            return
        if spec is not None:
            ns = re.match(r"export\s+\*\s+as\s+([\w$]+)", text)
            if ns:
                f.exports[ns.group(1)] = ("ns", spec)
            elif re.match(r"export\s+\*", text):
                f.star_from.append(spec)
            return
        decl = _field(node, "declaration")
        if decl is not None:
            nm = _field(decl, "name")
            if nm is not None:
                f.exports[_text(f, nm)] = ("local", _text(f, nm))
            for d in _named(decl):
                if d.type == "variable_declarator":
                    dn = _field(d, "name")
                    if dn is not None and dn.type == "identifier":
                        f.exports[_text(f, dn)] = ("local", _text(f, dn))
        if re.match(r"export\s+default\b", text):
            val = _field(node, "value") or decl
            if val is not None:
                nm = val if val.type == "identifier" else _field(val, "name")
                f.exports["default"] = ("local", _text(f, nm)) if nm is not None else ("expr", val)

    def _js_class(self, ctx: Ctx, node, name_override: str | None = None) -> None:
        f = ctx.file
        name_node = _field(node, "name")
        name = name_override or (_text(f, name_node) if name_node is not None else None)
        if not name:
            return
        ci = ClassInfo(self._key_at(f, node, name), name, f, node, parent=ctx)
        heritage = next((c for c in _named(node) if c.type == "class_heritage"), None)
        for clause in _named(heritage):
            if clause.type == "extends_clause":
                ci.base_nodes = [v for v in _named(clause) if v.type != "type_arguments"][:1]
            elif clause.type != "implements_clause":
                ci.base_nodes = [clause]
        ci.body_ctx = Ctx(f, ctx, owner=ci)
        for m in _named(_field(node, "body")):
            if m.type == "method_definition":
                mn = _field(m, "name")
                if mn is None:
                    continue
                mname = _text(f, mn)
                head = _text(f, m)[: mn.start_byte - m.start_byte]
                fi = FuncInfo(self._key_at(f, m, mname), mname, f, m, owner=ci, parent=ctx)
                fi.is_static = bool(re.search(r"\bstatic\b", head))
                fi.is_async = bool(re.search(r"\basync\b", head))
                if re.search(r"\bget\s*$", head):
                    fi.decorators = ("get",)
                elif re.search(r"\bset\s*$", head):
                    ci.others.append(fi)
                    continue
                if mname == "constructor":
                    self._js_param_properties(ci, m)
                ci.methods[mname] = fi
            elif m.type in ("public_field_definition", "field_definition"):
                nm = _field(m, "name") or _field(m, "property")
                if nm is None:
                    continue
                fname = _text(f, nm)
                typ, val = _field(m, "type"), _field(m, "value")
                if val is not None and val.type in ("arrow_function", "function_expression", "function"):
                    fi = FuncInfo(None, fname, f, val, owner=ci, parent=ctx)
                    fi.is_static = bool(re.search(r"\bstatic\b", _text(f, m)[: nm.start_byte - m.start_byte]))
                    ci.methods.setdefault(fname, fi)
                elif typ is not None:
                    ci.fields.setdefault(fname, []).append(("annot", _text(f, typ).lstrip(":").strip(), ci.body_ctx))
                elif val is not None:
                    ci.fields.setdefault(fname, []).append(("assign", val, ci.body_ctx))
        self.classes.append(ci)
        ctx.add(name, "cls", ci)

    def _js_param_properties(self, ci: ClassInfo, m) -> None:
        """``constructor(private readonly repo: Repo)`` declares a field ``repo``."""
        f = ci.file
        for p in _named(_field(m, "parameters")):
            if p.type not in ("required_parameter", "optional_parameter"):
                continue
            pat, typ = _field(p, "pattern"), _field(p, "type")
            if pat is None or pat.type != "identifier" or typ is None:
                continue
            head = _text(f, p)[: pat.start_byte - p.start_byte]
            if re.search(r"\b(public|private|protected|readonly|override)\b", head):
                ci.fields.setdefault(_text(f, pat), []).append(("annot", _text(f, typ).lstrip(":").strip(), ci.body_ctx))

    def _js_function(self, ctx: Ctx, node, name_node) -> FuncInfo:
        f = ctx.file
        name = _text(f, name_node) if name_node is not None else ""
        fi = FuncInfo(self._key_at(f, node, name) if name else None, name, f, node, parent=ctx)
        fi.is_async = _text(f, node).lstrip().startswith("async")
        if name:
            ctx.add(name, "fn", fi)
        return fi

    def _js_func_ctx(self, fi: FuncInfo) -> Ctx:
        f = fi.file
        this_owner = fi.owner
        if this_owner is None and fi.node.type == "arrow_function":
            # an arrow keeps the `this` of the scope it is written in
            par = fi.parent
            if par is not None and par.self_name == "this":
                this_owner = par.owner
        ctx = Ctx(f, fi.parent if fi.parent is not None else f.ctx, owner=this_owner, func=fi)
        fi.ctx = ctx
        if this_owner is not None:
            ctx.self_name = "this"
            ctx.self_kind = "cls" if fi.is_static else "inst"
        params = _field(fi.node, "parameters") or _field(fi.node, "parameter")
        for p in ([params] if params is not None and params.type == "identifier" else _named(params)):
            if p.type == "identifier":
                ctx.add(_text(f, p), "unknown")
            elif p.type == "assignment_pattern":
                left, right = _field(p, "left"), _field(p, "right")
                if left is not None and left.type == "identifier":
                    ctx.add(_text(f, left), "assign", right)
                else:
                    self._unknown_names(ctx, left)
            elif p.type in ("required_parameter", "optional_parameter"):
                pat, typ, val = _field(p, "pattern"), _field(p, "type"), _field(p, "value")
                if pat is not None and pat.type == "identifier":
                    nm = _text(f, pat)
                    if typ is not None:
                        ctx.add(nm, "annot", _text(f, typ).lstrip(":").strip())
                    elif val is not None:
                        ctx.add(nm, "assign", val)
                    else:
                        ctx.add(nm, "unknown")
                else:
                    self._unknown_names(ctx, pat)
            else:
                self._unknown_names(ctx, p)
        body = _field(fi.node, "body")
        if body is not None and body.type == "statement_block":
            self._js_collect(ctx, body)
        return ctx

    def func_ctx(self, fi: FuncInfo) -> Ctx:
        if fi.ctx is None:
            if fi.file.fam == PY:
                self._py_func_ctx(fi)
            else:
                self._js_func_ctx(fi)
        return fi.ctx

    # ------------------------------------------------------------- name lookup

    def lookup(self, ctx: Ctx, name: str) -> tuple | None:
        scope: Ctx | None = ctx
        while scope is not None:
            if name in scope.sites:
                return self._value(scope, name)
            scope = scope.parent
        f = ctx.file
        if f.fam == PY:
            return EXT if name in _PY_BUILTINS and not f.wildcard else None
        return EXT if name in _JS_GLOBALS else None

    def _value(self, scope: Ctx, name: str) -> tuple | None:
        if name in scope.memo:
            return scope.memo[name]
        if name in scope.busy:
            return None
        scope.busy.add(name)
        try:
            out = _join(self._site_value(scope, kind, payload) for kind, payload in scope.sites[name])
        finally:
            scope.busy.discard(name)
        scope.memo[name] = out
        return out

    def _site_value(self, ctx: Ctx, kind: str, payload) -> tuple | None:
        if kind == "type":
            return payload
        if kind == "fn":
            return ("fn", payload, None)
        if kind == "cls":
            return ("cls", payload)
        if kind == "annot":
            return self.parse_type(ctx, payload)
        if kind == "assign":
            return self.expr_type(ctx, payload)
        if kind == "for":
            return self._element(self.expr_type(ctx, payload))
        if kind == "with":
            t = self.expr_type(ctx, payload)
            if t is not None and t[0] == "inst":
                enter = self.find_method(t[1], "__enter__")
                if enter is not None and _field(enter.node, "return_type") is not None:
                    return self.return_type(enter, recv=t)
            return t
        if kind == "import":
            return self._import_value(ctx.file, payload)
        return None

    def _element(self, t: tuple | None) -> tuple | None:
        if t is None:
            return None
        if t[0] == "list":
            return t[1]
        if t == EXT:
            return EXT
        if t[0] == "inst":
            it = self.find_method(t[1], "__iter__")
            if it is not None:
                r = self.return_type(it, recv=t)
                return r[1] if r is not None and r[0] == "list" else None
        return None

    # ------------------------------------------------------------- imports

    def _import_value(self, f: FileInfo, payload) -> tuple | None:
        kind = payload[0]
        if kind == "pymod":
            return self._py_module(f, payload[1])
        if kind == "pyfrom":
            mod = self._py_module(f, payload[1])
            if mod is None or mod == EXT:
                return mod
            name = payload[2]
            sub = self._submodule(mod[1], name)
            if sub is not None:
                return ("mod", sub)
            return self.export(mod[1], name)
        if kind == "jsmod":
            return self._js_module(f, payload[1])
        if kind == "jsfrom":
            mod = self._js_module(f, payload[1])
            if mod is None or mod == EXT:
                return mod
            return self.export(mod[1], payload[2])
        return None

    def _submodule(self, module: str, name: str) -> str | None:
        if module.endswith("__init__.py"):
            base = module[: -len("__init__.py")]
        elif module.endswith("/"):
            base = module
        else:
            return None
        hit = self._py_file_for(base + name)
        return hit if hit != "/" else None

    def export(self, module: str, name: str, hops: int = 0) -> tuple | None:
        """The value ``name`` has when imported from repo module ``module``."""
        if hops > _MAX_EXPORT_HOPS:
            return None
        f = self.files.get(module)
        if f is None:
            if module.endswith("/"):
                sub = self._py_file_for(module + name)
                return ("mod", sub) if sub and sub != "/" else None
            return None
        if f.fam == PY:
            if name in f.ctx.sites:
                return self._value(f.ctx, name)
            sub = self._submodule(module, name)
            return ("mod", sub) if sub else None
        exp = f.exports.get(name)
        if exp is not None:
            if exp[0] == "local":
                return self._value(f.ctx, exp[1]) if exp[1] in f.ctx.sites else None
            if exp[0] in ("from", "ns"):
                mod = self._js_module(f, exp[1])
                if mod is None or mod == EXT or exp[0] == "ns":
                    return mod
                return self.export(mod[1], exp[2], hops + 1)
            if exp[0] == "expr":
                return self.expr_type(f.ctx, exp[1])
        for spec in f.star_from:
            mod = self._js_module(f, spec)
            if mod is not None and mod != EXT:
                v = self.export(mod[1], name, hops + 1)
                if v is not None:
                    return v
        if not f.exports and not f.star_from and name in f.ctx.sites:
            return self._value(f.ctx, name)          # CommonJS / a script: best effort
        return None

    # ------------------------------------------------------------- classes

    def bases(self, ci: ClassInfo) -> list[ClassInfo]:
        if ci.bases_cache is not None:
            return ci.bases_cache
        ci.bases_cache = []        # cycle guard
        out: list[ClassInfo] = []
        ctx = ci.parent or ci.file.ctx
        for b in ci.base_nodes:
            node = b
            if node.type in ("subscript", "generic_type"):
                node = _field(node, "value") or (node.named_child(0) if node.named_child_count else node)
            t = self.expr_type(ctx, node)
            if t is not None and t[0] == "cls" and t[1] is not ci:
                out.append(t[1])
        ci.bases_cache = out
        return out

    def mro(self, ci: ClassInfo) -> list[ClassInfo]:
        out: list[ClassInfo] = []
        queue = [ci]
        while queue and len(out) < 64:
            c = queue.pop(0)
            if not any(c is o for o in out):
                out.append(c)
                queue.extend(self.bases(c))
        return out

    def find_method(self, ci: ClassInfo, name: str, skip_self: bool = False) -> FuncInfo | None:
        for c in self.mro(ci)[1 if skip_self else 0:]:
            m = c.methods.get(name)
            if m is not None:
                return m
        return None

    def subclasses(self, ci: ClassInfo) -> list[ClassInfo]:
        if self._subclasses is None:
            index: dict[int, list[ClassInfo]] = {}
            for c in self.classes:
                for b in self.bases(c):
                    index.setdefault(id(b), []).append(c)
            self._subclasses = index
        out: list[ClassInfo] = []
        stack = list(self._subclasses.get(id(ci), ()))
        while stack:
            c = stack.pop()
            if not any(c is o for o in out):
                out.append(c)
                stack.extend(self._subclasses.get(id(c), ()))
        return out

    def is_stub(self, fi: FuncInfo) -> bool:
        """An abstract method: declared here, never what actually runs. A
        ``pass`` / ``{}`` body is a real no-op (a default hook), not a stub."""
        if fi.has_decorator("abstractmethod", "abstractproperty"):
            return True
        body = _field(fi.node, "body")
        if body is None:
            return True
        if fi.file.fam != PY:
            return False
        stmts = [s for s in _named(body) if s.type != "comment"]
        if stmts and stmts[0].type == "expression_statement" and stmts[0].named_child_count \
                and stmts[0].named_child(0).type == "string":
            stmts = stmts[1:]
        text = " ".join(_text(fi.file, s).strip() for s in stmts)
        return text == "..." or text.startswith("raise NotImplementedError")

    def field_type(self, ci: ClassInfo, name: str) -> tuple | None:
        for c in self.mro(ci):
            if not c.scanned:
                c.scanned = True
                for m in list(c.methods.values()):
                    self.func_ctx(m)
            entries = c.fields.get(name)
            if not entries:
                continue
            if name in c.field_memo:
                return c.field_memo[name]
            c.field_memo[name] = None      # cycle guard
            annots = [e for e in entries if e[0] == "annot"]
            out = _join(self.parse_type(ctx, p) if k == "annot" else self.expr_type(ctx, p)
                        for k, p, ctx in (annots or entries))
            if annots and out is not None and any(
                    not self.compatible(out, self.expr_type(ctx, p)) for k, p, ctx in entries if k == "assign"):
                out = None                 # the annotation is contradicted by what is assigned
            c.field_memo[name] = out
            return out
        return None

    def compatible(self, declared: tuple, actual: tuple | None) -> bool:
        """Whether a value of type ``actual`` can be what ``declared`` says.
        Unknown is never a contradiction, and neither is a third-party value
        (``field(default_factory=list)``, ``Field(...)``, ``Depends()``); a
        class is compatible with its bases."""
        if actual is None or actual == NONE or actual == EXT or declared is None:
            return True
        if declared[0] == "await" and actual[0] != "await":
            return self.compatible(declared[1], actual) if declared[1] is not None else True
        if declared[0] != actual[0]:
            return False
        if declared[0] in ("inst", "cls"):
            return any(c is declared[1] for c in self.mro(actual[1]))
        if declared[0] in ("list", "await"):
            return declared[1] is None or self.compatible(declared[1], actual[1])
        return True

    def member(self, t: tuple | None, name: str) -> tuple | None:
        """The type of ``<t>.<name>`` read as a value (not called)."""
        if t is None:
            return None
        kind = t[0]
        if kind in ("ext", "list", "await", "none"):
            return EXT
        if kind == "mod":
            return self.export(t[1], name)
        if kind in ("inst", "cls"):
            ci = t[1]
            m = self.find_method(ci, name)
            if m is not None:
                if m.is_property and kind == "inst":
                    return self.return_type(m, recv=t)
                return ("fn", m, t)
            ft = self.field_type(ci, name)
            if ft is not None:
                return ft
            for c in self.mro(ci):          # a nested class
                if c.body_ctx is not None and name in c.body_ctx.sites:
                    return self._value(c.body_ctx, name)
        return None

    # ------------------------------------------------------------- functions

    def return_type(self, fi: FuncInfo, recv: tuple | None = None) -> tuple | None:
        if fi.ret == "unset":
            fi.ret = None                   # cycle guard
            fi.ret = self._return_type(fi)
        r = fi.ret
        if r == SELF:
            if recv is not None and recv[0] in ("inst", "cls"):
                return ("inst", recv[1])
            return ("inst", fi.owner) if fi.owner is not None else None
        return r

    def _return_type(self, fi: FuncInfo) -> tuple | None:
        f = fi.file
        if fi.name in ("__init__", "constructor"):
            return None
        ctx = self.func_ctx(fi)
        rt = _field(fi.node, "return_type")
        body = _field(fi.node, "body")
        if rt is not None:
            text = _text(f, rt).lstrip(":").strip()
            if text.rsplit(".", 1)[-1] in ("Self", "this"):
                return SELF
            t = self.parse_type(ctx, text)
            # an annotation is a claim: `-> Phase` that returns a duck-typed
            # `_NullPhase` somewhere is not a Phase there, so trust it only when
            # nothing the function returns contradicts it
            if t is not None and body is not None and body.type in ("block", "statement_block"):
                rets: list = []
                self._returns(body, rets)
                if any(not self.compatible(t, self.expr_type(ctx, r)) for r in rets if r is not None):
                    return None
            return ("await", t) if fi.is_async and f.fam == PY and t is not None else t
        if body is None:
            return None
        if f.fam == JS and body.type != "statement_block":     # `() => expr`
            t = self.expr_type(ctx, body)
            return ("await", t) if fi.is_async and t is not None else t
        if self._has_yield(body):
            return None
        rets: list = []
        self._returns(body, rets)
        if not rets:
            return NONE
        vals = []
        for r in rets:
            if r is None:
                vals.append(NONE)
            elif fi.owner is not None and r.type in ("identifier", "this") and _text(f, r) == ctx.self_name:
                vals.append(SELF)
            else:
                vals.append(self.expr_type(ctx, r))
        t = _join(vals)
        if fi.is_async and t is not None and t != NONE:
            return ("await", t)
        return t

    def _returns(self, node, out: list) -> None:
        for c in _named(node):
            if c.type in _SCOPE_NODES:
                continue
            if c.type == "return_statement":
                out.append(c.named_child(0) if c.named_child_count else None)
                continue
            self._returns(c, out)

    def _has_yield(self, node) -> bool:
        for c in _named(node):
            if c.type in _SCOPE_NODES:
                continue
            if c.type in ("yield", "yield_expression") or self._has_yield(c):
                return True
        return False

    # ------------------------------------------------------------- types from annotations

    def parse_type(self, ctx: Ctx, text: str | None) -> tuple | None:
        if not text:
            return None
        text = text.strip()
        if text[:1] in "'\"" and text[-1:] == text[:1]:
            text = text[1:-1].strip()
        parts = _split_top(text, "|")
        if len(parts) > 1:
            return _join(NONE if p in ("None", "null", "undefined") else self.parse_type(ctx, p) for p in parts)
        if text in ("None", "null", "undefined", "void"):
            return NONE
        if text.endswith("[]"):
            return ("list", self.parse_type(ctx, text[:-2]))
        m = re.match(r"^([\w.$]+)\s*[\[<](.*)[\]>]$", text, re.S)
        if m:
            head, args = m.group(1), _split_top(m.group(2), ",")
            leaf = head.rsplit(".", 1)[-1]
            if leaf in _UNWRAP and args:
                return self.parse_type(ctx, args[0])
            if leaf == "Union":
                return _join(NONE if a == "None" else self.parse_type(ctx, a) for a in args)
            if leaf in _SEQ:
                return ("list", self.parse_type(ctx, args[0]) if args else None)
            if leaf in _AWAITABLE:
                return ("await", self.parse_type(ctx, args[-1]) if args else None)
            if leaf in _CLASS_OF and args:
                t = self.parse_type(ctx, args[0])
                return ("cls", t[1]) if t is not None and t[0] == "inst" else None
            if leaf in _EXT_TYPES:
                return EXT
            text = head
        if text.rsplit(".", 1)[-1] in ("Self", "this") and ctx.owner is not None:
            return ("inst", ctx.owner)
        if text in _OPAQUE:
            return None
        if text in _EXT_TYPES:
            return EXT
        if not re.match(r"^[A-Za-z_$][\w$]*(\.[A-Za-z_$][\w$]*)*$", text):
            return None
        t = self._type_name(ctx, text)
        if t is None:
            return None
        return ("inst", t[1]) if t[0] == "cls" else t

    def _type_name(self, ctx: Ctx, dotted: str) -> tuple | None:
        """A name used as a type: a class (("cls", ci)), something outside the
        repo (EXT), or unknown. A *variable* (a TypeVar, a type alias) is never
        a class, so it is unknown even when its value types as something."""
        parts = dotted.split(".")
        scope: Ctx | None = ctx
        while scope is not None and parts[0] not in scope.sites:
            scope = scope.parent
        if scope is None:
            if ctx.file.fam == PY and parts[0] in _PY_BUILTINS:
                return EXT
            return EXT if ctx.file.fam == JS and parts[0] in _JS_GLOBALS else None
        sites = scope.sites[parts[0]]
        if not all(k in ("cls", "import") for k, _ in sites):
            return None
        t = self._value(scope, parts[0])
        for p in parts[1:]:
            if t is None:
                return None
            if t[0] == "mod":
                t = self._export_type(t[1], p)
            elif t[0] == "cls":
                body = t[1].body_ctx
                t = self._value(body, p) if body is not None and p in body.sites else None
            elif t == EXT:
                return EXT
            else:
                return None
        if t is None or t == EXT or t[0] == "cls":
            return t
        return None

    def _export_type(self, module: str, name: str) -> tuple | None:
        f = self.files.get(module)
        if f is not None and f.fam == PY and name in f.ctx.sites:
            if not all(k in ("cls", "import") for k, _ in f.ctx.sites[name]):
                return None
        t = self.export(module, name)
        return t if t is None or t == EXT or t[0] in ("cls", "mod") else None

    # ------------------------------------------------------------- expressions

    def expr_type(self, ctx: Ctx, node) -> tuple | None:
        if node is None:
            return None
        self._steps += 1
        if self._depth > _MAX_DEPTH or self._steps > _MAX_STEPS_PER_FILE:
            return None
        self._depth += 1
        try:
            return self._expr_type(ctx, node)
        finally:
            self._depth -= 1

    def _expr_type(self, ctx: Ctx, node) -> tuple | None:
        f = ctx.file
        t = node.type
        if t == "identifier":
            return self.lookup(ctx, _text(f, node))
        if t == "this":
            return self._this(ctx)
        if t in ("parenthesized_expression", "expression_statement", "non_null_expression",
                 "satisfies_expression"):
            return self.expr_type(ctx, node.named_child(0) if node.named_child_count else None)
        if t in ("await", "await_expression"):
            inner = self.expr_type(ctx, node.named_child(0) if node.named_child_count else None)
            return inner[1] if inner is not None and inner[0] == "await" else inner
        if t in ("as_expression", "type_assertion"):
            kids = _named(node)
            return self.parse_type(ctx, _text(f, kids[-1])) if len(kids) >= 2 else None
        if t in ("attribute", "member_expression"):
            obj = _field(node, "object")
            attr = _field(node, "attribute") or _field(node, "property")
            if obj is None or attr is None:
                return None
            if obj.type == "super" or self._is_super_call(f, obj):
                m = self._super_method(ctx, _text(f, attr))
                return ("fn", m, self._this(ctx)) if m is not None else None
            return self.member(self.expr_type(ctx, obj), _text(f, attr))
        if t in ("call", "call_expression"):
            fn = _field(node, "function")
            if fn is None or (f.fam == PY and fn.type == "identifier" and _text(f, fn) == "super"):
                return None
            return self._call_result(self.expr_type(ctx, fn))
        if t == "new_expression":
            c = self.expr_type(ctx, _field(node, "constructor"))
            if c is not None and c[0] == "cls":
                return ("inst", c[1])
            return EXT if c == EXT else None
        if t in ("subscript", "subscript_expression"):
            base = self.expr_type(ctx, _field(node, "value") or _field(node, "object"))
            if base is not None and base[0] == "list":
                return base[1]
            return EXT if base == EXT else None
        if t == "conditional_expression":
            kids = _named(node)
            return _join([self.expr_type(ctx, kids[0]), self.expr_type(ctx, kids[2])]) if len(kids) >= 3 else None
        if t == "ternary_expression":
            return _join([self.expr_type(ctx, _field(node, "consequence")),
                          self.expr_type(ctx, _field(node, "alternative"))])
        if t in ("boolean_operator", "binary_expression"):
            op = _field(node, "operator")
            if op is not None and _text(f, op) in ("or", "||", "??"):
                return _join([self.expr_type(ctx, _field(node, "left")), self.expr_type(ctx, _field(node, "right"))])
            return EXT if op is not None and _text(f, op) in ("+", "-", "*", "/", "%", "==", "!=", "===",
                                                              "!==", "<", ">", "<=", ">=", "in") else None
        if t in ("none", "null", "undefined"):
            return NONE
        if t in ("list", "array"):
            first = node.named_child(0) if node.named_child_count else None
            return ("list", self.expr_type(ctx, first) if first is not None else None)
        if t in ("list_comprehension", "generator_expression"):
            return ("list", None)
        if t in ("string", "concatenated_string", "integer", "float", "true", "false", "dictionary",
                 "set", "tuple", "dictionary_comprehension", "set_comprehension", "number",
                 "template_string", "object", "regex", "comparison_operator", "not_operator",
                 "unary_expression"):
            return EXT
        return None

    @staticmethod
    def _is_super_call(f: FileInfo, node) -> bool:
        if node.type != "call":
            return False
        fn = _field(node, "function")
        return fn is not None and fn.type == "identifier" and _text(f, fn) == "super"

    def _this(self, ctx: Ctx) -> tuple | None:
        """The receiver of the method ``ctx`` is in: ``this`` (JS, through
        arrows but not through a plain ``function``), or the ``self`` a Python
        method -- and any function nested in it -- closes over."""
        scope: Ctx | None = ctx
        while scope is not None:
            if scope.owner is not None and scope.func is not None and scope.self_name is not None:
                return (scope.self_kind, scope.owner)
            if ctx.file.fam == JS and scope.func is not None and scope.func.node.type != "arrow_function":
                return None
            scope = scope.parent
        return None

    def _owner_scope(self, ctx: Ctx) -> Ctx | None:
        scope: Ctx | None = ctx
        while scope is not None:
            if scope.owner is not None and scope.func is not None and scope.self_name is not None:
                return scope
            scope = scope.parent
        return None

    def _super_method(self, ctx: Ctx, name: str) -> FuncInfo | None:
        scope = self._owner_scope(ctx)
        return self.find_method(scope.owner, name, skip_self=True) if scope is not None else None

    def _call_result(self, callee: tuple | None) -> tuple | None:
        if callee is None:
            return None
        if callee[0] == "cls":
            return ("inst", callee[1])
        if callee[0] == "fn":
            return self.return_type(callee[1], recv=callee[2])
        return EXT if callee == EXT else None

    # ------------------------------------------------------------- call sites

    def walk_calls(self, f: FileInfo, out: list[Site]) -> None:
        self._steps = 0
        self._walk(f.ctx, f.root, out)

    def _scope_for(self, ctx: Ctx, node) -> Ctx | None:
        f = ctx.file
        t = node.type
        if f.fam == PY:
            if t == "function_definition":
                fi = self._find_func(ctx, node)
                return self.func_ctx(fi) if fi is not None else None
            if t == "class_definition":
                ci = self._find_class(ctx, node)
                return ci.body_ctx if ci is not None else None
            return None
        if t in _JS_CLASSES:
            ci = self._find_class(ctx, node)
            return ci.body_ctx if ci is not None else None
        if t == "method_definition" and ctx.owner is not None and ctx.func is None:
            for m in list(ctx.owner.methods.values()) + ctx.owner.others:
                if m.node.start_byte == node.start_byte:
                    return self.func_ctx(m)
            return None
        if t in _JS_FUNCS:
            fi = self._find_func(ctx, node)
            if fi is None:
                fi = FuncInfo(None, "", f, node, parent=ctx)
                if ctx.owner is not None and ctx.func is None:
                    fi.owner = ctx.owner        # a class-field arrow: `this` is the instance
                fi.is_async = _text(f, node).lstrip().startswith("async")
            return self.func_ctx(fi)
        return None

    def _walk(self, ctx: Ctx, node, out: list[Site]) -> None:
        for child in _named(node):
            inner = self._scope_for(ctx, child) if child.type in _SCOPE_NODES else None
            if child.type in ("call", "call_expression"):
                site = self._site(ctx, child)
                if site is not None:
                    out.append(site)
            elif child.type == "new_expression":
                site = self._new_site(ctx, child)
                if site is not None:
                    out.append(site)
            self._walk(inner or ctx, child, out)

    def _find_func(self, ctx: Ctx, node) -> FuncInfo | None:
        scopes = [ctx] + ([ctx.owner.body_ctx] if ctx.owner is not None and ctx.owner.body_ctx else [])
        for scope in scopes:
            for sites in scope.sites.values():
                for kind, payload in sites:
                    if kind == "fn" and payload.node.start_byte == node.start_byte \
                            and payload.node.end_byte == node.end_byte:
                        return payload
        if ctx.owner is not None:
            for m in list(ctx.owner.methods.values()) + ctx.owner.others:
                if m.node.start_byte == node.start_byte and m.node.end_byte == node.end_byte:
                    return m
        return None

    def _find_class(self, ctx: Ctx, node) -> ClassInfo | None:
        for c in self.classes:
            if c.file is ctx.file and c.node.start_byte == node.start_byte and c.node.end_byte == node.end_byte:
                return c
        return None

    def _emit(self, ctx: Ctx, node, name: str, outcome: str, targets=(), strategy="") -> Site | None:
        line = node.start_point[0] + 1
        src = self._enclosing(ctx.file, line)
        if src is None:
            return None
        return Site(src, name, line, outcome, tuple(t for t in targets if t != src), strategy)

    def _new_site(self, ctx: Ctx, node) -> Site | None:
        ctor = _field(node, "constructor")
        if ctor is None:
            return None
        name = _text(ctx.file, ctor).rsplit(".", 1)[-1]
        c = self.expr_type(ctx, ctor)
        if c is not None and c[0] == "cls" and c[1].key in self.keys:
            return self._emit(ctx, node, name, RESOLVED, (c[1].key,), "new")
        return self._emit(ctx, node, name, EXTERNAL if c == EXT else UNKNOWN)

    def _site(self, ctx: Ctx, node) -> Site | None:
        f = ctx.file
        fn = _field(node, "function")
        if fn is None:
            return None
        if fn.type in ("attribute", "member_expression"):
            obj = _field(fn, "object")
            attr = _field(fn, "attribute") or _field(fn, "property")
            if obj is None or attr is None:
                return None
            name = _text(f, attr)
            if obj.type == "super" or self._is_super_call(f, obj):
                m = self._super_method(ctx, name)
                if m is not None and m.key in self.keys and not self.is_stub(m):
                    return self._emit(ctx, node, name, RESOLVED, (m.key,), "super")
                return self._emit(ctx, node, name, UNKNOWN)
            return self._method_site(ctx, node, name, self.expr_type(ctx, obj), obj)
        if fn.type == "identifier":
            name = _text(f, fn)
            if f.fam == PY and name == "super":
                return None
            return self._value_site(ctx, node, name, self.lookup(ctx, name), self._strategy_of(ctx, name))
        return None

    def _strategy_of(self, ctx: Ctx, name: str) -> str:
        scope: Ctx | None = ctx
        while scope is not None:
            if name in scope.sites:
                return "import" if any(k == "import" for k, _ in scope.sites[name]) else "local"
            scope = scope.parent
        return "local"

    def _value_site(self, ctx: Ctx, node, name: str, v: tuple | None, strategy: str) -> Site | None:
        if v is not None and v[0] in ("fn", "cls") and v[1].key in self.keys:
            return self._emit(ctx, node, name, RESOLVED, (v[1].key,), strategy)
        if v == EXT:
            return self._emit(ctx, node, name, EXTERNAL)
        return self._emit(ctx, node, name, UNKNOWN)

    def _method_site(self, ctx: Ctx, node, name: str, recv: tuple | None, obj) -> Site | None:
        if recv is None:
            return self._emit(ctx, node, name, UNKNOWN)
        kind = recv[0]
        if kind in ("ext", "list", "await", "none"):
            return self._emit(ctx, node, name, EXTERNAL)
        if kind == "mod":
            return self._value_site(ctx, node, name, self.export(recv[1], name), "module")
        if kind not in ("inst", "cls"):
            return self._emit(ctx, node, name, UNKNOWN)
        ci = recv[1]
        m = self.find_method(ci, name)
        if m is None:
            ft = self.field_type(ci, name)
            if ft is not None and (ft[0] in ("fn", "cls") or ft == EXT):
                return self._value_site(ctx, node, name, ft, "type")
            return self._emit(ctx, node, name, UNKNOWN)
        targets: list[str] = []
        if not self.is_stub(m) and m.key in self.keys:
            targets.append(m.key)
        # virtual dispatch: an override in any repo subclass can run instead
        for sub in self.subclasses(ci):
            o = sub.methods.get(name)
            if o is not None and o.key in self.keys and o.key not in targets and not self.is_stub(o):
                targets.append(o.key)
        if not targets or ci.protocol or any(c.protocol for c in self.mro(ci)):
            return self._emit(ctx, node, name, UNKNOWN)
        is_self = obj is not None and obj.type in ("identifier", "this") and \
            _text(ctx.file, obj) in (ctx.self_name or "\0", "this")
        return self._emit(ctx, node, name, RESOLVED, targets, "self" if is_self else "type")


# --------------------------------------------------------------------------- entry


def analyze(
    file_rows: Iterable,
    sym_rows: Iterable,
    file_bytes: Callable[[str], bytes | None],
    aliases: list | None = None,
) -> Result:
    """Every Python / JS / TS call site's outcome. ``file_rows`` need ``path``
    and ``lang``; ``sym_rows`` need ``key``, ``name``, ``file``, ``start_line``
    and ``end_line``; ``file_bytes(path)`` is the file as codemap indexed it."""
    file_rows = list(file_rows)
    tf = TypeFlow({r["path"] for r in file_rows}, aliases)
    tf.add_symbols(sym_rows)
    res = Result()
    for r in file_rows:
        if r["lang"] not in _FAMILY:
            continue
        try:
            blob = file_bytes(r["path"])
            if blob is not None and tf.load(r["path"], r["lang"], blob):
                res.files += 1
        except (RecursionError, OSError, ValueError):
            res.failed.append(r["path"])
    tf.finish_loading()
    for f in list(tf.files.values()):
        try:
            tf.walk_calls(f, res.sites)
        except RecursionError:
            res.failed.append(f.path)
    return res
