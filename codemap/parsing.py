"""Tree-sitter -> symbols, references, imports (T1, universal).

Extraction is driven entirely by each language's ``tags.scm`` (see
``languages/registry.py`` for the capture contract). Everything structural —
nesting, method-vs-function, decorator binding, the enclosing symbol of a call —
is derived here from byte ranges, so this module stays language agnostic.

``parse_source`` never raises: a syntax error records ``error_ranges`` (the
line spans tree-sitter couldn't parse) but keeps ``ok=True`` and whatever
partial results were recovered, unless a single gap swallows at least 80% of
the file — then the file is genuinely unusable and ``ok=False``. An
unexpected failure also yields ``ok=False`` (spec section 9, fixture commit 10).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache

from tree_sitter import Parser, Query, QueryCursor

from . import normalize
from .languages.registry import LanguageSpec, get_ts_language, load_tags_query

# Incremented once per file actually parsed. Tests assert incrementality against it.
parse_count = 0

_WS = re.compile(r"\s+")


def _norm_ws(text: str) -> str:
    return _WS.sub(" ", text).strip()


@dataclass
class Symbol:
    key: str                    # relative/path.ext::Qualified.Name
    kind: str                   # function|method|class|interface|type
    name: str
    qualified_name: str
    start_line: int             # 1-based, inclusive
    end_line: int
    signature: str
    decorators: list[str] = field(default_factory=list)
    docstring: str | None = None
    body_hash: str = ""          # normalized (see normalize.py)
    raw_hash: str = ""           # verbatim declaration text
    start_byte: int = 0
    end_byte: int = 0


@dataclass
class Ref:
    target_name: str
    line: int
    from_key: str | None        # enclosing symbol key, None at module level
    receiver: str = "-"         # what the call was made on — see _receiver_of()


@dataclass
class Import:
    raw: str
    line: int


@dataclass
class ParsedFile:
    path: str
    lang: str
    tier: int
    symbols: list[Symbol] = field(default_factory=list)
    refs: list[Ref] = field(default_factory=list)
    imports: list[Import] = field(default_factory=list)
    main_calls: list[str] = field(default_factory=list)  # names called under `if __name__ == "__main__"`
    default_export: str | None = None  # JS/TS only: name of `export default ...`, if resolvable
    error_ranges: list[tuple[int, int]] = field(default_factory=list)  # 1-based, inclusive: unparsed gaps
    ok: bool = True
    error: str | None = None


@lru_cache(maxsize=None)
def _compiled(grammar: str, query_dir: str) -> tuple[object, Query]:
    lang = get_ts_language(grammar)
    query = Query(lang, load_tags_query(query_dir))
    return lang, query


def _text(src: bytes, node) -> str:
    return src[node.start_byte:node.end_byte].decode("utf-8", "replace")


_STR_PREFIX = re.compile(r"^[rRbBfFuU]{1,2}(?=['\"])")

# Keywords allowed to sit between a decorator and the declaration it binds to.
_DECL_MODIFIERS = re.compile(
    rb"\b(export|default|abstract|public|private|protected|static|async|readonly|final|override)\b"
)


def _strip_doc(raw: str) -> str:
    s = _STR_PREFIX.sub("", raw.strip())
    for q in ('"""', "'''", '"', "'", "`"):
        if s.startswith(q) and s.endswith(q) and len(s) >= 2 * len(q):
            return s[len(q):-len(q)].strip()
    return s.strip()


def _raw_error_spans(root_node) -> list[tuple[int, int, int, int]]:
    """(start_line, end_line, start_byte, end_byte), 1-based lines, for each
    top-level ERROR/MISSING node. Doesn't descend into one once found — its
    whole span is already the reported gap, and descending would just report
    the same damage again in smaller, more confusing pieces."""
    if not root_node.has_error:
        return []
    out: list[tuple[int, int, int, int]] = []
    stack = [root_node]
    while stack:
        node = stack.pop()
        if node.type == "ERROR" or node.is_missing:
            out.append((node.start_point[0] + 1, node.end_point[0] + 1, node.start_byte, node.end_byte))
            continue
        if node.has_error:
            for i in range(node.child_count - 1, -1, -1):
                stack.append(node.child(i))
    return out


# Identifiers that mean "the object this method is defined on", across the
# languages codemap indexes — Python self/cls, JS/TS this/super, Ruby self,
# PHP $this. A call through one of these is the only receiver shape trusted
# to mean "this class's own method" in call_graph() (impact.py).
_SELF_LIKE = frozenset({"self", "cls", "this", "super", "Self", "$this", "$self"})


def _receiver_of(name_node, call_node, source: bytes) -> str:
    """Classify what a call's name was found *on*, structurally — no
    per-language field names, so every grammar's attribute/member/selector
    wrapper is handled the same way without touching the 12 query files:

    - the name node's parent IS the call node itself (the `function:` field
      is the bare identifier) -> a bare call, receiver ``"-"``
    - otherwise the parent is the attribute/member/selector node; its first
      named child that isn't the name is the receiver expression:
      - a self-like identifier (see ``_SELF_LIKE``) -> ``"self"``
      - a capitalized identifier (a class name, or a `Ns.Thing` alias) ->
        ``"N:<name>"``
      - a lowercase identifier (a local/param/module alias) -> ``"v:<name>"``
      - anything else (a chained attribute, a call result, a subscript, an
        object literal) -> ``"x"`` — opaque, deliberately never a class or
        variable hint call_graph() could over-trust

    This is intentionally conservative: `self.x.get()` (chained past one
    level) and `super().method()` (the receiver is a *call*, not a bare
    identifier) both fall into "x" rather than being guessed at further.
    """
    if name_node is None:
        return "-"
    parent = name_node.parent
    if parent is None or (parent.start_byte, parent.end_byte) == (call_node.start_byte, call_node.end_byte):
        return "-"
    receiver_node = None
    for i in range(parent.named_child_count):
        child = parent.named_child(i)
        if (child.start_byte, child.end_byte) != (name_node.start_byte, name_node.end_byte):
            receiver_node = child
            break
    if receiver_node is None:
        return "-"
    rtype = receiver_node.type
    if rtype.endswith("identifier") or rtype == "variable_name":
        text = _text(source, receiver_node).strip()
        if text in _SELF_LIKE:
            return "self"
        bare = text.lstrip("$")
        return f"N:{bare}" if bare[:1].isupper() else f"v:{bare}"
    return "x"


def _enclosing(defs: list[Symbol], start: int, end: int) -> Symbol | None:
    """Innermost symbol whose byte range strictly contains [start, end)."""
    best: Symbol | None = None
    for d in defs:
        if d.start_byte <= start and d.end_byte >= end and (d.start_byte, d.end_byte) != (start, end):
            if best is None or d.start_byte > best.start_byte or (
                d.start_byte == best.start_byte and d.end_byte < best.end_byte
            ):
                best = d
    return best


def parse_source(rel_path: str, source: bytes, spec: LanguageSpec) -> ParsedFile:
    global parse_count
    parse_count += 1
    pf = ParsedFile(path=rel_path, lang=spec.name, tier=spec.tier)
    try:
        _parse_into(pf, rel_path, source, spec)
    except Exception as exc:  # never propagate — a bad file must not stop a scan
        pf.ok = False
        pf.error = f"{type(exc).__name__}: {exc}"
    return pf


def _parse_into(pf: ParsedFile, rel_path: str, source: bytes, spec: LanguageSpec) -> None:
    lang, query = _compiled(spec.grammar, spec.query_dir)
    tree = Parser(lang).parse(source)
    error_spans = _raw_error_spans(tree.root_node)

    raw_defs: list[dict] = []
    doc_nodes: list = []
    decorator_nodes: list = []
    call_items: list[tuple[str, object, object]] = []
    import_nodes: list = []

    for _pattern, caps in QueryCursor(query).matches(tree.root_node):
        dkey = next((k for k in caps if k.startswith("definition.")), None)
        if dkey:
            name_nodes = caps.get("name")
            if not name_nodes:
                continue
            dnode = caps[dkey][0]
            raw_defs.append(
                {
                    "kind": dkey.split(".", 1)[1],
                    "node": dnode,
                    "name": _text(source, name_nodes[0]),
                    "name_node": name_nodes[0],
                    "params": caps["params"][0] if caps.get("params") else None,
                    "returns": caps["returns"][0] if caps.get("returns") else None,
                }
            )
        elif "docstring" in caps:
            doc_nodes.extend(caps["docstring"])
        elif "decorator" in caps:
            decorator_nodes.extend(caps["decorator"])
        elif "reference.call" in caps:
            if caps.get("name"):
                name_node = caps["name"][0]
                call_items.append((_text(source, name_node), caps["reference.call"][0], name_node))
        elif "reference.render" in caps:
            # JSX composition folds into the same call-graph edges as an
            # ordinary call — a rendered <Component/> IS a reference to it.
            # Keep only capitalized names: that's the JSX convention that
            # separates a component (`<TodayCard/>`) from an intrinsic host
            # element (`<div/>`), which the grammar itself doesn't encode.
            # There's no receiver concept for JSX composition, so the name
            # node is left out here — the ref below always treats it as bare.
            if caps.get("name"):
                name = _text(source, caps["name"][0])
                if name[:1].isupper():
                    call_items.append((name, caps["reference.render"][0], None))
        elif "import" in caps:
            import_nodes.extend(caps["import"])

    # --- build symbols (outer before inner so parents exist first) -----------
    raw_defs.sort(key=lambda d: (d["node"].start_byte, -d["node"].end_byte))
    symbols: list[Symbol] = []
    node_to_symbol: dict[int, Symbol] = {}
    pending_hash: dict[str, tuple] = {}
    used_keys: set[str] = set()

    for d in raw_defs:
        node = d["node"]
        parent = _enclosing(symbols, node.start_byte, node.end_byte)
        kind = d["kind"]
        if kind == "function" and parent is not None and parent.kind in ("class", "interface"):
            kind = "method"
        qname = f"{parent.qualified_name}.{d['name']}" if parent else d["name"]

        key = f"{rel_path}::{qname}"
        if key in used_keys:
            n = 2
            while f"{key}#{n}" in used_keys:
                n += 1
            key = f"{key}#{n}"
        used_keys.add(key)

        sig = d["name"]
        if d["params"] is not None:
            sig += _norm_ws(_text(source, d["params"]))
        if d["returns"] is not None:
            ret = _norm_ws(_text(source, d["returns"]))
            sig += ret if ret.startswith((":", "->")) else f" -> {ret}"

        header_end = d["name_node"].end_byte
        for hn in (d["params"], d["returns"]):
            if hn is not None:
                header_end = max(header_end, hn.end_byte)

        sym = Symbol(
            key=key,
            kind=kind,
            name=d["name"],
            qualified_name=qname,
            start_line=node.start_point[0] + 1,
            end_line=node.end_point[0] + 1,
            signature=_norm_ws(sig),
            start_byte=node.start_byte,
            end_byte=node.end_byte,
        )
        symbols.append(sym)
        node_to_symbol[node.start_byte] = sym
        pending_hash[key] = (node, header_end)

    # --- hashes: computed over each symbol's *own* content, with nested
    #     definitions excluded so a method change does not bubble to its class
    for sym in symbols:
        node, header_end = pending_hash[sym.key]
        nested = [
            (c.start_byte, c.end_byte)
            for c in symbols
            if c is not sym
            and sym.start_byte <= c.start_byte
            and c.end_byte <= sym.end_byte
        ]
        sym.body_hash = normalize.body_hash(node, source, spec, header_end, nested)
        sym.raw_hash = normalize.raw_hash(source, sym.start_byte, sym.end_byte, nested)

    # --- docstrings: attach to the innermost enclosing symbol ---------------
    for dn in doc_nodes:
        host = _enclosing(symbols, dn.start_byte, dn.end_byte)
        if host is not None and host.docstring is None:
            host.docstring = _strip_doc(_text(source, dn))

    # --- decorators: walk the whitespace-only chain back from each symbol ---
    for sym in symbols:
        cursor = sym.start_byte
        chain: list = []
        while True:
            cand = None
            for dec in decorator_nodes:
                if dec.end_byte > cursor:
                    continue
                between = _DECL_MODIFIERS.sub(b"", source[dec.end_byte:cursor])
                if not between.strip():
                    if cand is None or dec.end_byte > cand.end_byte:
                        cand = dec
            if cand is None:
                break
            chain.append(cand)
            cursor = cand.start_byte
        sym.decorators = [_norm_ws(_text(source, d)) for d in reversed(chain)]

    # --- references -------------------------------------------------------
    for target_name, cnode, name_node in call_items:
        host = _enclosing(symbols, cnode.start_byte, cnode.end_byte)
        pf.refs.append(
            Ref(
                target_name=target_name,
                line=cnode.start_point[0] + 1,
                from_key=host.key if host else None,
                receiver=_receiver_of(name_node, cnode, source),
            )
        )

    # --- imports --------------------------------------------------------
    seen_imports: set[tuple[str, int]] = set()
    for inode in sorted(import_nodes, key=lambda n: n.start_byte):
        raw = _norm_ws(_text(source, inode))
        line = inode.start_point[0] + 1
        if (raw, line) in seen_imports:
            continue
        seen_imports.add((raw, line))
        pf.imports.append(Import(raw=raw, line=line))

    if spec.name == "python":
        pf.main_calls = _main_guard_calls(tree.root_node, source)
    elif spec.name in ("javascript", "typescript", "tsx"):
        pf.default_export = _default_export_name(tree.root_node, source)

    pf.symbols = symbols

    # --- error gaps: a syntax error doesn't fail a file (spec section 9) —
    #     record where the damage is and only give up if one gap swallows
    #     most of the file. A file that still yields symbols despite a
    #     malformed macro or an unsupported construct elsewhere stays usable.
    if error_spans:
        total_lines = max(source.count(b"\n") + 1, 1)
        ranges: set[tuple[int, int]] = set()
        for start_line, end_line, start_byte, end_byte in error_spans:
            if start_byte == end_byte and start_byte >= len(source):
                continue  # zero-width MISSING synthesized at end of file
            if any(sym.start_byte <= start_byte and end_byte <= sym.end_byte for sym in symbols):
                continue  # inside a definition we still extracted cleanly
            ranges.add((start_line, end_line))
        if ranges:
            pf.error_ranges = sorted(ranges)
            worst = max(end - start + 1 for start, end in pf.error_ranges)
            if worst / total_lines >= 0.8:
                pf.ok = False
                pf.error = f"unusable: syntax errors cover {round(worst / total_lines * 100)}% of the file"


def _main_guard_calls(root_node, source: bytes) -> list[str]:
    """Names called under a module-level ``if __name__ == "__main__":`` guard."""
    out: list[str] = []
    for i in range(root_node.named_child_count):
        node = root_node.named_child(i)
        if node.type != "if_statement":
            continue
        cond = node.child_by_field_name("condition")
        if cond is None:
            continue
        ctext = source[cond.start_byte:cond.end_byte]
        if b"__name__" not in ctext or b"__main__" not in ctext:
            continue
        stack = [node]
        while stack:
            n = stack.pop()
            if n.type == "call":
                fn = n.child_by_field_name("function")
                if fn is not None and fn.type == "identifier":
                    out.append(_text(source, fn))
                elif fn is not None and fn.type == "attribute":
                    attr = fn.child_by_field_name("attribute")
                    if attr is not None:
                        out.append(_text(source, attr))
            for j in range(n.named_child_count):
                stack.append(n.named_child(j))
    return out


def _default_export_name(root_node, source: bytes) -> str | None:
    """Name of a top-level ``export default ...`` (JS/TS), if one exists and
    names something resolvable. Used by ``entrypoints.py`` to tie a route file
    (one component per file, by convention) to a symbol."""
    for i in range(root_node.named_child_count):
        node = root_node.named_child(i)
        if node.type != "export_statement":
            continue
        children = node.children  # includes anonymous tokens: 'export', 'default'
        kinds = [c.type for c in children]
        if "default" not in kinds:
            continue
        idx = kinds.index("default")
        if idx + 1 >= len(children):
            continue
        name = _name_of_export_target(children[idx + 1], source)
        if name:
            return name
    return None


def _name_of_export_target(node, source: bytes) -> str | None:
    if node.type in ("function_declaration", "class_declaration", "generator_function_declaration"):
        name_node = node.child_by_field_name("name")
        return _text(source, name_node) if name_node else None
    if node.type == "identifier":
        return _text(source, node)
    if node.type == "call_expression":
        # best-effort: `export default memo(Foo)` / `connect(...)(Foo)` — only
        # when there's exactly one identifier argument, so this doesn't guess
        # wrong on a genuinely ambiguous call
        args = node.child_by_field_name("arguments")
        if args is not None:
            idents = [c for c in args.named_children if c.type == "identifier"]
            if len(idents) == 1:
                return _text(source, idents[0])
    return None
