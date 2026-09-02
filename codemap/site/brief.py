"""``codemap explore --emit-brief`` — a deterministic analysis pack for the
course-authoring skill (repo-root ``SKILL.md``).

The skill reads these briefs and writes ``.codemap/learn.json``; it never has to
re-read the repository, because every code snippet a module needs is
pre-extracted here verbatim with its ``file (lines a-b)`` header. Mirrors the
``module-brief-template.md`` idea from the upstream ``codebase-to-course`` skill.
"""

from __future__ import annotations

import sqlite3

from ..config import Config

_MAX_SNIPPETS_PER_MODULE = 8


def _fence(lang: str) -> str:
    return {"python": "python", "typescript": "ts", "tsx": "tsx", "javascript": "js"}.get(lang, "")


def emit(conn: sqlite3.Connection, cfg: Config, data: dict) -> list[str]:
    briefs_dir = cfg.codemap_dir / "briefs"
    briefs_dir.mkdir(parents=True, exist_ok=True)

    nodes = data["nodes"]
    files = data["files"]
    file_by_fi = {f["fi"]: f for f in files}
    modules = data["modules"]
    written: list[str] = []

    # ---- 00-overview -------------------------------------------------
    ov = ["# Codebase analysis pack", ""]
    ov.append(f"Repository graph at `{data['commit_short']}` — "
              f"{data['stats']['files']} files, {data['stats']['symbols']} symbols, "
              f"{data['stats']['edges']} call edges.")
    ov.append("")
    ov.append("## Entry points (where execution enters)")
    if data["entry_points"]:
        for ep in data["entry_points"]:
            tgt = f" -> `{nodes[ep['node']]['qual']}`" if ep["node"] is not None else ""
            ov.append(f"- **[{ep['kind']}]** {ep['detail']}{tgt}")
    else:
        ov.append("- none detected")
    ov.append("")
    ov.append("## Modules (top-level directories)")
    for m in modules:
        ov.append(f"- `{m['name']}` — {len(m['files'])} files, {m['symbol_count']} symbols")
    ov.append("")
    ov.append("## Hotspots (fan-in + non-cosmetic churn)")
    hot = sorted(nodes, key=lambda n: -(n["fan_in"] + n["churn"] * 2))[:12]
    for n in hot:
        ov.append(f"- `{n['qual']}` ({n['file']}) — {n['fan_in']} callers, {n['churn']} changes")
    ov.append("")
    ov.append("## What to produce")
    ov.append("1. `.codemap/learn.json` — the Learn-tab course. Follow "
              "`references/learn-schema.md`; design 4–6 modules per `SKILL.md`; "
              "every screen ≥50% visual, every technical term tooltipped, quizzes "
              "test application not recall.")
    ov.append("2. `.codemap/explanations.json` — one plain-English `what` line per "
              "symbol, shown in the Graph inspector. Follow "
              "`references/explanations-schema.md`. Key every entry by the symbol "
              "`key:` printed in the per-module briefs. Cover at least every "
              "snippet in those briefs (hotspots + entry points).")
    ov.append("")
    ov.append("Both files are optional and fall back silently — but you were asked "
              "for both.")
    _write(briefs_dir / "00-overview.md", ov, written)

    # ---- one brief per module ------------------------------------
    mod_edges: dict[str, set[str]] = {m["name"]: set() for m in modules}
    for fe in data["file_edges"]:
        a, b = file_by_fi[fe["s"]]["module"], file_by_fi[fe["t"]]["module"]
        if a != b:
            mod_edges.setdefault(a, set()).add(b)
            mod_edges.setdefault(b, set()).add(a)

    for i, m in enumerate(modules, start=1):
        name = m["name"]
        lines = [f"# Module {i}: {name}", ""]
        lines.append("## Teaching arc (fill these in)")
        lines.append("- **Metaphor:** _(never reuse across modules; never 'restaurant')_")
        lines.append("- **Opening hook:** _(connect to something the learner already did)_")
        lines.append("- **Key insight:** _(the one takeaway)_")
        lines.append("- **Why care:** _(how it helps them steer AI / debug / decide)_")
        lines.append("")
        deps = sorted(mod_edges.get(name, ()))
        lines.append(f"**Talks to:** {', '.join('`' + d + '`' for d in deps) or 'nothing else'}")
        lines.append("")
        lines.append("## Files")
        for fi in m["files"]:
            f = file_by_fi[fi]
            lines.append(f"- `{f['path']}` ({f['lang']}, T{f['tier']}, {f['loc']} loc, "
                         f"{len(f['symbols'])} symbols)")
        lines.append("")
        lines.append("## Code snippets (pre-extracted — do NOT re-read the repo)")
        mod_nodes = [n for n in nodes if n["module"] == name and n["excerpt"]]
        mod_nodes.sort(key=lambda n: -(n["fan_in"] + n["churn"] * 2))
        for n in mod_nodes[:_MAX_SNIPPETS_PER_MODULE]:
            f = file_by_fi[next(fi for fi in m["files"] if file_by_fi[fi]["path"] == n["file"])]
            lines.append("")
            lines.append(f"### `{n['qual']}` — {n['file']} (lines {n['line'][0]}-{n['line'][1]})")
            lines.append(f"`key:` `{n['key']}`  ·  {n['fan_in']} callers, {n['churn']} changes")
            if n["doc"]:
                lines.append(f"> {n['doc']}")
            lines.append(f"```{_fence(f['lang'])}")
            lines.append(n["excerpt"])
            lines.append("```")
        lines.append("")
        lines.append("## Interactive elements checklist")
        lines.append("- [ ] Code↔English translation (pick from the snippets above)")
        lines.append("- [ ] Quiz — 3–5 questions, scenario / debugging / tracing style")
        lines.append("- [ ] Call-path replay or trace exercise (use a real entry path)")
        lines.append("- [ ] Glossary tooltips on every technical term, first use")
        lines.append("- [ ] `explanations.json` — a `what` line for every snippet key above")
        lines.append("")
        lines.append("## Reference files to read")
        lines.append("- `references/content-philosophy.md` — always")
        lines.append("- `references/gotchas.md` — always")
        lines.append("- `references/interactive-elements.md` — the sections you use")
        lines.append("- `references/learn-schema.md` — for `learn.json`")
        lines.append("- `references/explanations-schema.md` — for `explanations.json`")
        _write(briefs_dir / f"{i:02d}-{_slug(name)}.md", lines, written)

    return written


def _write(path, lines: list[str], acc: list[str]) -> None:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    acc.append(str(path))


def _slug(name: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in name.lower()).strip("-") or "module"
