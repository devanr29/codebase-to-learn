"""``codemap mcp``'s logic (``codemap/query.py``) and the findings it writes
(``site/findings.py``): read tools, open questions, verified writes, and the
graph/page changes a finding makes."""

from __future__ import annotations

import json
import subprocess

import pytest

from codemap import cli
from codemap.impact import AMBIGUOUS, INFERRED
from codemap.query import QueryError, Session

FILES = {
    "app/__init__.py": "",
    "app/a.py": "def helper():\n    return 1\n",
    "app/b.py": "def helper():\n    return 2\n",
    "app/main.py": (
        "import os\n"
        "\n"
        "DB = os.environ.get(\"DATABASE_URL\")\n"
        "\n"
        "\n"
        "def run():\n"
        "    return helper()\n"
        "\n"
        "\n"
        "def lonely():\n"
        "    return 3\n"
        "\n"
        "\n"
        "if __name__ == \"__main__\":\n"
        "    run()\n"
    ),
}
RUN, A, B, LONELY = "app/main.py::run", "app/a.py::helper", "app/b.py::helper", "app/main.py::lonely"


def _git(path, *args):
    subprocess.run(["git", *args], cwd=path, check=True, capture_output=True)


@pytest.fixture()
def repo(tmp_path):
    for rel, text in FILES.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(text.encode())
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "-c", "user.email=t@t", "-c", "user.name=t", "add", ".")
    _git(tmp_path, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init")
    assert cli.main(["scan", "--path", str(tmp_path), "--no-progress"]) == 0
    return tmp_path


def _model(repo):
    from codemap import config, db
    from codemap.site import model

    cfg = config.load(repo)
    conn = db.connect(cfg.db_path)
    try:
        return model.build(conn, cfg, max_source_bytes=0)
    finally:
        conn.close()


def _edge(data, src, dst):
    i = {n["key"]: n["i"] for n in data["nodes"]}
    return next((e for e in data["edges"] if e["s"] == i[src] and e["t"] == i[dst]), None)


# --------------------------------------------------------------------------- read tools


def test_no_index_is_a_clear_error(tmp_path):
    with pytest.raises(QueryError, match="codemap scan"):
        Session(tmp_path).overview()


def test_overview_and_read_tools(repo):
    s = Session(repo)
    o = s.overview()
    assert o["stats"]["symbols"] >= 4 and o["stale"] is False
    assert o["open_questions"]["ambiguous_call"] == 1
    assert [m["key"] for m in s.find_symbol("lonely")["matches"]] == [LONELY]
    assert s.read_source("app/main.py", 6, 7)["lines"].splitlines()[0].endswith("def run():")
    with pytest.raises(QueryError):
        s.read_source("nope.py")
    with pytest.raises(QueryError, match="matches 2 symbols"):
        s.calls("helper")
    out = s.calls(RUN)
    assert {(e["to"], e["confidence"]) for e in out["edges"]} == {(A, AMBIGUOUS), (B, AMBIGUOUS)}
    assert s.search_code(r"DATABASE_URL")["hits"][0]["where"] == "app/main.py:3"


def test_open_questions_cover_the_gaps(repo):
    qs = Session(repo).open_questions(limit=50)
    ids = {q["id"] for q in qs["questions"]}
    assert f"call:{RUN}->helper" in ids
    assert f"orphan:{LONELY}" in ids
    assert "config:DATABASE_URL" in ids
    call = next(q for q in qs["questions"] if q["kind"] == "ambiguous_call")
    assert {c["key"] for c in call["candidates"]} == {A, B}
    assert call["call_site"] == "app/main.py:7"


# --------------------------------------------------------------------------- writes


def test_answer_is_rejected_without_good_evidence(repo):
    s = Session(repo)
    qid = f"call:{RUN}->helper"
    with pytest.raises(QueryError, match="evidence is required"):
        s.answer_question(qid, {"calls": A})
    with pytest.raises(QueryError, match="past the end"):
        s.answer_question(qid, {"calls": A}, [{"file": "app/main.py", "line": 99}])
    with pytest.raises(QueryError, match="not an indexed file"):
        s.answer_question(qid, {"calls": A}, [{"file": "elsewhere.py", "line": 1}])
    with pytest.raises(QueryError, match="must be one of"):
        s.answer_question(qid, {"calls": LONELY}, [{"file": "app/main.py", "line": 7}])
    assert not (repo / ".codemap" / "findings.json").exists()


def test_confirmed_call_reaches_the_graph_and_page(repo):
    s = Session(repo)
    res = s.answer_question(f"call:{RUN}->helper", {"calls": A},
                            [{"file": "app/main.py", "line": 7, "note": "the call"}], note="a.helper is the one")
    assert res["ok"], res
    saved = json.loads((repo / ".codemap" / "findings.json").read_text(encoding="utf-8"))
    kinds = sorted(f["kind"] for f in saved["items"])
    assert kinds == ["call", "not_call"]
    assert saved["items"][0]["evidence"][0]["text"] == "return helper()"

    # the session sees it at once: the question is gone, the guess is now a fact
    assert s.open_questions(limit=0)["counts"]["ambiguous_call"] == 0
    assert {(e["to"], e["confidence"]) for e in s.calls(RUN)["edges"]} == {(A, INFERRED)}

    data = _model(repo)
    edge = _edge(data, RUN, A)
    assert edge["confidence"] == INFERRED and edge["review"]["note"] == "a.helper is the one"
    assert _edge(data, RUN, B) is None
    assert data["review"] == {"findings": 2, "applied": 2, "stale": []}


def test_polymorphic_call_keeps_every_real_target(repo):
    s = Session(repo)
    assert s.answer_question(f"call:{RUN}->helper", {"calls": [A, B]},
                             [{"file": "app/main.py", "line": 7}], note="both implementations run")["ok"]
    data = _model(repo)
    assert _edge(data, RUN, A)["confidence"] == _edge(data, RUN, B)["confidence"] == INFERRED
    assert s.open_questions(limit=0)["counts"]["ambiguous_call"] == 0


def test_orphan_answer_adds_a_reviewed_edge(repo):
    s = Session(repo)
    assert s.answer_question(f"orphan:{LONELY}", {"callers": [RUN]},
                             [{"file": "app/main.py", "line": 7}])["ok"]
    edge = _edge(_model(repo), RUN, LONELY)
    assert edge["confidence"] == INFERRED and edge["review"]


def test_unknown_needs_a_note_and_closes_the_question(repo):
    s = Session(repo)
    with pytest.raises(QueryError, match="note"):
        s.answer_question("config:DATABASE_URL", {"unknown": True})
    assert s.answer_question("config:DATABASE_URL", {"unknown": True}, note="read but never used")["ok"]
    assert s.open_questions(limit=0)["counts"]["hidden_dependency"] == 0


def test_submit_is_checked_before_it_is_written(repo):
    s = Session(repo)
    with pytest.raises(QueryError, match="no symbol matches"):
        s.submit("explanation", {"key": "app/nope.py::x", "what": "x"})
    bad = s.submit("scenario", {"id": "x", "title": "X", "steps": [{"node": "app/nope.py::x"}]})
    assert not bad["ok"] and bad["rejected"]
    assert not (repo / ".codemap" / "scenarios.json").exists()

    ok = s.answer_question(f"explain:{RUN}", {"what": "Entry work: calls the helper and returns its value."},
                           [{"file": "app/main.py", "line": 6}])
    assert ok["ok"]
    stored = json.loads((repo / ".codemap" / "explanations.json").read_text(encoding="utf-8"))
    assert stored["symbols"][RUN]["evidence"] == ["app/main.py:6"]
    assert s.explain_symbol(RUN)["explanation"]["what"].startswith("Entry work")

    arch = s.submit("architecture", {"stores": [{"id": "postgresql", "label": "Main DB", "via": ["app"]}]})
    assert arch["ok"]
    assert s.check()["ok"]


def test_finding_goes_stale_when_the_code_changes(repo):
    s = Session(repo)
    s.answer_question(f"call:{RUN}->helper", {"calls": A}, [{"file": "app/main.py", "line": 7}])
    text = FILES["app/main.py"].replace("    return helper()\n", "    x = helper()\n    return x\n")
    (repo / "app" / "main.py").write_bytes(text.encode())
    assert cli.main(["scan", "--path", str(repo), "--no-progress"]) == 0
    data = _model(repo)
    assert data["review"]["applied"] == 0 and data["review"]["stale"]
    assert _edge(data, RUN, A)["confidence"] == AMBIGUOUS   # back to the guess
    issues = Session(repo).check()["issues"]
    assert any(i["file"] == "findings.json" and "stale" in i["message"] for i in issues)


def test_no_findings_file_changes_nothing(repo):
    assert "review" not in _model(repo)


# --------------------------------------------------------------------------- server


def test_server_registers_every_tool(repo):
    pytest.importorskip("mcp")
    import asyncio

    from codemap.mcp_server import build_server

    names = {t.name for t in asyncio.run(build_server(repo).list_tools())}
    assert names == {"overview", "find_symbol", "explain_symbol", "calls", "impact", "read_source",
                     "search_code", "get_authored", "open_questions", "answer_question", "submit", "check"}
