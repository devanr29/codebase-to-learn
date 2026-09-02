"""M10 acceptance: the graph is rooted in the worktree, not gated behind a
commit. Git is an overlay — it drives the history walk (unchanged) and, on
top of that, ``scan()`` syncs a live "worktree" pseudo-commit that reflects
whatever is on disk, including changes nobody has committed yet.
"""

from __future__ import annotations

import subprocess

from codemap import cli, config, db, indexer
from codemap.site import model


def _git(root, *args):
    subprocess.run(["git", *args], cwd=str(root), capture_output=True, text=True, check=True)


def _git_out(root, *args) -> str:
    proc = subprocess.run(["git", *args], cwd=str(root), capture_output=True, text=True, check=True)
    return proc.stdout.strip()


def _init_repo(root):
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@e.com")
    _git(root, "config", "user.name", "t")
    _git(root, "config", "commit.gpgsign", "false")


def _idx(root):
    cfg = config.load(root)
    conn = db.connect(cfg.db_path)
    db.migrate(conn)
    return cfg, conn


def test_scan_default_syncs_worktree_and_sets_graph_head(tmp_path):
    _init_repo(tmp_path)
    (tmp_path / "a.py").write_text("def a():\n    return 1\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "seed")

    cfg, conn = _idx(tmp_path)
    indexer.scan(conn, cfg)  # until="HEAD" (the default) — every real invocation

    assert db.get_meta(conn, "graph_head") == indexer.WORKTREE_SHA
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM symbol_versions WHERE commit_sha=?", (indexer.WORKTREE_SHA,)
    ).fetchone()
    assert row["n"] == 1  # a()


def test_uncommitted_edit_shows_up_without_a_commit(tmp_path):
    """The exact scenario the git-rooted design couldn't do: edit code, don't
    commit, and the graph already reflects it on the next scan."""
    _init_repo(tmp_path)
    (tmp_path / "a.py").write_text("def a():\n    return 1\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "seed")

    cfg, conn = _idx(tmp_path)
    indexer.scan(conn, cfg)
    data = model.build(conn, cfg)
    keys = {n["key"] for n in data["nodes"]}
    assert "a.py::b" not in keys

    # add a new function, but do NOT commit
    (tmp_path / "a.py").write_text("def a():\n    return 1\n\n\ndef b():\n    return 2\n")
    indexer.scan(conn, cfg)

    data = model.build(conn, cfg)
    keys = {n["key"] for n in data["nodes"]}
    assert "a.py::b" in keys
    assert data["commit"] == indexer.WORKTREE_SHA
    # HEAD is still the single commit; nothing was committed
    assert conn.execute("SELECT COUNT(*) AS n FROM commits WHERE sha != ?",
                         (indexer.WORKTREE_SHA,)).fetchone()["n"] == 1


def test_bounded_until_leaves_graph_head_untouched(tmp_path):
    """Every existing test in this suite scans `until=<a specific sha>` to
    inspect one point in history in isolation — that must keep working
    exactly as before, with no live-graph side effect."""
    _init_repo(tmp_path)
    (tmp_path / "a.py").write_text("def a():\n    return 1\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "seed")
    sha = _git_out(tmp_path, "rev-parse", "HEAD")

    cfg, conn = _idx(tmp_path)
    indexer.scan(conn, cfg, until=sha)

    assert db.get_meta(conn, "graph_head") is None
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM commits WHERE sha=?", (indexer.WORKTREE_SHA,)
    ).fetchone()
    assert row["n"] == 0


def test_explain_worktree_shows_uncommitted_changes(tmp_path):
    _init_repo(tmp_path)
    (tmp_path / "a.py").write_text("def a():\n    return 1\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "seed")

    cfg, conn = _idx(tmp_path)
    indexer.scan(conn, cfg)
    conn.close()

    (tmp_path / "a.py").write_text("def a():\n    return 2\n")  # uncommitted body change
    cfg2, conn2 = _idx(tmp_path)
    indexer.scan(conn2, cfg2)
    conn2.close()

    class Args:
        path = str(tmp_path)
        rev = "worktree"
        breakdown = False

    import io
    import contextlib

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = cli.cmd_explain(Args())
    assert rc == 0
    out = buf.getvalue()
    assert out.startswith("## worktree")
    assert "`a()` body changed" in out


def test_empty_repo_still_gets_a_worktree_graph(tmp_path):
    """A brand-new project with zero commits used to index nothing at all."""
    _init_repo(tmp_path)
    (tmp_path / "a.py").write_text("def a():\n    return 1\n")

    cfg, conn = _idx(tmp_path)
    stats = indexer.scan(conn, cfg)

    assert stats.commits_indexed == 0  # no real commits exist
    assert db.get_meta(conn, "graph_head") == indexer.WORKTREE_SHA
    data = model.build(conn, cfg)
    assert not data.get("empty")
    assert {n["key"] for n in data["nodes"]} == {"a.py::a"}
