"""M15 resolution uplift, plus the receiver-aware follow-up: impact.call_graph
resolves a call through the same import-resolution machinery the explorer's
file graph already uses (resolve.resolve_imports) before falling back to a
global name match, and tags every edge with a confidence — EXTRACTED /
INFERRED / AMBIGUOUS.

For a **bare** call (`helper()`, no object) that's still the whole story:
confidence is metadata there, not a filter — every candidate gets an edge,
just a less-confident one when there's no import evidence. That's
`test_confidence_never_changes_which_edges_exist_for_bare_calls` below.

For a call made **on an object** (`self.get()`, `body.get()`,
`WalletClient().get()`), `refs.receiver` (`parsing._receiver_of`) now decides
whether an edge exists *at all* — a dict's `.get()` no longer resolves to an
unrelated `WalletClient.get` just because the name matches somewhere in the
repo. See `call_graph()`'s docstring in impact.py for the exact rules; the
rest of this file tests each one.
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


def test_confidence_never_changes_which_edges_exist_for_bare_calls(indexed):
    """The exact contract test_impact.py already exercises — restated here to
    make the "metadata, not a filter" guarantee explicit and directly tested.
    Scoped to *bare* calls in the name (see the module docstring): a call
    made on an object is a different, stricter contract, covered below."""
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


# --------------------------------------------------- receiver-aware resolution


def _git_init(root):
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@e.com")
    _git(root, "config", "user.name", "t")
    _git(root, "config", "commit.gpgsign", "false")


def _git_commit(root, msg="seed"):
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", msg)


def _head(root) -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(root), capture_output=True, text=True
    ).stdout.strip()


def _index(tmp_path, sha=None):
    cfg = config.load(tmp_path)
    conn = db.connect(cfg.db_path)
    db.migrate(conn)
    indexer.scan(conn, cfg, until=sha) if sha else indexer.scan(conn, cfg)
    return cfg, conn


def test_dict_get_is_not_linked_to_a_same_named_method(tmp_path):
    """The bug this whole change exists to fix: `body.get(...)` (a plain
    local variable, receiver "v:body") must never resolve to an unrelated
    class's same-named method just because import evidence connects the two
    files — `get` is on the stoplist precisely because it's this common."""
    _git_init(tmp_path)
    (tmp_path / "client.py").write_text(
        "class WalletClient:\n    def get(self):\n        return 1\n"
    )
    (tmp_path / "app.py").write_text(
        "from client import WalletClient\n\n"
        "def handle(body):\n    return body.get('x')\n"
    )
    _git_commit(tmp_path)
    sha = _head(tmp_path)
    _cfg, conn = _index(tmp_path, sha)

    g = impact.call_graph(conn, sha)
    assert not g.has_edge("app.py::handle", "client.py::WalletClient.get")


def test_self_dot_call_resolves_to_its_own_class_only(tmp_path):
    """`self.get()` (receiver "self") must resolve only within the enclosing
    class — not to a same-named method on an unrelated class in the same
    file, which the old same-file EXTRACTED tier couldn't tell apart."""
    _git_init(tmp_path)
    (tmp_path / "a.py").write_text(
        "class Reader:\n"
        "    def go(self):\n"
        "        return self.get()\n"
        "    def get(self):\n"
        "        return 1\n"
        "\n"
        "class Writer:\n"
        "    def get(self):\n"
        "        return 2\n"
    )
    _git_commit(tmp_path)
    sha = _head(tmp_path)
    _cfg, conn = _index(tmp_path, sha)

    g = impact.call_graph(conn, sha)
    assert _edge_confidence(g, "a.py::Reader.go", "a.py::Reader.get") == impact.EXTRACTED
    assert not g.has_edge("a.py::Reader.go", "a.py::Writer.get")


def test_call_never_crosses_language_family(tmp_path):
    """A bare call still falls back to a repo-wide name guess (see the module
    docstring), but never into a different language's same-named symbol —
    a .py function and a .ts function sharing a name is coincidence, not a
    real call edge."""
    _git_init(tmp_path)
    (tmp_path / "a.py").write_text("def get():\n    return 1\n")
    (tmp_path / "b.ts").write_text("function caller() {\n  return get();\n}\n")
    _git_commit(tmp_path)
    sha = _head(tmp_path)
    _cfg, conn = _index(tmp_path, sha)

    g = impact.call_graph(conn, sha)
    assert not g.has_edge("b.ts::caller", "a.py::get")


