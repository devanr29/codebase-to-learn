"""Optional codebase-memory-mcp enrichment: merged in when present, invisible when not."""

from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import networkx as nx
import pytest

from codemap import cli, config, db, indexer
from codemap.enrich import cbm, engine
from codemap.impact import AMBIGUOUS, EXTRACTED, INFERRED
from codemap.site import model
from tests.cbm_fake import (
    BUILD, FETCH, MAIN, RENDER, VIEW,
    IMPACT_EDGES as EDGES, IMPACT_NODES as NODES, make_cbm_db,
)
from tests.fixtures.build_repo import build_impact_repo


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    repo = build_impact_repo(tmp_path_factory.mktemp("cbm_repo"))
    cfg = config.load(repo.path)
    cfg.codemap_dir.mkdir(exist_ok=True)
    conn = db.connect(cfg.db_path)
    db.migrate(conn)
    indexer.scan(conn, cfg, until=repo.sha("i3-sig-partial"))
    hashes = {
        p: hashlib.sha256((repo.path / p).read_bytes()).hexdigest()
        for p in ("svc/cli.py", "svc/data.py", "svc/report.py", "svc/web.py")
    }
    yield SimpleNamespace(repo=repo, conn=conn, hashes=hashes)
    conn.close()


@pytest.fixture
def cache(tmp_path, monkeypatch):
    d = tmp_path / "cbm-cache"
    d.mkdir()
    monkeypatch.setenv("CBM_CACHE_DIR", str(d))
    return d


def _cfg(env, mode="auto"):
    cfg = config.load(env.repo.path)
    cfg.engine.codebase_memory = mode
    return cfg


def _fake(env, cache, **kw):
    args = dict(nodes=NODES, edges=EDGES, hashes=env.hashes)
    args.update(kw)
    return make_cbm_db(cache, env.repo.path, **args)


def _edges(data):
    key = {n["i"]: n["key"] for n in data["nodes"]}
    return {(key[e["s"]].split("::")[1], key[e["t"]].split("::")[1]): e for e in data["edges"]}


# --------------------------------------------------------------------------- nothing there


def test_no_index_means_output_identical_to_engine_off(env, cache):
    plain = model.build(env.conn, _cfg(env, "off"))
    auto = model.build(env.conn, _cfg(env))          # cache dir is empty
    for k in ("nodes", "edges", "stats", "files", "file_edges"):
        assert plain[k] == auto[k], k
    assert "engine" not in auto and "routes" not in auto
    assert all("via" not in e for e in auto["edges"])


def test_off_never_looks_even_when_an_index_exists(env, cache):
    _fake(env, cache)
    data = model.build(env.conn, _cfg(env, "off"))
    assert "engine" not in data and "routes" not in data
    assert engine.describe(_cfg(env, "off")).startswith("codemap only")
    assert "off" in engine.describe(_cfg(env, "off"))


# --------------------------------------------------------------------------- merging into the model


def test_missing_links_are_added_and_tagged(env, cache):
    _fake(env, cache)
    data = model.build(env.conn, _cfg(env))
    edges = _edges(data)

    new = edges[("main", "render")]
    assert new["via"] == "cbm" and new["confidence"] == EXTRACTED and new["engine_strategy"] == "import_map"
    guess = edges[("report_view", "fetch")]
    assert guess["via"] == "cbm" and guess["confidence"] == INFERRED

    assert ("fetch", "render") not in edges, "a sub-0.70 guess must not become an edge"
    assert "via" not in edges[("main", "build_report")], "codemap's own edge is left alone"
    assert data["engine"]["added"] == 2 and data["engine"]["name"] == "codemap + codebase-memory"
    assert data["engine"]["project"] == "fake-project"
    plain = model.build(env.conn, _cfg(env, "off"))
    assert len(data["edges"]) == len(plain["edges"]) + 2
    assert data["stats"]["edges"] == len(data["edges"])
    json.dumps(data)  # the payload stays serializable


def test_added_links_count_towards_fan_in_and_out(env, cache):
    _fake(env, cache)
    with_cbm = {n["name"]: n for n in model.build(env.conn, _cfg(env))["nodes"]}
    without = {n["name"]: n for n in model.build(env.conn, _cfg(env, "off"))["nodes"]}
    assert with_cbm["render"]["fan_in"] == without["render"]["fan_in"] + 1
    assert with_cbm["main"]["fan_out"] == without["main"]["fan_out"] + 1


