"""Normalized body hashing (spec M4).

Comments and whitespace are stripped using tree-sitter *node types* — not
regexes over source — so "cosmetic change" detection is deterministic. A body's
normalized form is the space-joined stream of its non-comment leaf tokens
(identifiers, literals, operators, keywords, punctuation).

Both hashes are computed over a symbol's *own* content: byte ranges belonging to
nested definitions are excluded, so a change to a method does not bubble up into
its class's hash.
"""

from __future__ import annotations

import hashlib
import re

from tree_sitter import Parser

from .languages.registry import LanguageSpec, get_ts_language

_WS = re.compile(r"\s+")

Range = tuple[int, int]


def _excluded(start: int, end: int, exclude: list[Range]) -> bool:
    return any(s <= start and end <= e for s, e in exclude)


def _leaf_tokens(
    root, source: bytes, comment_types: frozenset[str], min_byte: int, exclude: list[Range]
) -> list[str]:
    out: list[tuple[int, str]] = []
    stack = [root]
    while stack:
        node = stack.pop()
        if node.type in comment_types:
            continue
        if exclude and _excluded(node.start_byte, node.end_byte, exclude):
            continue
        if node.child_count == 0:
            if node.end_byte <= min_byte:
                continue
            text = source[max(node.start_byte, min_byte) : node.end_byte].decode("utf-8", "replace")
            text = _WS.sub(" ", text).strip()
            if text:
                out.append((node.start_byte, text))
            continue
        for i in range(node.child_count - 1, -1, -1):
            stack.append(node.child(i))
    out.sort()
    return [t for _, t in out]


def body_tokens(
    node, source: bytes, spec: LanguageSpec, header_end: int, exclude: list[Range] | None = None
) -> list[str]:
    return _leaf_tokens(
        node, source, frozenset(spec.comment_types), header_end, exclude or []
    )


def body_hash(
    node, source: bytes, spec: LanguageSpec, header_end: int, exclude: list[Range] | None = None
) -> str:
    return _hash(body_tokens(node, source, spec, header_end, exclude))


def raw_hash(source: bytes, start_byte: int, end_byte: int, exclude: list[Range] | None = None) -> str:
    if not exclude:
        return hashlib.sha1(source[start_byte:end_byte]).hexdigest()
    kept = bytearray()
    cut = sorted((s, e) for s, e in exclude if s >= start_byte and e <= end_byte)
    cursor = start_byte
    for s, e in cut:
        if s > cursor:
            kept += source[cursor:s]
        cursor = max(cursor, e)
    kept += source[cursor:end_byte]
    return hashlib.sha1(bytes(kept)).hexdigest()


def tokens_from_bytes(source: bytes, spec: LanguageSpec) -> list[str]:
    """Normalized token stream for an arbitrary source slice (used by the
    rename-fallout check in ``semdiff``). Parses tolerantly — a bare method
    body is not a valid module but tree-sitter still yields usable leaves."""
    tree = Parser(get_ts_language(spec.grammar)).parse(source)
    return _leaf_tokens(tree.root_node, source, frozenset(spec.comment_types), 0, [])


def _hash(tokens: list[str]) -> str:
    return hashlib.sha1(" ".join(tokens).encode("utf-8")).hexdigest()
