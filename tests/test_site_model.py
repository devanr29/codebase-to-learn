"""M10/M11: the explorer graph model built from the index."""

from __future__ import annotations

import json
import subprocess

from codemap import config, db, indexer
from codemap.impact import call_graph
from codemap.site import model


def _idx(repo, tmp_path, until):
    cfg = config.load(repo.path)
    conn = db.connect(tmp_path / "index.db")
    db.migrate(conn)
    indexer.scan(conn, cfg, until=repo.sha(until))
    return cfg, conn


def test_model_is_json_serializable_and_shaped(fixture_impact_repo, tmp_path):
    cfg, conn = _idx(fixture_impact_repo, tmp_path, "i3-sig-partial")
    data = model.build(conn, cfg)

    # round-trips through JSON unchanged
    assert json.loads(json.dumps(data)) == data

    assert set(data) >= {
        "nodes", "edges", "files", "file_edges", "modules",
        "entry_points", "timeline", "stats", "commit", "root",
    }
    assert data["stats"]["files"] == len(data["files"])
    assert data["stats"]["symbols"] == len(data["nodes"])
    # every node carries the fields the front-end reads
    n = data["nodes"][0]
    assert set(n) >= {"i", "key", "kind", "name", "qual", "file", "line", "fan_in", "fan_out"}
    assert n["i"] == 0 and data["nodes"][-1]["i"] == len(data["nodes"]) - 1


def test_edges_are_a_subset_of_the_call_graph(fixture_impact_repo, tmp_path):
    cfg, conn = _idx(fixture_impact_repo, tmp_path, "i3-sig-partial")
    data = model.build(conn, cfg)
    g = call_graph(conn, data["commit"])
    key = {n["i"]: n["key"] for n in data["nodes"]}
    for e in data["edges"]:
        assert g.has_edge(key[e["s"]], key[e["t"]]), (key[e["s"]], key[e["t"]])


def test_import_edges_and_entry_points(fixture_impact_repo, tmp_path):
    cfg, conn = _idx(fixture_impact_repo, tmp_path, "i3-sig-partial")
    data = model.build(conn, cfg)
    paths = [f["path"] for f in data["files"]]

    # svc/web.py imports svc/report.py -> a file edge between them
    fi = {f["path"]: f["fi"] for f in data["files"]}
    assert {"s": fi["svc/web.py"], "t": fi["svc/report.py"]} in data["file_edges"]

    kinds = {ep["kind"] for ep in data["entry_points"]}
    assert "route" in kinds and "main" in kinds
    route = next(ep for ep in data["entry_points"] if ep["kind"] == "route")
    assert data["nodes"][route["node"]]["name"] == "report_view"


def test_timeline_covers_indexed_commits_with_headlines(fixture_impact_repo, tmp_path):
    cfg, conn = _idx(fixture_impact_repo, tmp_path, "i3-sig-partial")
    data = model.build(conn, cfg)
    shas = {c["short"] for c in data["timeline"]}
    assert fixture_impact_repo.sha("i3-sig-partial")[:7] in shas
    sig = next(c for c in data["timeline"] if c["short"] == fixture_impact_repo.sha("i3-sig-partial")[:7])
    assert "build_report" in (sig["headline"] or "")
    assert sig["intent"]["source"] == "commit_message"
    # single-line commit message: the subject IS the whole "intent" — the
    # card must not print it a second time under intent.text (see the
    # multi-line case below for when there genuinely is more to show).
    assert sig["intent"]["text"] == ""


def test_timeline_intent_text_is_the_body_not_the_subject_again(tmp_path):
    """A multi-line commit message used to duplicate its own subject line as
    the "intent" line — this is the fix: intent.text is the body only."""
    _git = lambda *args: subprocess.run(  # noqa: E731
        ["git", *args], cwd=str(tmp_path), capture_output=True, text=True, check=True
    )
    _git("init", "-q")
    _git("config", "user.email", "t@e.com")
    _git("config", "user.name", "t")
    _git("config", "commit.gpgsign", "false")
    (tmp_path / "a.py").write_text("def f():\n    return 1\n")
    _git("add", "-A")
    _git("commit", "-q", "-m", "add ignore", "-m", "explains why in more detail")
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(tmp_path), capture_output=True, text=True
    ).stdout.strip()

    cfg = config.load(tmp_path)
    conn = db.connect(cfg.db_path)
    db.migrate(conn)
    indexer.scan(conn, cfg, until=sha)
    data = model.build(conn, cfg)

    card = next(c for c in data["timeline"] if c["short"] == sha[:7])
    assert card["subject"] == "add ignore"
    assert card["intent"]["text"] == "explains why in more detail"


