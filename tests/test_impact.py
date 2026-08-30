"""M5 acceptance: callers (direct + transitive to impact_depth), the
modified-vs-unmodified caller split, entry-point reachability paths, and tier
marking on the results.
"""

from __future__ import annotations

import pytest

from codemap import config, db, impact, indexer, semdiff


@pytest.fixture(scope="module")
def indexed(fixture_impact_repo, tmp_path_factory):
    cfg = config.load(fixture_impact_repo.path)
    conn = db.connect(tmp_path_factory.mktemp("impact") / "index.db")
    db.migrate(conn)
    indexer.scan(conn, cfg, until=fixture_impact_repo.sha("i3-sig-partial"))
    return fixture_impact_repo, cfg, conn


def _impact_for(conn, cfg, repo, tag, key):
    sha = repo.sha(tag)
    parent = indexer.parent_sha(conn, sha)
    changes = semdiff.load_changes(conn, sha)
    return impact.analyze(conn, cfg, parent, sha, changes)[key]


def test_entry_points_detected(indexed):
    repo, _cfg, conn = indexed
    eps = impact.entry_points(conn, repo.sha("i1-initial"))
    assert ("route", "GET /report") in eps.get("svc/web.py::report_view", [])
    assert any(k == "main" for k, _d in eps.get("svc/cli.py::main", []))


def test_leaf_change_transitive_callers(indexed):
    repo, cfg, conn = indexed
    imp = _impact_for(conn, cfg, repo, "i2-leaf-body", "svc/data.py::db_query")

    assert [c.qualified_name for c in imp.direct] == ["fetch"]
    keys = {c.key for c in imp.callers}
    assert keys == {
        "svc/data.py::fetch",
        "svc/report.py::build_report",
        "svc/web.py::report_view",
        "svc/cli.py::main",
    }
    assert max(c.depth for c in imp.callers) == 3
    # only db_query itself changed -> no caller was updated alongside it
    assert imp.n_modified == 0
    assert imp.tier == 2 and imp.resolvable


def test_entry_path_reaches_leaf(indexed):
    repo, cfg, conn = indexed
    imp = _impact_for(conn, cfg, repo, "i2-leaf-body", "svc/data.py::db_query")
    assert imp.entry_paths, "expected a path from an entry point"
    path = imp.entry_paths[0]
    assert path.endswith("report_view -> build_report -> fetch -> db_query")
    assert path.startswith("GET /report ->")


def test_depth_cutoff_is_honored(indexed):
    repo, _cfg, conn = indexed
    shallow = config.load(repo.path)
    shallow.impact_depth = 1
    sha = repo.sha("i2-leaf-body")
    changes = semdiff.load_changes(conn, sha)
    imp = impact.analyze(conn, shallow, indexer.parent_sha(conn, sha), sha, changes)[
        "svc/data.py::db_query"
    ]
    assert {c.key for c in imp.callers} == {"svc/data.py::fetch"}


def test_partial_caller_update_split(indexed):
    repo, cfg, conn = indexed
    imp = _impact_for(conn, cfg, repo, "i3-sig-partial", "svc/report.py::build_report")

    callers = {c.key: c for c in imp.direct}
    assert set(callers) == {"svc/web.py::report_view", "svc/cli.py::main"}
    assert callers["svc/web.py::report_view"].modified is True   # updated in the same commit
    assert callers["svc/cli.py::main"].modified is False         # left untouched
    assert imp.n_modified == 1

    lines = impact.render_lines(imp)
    assert any("1 updated, 1 unchanged (worth checking)" in ln for ln in lines)


def test_impact_summary_persisted_on_changes(indexed):
    repo, _cfg, conn = indexed
    changes = semdiff.load_changes(conn, repo.sha("i2-leaf-body"))
    leaf = next(c for c in changes if c.symbol_key == "svc/data.py::db_query")
    assert leaf.details.get("impact", {}).get("callers") == 4