def test_a_stale_file_is_skipped_not_trusted(env, cache):
    stale = dict(env.hashes, **{"svc/report.py": "0" * 64})   # CBM indexed a different report.py
    _fake(env, cache, hashes=stale)
    data = model.build(env.conn, _cfg(env))
    edges = _edges(data)
    assert ("main", "render") not in edges                      # render lives in the stale file
    assert edges[("report_view", "fetch")]["via"] == "cbm"      # files still in sync: kept
    assert data["engine"]["added"] == 1 and data["engine"]["stale_files"] == 1


def test_a_file_cbm_never_hashed_is_skipped(env, cache):
    _fake(env, cache, hashes={k: v for k, v in env.hashes.items() if k != "svc/web.py"})
    edges = _edges(model.build(env.conn, _cfg(env)))
    assert ("report_view", "fetch") not in edges and ("main", "render") in edges


def test_route_links_are_carried_as_node_indexes(env, cache):
    _fake(env, cache)
    data = model.build(env.conn, _cfg(env))
    idx = {n["name"]: n["i"] for n in data["nodes"]}
    assert data["routes"] == [
        {"url": "/api/report", "method": "GET", "callers": [idx["main"]], "handlers": [idx["report_view"]]}
    ]


# --------------------------------------------------------------------------- degrading


def test_a_newer_index_format_disables_enrichment_with_a_reason(env, cache):
    _fake(env, cache, user_version=2)
    data = model.build(env.conn, _cfg(env))
    assert "engine" not in data
    assert all("via" not in e for e in data["edges"])
    line = engine.describe(_cfg(env))
    assert line.startswith("codemap only") and "format v2" in line


def test_a_renamed_column_disables_enrichment_with_a_reason(env, cache):
    _fake(env, cache, after_sql="ALTER TABLE nodes RENAME COLUMN file_path TO path;")
    assert "engine" not in model.build(env.conn, _cfg(env))
    assert "nodes.file_path" in engine.describe(_cfg(env))


def test_a_garbage_file_in_the_cache_is_ignored(env, cache):
    (cache / "junk.db").write_bytes(b"this is not sqlite")
    assert "engine" not in model.build(env.conn, _cfg(env))
    assert "no index for this repo" in engine.describe(_cfg(env))
    _fake(env, cache)
    assert model.build(env.conn, _cfg(env))["engine"]["added"] == 2


def test_no_cache_dir_at_all_says_so(env, tmp_path, monkeypatch):
    monkeypatch.setenv("CBM_CACHE_DIR", str(tmp_path / "nope"))
    monkeypatch.setattr(cbm, "DEFAULT_CACHE_DIR", str(tmp_path / "also-nope"))
    assert "not detected" in engine.describe(_cfg(env))


def test_the_index_is_matched_by_root_not_by_project_name(env, cache, tmp_path):
    make_cbm_db(cache, tmp_path / "somewhere-else", project="a-decoy", nodes=NODES, edges=[], hashes={})
    _fake(env, cache, project="whatever-cbm-named-it")
    found, why = cbm.locate(env.repo.path, str(cache))
    assert found is not None and found.project == "whatever-cbm-named-it" and why == ""
    found.close()
    assert cbm.locate(tmp_path / "unknown", str(cache))[0] is None


def test_the_index_is_never_written_to(env, cache):
    path = _fake(env, cache)
    before = path.read_bytes()
    model.build(env.conn, _cfg(env))
    assert path.read_bytes() == before
    assert not list(cache.glob("*-wal")) and not list(cache.glob("*-shm"))


def test_config_cache_dir_is_used_before_the_environment(env, cache, tmp_path, monkeypatch):
    monkeypatch.setenv("CBM_CACHE_DIR", str(tmp_path / "wrong"))
    _fake(env, cache)
    cfg = _cfg(env)
    cfg.engine.cache_dir = str(cache)
    assert model.build(env.conn, cfg)["engine"]["added"] == 2


