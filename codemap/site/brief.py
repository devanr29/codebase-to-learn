"""``codemap explore --emit-brief`` — a deterministic analysis pack for the
course-authoring skill (repo-root ``SKILL.md``).

The skill reads these briefs and writes ``.codemap/libraries.json``,
``.codemap/explanations.json`` and ``.codemap/scenarios.json``; it never has to
re-read the repository, because every code snippet it needs is pre-extracted
here verbatim with its ``file (lines a-b)`` header. Mirrors the
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


# entry-point kind -> (Simulate-rail group, base order). The skill orders the
# rail ascending by `order`, so startup fires before an inbound request fires
# before a background job. `main`/`docker` cover process start; `route`/
# `controller` an inbound call; `task` a queue handler; `script` a one-shot tool.
_SCENARIO_GROUP = {
    "main": ("Startup", 10),
    "docker": ("Startup", 10),
    "route": ("A request comes in", 20),
    "controller": ("A request comes in", 20),
    "task": ("Background jobs", 40),
    "script": ("Scripts & tools", 50),
}
_SCENARIO_GROUP_DEFAULT = ("Other entry points", 60)
_HERO_STEP_BUDGET = 8   # emit real derived step trees for the first N candidates only


def _scenario_candidates(data: dict) -> list[dict]:
    """Every detected entry point as a candidate Simulate scenario, ordered the
    way the app actually runs (startup -> inbound -> background -> tools), with a
    suggested `group`/`order` the skill can keep or adjust. Nothing is capped —
    a repo with 100 routes yields 100 candidates."""
    nodes = data["nodes"]
    seen: set[int] = set()
    by_kind: dict[str, list[dict]] = {}
    for ep in data["entry_points"]:
        i = ep.get("node")
        if i is None or i in seen:
            continue
        seen.add(i)
        by_kind.setdefault(ep["kind"], []).append(
            {"i": i, "key": nodes[i]["key"], "qual": nodes[i]["qual"],
             "file": nodes[i]["file"], "kind": ep["kind"], "detail": ep["detail"],
             "fan_in": nodes[i]["fan_in"]}
        )

    out: list[dict] = []
    for kind, items in by_kind.items():
        group, base = _SCENARIO_GROUP.get(kind, _SCENARIO_GROUP_DEFAULT)
        items.sort(key=lambda c: (-c["fan_in"], c["detail"], c["qual"]))
        for rank, c in enumerate(items):
            c["group"], c["order"] = group, base + rank
            out.append(c)
    out.sort(key=lambda c: (c["order"], c["qual"]))
    return out


def _emit_scenarios_derived(briefs_dir, data: dict, written: list[str]) -> list[dict]:
    """``.codemap/briefs/scenarios-derived.json`` — every detected entry point as
    a candidate scenario (key, qual, entry kind/detail, fan-in, suggested
    group/order), plus a real call-graph-derived step tree for the first
    ``_HERO_STEP_BUDGET`` of them so the skill narrates/trims a real tree for the
    likely hero scenarios instead of reconstructing one by hand (SKILL.md step 6,
    references/scenarios-schema.md). Returns the candidate list for the overview."""
    import json as _json

    nodes = data["nodes"]
    out_calls: dict[int, list[tuple[int, int]]] = {}
    for e in data["edges"]:
        out_calls.setdefault(e["s"], []).append((e["t"], e.get("line") or 0))
    for lst in out_calls.values():
        lst.sort(key=lambda p: p[1])

    candidates = _scenario_candidates(data)
    if not candidates:
        # no entry point detected at all — fall back to the busiest few symbols so
        # the skill still has a real tree to narrate (old behaviour)
        ranked = sorted(
            range(len(nodes)),
            key=lambda i: -(nodes[i]["fan_in"] + nodes[i]["churn"] * 2 + len(out_calls.get(i, ()))),
        )[:5]
        candidates = [
            {"i": i, "key": nodes[i]["key"], "qual": nodes[i]["qual"], "file": nodes[i]["file"],
             "kind": "hotspot", "detail": "", "fan_in": nodes[i]["fan_in"],
             "group": "Derived", "order": 60 + r}
            for r, i in enumerate(ranked)
        ]

    scenarios = []
    for rank, c in enumerate(candidates):
        entry = {
            "root_key": c["key"], "root_qual": c["qual"], "file": c["file"],
            "kind": c["kind"], "detail": c["detail"], "fan_in": c["fan_in"],
            "suggested_group": c["group"], "suggested_order": c["order"],
        }
        if rank < _HERO_STEP_BUDGET:
            steps = _derive_scenario_steps(nodes, out_calls, c["i"])
            if len(steps) >= 2:
                entry["steps"] = steps
        scenarios.append(entry)

    path = briefs_dir / "scenarios-derived.json"
    path.write_text(_json.dumps({"scenarios": scenarios}, indent=2), encoding="utf-8")
    written.append(str(path))
    return candidates


def _call_sites_in_files(data: dict, paths: set[str], limit: int) -> list[str]:
    """The busiest symbols (by fan-out — they're the ones making calls) that live
    in ``paths``. A cheap proxy for "the code that actually uses this import"."""
    nodes = data["nodes"]
    hits = [n for n in nodes if n["file"] in paths]
    hits.sort(key=lambda n: (-n["fan_out"], -n["fan_in"], n["qual"]))
    return [n["key"] for n in hits[:limit]]


def _library_candidates(data: dict) -> list[dict]:
    """Every import dependency (third-party + stdlib) and every top-level repo
    module as a Learn-tab reference candidate, each with its importing files and
    the call-site symbol keys worth annotating. Nothing is capped."""
    nodes = data["nodes"]
    ep_by_module: dict[str, list[str]] = {}
    for ep in data["entry_points"]:
        i = ep.get("node")
        if i is not None:
            ep_by_module.setdefault(nodes[i]["module"], []).append(nodes[i]["key"])

    out: list[dict] = []
    for d in data["dependencies"]:
        importers = list(d.get("importers", []))
        out.append({
            "name": d["name"], "kind": d["kind"], "scope": "external",
            "importers": importers,
            "see": _call_sites_in_files(data, set(importers), 4),
        })
    for m in data["modules"]:
        paths = {f["path"] for f in data["files"] if f["fi"] in m["files"]}
        see = ep_by_module.get(m["name"], [])[:4] or _call_sites_in_files(data, paths, 4)
        out.append({
            "name": m["name"], "kind": "module", "scope": "internal",
            "files": len(m["files"]), "symbols": m["symbol_count"],
            "entry_points": ep_by_module.get(m["name"], []),
            "see": see,
        })
    return out


def _emit_libraries_derived(briefs_dir, data: dict, written: list[str]) -> list[dict]:
    """``.codemap/briefs/libraries-derived.json`` — the import-graph half of the
    Learn-tab reference (every dependency + module, its importers, its call
    sites) for the skill to annotate with ``general`` / ``here`` prose. Returns
    the candidate list so the overview can list the same set."""
    import json as _json

    candidates = _library_candidates(data)
    items = {c["name"]: {k: v for k, v in c.items() if k != "name"} for c in candidates}
    path = briefs_dir / "libraries-derived.json"
    path.write_text(_json.dumps({"items": items}, indent=2), encoding="utf-8")
    written.append(str(path))
    return candidates


def emit(conn: sqlite3.Connection, cfg: Config, data: dict) -> list[str]:
    briefs_dir = cfg.codemap_dir / "briefs"
    briefs_dir.mkdir(parents=True, exist_ok=True)

    nodes = data["nodes"]
    files = data["files"]
    file_by_fi = {f["fi"]: f for f in files}
    modules = data["modules"]
    written: list[str] = []

    # derived JSON packs first — the overview lists the same candidate sets
    library_candidates = _emit_libraries_derived(briefs_dir, data, written)
    scenario_candidates = _emit_scenarios_derived(briefs_dir, data, written)

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
    ov.append("## Dependency reference (candidates for `libraries.json`)")
    ov.append("The Learn tab is a library / module reference. Cover **every** "
              "entry below: write a `general` line (what it is, for someone who's "
              "never used it — skip for obvious stdlib) and a `here` line (its "
              "concrete job in this repo). `libraries-derived.json` carries the "
              "importers + call-site `see` keys — annotate that.")
    ov.append("")
    ext = [c for c in library_candidates if c["scope"] == "external"]
    ov.append(f"- **External packages** ({len(ext)})")
    for c in ext:
        imp = ", ".join(f"`{p}`" for p in c["importers"][:6]) or "—"
        ov.append(f"  - `{c['name']}` [{c['kind']}] — imported by {imp}")
    ov.append("- **This repo's modules**")
    for c in (c for c in library_candidates if c["scope"] == "internal"):
        eps = f", {len(c['entry_points'])} entry points" if c["entry_points"] else ""
        ov.append(f"  - `{c['name']}` — {c['files']} files, {c['symbols']} symbols{eps}")
    ov.append("")
    ov.append("## Scenario index (candidates for `scenarios.json`)")
    if scenario_candidates:
        ov.append("Every entry point below is a candidate Simulate scenario. Ship "
                  "them **all** as an ordered, grouped index (`id` + `title` + "
                  "`root` + `group` + `order` + `summary`, no `steps`); the "
                  "renderer derives each call tree. Hand-author `steps` only for "
                  "the few a Learn screen links via `\"sim\"`. `scenarios-derived.json` "
                  "has real step trees for the first "
                  f"{_HERO_STEP_BUDGET}. Suggested `group`/`order` are a starting "
                  "point — reorder to match how the app really runs.")
        ov.append("")
        last_group = None
        for c in scenario_candidates:
            if c["group"] != last_group:
                ov.append(f"- **{c['group']}**")
                last_group = c["group"]
            det = f" — {c['detail']}" if c["detail"] else ""
            ov.append(f"  - `order {c['order']}` [{c['kind']}]{det} -> `{c['qual']}`  "
                      f"(`key:` `{c['key']}`, {c['fan_in']} callers)")
    else:
        ov.append("- no entry points detected — see `scenarios-derived.json` for "
                  "the busiest-symbol fallback the Simulate tab uses on its own.")
    ov.append("")
    ov.append("## What to produce")
    ov.append("1. `.codemap/libraries.json` — the Learn tab's library / module "
              "reference. Follow `references/libraries-schema.md`. Annotate every "
              "entry from the **Dependency reference** above with `general` + "
              "`here` (+ optional `see` keys); start from `libraries-derived.json`.")
    ov.append("2. `.codemap/explanations.json` — one plain-English `what` line per "
              "symbol, shown in the Graph inspector. Follow "
              "`references/explanations-schema.md`. Key every entry by the symbol "
              "`key:` printed in the per-module briefs. Cover at least every "
              "snippet in those briefs (hotspots + entry points).")
    ov.append("3. `.codemap/scenarios.json` — the Simulate tab's scenario "
              "curriculum. Follow `references/scenarios-schema.md`. Turn the "
              "**Scenario index** above into one ordered, grouped list: every "
              "entry an `id` + `title` + `root` (the `key:` shown) + `group` + "
              "`order` + one-line `summary`, sorted by how the app really runs. "
              "Omit `steps` — the renderer derives them. Hand-author `steps` only "
              "for the 3–6 hero scenarios a Learn screen links via `\"sim\"`, "
              "narrating/trimming `scenarios-derived.json` rather than rebuilding "
              "a tree. For real branch/output fidelity record an actual run: "
              "`codemap trace --name \"<title>\" -- <command>`.")
    ov.append("")
    ov.append("All three files are optional and fall back silently — but you were "
              "asked for what you were asked for.")
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
        lines.append("## Checklist for this module")
        lines.append(f"- [ ] `libraries.json` — `here` line for `{name}` naming these symbols")
        lines.append("- [ ] `libraries.json` — every third-party / stdlib import this "
                     "module uses (see **Talks to** and the per-file imports)")
        lines.append("- [ ] `explanations.json` — a `what` line for every snippet key above")
        lines.append("")
        lines.append("## Reference files to read")
        lines.append("- `references/content-philosophy.md` — always")
        lines.append("- `references/gotchas.md` — always")
        lines.append("- `references/libraries-schema.md` — for `libraries.json`")
        lines.append("- `references/explanations-schema.md` — for `explanations.json`")
        lines.append("- `references/scenarios-schema.md` — for `scenarios.json`")
        _write(briefs_dir / f"{i:02d}-{_slug(name)}.md", lines, written)

    return written


def _write(path, lines: list[str], acc: list[str]) -> None:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    acc.append(str(path))


def _slug(name: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in name.lower()).strip("-") or "module"
