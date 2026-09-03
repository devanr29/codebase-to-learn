"""Simulate tab: model.py's edge `line`, scenarios.py (Lane 2 loader),
tracer.py (Lane 3 recorder), and simulate.py (merges both into `data.sim`,
resolving symbol keys to graph node indices)."""

from __future__ import annotations

import json

from codemap import config, db, indexer, tracer
from codemap.site import model, scenarios, simulate


def _idx(repo, tmp_path, until):
    cfg = config.load(repo.path)
    conn = db.connect(tmp_path / "index.db")
    db.migrate(conn)
    indexer.scan(conn, cfg, until=repo.sha(until))
    return cfg, conn


def test_edges_carry_call_site_line(fixture_impact_repo, tmp_path):
    cfg, conn = _idx(fixture_impact_repo, tmp_path, "i3-sig-partial")
    data = model.build(conn, cfg)
    assert data["edges"], "fixture repo should have at least one call edge"
    # every edge either has a real line, or None (only when the underlying ref
    # genuinely wasn't found — never a crash, never silently the wrong number)
    for e in data["edges"]:
        assert e["line"] is None or isinstance(e["line"], int)
    assert any(e["line"] is not None for e in data["edges"])


def test_model_always_has_a_sim_key_even_with_no_scenarios(fixture_impact_repo, tmp_path):
    cfg, conn = _idx(fixture_impact_repo, tmp_path, "i3-sig-partial")
    cfg.codemap_dir.mkdir(exist_ok=True)
    for name in ("scenarios.json",):
        p = cfg.codemap_dir / name
        if p.exists():
            p.unlink()
    data = model.build(conn, cfg)
    assert data["sim"] == {"scenarios": []}
    # round-trips through JSON unchanged, same contract as the rest of the payload
    assert json.loads(json.dumps(data["sim"])) == data["sim"]


def test_scenarios_json_absent_malformed_then_valid(fixture_impact_repo, tmp_path):
    cfg = config.load(fixture_impact_repo.path)
    cfg.codemap_dir.mkdir(exist_ok=True)
    sf = cfg.codemap_dir / "scenarios.json"
    sf.unlink(missing_ok=True)
    try:
        assert scenarios.load(cfg) == []  # absent -> []

        sf.write_text("{ not json", encoding="utf-8")
        assert scenarios.load(cfg) == []  # malformed -> []

        sf.write_text(json.dumps({"scenarios": [
            {"id": "ok", "title": "A run", "steps": [
                {"node": "a::b", "t": "call"}, {"node": "a::b", "t": "return"},
            ]},
            {"id": "no-steps", "title": "Empty", "steps": []},          # dropped: no steps
            {"id": "", "title": "Blank id", "steps": [{"node": "x", "t": "call"}]},  # dropped: no id
            {"title": "Missing id field", "steps": [{"node": "x", "t": "call"}]},    # dropped
            "not an object",                                            # dropped
        ]}), encoding="utf-8")
        loaded = scenarios.load(cfg)
        assert [s["id"] for s in loaded] == ["ok"]
        assert loaded[0]["source"] == "authored"
        assert loaded[0]["trigger"] == {"surface": "terminal", "text": ""}
    finally:
        sf.unlink(missing_ok=True)


