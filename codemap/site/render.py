"""Inline the model + frozen assets into a single self-contained ``explore.html``.

The assets in ``assets/`` are hand-authored once and **never regenerated** — all
variability flows through the JSON payload and ``data-*`` attributes. Keep it
that way: edit ``explore.css`` / ``explore.js`` by hand, not from here.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

_ASSETS = Path(__file__).with_name("assets")


@lru_cache(maxsize=None)
def _asset(name: str) -> str:
    return (_ASSETS / name).read_text(encoding="utf-8")


def _title(data: dict) -> str:
    """The browser tab's title: the repo's own folder name, so multiple
    builds (or multiple repos) are actually distinguishable in a tab strip —
    ``"Codegraph <sha-or-'worktree'>"`` was identical across every repo, since
    a worktree graph's commit_short is always the literal string
    ``"worktree"`` (indexer.WORKTREE_SHA)."""
    root = data.get("root") or ""
    name = Path(root).name if root else ""
    return f"{name} · codemap" if name else "codemap"


def _slim(data: dict) -> dict:
    """The page rebuilds a symbol's `excerpt` from the embedded file text, so a
    file that ships whole doesn't also ship every symbol's lines a second time
    (nested symbols repeat their parent's). Files that missed the source budget
    keep their excerpts."""
    sources = data.get("sources")
    if not sources:
        return data
    held = {int(fi) for fi in sources}
    embedded = {f["path"] for f in data.get("files", ()) if f["fi"] in held}
    nodes = [
        {k: v for k, v in n.items() if k != "excerpt"} if n["file"] in embedded else n
        for n in data.get("nodes", ())
    ]
    return {**data, "nodes": nodes}


def render(data: dict) -> str:
    shell = _asset("shell.html")
    css = _asset("explore.css")
    js = _asset("explore.js")
    icons_css = _asset("phosphor-icons.css")

    payload = json.dumps(_slim(data), separators=(",", ":"), sort_keys=True)
    # every '<' in valid JSON is inside a string literal, so a unicode escape is
    # both safe and keeps '</script>' / '<!--' from ending the inline block early
    payload = payload.replace("<", "\\u003c")

    return (
        shell.replace("/*{{ICONS_CSS}}*/", icons_css)
        .replace("/*{{CSS}}*/", css)
        .replace("/*{{JS}}*/", js)
        .replace("{{TITLE}}", _escape(_title(data)))
        .replace("{{DATA}}", payload)
    )


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
