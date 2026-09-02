"""M11-M14: render, the `explore` command, learn.json loading, briefs."""

from __future__ import annotations

import json

from codemap import cli, config, db, indexer
from codemap.site import brief, explain, learn, model, render


def _idx(repo, tmp_path, until):
    cfg = config.load(repo.path)
    conn = db.connect(tmp_path / "index.db")
    db.migrate(conn)
    indexer.scan(conn, cfg, until=repo.sha(until))
    return cfg, conn


def test_render_is_one_self_contained_file(fixture_impact_repo, tmp_path):
    cfg, conn = _idx(fixture_impact_repo, tmp_path, "i3-sig-partial")
    html = render.render(model.build(conn, cfg))

    assert html.startswith("<!DOCTYPE html>")
    assert "/*{{CSS}}*/" not in html and "/*{{JS}}*/" not in html
    assert "{{DATA}}" not in html and "{{TITLE}}" not in html
    assert "--color-accent" in html          # css inlined
    assert "renderGraphSVG" in html          # js inlined
    # the payload must not break out of the inline <script>
    payload = html.split('id="codemap-data">', 1)[1].split("</script>", 1)[0]
    assert "</" not in payload
    data = json.loads(payload.replace("\\u003c", "<"))
    assert data["stats"]["symbols"] == len(data["nodes"])


def test_explore_command_writes_html(fixture_impact_repo, tmp_path, capsys):
    _cfg, conn = _idx(fixture_impact_repo, tmp_path, "i3-sig-partial")
    conn.close()

    class Args:
        path = str(fixture_impact_repo.path)
        out = str(tmp_path / "out.html")
        json = False
        emit_brief = False
        max_symbols = 0
        open = False
        quiet = False
        if_enabled = False

    # point config discovery at the fixture repo but keep its own .codemap db
    cfg = config.load(fixture_impact_repo.path)
    cfg.codemap_dir.mkdir(exist_ok=True)
    dbc = db.connect(cfg.db_path)
    db.migrate(dbc)
    indexer.scan(dbc, cfg, until=fixture_impact_repo.sha("i3-sig-partial"))
    dbc.close()

    rc = cli.cmd_explore(Args())
    assert rc == 0
    body = (tmp_path / "out.html").read_text(encoding="utf-8")
    assert "<!DOCTYPE html>" in body and "codemap-data" in body


def test_explore_json_flag(fixture_impact_repo, tmp_path, capsys):
    cfg = config.load(fixture_impact_repo.path)
    cfg.codemap_dir.mkdir(exist_ok=True)
    dbc = db.connect(cfg.db_path)
    db.migrate(dbc)
    indexer.scan(dbc, cfg, until=fixture_impact_repo.sha("i3-sig-partial"))
    dbc.close()

    class Args:
        path = str(fixture_impact_repo.path)
        out = None
        json = True
        emit_brief = False
        max_symbols = 0
        open = False
        quiet = True
        if_enabled = False

    assert cli.cmd_explore(Args()) == 0
    data = json.loads(capsys.readouterr().out)
    assert "nodes" in data and "timeline" in data


def test_if_enabled_noops_when_disabled(fixture_impact_repo, tmp_path, monkeypatch):
    cfg = config.load(fixture_impact_repo.path)
    cfg.codemap_dir.mkdir(exist_ok=True)
    (cfg.codemap_dir / "config.toml").write_text(
        "[explore]\nrebuild_on_commit = false\n", encoding="utf-8"
    )
    dbc = db.connect(cfg.db_path)
    db.migrate(dbc)
    indexer.scan(dbc, cfg, until=fixture_impact_repo.sha("i3-sig-partial"))
    dbc.close()
    out = cfg.codemap_dir / "explore.html"
    if out.exists():
        out.unlink()

    class Args:
        path = str(fixture_impact_repo.path)
        out = None
        json = False
        emit_brief = False
        max_symbols = 0
        open = False
        quiet = True
        if_enabled = True

    assert cli.cmd_explore(Args()) == 0
    assert not out.exists()


