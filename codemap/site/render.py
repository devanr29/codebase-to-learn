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
    short = data.get("commit_short") or ""
    return f"Codegraph {short}".strip()


def render(data: dict) -> str:
    shell = _asset("shell.html")
    css = _asset("explore.css")
    js = _asset("explore.js")

    payload = json.dumps(data, separators=(",", ":"), sort_keys=True)
    # every '<' in valid JSON is inside a string literal, so a unicode escape is
    # both safe and keeps '</script>' / '<!--' from ending the inline block early
    payload = payload.replace("<", "\\u003c")

    return (
        shell.replace("/*{{CSS}}*/", css)
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
