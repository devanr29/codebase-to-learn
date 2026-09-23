"""`codemap calls`: what a symbol calls / what calls it, straight from the graph."""

from __future__ import annotations

import json
from types import SimpleNamespace

import networkx as nx
import pytest

from codemap import calls, cli, config, db, indexer
from codemap.impact import AMBIGUOUS, EXTRACTED, INFERRED
from tests.fixtures.build_repo import build_impact_repo

# The impact fixture at i3-sig-partial resolves exactly these call edges:
#   main -> build_report (INFERRED, line 5)     report_view -> build_report (INFERRED, line 6)
#   build_report -> fetch (INFERRED, line 5)    build_report -> render (EXTRACTED, line 6)
#   fetch -> db_query (EXTRACTED, line 2)
MAIN = "svc/cli.py::main"
VIEW = "svc/web.py::report_view"
BUILD = "svc/report.py::build_report"
FETCH = "svc/data.py::fetch"
RENDER = "svc/report.py::render"
DBQ = "svc/data.py::db_query"


@pytest.fixture(scope="module")
def repo(tmp_path_factory):
    r = build_impact_repo(tmp_path_factory.mktemp("calls_repo"))
    cfg = config.load(r.path)
    cfg.codemap_dir.mkdir(exist_ok=True)
    conn = db.connect(cfg.db_path)
    db.migrate(conn)
    indexer.scan(conn, cfg, until=r.sha("i3-sig-partial"))
    conn.close()
    return r


def _args(repo, target, **kw):
    base = dict(path=str(repo.path), target=target, direction="out", depth=3,
                no_guesses=False, json=False, no_progress=True)
    return SimpleNamespace(**{**base, **kw})


def _run(repo, capsys, target, **kw):
    rc = cli.cmd_calls(_args(repo, target, **kw))
    return rc, capsys.readouterr().out


# --------------------------------------------------------------------------- CLI


def test_out_lists_the_real_call_tree_with_confidence_and_call_sites(repo, capsys):
    rc, out = _run(repo, capsys, MAIN)
    assert rc == 0
    lines = out.splitlines()
    assert lines[0].startswith(f"{MAIN}   svc/cli.py:4")
    body = "\n".join(lines[1:])
    for key in (BUILD, FETCH, RENDER, DBQ):
        assert key in body
    build = next(ln for ln in lines if ln.strip().startswith(f"-> {BUILD}"))
    assert "INFERRED" in build and "call at svc/cli.py:5" in build
    render = next(ln for ln in lines if ln.strip().startswith(f"-> {RENDER}"))
    assert "EXTRACTED" in render and "call at svc/report.py:6" in render
    assert "more beyond" not in out
    # a callee is indented under the function that calls it
    assert next(i for i, ln in enumerate(lines) if FETCH in ln) > next(i for i, ln in enumerate(lines) if BUILD in ln)
    assert lines[[i for i, ln in enumerate(lines) if FETCH in ln][0]].startswith("    ->")


def test_depth_limits_the_walk_and_says_so(repo, capsys):
    rc, out = _run(repo, capsys, MAIN, depth=1)
    assert rc == 0 and BUILD in out and FETCH not in out and RENDER not in out
    assert "more beyond depth 1" in out


def test_in_lists_callers(repo, capsys):
    rc, out = _run(repo, capsys, BUILD, direction="in")
    assert rc == 0
    assert f"<- {MAIN}" in out and f"<- {VIEW}" in out
    assert FETCH not in out  # a callee is not a caller


def test_a_symbol_with_no_callers_says_so(repo, capsys):
    rc, out = _run(repo, capsys, MAIN, direction="in")
    assert rc == 0 and "no resolved callers" in out


def test_bare_name_and_suffix_resolve_to_the_key(repo, capsys):
    for target in ("build_report", "report.py::build_report", BUILD):
        rc, out = _run(repo, capsys, target, depth=1)
        assert rc == 0 and out.splitlines()[0].startswith(BUILD), target


def test_json_has_nodes_edges_and_call_lines(repo, capsys):
    rc, out = _run(repo, capsys, BUILD, direction="both", json=True)
    assert rc == 0
    d = json.loads(out)
    assert d["target"]["key"] == BUILD and d["depth"] == 3
    edges = {(e["from"], e["to"]): e for e in d["out"]["edges"]}
    assert edges[(BUILD, FETCH)]["confidence"] == INFERRED and edges[(BUILD, FETCH)]["call_line"] == 5
    assert edges[(BUILD, RENDER)]["confidence"] == EXTRACTED
    assert edges[(FETCH, DBQ)]["depth"] == 2
    assert {e["from"] for e in d["in"]["edges"]} == {MAIN, VIEW}
    assert d["out"]["truncated"] is False
    depth_of = {n["key"]: n["depth"] for n in d["out"]["nodes"]}
    assert depth_of == {BUILD: 0, FETCH: 1, RENDER: 1, DBQ: 2}
    assert all(set(n) == {"key", "kind", "file", "line", "depth"} for n in d["out"]["nodes"])


def test_unknown_symbol_fails_with_a_hint_not_a_crash(repo, capsys):
    rc, out = _run(repo, capsys, "svc/report.py::build_reprot")
    assert rc == 1 and "no symbol matches" in out
    rc, out = _run(repo, capsys, "nope", json=True)
    assert rc == 1 and json.loads(out)["error"] == "not_found"


