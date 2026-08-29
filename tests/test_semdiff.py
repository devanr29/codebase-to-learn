"""M4 acceptance: each scripted fixture commit produces its expected change set,
including a purely-cosmetic commit that must classify as cosmetic and a
syntax-error commit that must not crash the run.
"""

from __future__ import annotations

import pytest

from codemap import config, db, indexer, semdiff


@pytest.fixture(scope="module")
def indexed(fixture_repo, tmp_path_factory):
    cfg = config.load(fixture_repo.path)
    conn = db.connect(tmp_path_factory.mktemp("semdiff") / "index.db")
    db.migrate(conn)
    indexer.scan(conn, cfg, until=fixture_repo.sha("c10-syntaxerror"))
    return fixture_repo, cfg, conn


def _tuples(conn, sha):
    return {c.as_tuple() for c in semdiff.load_changes(conn, sha)}


def _matches(actual: set[tuple], expected: set[tuple]) -> bool:
    """expected entries may be 2-tuples (severity unspecified) or 3-tuples."""
    two = {(ct, s) for (ct, s, _sev) in actual}
    for item in expected:
        if len(item) == 2:
            if item not in two:
                return False
        elif item not in actual:
            return False
    # no unexpected structural/behavioral changes
    expected_two = {i[:2] for i in expected}
    for (ct, subj, sev) in actual:
        if sev != semdiff.COSMETIC and (ct, subj) not in expected_two:
            return False
    return True


@pytest.mark.parametrize("tag", ["c2-add-fn", "c3-signature", "c4-body", "c6-rename",
                                 "c7-move", "c8-delete", "c9-dependency"])
def test_commit_change_set(indexed, tag):
    repo, _cfg, conn = indexed
    commit = next(c for c in repo.commits if c.tag == tag)
    actual = _tuples(conn, commit.sha)
    assert _matches(actual, commit.expected), f"{tag}\n  actual={sorted(actual)}\n  expected={sorted(commit.expected)}"


def test_c1_bootstraps_every_file_and_symbol(indexed):
    repo, _cfg, conn = indexed
    c1 = repo.sha("c1-initial")
    adds = {(ct, subj) for (ct, subj, _s) in _tuples(conn, c1) if ct in ("file_added", "symbol_added")}
    assert adds == {i[:2] for i in next(c for c in repo.commits if c.tag == "c1-initial").expected}


def test_c5_is_purely_cosmetic(indexed):
    repo, _cfg, conn = indexed
    changes = semdiff.load_changes(conn, repo.sha("c5-cosmetic"))
    assert changes, "expected the cosmetic changes to be recorded, not dropped"
    assert all(c.severity == semdiff.COSMETIC for c in changes)
    subjects = {c.subject for c in changes}
    assert "app/core.py::read" in subjects
    assert "web/loader.ts::dispatch" in subjects


def test_c10_syntax_error_does_not_crash(indexed):
    repo, cfg, conn = indexed
    c10 = repo.sha("c10-syntaxerror")
    # changes were computed and stored without raising
    changes = semdiff.load_changes(conn, c10)
    assert all(c.severity in (semdiff.STRUCTURAL, semdiff.BEHAVIORAL, semdiff.COSMETIC) for c in changes)
    # loader.ts is still a known file — the bad parse did not wipe the graph
    row = conn.execute("SELECT COUNT(*) AS n FROM files WHERE path='web/loader.ts'").fetchone()
    assert row["n"] == 1


def test_changes_persist_and_reload_identically(indexed):
    repo, cfg, conn = indexed
    for commit in repo.commits[1:]:
        parent = indexer.parent_sha(conn, commit.sha)
        recomputed = {c.as_tuple() for c in semdiff.diff_commits(conn, cfg, parent, commit.sha, persist=False)}
        stored = _tuples(conn, commit.sha)
        assert recomputed == stored, commit.tag
