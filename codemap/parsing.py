"""Tree-sitter -> symbols, references, imports (T1, universal).

Extraction is driven entirely by each language's ``tags.scm`` (see
``languages/registry.py`` for the capture contract). Everything structural —
nesting, method-vs-function, decorator binding, the enclosing symbol of a call —
is derived here from byte ranges, so this module stays language agnostic.

``parse_source`` never raises: a syntax error or an unexpected failure yields a
``ParsedFile`` with ``ok=False`` and whatever partial results were recovered
(spec section 9, fixture commit 10).
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
    if tree.root_node.has_error:
        pf.ok = False
        pf.error = "syntax error"

    raw_defs: list[dict] = []
    doc_nodes: list = []
    decorator_nodes: list = []
    call_items: list[tuple[str, object]] = []
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
                call_items.append((_text(source, caps["name"][0]), caps["reference.call"][0]))
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
    for target_name, cnode in call_items:
        host = _enclosing(symbols, cnode.start_byte, cnode.end_byte)
        pf.refs.append(
            Ref(
                target_name=target_name,
                line=cnode.start_point[0] + 1,
                from_key=host.key if host else None,
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

    pf.symbols = symbols
