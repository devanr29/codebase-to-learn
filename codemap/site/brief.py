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


def _derive_scenario_steps(
    nodes: list[dict], out_calls: dict[int, list[tuple[int, int]]], root_i: int,
    budget: int = 50, depth_cap: int = 6,
) -> list[dict]:
    """A Python mirror of explore.js's client-side Lane-1 `deriveSteps` — same
    idea (DFS in call-site line order, recursion folded, depth-capped), kept
    deliberately simpler since this only ever seeds a brief for a human/Claude
    to narrate, never the shipped runtime (that stays entirely in explore.js
    so Simulate keeps working with zero authoring on any repo)."""
    steps: list[dict] = []
    on_stack: set[int] = set()

    def visit(i: int, depth: int) -> None:
        if len(steps) >= budget:
            return
        key = nodes[i]["key"]
        steps.append({"node": key, "t": "call"})
        if i in on_stack:
            steps.append({"node": key, "t": "note", "code": "calls itself — fold this in the narration"})
        elif depth >= depth_cap:
            steps.append({"node": key, "t": "note", "code": "depth limit reached here"})
        else:
            on_stack.add(i)
            for t, _line in out_calls.get(i, [])[:6]:
                if len(steps) >= budget:
                    break
                visit(t, depth + 1)
            on_stack.discard(i)
        steps.append({"node": key, "t": "return"})

    visit(root_i, 1)
    return steps


def _emit_scenarios_derived(briefs_dir, data: dict, written: list[str]) -> None:
    """``.codemap/briefs/scenarios-derived.json`` — the same call-graph-derived
    steps Simulate's Lane 1 computes on its own, pre-extracted so the
    course-authoring skill narrates/trims a real call tree instead of
    reconstructing one by hand (SKILL.md step 6, references/scenarios-schema.md)."""
    import json as _json

    nodes = data["nodes"]
    out_calls: dict[int, list[tuple[int, int]]] = {}
    fan_in = [0] * len(nodes)
    for e in data["edges"]:
        out_calls.setdefault(e["s"], []).append((e["t"], e.get("line") or 0))
        fan_in[e["t"]] += 1
    for lst in out_calls.values():
        lst.sort(key=lambda p: p[1])

    roots = list(dict.fromkeys(ep["node"] for ep in data["entry_points"] if ep["node"] is not None))
    if len(roots) < 5:
        ranked = sorted(
            range(len(nodes)),
            key=lambda i: -(fan_in[i] + nodes[i]["churn"] * 2 + len(out_calls.get(i, ()))),
        )
        for i in ranked:
            if i not in roots:
                roots.append(i)
            if len(roots) >= 5:
                break

    scenarios = []
    for i in roots[:5]:
        steps = _derive_scenario_steps(nodes, out_calls, i)
        if len(steps) >= 2:
            scenarios.append({"root_key": nodes[i]["key"], "root_qual": nodes[i]["qual"], "steps": steps})

    path = briefs_dir / "scenarios-derived.json"
    path.write_text(_json.dumps({"scenarios": scenarios}, indent=2), encoding="utf-8")
    written.append(str(path))


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
    ov.append("3. (only if asked to simulate/walk through a run) "
              "`.codemap/scenarios.json` — narrated steps for the Simulate tab. "
              "Follow `references/scenarios-schema.md`. `scenarios-derived.json` "
              "in this directory already has a real call tree per likely entry "
              "point (node key, call/return/note, in call-site order) — narrate "
              "and trim that rather than reconstructing one by hand. For real "
              "branch/output fidelity, record an actual run instead: "
              "`codemap trace --name \"<title>\" -- <command>`.")
    ov.append("")
    ov.append("All three files are optional and fall back silently — but you were "
              "asked for what you were asked for.")
    _write(briefs_dir / "00-overview.md", ov, written)
    _emit_scenarios_derived(briefs_dir, data, written)

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
