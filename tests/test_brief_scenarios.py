"""M19: site/brief.py's scenario-candidate grouping — screen/layout groups
sort ahead of the backend, and the hero step-tree budget is per-group so a
repo with many frontend screens can't starve every backend route of one."""

from __future__ import annotations

from codemap.site import brief


def _node(key: str, file: str, fan_in: int = 0, churn: int = 0) -> dict:
    return {"key": key, "qual": key.split("::", 1)[-1], "file": file, "fan_in": fan_in, "churn": churn}


def _make_data(nodes: list[dict], entry_points: list[dict], edges: list[dict] | None = None) -> dict:
    return {"nodes": nodes, "entry_points": entry_points, "edges": edges or []}


def test_screen_and_layout_groups_sort_ahead_of_backend_routes():
    nodes = [
        _node("mobile/app/index.tsx::Home", "mobile/app/index.tsx"),
        _node("mobile/app/_layout.tsx::RootLayout", "mobile/app/_layout.tsx"),
        _node("api.py::route_a", "api.py"),
        _node("app.py::main", "app.py"),
    ]
    entry_points = [
        {"node": 0, "kind": "screen", "detail": "/"},
        {"node": 1, "kind": "layout", "detail": "/"},
        {"node": 2, "kind": "route", "detail": "GET /a"},
        {"node": 3, "kind": "main", "detail": "__main__ @ app.py"},
    ]
    candidates = brief._scenario_candidates(_make_data(nodes, entry_points))
    groups_in_order = [c["group"] for c in candidates]
    # Startup (main) is first, then the frontend shell/screen groups, then
    # the backend route group — this is the whole point of the change: the
    # curriculum reads as one narrative across the frontend/backend seam.
    assert groups_in_order.index("Startup") < groups_in_order.index("The app shell mounts")
    assert groups_in_order.index("The app shell mounts") < groups_in_order.index("A screen opens")
    assert groups_in_order.index("A screen opens") < groups_in_order.index("A request comes in")


def test_hero_step_budget_is_shared_per_group_not_globally_first_n(tmp_path):
    # 10 screens (would have exhausted an old flat first-8 budget entirely)
    # plus 3 backend routes — every group must still get its per-group share.
    nodes = [_node(f"mobile/app/s{i}.tsx::S{i}", f"mobile/app/s{i}.tsx") for i in range(10)]
    nodes += [_node(f"api.py::r{i}", "api.py") for i in range(3)]
    entry_points = [{"node": i, "kind": "screen", "detail": f"/s{i}"} for i in range(10)]
    entry_points += [{"node": 10 + i, "kind": "route", "detail": f"GET /r{i}"} for i in range(3)]
    # every node calls the next one so `_derive_scenario_steps` has >=2 steps
    edges = [{"s": i, "t": i + 1, "line": 1} for i in range(len(nodes) - 1)]
    data = _make_data(nodes, entry_points, edges)

    briefs_dir = tmp_path / "briefs"
    briefs_dir.mkdir()
    written: list[str] = []
    candidates = brief._emit_scenarios_derived(briefs_dir, data, written)
    assert candidates  # sanity: entry points were detected

    import json

    payload = json.loads((briefs_dir / "scenarios-derived.json").read_text(encoding="utf-8"))
    by_group: dict[str, int] = {}
    for entry in payload["scenarios"]:
        if "steps" in entry:
            by_group[entry["suggested_group"]] = by_group.get(entry["suggested_group"], 0) + 1

    assert by_group.get("A screen opens", 0) == brief._HERO_PER_GROUP
    assert by_group.get("A request comes in", 0) == brief._HERO_PER_GROUP
    assert sum(by_group.values()) <= brief._HERO_STEP_BUDGET