def test_simulate_build_resolves_keys_and_drops_unresolvable(fixture_impact_repo, tmp_path):
    cfg, conn = _idx(fixture_impact_repo, tmp_path, "i3-sig-partial")
    cfg.codemap_dir.mkdir(exist_ok=True)
    data = model.build(conn, cfg)
    conn.close()
    real_key = data["nodes"][0]["key"]
    real_i = data["nodes"][0]["i"]

    sf = cfg.codemap_dir / "scenarios.json"
    sf.write_text(json.dumps({"scenarios": [
        {
            "id": "demo", "title": "Demo", "root": real_key,
            "steps": [
                {"node": real_key, "t": "call"},
                {"node": "does/not::exist", "t": "call"},   # dropped, not an error
                {"node": real_key, "t": "return"},
            ],
        },
        {
            "id": "all-broken", "title": "Nothing resolves",
            "steps": [{"node": "does/not::exist", "t": "call"}, {"node": "also/not::real", "t": "return"}],
        },
    ]}), encoding="utf-8")
    try:
        out = simulate.build(cfg, {n["key"]: n["i"] for n in data["nodes"]})
    finally:
        sf.unlink(missing_ok=True)

    ids = [s["id"] for s in out["scenarios"]]
    assert ids == ["demo"]                       # the all-broken one had <2 resolvable steps
    demo = out["scenarios"][0]
    assert demo["root"] == real_i
    assert [s["node"] for s in demo["steps"]] == [real_i, real_i]   # the bad step vanished, not crashed


def _idx_at_cfg_db(repo, until):
    """tracer.record() reads `cfg.db_path` directly (it's a CLI-level op, like
    `cmd_explore`, not a pure function of an injected `conn` like model.build) —
    so its tests need the index at the real config path, not a scratch one."""
    cfg = config.load(repo.path)
    cfg.codemap_dir.mkdir(exist_ok=True)
    conn = db.connect(cfg.db_path)
    db.migrate(conn)
    indexer.scan(conn, cfg, until=repo.sha(until))
    conn.close()
    return cfg


def test_tracer_records_a_real_run_inside_the_repo(fixture_impact_repo, tmp_path):
    cfg = _idx_at_cfg_db(fixture_impact_repo, "i3-sig-partial")

    script = tmp_path / "runner.py"
    target_file = fixture_impact_repo.path / "svc" / "report.py"
    assert target_file.exists(), "fixture repo layout changed — update this test's target"
    script.write_text(
        "import sys\n"
        f"sys.path.insert(0, {str(fixture_impact_repo.path)!r})\n"
        "from svc import report\n"
        "print('about to build')\n"
        "report.build_report('daily')\n"
        "print('done')\n",
        encoding="utf-8",
    )
    trace = tracer.record(cfg, [str(script)], name="test run")
    assert trace["source"] == "recorded"
    assert "crashed" not in trace
    assert any(s["t"] == "call" and s["node"] == "svc/report.py::build_report" for s in trace["steps"])
    emits = [s for s in trace["steps"] if s["t"] == "emit"]
    assert any(e["emit"]["text"] == "about to build" for e in emits)
    assert any(e["emit"]["text"] == "done" for e in emits)
    # call/return balance
    depth = 0
    for s in trace["steps"]:
        if s["t"] == "call":
            depth += 1
        elif s["t"] == "return":
            depth -= 1
    assert depth == 0

    out_path = tracer.write(cfg, trace)
    assert out_path.exists()
    loaded = tracer.load_all(cfg)
    assert any(t["id"] == trace["id"] for t in loaded)
    out_path.unlink()


def test_tracer_survives_a_crashing_target(fixture_impact_repo, tmp_path):
    cfg = _idx_at_cfg_db(fixture_impact_repo, "i3-sig-partial")
    script = tmp_path / "boom.py"
    script.write_text("raise ValueError('boom')\n", encoding="utf-8")
    trace = tracer.record(cfg, [str(script)], name="boom run")
    assert trace.get("crashed", "").startswith("ValueError")


def test_tracer_load_all_ignores_junk_files(fixture_impact_repo, tmp_path):
    cfg = config.load(fixture_impact_repo.path)
    cfg.codemap_dir.mkdir(exist_ok=True)
    traces_dir = cfg.codemap_dir / "traces"
    traces_dir.mkdir(exist_ok=True)
    junk = traces_dir / "junk.json"
    junk.write_text("{ not json", encoding="utf-8")
    try:
        assert tracer.load_all(cfg) == []
    finally:
        junk.unlink()
