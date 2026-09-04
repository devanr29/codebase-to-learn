"""Regenerate ``phosphor-icons.css`` — run whenever ``explore.js`` starts
using a new ``ph-*`` / ``ph-fill ph-*`` class.

Why this exists: ``explore.html`` used to load Phosphor Icons from
``unpkg.com`` via a plain ``<link>``. That's fine on an open network, but on
plenty of real ones (this repo's own dev sandbox included) ``unpkg.com`` is
unreachable while ``fonts.googleapis.com`` works fine — the icon stylesheet
just hangs forever and every icon-only button in the app renders blank, with
no error and no fallback glyph. So the glyphs this app actually uses are
vendored into the repo instead: this script pulls the pinned upstream
release, keeps only the ``@font-face`` + base rule + the ``:before`` rule for
each icon class ``explore.js`` references (regular and fill, whichever exist
— see PHOSPHOR_VERSION below), and writes the result as one CSS file with the
webfonts embedded as base64 ``data:`` URIs. No local dependency beyond the
standard library; needs network access to raw.githubusercontent.com.

Usage: ``python codemap/site/assets/vendor_phosphor_icons.py``
"""

from __future__ import annotations

import base64
import re
import urllib.request
from pathlib import Path

PHOSPHOR_VERSION = "v2.1.1"
RAW_BASE = f"https://raw.githubusercontent.com/phosphor-icons/web/{PHOSPHOR_VERSION}/src"
ASSETS = Path(__file__).parent
EXPLORE_JS = ASSETS / "explore.js"
OUT = ASSETS / "phosphor-icons.css"


def fetch(path: str) -> bytes:
    with urllib.request.urlopen(f"{RAW_BASE}/{path}", timeout=30) as r:
        return r.read()


def used_icon_names() -> list[str]:
    js = EXPLORE_JS.read_text(encoding="utf-8")
    # only inside "..." string literals — a code comment mentioning an icon
    # class by name (e.g. explaining why it's wrong) shouldn't count as usage
    names = set()
    for lit in re.findall(r'"([^"\n]*)"', js):
        names.update(re.findall(r"\bph-[a-z][a-z0-9-]*\b", lit))
    names.discard("ph-fill")  # the style-class token, not an icon name
    return sorted(names)


def extract_rules(css_text: str, style_class: str, icon_names: list[str]):
    m = re.search(r"^\." + re.escape(style_class) + r" \{.*?\n\}\n", css_text, re.S | re.M)
    base_rule = m.group(0) if m else ""
    rules, missing = [], []
    for name in icon_names:
        pat = re.compile(
            r"^\." + re.escape(style_class) + r"\." + re.escape(name)
            + r":before \{\n  content: \"(\\[0-9a-fA-F]+)\";\n\}\n",
            re.M,
        )
        mm = pat.search(css_text)
        if mm:
            rules.append(f'.{style_class}.{name}:before {{\n  content: "{mm.group(1)}";\n}}\n')
        else:
            missing.append(name)
    return base_rule, rules, missing


def font_face(family: str, b64: str) -> str:
    return (
        "@font-face {\n"
        f'  font-family: "{family}";\n'
        f'  src: url("data:font/woff2;base64,{b64}") format("woff2");\n'
        "  font-weight: normal;\n"
        "  font-style: normal;\n"
        "  font-display: block;\n"
        "}\n"
    )


def base_props(family: str) -> str:
    return (
        "{\n"
        f'  font-family: "{family}" !important;\n'
        "  font-style: normal;\n"
        "  font-weight: normal;\n"
        "  font-variant: normal;\n"
        "  text-transform: none;\n"
        "  line-height: 1;\n"
        "  -webkit-font-smoothing: antialiased;\n"
        "  -moz-osx-font-smoothing: grayscale;\n"
        "}\n"
    )


def main() -> None:
    icons = used_icon_names()
    regular_css = fetch("regular/style.css").decode("utf-8")
    fill_css = fetch("fill/style.css").decode("utf-8")
    regular_woff2 = fetch("regular/Phosphor.woff2")
    fill_woff2 = fetch("fill/Phosphor-Fill.woff2")

    _, reg_rules, reg_missing = extract_rules(regular_css, "ph", icons)
    _, fill_rules, fill_missing = extract_rules(fill_css, "ph-fill", icons)
    if reg_missing:
        print("note: not in the regular set (fine if only used as ph-fill):", reg_missing)
    if fill_missing:
        print("note: not in the fill set (fine if only used as ph):", fill_missing)

    out = [
        "/* Vendored Phosphor Icons "
        + PHOSPHOR_VERSION
        + " (MIT) — github.com/phosphor-icons/web\n"
        "   Self-hosted (base64 woff2, only the glyphs this app actually uses) so\n"
        "   icons never depend on unpkg.com being reachable. That CDN is blocked on\n"
        "   plenty of real networks that otherwise allow Google Fonts fine (this is\n"
        "   exactly what happened testing this file in a network-restricted sandbox:\n"
        "   Inter loaded, Phosphor's two <link> stylesheets silently hung forever —\n"
        "   every icon-only button in the app rendered blank, with no error, no\n"
        "   fallback glyph, nothing). Regenerate with\n"
        "   `python codemap/site/assets/vendor_phosphor_icons.py` after adding a new\n"
        "   `ph-*` / `ph-fill ph-*` class to explore.js. */\n",
        font_face("Phosphor", base64.b64encode(regular_woff2).decode("ascii")),
        ".ph " + base_props("Phosphor"),
        *reg_rules,
        font_face("Phosphor-Fill", base64.b64encode(fill_woff2).decode("ascii")),
        ".ph-fill " + base_props("Phosphor-Fill"),
        *fill_rules,
    ]
    OUT.write_text("\n".join(out), encoding="utf-8", newline="\n")
    print(f"wrote {OUT} ({OUT.stat().st_size:,} bytes) — {len(reg_rules)} regular + {len(fill_rules)} fill glyphs")


if __name__ == "__main__":
    main()