def test_production_never_links_into_a_test_file(tmp_path):
    """Prod -> test is never a real call edge; test -> prod still is (tests
    routinely call the code they exercise, by a unique name that only exists
    in production, resolved the old repo-wide-guess way)."""
    _git_init(tmp_path)
    (tmp_path / "tests").mkdir()
    (tmp_path / "app.py").write_text(
        "def handle():\n    return helper()\n\ndef prod_only():\n    return 1\n"
    )
    (tmp_path / "tests" / "test_app.py").write_text(
        "def helper():\n    return 1\n\ndef test_it():\n    return prod_only()\n"
    )
    _git_commit(tmp_path)
    sha = _head(tmp_path)
    _cfg, conn = _index(tmp_path, sha)

    g = impact.call_graph(conn, sha)
    assert not g.has_edge("app.py::handle", "tests/test_app.py::helper")
    assert g.has_edge("tests/test_app.py::test_it", "app.py::prod_only")


def test_import_alias_redirects_a_bare_call(tmp_path):
    """`from common import ok as _ok` then `_ok(...)` (receiver "-", the name
    literally called is `_ok`) redirects to the real symbol `ok` through the
    one import that named it — INFERRED, never a same-file/repo-wide guess."""
    _git_init(tmp_path)
    (tmp_path / "common.py").write_text("def ok(x):\n    return x\n")
    (tmp_path / "app.py").write_text(
        "from common import ok as _ok\n\ndef handle():\n    return _ok(1)\n"
    )
    _git_commit(tmp_path)
    sha = _head(tmp_path)
    _cfg, conn = _index(tmp_path, sha)

    g = impact.call_graph(conn, sha)
    assert _edge_confidence(g, "app.py::handle", "common.py::ok") == impact.INFERRED


def test_schema_v3_migration_flags_a_forced_reparse(tmp_path):
    """Rolling a fresh (already-v3) database back to what a real v2 install
    would look like, then migrating it forward, must both add refs.receiver
    and set the one-shot reparse_all flag the next worktree sync honours."""
    conn = db.connect(tmp_path / "index.db")
    db.migrate(conn)
    conn.execute("ALTER TABLE refs RENAME TO refs_v3")
    conn.execute(
        "CREATE TABLE refs (id INTEGER PRIMARY KEY, commit_sha TEXT, from_symbol_id INTEGER, "
        "target_name TEXT, target_symbol_id INTEGER, resolved INTEGER, tier INTEGER, line INTEGER)"
    )
    conn.execute("DROP TABLE refs_v3")
    conn.execute("UPDATE meta SET value = '2' WHERE key = 'schema_version'")
    conn.execute("DELETE FROM meta WHERE key = 'reparse_all'")
    conn.commit()

    version = db.migrate(conn)

    assert version == 3
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(refs)")}
    assert "receiver" in cols
    assert db.get_meta(conn, "reparse_all") == "1"


def test_forced_reparse_backfills_receiver_on_unchanged_content(tmp_path):
    """The mechanism schema_v3's migration flag actually drives end to end:
    once `reparse_all` is set, the next worktree sync must reparse a file
    even though its content hash hasn't changed, and clear the flag after."""
    _git_init(tmp_path)
    (tmp_path / "a.py").write_text(
        "class C:\n    def go(self):\n        return self.get()\n    def get(self):\n        return 1\n"
    )
    _git_commit(tmp_path)
    cfg, conn = _index(tmp_path)

    conn.execute("UPDATE refs SET receiver = NULL")
    conn.commit()
    db.set_meta(conn, "reparse_all", "1")

    indexer.scan(conn, cfg)  # content on disk is unchanged — only the flag forces this

    row = conn.execute(
        "SELECT receiver FROM refs WHERE target_name = 'get' AND commit_sha = 'worktree'"
    ).fetchone()
    assert row["receiver"] == "self"
    assert db.get_meta(conn, "reparse_all") is None