def test_learn_json_absent_then_valid(fixture_impact_repo, tmp_path):
    cfg = config.load(fixture_impact_repo.path)
    cfg.codemap_dir.mkdir(exist_ok=True)
    lf = cfg.codemap_dir / "learn.json"
    if lf.exists():
        lf.unlink()
    assert learn.load(cfg) is None

    lf.write_text(json.dumps({"title": "T", "modules": [
        {"id": "m1", "title": "Intro", "screens": [], "quiz": []}
    ]}), encoding="utf-8")
    course = learn.load(cfg)
    assert course and course["modules"][0]["title"] == "Intro"

    lf.write_text("{ not json", encoding="utf-8")
    assert learn.load(cfg) is None
    lf.unlink()


def test_explanations_json_absent_invalid_then_valid(fixture_impact_repo, tmp_path):
    cfg = config.load(fixture_impact_repo.path)
    cfg.codemap_dir.mkdir(exist_ok=True)
    ef = cfg.codemap_dir / "explanations.json"
    ef.unlink(missing_ok=True)
    try:
        assert explain.load(cfg) == {}                  # absent -> {}

        ef.write_text("{ not json", encoding="utf-8")
        assert explain.load(cfg) == {}                  # malformed -> {}

        ef.write_text(json.dumps({"symbols": {
            "svc/report.py::build_report": {
                "what": "  Builds the report for a kind.  ",
                "why": "Start here when a report looks wrong.",
                "terms": {"kind": "which report to build", "": "dropped"},
            },
            "svc/report.py::render": {"what": ""},       # empty -> dropped
            "does/not::exist": {"what": "orphan, ignored on attach"},
            "bad": "not an object",
        }}), encoding="utf-8")

        loaded = explain.load(cfg)
        assert set(loaded) == {"svc/report.py::build_report", "does/not::exist"}
        e = loaded["svc/report.py::build_report"]
        assert e["what"] == "Builds the report for a kind."   # trimmed
        assert e["why"].startswith("Start here")
        assert e["terms"] == {"kind": "which report to build"}  # blank key removed
    finally:
        ef.unlink(missing_ok=True)


def test_model_attaches_explanations_to_matching_nodes(fixture_impact_repo, tmp_path):
    cfg = config.load(fixture_impact_repo.path)
    cfg.codemap_dir.mkdir(exist_ok=True)
    ef = cfg.codemap_dir / "explanations.json"
    ef.write_text(json.dumps({"symbols": {
        "svc/report.py::build_report": {"what": "Assembles a report from fetched rows."},
    }}), encoding="utf-8")
    try:
        dbc = db.connect(cfg.db_path)
        db.migrate(dbc)
        indexer.scan(dbc, cfg, until=fixture_impact_repo.sha("i3-sig-partial"))
        data = model.build(dbc, cfg)
        dbc.close()
    finally:
        ef.unlink(missing_ok=True)

    by_key = {n["key"]: n for n in data["nodes"]}
    assert by_key["svc/report.py::build_report"]["explain"] == {
        "what": "Assembles a report from fetched rows."
    }
    assert by_key["svc/report.py::render"]["explain"] is None


def test_emit_brief_writes_overview_and_per_module(fixture_impact_repo, tmp_path):
    cfg, conn = _idx(fixture_impact_repo, tmp_path, "i3-sig-partial")
    data = model.build(conn, cfg)
    # write briefs under the tmp db's dir to avoid touching the real repo
    cfg2 = config.load(fixture_impact_repo.path)
    cfg2.codemap_dir.mkdir(exist_ok=True)
    paths = brief.emit(conn, cfg2, data)
    names = {p.rsplit("\\", 1)[-1].rsplit("/", 1)[-1] for p in paths}
    assert "00-overview.md" in names
    assert any(n.startswith("01-") for n in names)
    body = (cfg2.codemap_dir / "briefs" / "00-overview.md").read_text(encoding="utf-8")
    assert "Entry points" in body and "build_report" in body
