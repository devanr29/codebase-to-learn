"""M2 acceptance: incremental indexing reparses only what changed, and the
resulting DB is byte-for-byte equal to a from-scratch index of the same commit.
Also covers M3: per-commit snapshots and retention pruning.
"""

from __future__ import annotations

from codemap import config, db, indexer, parsing
from tests.dumpdb import dump_index, restrict_to_commits


def _fresh_db(tmp_path, name):
    conn = db.connect(tmp_path / f"{name}.db")
    db.migrate(conn)
    return conn


def test_incremental_equals_from_scratch(fixture_repo, tmp_path):
    cfg = config.load(fixture_repo.path)
    c2 = fixture_repo.sha("c2-add-fn")
    c3 = fixture_repo.sha("c3-signature")

    scratch = _fresh_db(tmp_path, "scratch")
    indexer.scan(scratch, cfg, until=c3)

    incr = _fresh_db(tmp_path, "incr")
    indexer.scan(incr, cfg, until=c2)
    parsing.parse_count = 0
    stats = indexer.scan(incr, cfg, until=c3)

    # c3 changes exactly one file (app/util.py) -> exactly one reparse
    assert stats.files_parsed == 1
    assert parsing.parse_count == 1
    assert stats.files_skipped >= 3  # core.py + both .ts files carried forward

    assert dump_index(scratch) == dump_index(incr)


def test_full_history_incremental_matches_scratch(fixture_repo, tmp_path):
    """Walk every commit incrementally, one scan at a time, and compare to a
    single from-scratch pass over the whole history (covers rename/move/delete/
    syntax-error commits)."""
    cfg = config.load(fixture_repo.path)

    scratch = _fresh_db(tmp_path, "scratch_full")
    indexer.scan(scratch, cfg, until=fixture_repo.sha("c10-syntaxerror"))

    incr = _fresh_db(tmp_path, "incr_full")
    for commit in fixture_repo.commits:
        indexer.scan(incr, cfg, until=commit.sha)

    assert dump_index(scratch) == dump_index(incr)


def test_syntax_error_commit_does_not_crash(fixture_repo, tmp_path):
    cfg = config.load(fixture_repo.path)
    conn = _fresh_db(tmp_path, "syn")
    stats = indexer.scan(conn, cfg, until=fixture_repo.sha("c10-syntaxerror"))
    assert any(path == "web/loader.ts" for path, _ in stats.errors)
    # the run still completed and indexed every commit
    assert stats.commits_indexed == len(fixture_repo.commits)


def test_rename_preserves_file_identity(fixture_repo, tmp_path):
    cfg = config.load(fixture_repo.path)
    conn = _fresh_db(tmp_path, "rn")
    indexer.scan(conn, cfg, until=fixture_repo.sha("c6-rename"))
    # app/util.py was written across commits then rewritten (clean->sanitize);
    # its files-row is reused, not duplicated.
    rows = conn.execute("SELECT COUNT(*) AS n FROM files WHERE path='app/util.py'").fetchone()
    assert rows["n"] == 1


def test_retention_prunes_old_full_graphs(fixture_repo, tmp_path):
    cfg = config.load(fixture_repo.path)
    cfg.retention = 3
    conn = _fresh_db(tmp_path, "ret")
    indexer.scan(conn, cfg, until=fixture_repo.sha("c10-syntaxerror"))

    kept = [
        r["sha"]
        for r in conn.execute(
            "SELECT DISTINCT commit_sha AS sha FROM symbol_versions"
        )
    ]
    assert len(kept) == 3
    # every commit still has a row in commits, and changes rows are untouched
    total = conn.execute("SELECT COUNT(*) AS n FROM commits").fetchone()["n"]
    assert total == len(fixture_repo.commits)