def test_no_index_fails_cleanly(tmp_path, capsys):
    (tmp_path / ".git").mkdir()
    rc = cli.cmd_calls(_args(SimpleNamespace(path=tmp_path), "x"))
    assert rc == 1 and "codemap scan" in capsys.readouterr().out


# --------------------------------------------------------------------------- find / walk (pure)


def _sym(key, sid=1):
    path, name = key.split("::")
    return calls.Sym(sid, key, "function", name.split(".")[-1], name, path, 1, 2)


def test_find_prefers_the_most_specific_reading():
    syms = {s.key: s for s in map(_sym, ["a/x.py::run", "b/x.py::run", "a/x.py::Job.run", "a/y.py::go"])}
    assert [s.key for s in calls.find(syms, "a/x.py::run")] == ["a/x.py::run"]      # exact key: never ambiguous
    assert [s.key for s in calls.find(syms, "Job.run")] == ["a/x.py::Job.run"]      # qualified name
    assert [s.key for s in calls.find(syms, "b/x.py::run")] == ["b/x.py::run"]
    assert [s.key for s in calls.find(syms, "run")] == ["a/x.py::Job.run", "a/x.py::run", "b/x.py::run"]  # bare name: ambiguous
    assert calls.find(syms, "y.py::go")[0].key == "a/y.py::go"
    assert calls.find(syms, "missing") == []


def _graph(*edges):
    g = nx.DiGraph()
    for a, b, conf in edges:
        g.add_edge(a, b, confidence=conf)
    return g


def test_walk_lists_each_edge_once_even_with_a_diamond_and_a_cycle():
    g = _graph(("a", "b", EXTRACTED), ("a", "c", EXTRACTED), ("b", "d", EXTRACTED),
               ("c", "d", EXTRACTED), ("d", "a", EXTRACTED))
    edges, truncated = calls.walk(g, "a", direction="out", depth=5)
    assert sorted((e.src, e.dst) for e in edges) == [("a", "b"), ("a", "c"), ("b", "d"), ("c", "d"), ("d", "a")]
    assert truncated is False
    assert {(e.src, e.dst): e.depth for e in edges}[("d", "a")] == 3

    syms = {k: _sym(f"p.py::{k}") for k in "abcd"}
    rekey = {k: f"p.py::{k}" for k in "abcd"}
    g2 = _graph(*[(rekey[a], rekey[b], c) for a, b, c in [("a", "b", EXTRACTED), ("a", "c", EXTRACTED),
                                                             ("b", "d", EXTRACTED), ("c", "d", EXTRACTED),
                                                             ("d", "a", EXTRACTED)]])
    edges2, _ = calls.walk(g2, rekey["a"], direction="out", depth=5)
    tree = calls.render_tree({s.key: s for s in syms.values()}, syms["a"], edges2, direction="out", lines={})
    assert len(tree) - 1 == len(edges2)                      # one printed line per edge
    assert sum(1 for ln in tree if ln.endswith("(*)")) == 2  # d shown twice, a shown again via the cycle


def test_walk_truncates_and_reports_it():
    g = _graph(("a", "b", EXTRACTED), ("b", "c", EXTRACTED), ("c", "d", EXTRACTED))
    edges, truncated = calls.walk(g, "a", direction="out", depth=2)
    assert [(e.src, e.dst) for e in edges] == [("a", "b"), ("b", "c")] and truncated is True
    edges, truncated = calls.walk(g, "a", direction="out", depth=3)
    assert len(edges) == 3 and truncated is False


def test_walk_in_reads_callers_and_keeps_edges_caller_to_callee():
    g = _graph(("a", "c", EXTRACTED), ("b", "c", INFERRED), ("z", "a", EXTRACTED))
    edges, _ = calls.walk(g, "c", direction="in", depth=1)
    assert sorted((e.src, e.dst, e.depth) for e in edges) == [("a", "c", 1), ("b", "c", 1)]
    edges, _ = calls.walk(g, "c", direction="in", depth=2)
    assert ("z", "a") in {(e.src, e.dst) for e in edges}


def test_no_guesses_drops_ambiguous_edges_and_what_only_they_reach():
    g = _graph(("a", "b", EXTRACTED), ("a", "guess", AMBIGUOUS), ("guess", "deep", EXTRACTED))
    with_guesses, _ = calls.walk(g, "a", direction="out", depth=3)
    assert {e.dst for e in with_guesses} == {"b", "guess", "deep"}
    confident, truncated = calls.walk(g, "a", direction="out", depth=3, guesses=False)
    assert {e.dst for e in confident} == {"b"} and truncated is False


def test_a_long_candidate_list_is_capped_in_text_but_not_in_json(monkeypatch, repo, capsys):
    many = {f"pkg/m{i}.py::run": calls.Sym(i, f"pkg/m{i}.py::run", "function", "run", "run", f"pkg/m{i}.py", 1, 2)
            for i in range(40)}
    monkeypatch.setattr(calls, "load_symbols", lambda conn, sha: many)
    rc, out = _run(repo, capsys, "run")
    assert rc == 1 and "matches 40 symbols" in out
    assert sum(1 for ln in out.splitlines() if ln.startswith("  pkg/m")) == 15 and "and 25 more" in out
    rc, out = _run(repo, capsys, "run", json=True)
    d = json.loads(out)
    assert d["error"] == "ambiguous" and len(d["candidates"]) == 40