def test_config_template_documents_the_engine_table(tmp_path):
    cfg = config.init(tmp_path)
    assert cfg.engine.codebase_memory == "auto" and cfg.engine.cache_dir == ""
    text = cfg.config_path.read_text(encoding="utf-8")
    assert "[engine]" in text and 'codebase_memory = "auto"' in text
    cfg.config_path.write_text('[engine]\ncodebase_memory = "OFF"\ncache_dir = "/x"\n', encoding="utf-8")
    loaded = config.load(tmp_path)
    assert loaded.engine.codebase_memory == "off" and loaded.engine.cache_dir == "/x"
    cfg.config_path.write_text('[engine]\ncodebase_memory = "sometimes"\n', encoding="utf-8")
    assert config.load(tmp_path).engine.codebase_memory == "auto"


# --------------------------------------------------------------------------- merge (pure)


def _g(*edges):
    g = nx.DiGraph()
    for a, b, conf in edges:
        g.add_edge(a, b, confidence=conf)
    return g


def test_merge_settles_an_ambiguous_guess_when_cbm_resolves_one_target():
    # `caller` calls `run()`: codemap could only guess among three same-named symbols
    g = _g(("caller", "a.run", AMBIGUOUS), ("caller", "b.run", AMBIGUOUS), ("caller", "c.run", AMBIGUOUS))
    names = {"caller": "caller", "a.run": "run", "b.run": "run", "c.run": "run"}
    stats = cbm.merge_into_graph(g, [cbm.CbmEdge("caller", "b.run", 0.95, "import_map", 12)], names, AMBIGUOUS)
    assert stats == {"added": 0, "upgraded": 1, "dropped": 2}
    assert list(g.edges) == [("caller", "b.run")]
    e = g.edges["caller", "b.run"]
    assert e["confidence"] == EXTRACTED and e["via"] == "cbm" and e["line"] == 12


def test_merge_keeps_the_guesses_when_cbm_resolves_several_targets():
    g = _g(("caller", "a.run", AMBIGUOUS), ("caller", "b.run", AMBIGUOUS), ("caller", "c.run", AMBIGUOUS))
    names = {k: "run" for k in ("a.run", "b.run", "c.run")} | {"caller": "caller"}
    stats = cbm.merge_into_graph(
        g, [cbm.CbmEdge("caller", "a.run", 0.9, "s"), cbm.CbmEdge("caller", "b.run", 0.9, "s")], names, AMBIGUOUS)
    assert stats["dropped"] == 0 and g.has_edge("caller", "c.run")


def test_merge_never_touches_a_confident_edge_or_an_unknown_symbol():
    g = _g(("a", "b", EXTRACTED))
    stats = cbm.merge_into_graph(
        g, [cbm.CbmEdge("a", "b", 0.70, "unique_name"), cbm.CbmEdge("a", "ghost", 0.95, "s")], {"a": "a", "b": "b"}, AMBIGUOUS)
    assert stats == {"added": 0, "upgraded": 0, "dropped": 0}
    assert g.edges["a", "b"] == {"confidence": EXTRACTED}


def test_paths_compare_across_separators_and_trailing_slashes():
    assert cbm._norm("/a/b/") == cbm._norm("/a/b")
    assert cbm._norm("C:\\Users\\x") == cbm._norm("C:/Users/x/")


def test_root_recorded_in_another_spelling_still_matches(env, cache):
    # `repo/../repo` and (on Windows) an 8.3 short name are the same directory
    roundabout = env.repo.path / ".." / env.repo.path.name
    make_cbm_db(cache, roundabout, nodes=NODES, edges=EDGES, hashes=env.hashes)
    found, why = cbm.locate(env.repo.path, str(cache))
    assert found is not None, why
    found.close()


# --------------------------------------------------------------------------- status


def test_status_reports_the_engine(env, cache, capsys):
    args = SimpleNamespace(path=str(env.repo.path), no_progress=True)
    assert cli.cmd_status(args) == 0
    assert "engine:        codemap only" in capsys.readouterr().out

    _fake(env, cache)
    assert cli.cmd_status(args) == 0
    out = capsys.readouterr().out
    assert "engine:        codemap + codebase-memory (project fake-project, 5 call links indexed" in out
