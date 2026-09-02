"""M15 resolution uplift: impact.call_graph now resolves a call through the
same import-resolution machinery the explorer's file graph already uses
(resolve.resolve_imports), before falling back to a global name match, and
tags every edge with a confidence — EXTRACTED / INFERRED / AMBIGUOUS. Which
edges exist is unchanged (confidence is metadata, not a filter) — see
test_impact.py for that contract, still passing untouched.
"""

from __future__ import annotations

import subprocess

import pytest

from codemap import config, db, impact, indexer


@pytest.fixture(scope="module")
def indexed(fixture_impact_repo, tmp_path_factory):
    cfg = config.load(fixture_impact_repo.path)
    conn = db.connect(tmp_path_factory.mktemp("resolution") / "index.db")
    db.migrate(conn)
    indexer.scan(conn, cfg, until=fixture_impact_repo.sha("i3-sig-partial"))
    return fixture_impact_repo, cfg, conn


def _edge_confidence(g, src, dst):
    return g.get_edge_data(src, dst)["confidence"]


def test_same_file_call_is_extracted(indexed):
    repo, _cfg, conn = indexed
    g = impact.call_graph(conn, repo.sha("i1-initial"))
    # build_report() calls render() in the same file
    assert _edge_confidence(g, "svc/report.py::build_report", "svc/report.py::render") == impact.EXTRACTED
    assert _edge_confidence(g, "svc/data.py::fetch", "svc/data.py::db_query") == impact.EXTRACTED


def test_import_resolved_call_is_inferred(indexed):
    repo, _cfg, conn = indexed
    g = impact.call_graph(conn, repo.sha("i1-initial"))
    # report.py has `from svc.data import fetch` — a real import connects the two files
    assert _edge_confidence(g, "svc/report.py::build_report", "svc/data.py::fetch") == impact.INFERRED
    # web.py and cli.py both `from svc.report import build_report`
    assert _edge_confidence(g, "svc/web.py::report_view", "svc/report.py::build_report") == impact.INFERRED
    assert _edge_confidence(g, "svc/cli.py::main", "svc/report.py::build_report") == impact.INFERRED


def test_confidence_never_changes_which_edges_exist(indexed):
    """The exact contract test_impact.py already exercises — restated here to
    make the "metadata, not a filter" guarantee explicit and directly tested."""
    repo, cfg, conn = indexed
    from codemap import semdiff

    sha = repo.sha("i2-leaf-body")
    parent = indexer.parent_sha(conn, sha)
    changes = semdiff.load_changes(conn, sha)
    imp = impact.analyze(conn, cfg, parent, sha, changes)["svc/data.py::db_query"]
    assert {c.key for c in imp.callers} == {
        "svc/data.py::fetch",
        "svc/report.py::build_report",
        "svc/web.py::report_view",
        "svc/cli.py::main",
    }


def _git(root, *args):
    subprocess.run(["git", *args], cwd=str(root), capture_output=True, text=True, check=True)


def test_unconnected_same_name_match_is_ambiguous(tmp_path):
    """Two files, same function name, no import linking them — the genuine
    T1 fallback this whole tier system exists to hedge honestly about."""
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@e.com")
    _git(tmp_path, "config", "user.name", "t")
    _git(tmp_path, "config", "commit.gpgsign", "false")
    (tmp_path / "a.py").write_text("def caller():\n    return helper()\n")
    (tmp_path / "b.py").write_text("def helper():\n    return 1\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "seed")
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(tmp_path), capture_output=True, text=True
    ).stdout.strip()

    cfg = config.load(tmp_path)
    conn = db.connect(cfg.db_path)
    db.migrate(conn)
    indexer.scan(conn, cfg, until=sha)

    g = impact.call_graph(conn, sha)
    assert _edge_confidence(g, "a.py::caller", "b.py::helper") == impact.AMBIGUOUS


def test_model_edges_carry_confidence(indexed):
    from codemap.site import model

    _repo, cfg, conn = indexed
    data = model.build(conn, cfg)
    assert data["edges"], "expected at least one call edge"
    seen = {e["confidence"] for e in data["edges"]}
    assert seen <= {impact.EXTRACTED, impact.INFERRED, impact.AMBIGUOUS}
    assert impact.EXTRACTED in seen or impact.INFERRED in seen