def test_cycles_stat_counts_file_import_cycles_not_symbol_call_cycles(tmp_path):
    """Map's Layers view (the only place "cycles" is drawn) only ever cycles
    over *files* that import each other. Two functions calling each other
    within one file (no import at all) used to inflate this same top-bar
    stat, because it was counted from the symbol-level call graph instead."""
    _git = lambda *args: subprocess.run(  # noqa: E731
        ["git", *args], cwd=str(tmp_path), capture_output=True, text=True, check=True
    )
    _git("init", "-q")
    _git("config", "user.email", "t@e.com")
    _git("config", "user.name", "t")
    _git("config", "commit.gpgsign", "false")
    (tmp_path / "a.py").write_text("from b import g\n\ndef f():\n    return g()\n")
    (tmp_path / "b.py").write_text("from a import f\n\ndef g():\n    return f()\n")
    (tmp_path / "solo.py").write_text(
        "def ping():\n    return pong()\n\ndef pong():\n    return ping()\n"
    )
    _git("add", "-A")
    _git("commit", "-q", "-m", "seed")
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(tmp_path), capture_output=True, text=True
    ).stdout.strip()

    cfg = config.load(tmp_path)
    conn = db.connect(cfg.db_path)
    db.migrate(conn)
    indexer.scan(conn, cfg, until=sha)
    data = model.build(conn, cfg)

    assert data["stats"]["cycles"] == 1


def test_fan_in_excludes_ambiguous_guesses(tmp_path):
    """A repo-wide name guess (AMBIGUOUS) must not blend into fan_in — it's
    the exact mechanism that used to make a dict `.get()` inflate an
    unrelated class's method into looking like the busiest symbol in the
    repo. It's still visible, just separately, as fan_in_guess."""
    _git = lambda *args: subprocess.run(  # noqa: E731
        ["git", *args], cwd=str(tmp_path), capture_output=True, text=True, check=True
    )
    _git("init", "-q")
    _git("config", "user.email", "t@e.com")
    _git("config", "user.name", "t")
    _git("config", "commit.gpgsign", "false")
    (tmp_path / "a.py").write_text(
        "def helper():\n    return 1\n\ndef caller():\n    return helper()\n"
    )
    (tmp_path / "b.py").write_text("def helper():\n    return 2\n")
    (tmp_path / "c.py").write_text("def other():\n    return helper()\n")
    _git("add", "-A")
    _git("commit", "-q", "-m", "seed")
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(tmp_path), capture_output=True, text=True
    ).stdout.strip()

    cfg = config.load(tmp_path)
    conn = db.connect(cfg.db_path)
    db.migrate(conn)
    indexer.scan(conn, cfg, until=sha)
    data = model.build(conn, cfg)
    by_key = {n["key"]: n for n in data["nodes"]}

    assert by_key["a.py::helper"]["fan_in"] == 1        # caller()'s same-file EXTRACTED call
    assert by_key["a.py::helper"]["fan_in_guess"] == 1  # other()'s repo-wide AMBIGUOUS guess
    assert by_key["b.py::helper"]["fan_in"] == 0
    assert by_key["b.py::helper"]["fan_in_guess"] == 1
    assert data["impact_depth"] == cfg.impact_depth


def test_model_is_deterministic_apart_from_built_at(fixture_impact_repo, tmp_path):
    cfg, conn = _idx(fixture_impact_repo, tmp_path, "i3-sig-partial")
    a = model.build(conn, cfg)
    b = model.build(conn, cfg)
    a.pop("built_at")
    b.pop("built_at")
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_symbol_budget_degrades_but_keeps_all_files(fixture_impact_repo, tmp_path):
    cfg, conn = _idx(fixture_impact_repo, tmp_path, "i3-sig-partial")
    data = model.build(conn, cfg, max_symbols=3)
    assert len(data["nodes"]) == 3
    assert data["degraded"]["shown"] == 3
    assert data["stats"]["files"] == len(data["files"])  # files always complete


def test_dependencies_panel_shape_is_well_formed(fixture_impact_repo, tmp_path):
    cfg, conn = _idx(fixture_impact_repo, tmp_path, "i3-sig-partial")
    data = model.build(conn, cfg)

    assert isinstance(data["dependencies"], list)
    rank = {"third_party": 0, "stdlib": 1}
    seen = []
    for d in data["dependencies"]:
        assert set(d) == {"name", "kind", "count", "importers"}
        assert d["kind"] in rank
        assert d["count"] == len(d["importers"]) or len(d["importers"]) == 40
        assert d["importers"] == sorted(d["importers"])
        seen.append((rank[d["kind"]], d["name"]))
    assert seen == sorted(seen)  # third_party before stdlib, then name

    for f in data["files"]:
        for dep in f["deps"]:
            assert set(dep) == {"name", "kind"}
            assert dep["kind"] in ("third_party", "stdlib", "internal")


def test_third_party_dependency_is_detected(fixture_repo, tmp_path):
    # fixture_repo's app/core.py gains `import requests` at c9-dependency
    cfg, conn = _idx(fixture_repo, tmp_path, "c9-dependency")
    data = model.build(conn, cfg)

    req = next((d for d in data["dependencies"] if d["name"] == "requests"), None)
    assert req is not None and req["kind"] == "third_party"
    assert "app/core.py" in req["importers"]

    core = next(f for f in data["files"] if f["path"] == "app/core.py")
    assert {"name": "requests", "kind": "third_party"} in core["deps"]
    # the app.util sibling import shows up as an internal dep on the same file
    assert any(d["kind"] == "internal" for d in core["deps"])
