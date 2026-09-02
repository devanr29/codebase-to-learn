"""Extension -> grammar + tags query + tier, plus an optional T2 resolver.

The ``tags.scm`` contract (captures the parser understands):

    @definition.function / @definition.class / @definition.type /
    @definition.interface / @definition.method
        the whole declaration node; kind comes from the capture suffix
    @name         identifier naming the definition (or the callee of a call)
    @params       parameter list / superclass list node (for the signature)
    @returns      return-type node (for the signature), optional
    @docstring    string node that is the first body statement, optional
    @decorator    a decorator / annotation node
    @reference.call   a call expression node (paired with its @name)
    @import       a whole import statement node (raw text is kept verbatim)

Everything else — nesting, method-vs-function, which decorator binds to which
definition — is derived from byte ranges in ``parsing.py`` and stays language
agnostic.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

_QUERIES_DIR = Path(__file__).parent / "queries"


@dataclass(frozen=True)
class LanguageSpec:
    name: str            # codemap's language label, stored in files.lang
    grammar: str         # tree_sitter_language_pack grammar name
    query_dir: str       # subdir under languages/queries/
    tier: int            # resolution tier available for this language (5.1)
    comment_types: tuple[str, ...] = ("comment",)  # node types normalize.py strips
    string_types: tuple[str, ...] = ("string", "string_literal")


# Extension (lowercase, with dot) -> spec. Order-independent.
_BY_EXT: dict[str, LanguageSpec] = {}


def _register(exts: list[str], spec: LanguageSpec) -> None:
    for ext in exts:
        _BY_EXT[ext] = spec


_PY = LanguageSpec("python", "python", "python", tier=2)
_JS = LanguageSpec("javascript", "javascript", "javascript", tier=1)
_TS = LanguageSpec("typescript", "typescript", "typescript", tier=2)
_TSX = LanguageSpec("tsx", "tsx", "typescript", tier=2)

_register([".py", ".pyi"], _PY)
_register([".js", ".jsx", ".mjs", ".cjs"], _JS)
_register([".ts", ".mts", ".cts"], _TS)
_register([".tsx"], _TSX)

# Newer additions (spec M15 language breadth): T1 only — no per-language
# resolver, so tier=1 across the board, matching javascript's precedent for a
# "capture works, cross-file resolution doesn't" language. Grammar names are
# tree-sitter-language-pack's; verified against the pack's own manifest.
_GO = LanguageSpec("go", "go", "go", tier=1)
_RUST = LanguageSpec("rust", "rust", "rust", tier=1)
_JAVA = LanguageSpec("java", "java", "java", tier=1)
_CSHARP = LanguageSpec("csharp", "csharp", "csharp", tier=1)
_RUBY = LanguageSpec("ruby", "ruby", "ruby", tier=1)
_PHP = LanguageSpec("php", "php", "php", tier=1)
_C = LanguageSpec("c", "c", "c", tier=1)
_CPP = LanguageSpec("cpp", "cpp", "cpp", tier=1)

_register([".go"], _GO)
_register([".rs"], _RUST)
_register([".java"], _JAVA)
_register([".cs"], _CSHARP)
_register([".rb"], _RUBY)
_register([".php"], _PHP)
_register([".c", ".h"], _C)
_register([".cc", ".cpp", ".cxx", ".hpp", ".hh", ".hxx"], _CPP)


def spec_for_path(path: str) -> LanguageSpec | None:
    ext = Path(path).suffix.lower()
    return _BY_EXT.get(ext)


def supported_extensions() -> frozenset[str]:
    return frozenset(_BY_EXT)


def all_specs() -> list[LanguageSpec]:
    seen: dict[str, LanguageSpec] = {}
    for spec in _BY_EXT.values():
        seen.setdefault(spec.name, spec)
    return list(seen.values())


@lru_cache(maxsize=None)
def load_tags_query(query_dir: str) -> str:
    path = _QUERIES_DIR / query_dir / "tags.scm"
    if not path.exists():
        raise FileNotFoundError(f"no tags.scm for language dir {query_dir!r} at {path}")
    return path.read_text(encoding="utf-8")


@lru_cache(maxsize=None)
def get_ts_language(grammar: str):
    """Return the tree-sitter ``Language`` for a grammar name (cached)."""
    from tree_sitter_language_pack import get_language

    return get_language(grammar)
