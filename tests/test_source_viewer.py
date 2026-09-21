"""The explorer's source viewer and the Folder | Layer graph grouping: what the
model embeds (whole files, under a byte budget), what render.py leaves out of
the payload as a result, and that the page carries the client hooks."""

from __future__ import annotations

import json
from types import SimpleNamespace

from codemap import cli, config, db, indexer
from codemap.site import model, render


def _idx(repo, tmp_path, until="i3-sig-partial"):
    cfg = config.load(repo.path)
    conn = db.connect(tmp_path / "index.db")
    db.migrate(conn)
    indexer.scan(conn, cfg, until=repo.sha(until))
    return cfg, conn


def _payload(html: str) -> dict:
    raw = html.split('id="codemap-data">', 1)[1].split("</script>", 1)[0]
    return json.loads(raw.replace("\\u003c", "<"))


def _sizes(data: dict) -> dict[str, int]:
    return {fi: len(text.encode("utf-8")) for fi, text in data["sources"].items()}


# --------------------------------------------------------------------------- model


def test_model_embeds_the_whole_text_of_files_that_hold_symbols(fixture_impact_repo, tmp_path):
    cfg, conn = _idx(fixture_impact_repo, tmp_path)
    data = model.build(conn, cfg)

    path_of = {str(f["fi"]): f["path"] for f in data["files"]}
    assert data["sources"]
    assert {path_of[fi] for fi in data["sources"]} == {n["file"] for n in data["nodes"]}
    assert json.loads(json.dumps(data["sources"])) == data["sources"]   # string keys survive JSON

    # a symbol's capped excerpt is literally its line range of the embedded text --
    # that is what lets the page rebuild it instead of shipping it twice
    n = next(n for n in data["nodes"] if n["excerpt"])
    fi = next(str(f["fi"]) for f in data["files"] if f["path"] == n["file"])
    lines = data["sources"][fi].split("\n")
    assert "\n".join(lines[n["line"][0] - 1 : n["line"][1]]) == n["excerpt"]
    assert data["snippet_lines"] == 40


def test_source_budget_zero_embeds_nothing_and_excerpts_stay(fixture_impact_repo, tmp_path):
    cfg, conn = _idx(fixture_impact_repo, tmp_path)
    data = model.build(conn, cfg, max_source_bytes=0)
    assert data["sources"] == {}
    assert any(n["excerpt"] for n in data["nodes"])


def test_source_budget_is_never_exceeded_and_entry_point_files_go_first(fixture_impact_repo, tmp_path):
    cfg, conn = _idx(fixture_impact_repo, tmp_path)
    full = model.build(conn, cfg)
    sizes = _sizes(full)
    entry_paths = {n["file"] for n in full["nodes"] if n["entry"]}
    entry_fi = {str(f["fi"]) for f in full["files"] if f["path"] in entry_paths}
    assert entry_fi and len(sizes) > len(entry_fi)

    budget = max(sizes[fi] for fi in entry_fi)
    assert budget < sum(sizes.values())
    part = model.build(conn, cfg, max_source_bytes=budget)
    assert sum(_sizes(part).values()) <= budget
    assert set(part["sources"]) & entry_fi
    assert set(part["sources"]) < set(full["sources"])


def test_embeddable_skips_binary_and_minified_and_normalises_newlines():
    assert model._embeddable("a\x00b") is None
    assert model._embeddable("x" * 5000) is None
    assert model._embeddable("a\r\nb\r\n") == "a\nb\n"
    assert model._embeddable("") == ""


def test_max_source_bytes_config(tmp_path):
    (tmp_path / ".codemap").mkdir()
    assert config.load(tmp_path).explore.max_source_bytes == 4_000_000
    (tmp_path / ".codemap" / "config.toml").write_text(
        "[explore]\nmax_source_bytes = 123\n", encoding="utf-8"
    )
    assert config.load(tmp_path).explore.max_source_bytes == 123


# --------------------------------------------------------------------------- render


def test_render_ships_embedded_files_once(fixture_impact_repo, tmp_path):
    cfg, conn = _idx(fixture_impact_repo, tmp_path)
    data = model.build(conn, cfg)
    payload = _payload(render.render(data))
    assert payload["sources"] == data["sources"]
    assert all("excerpt" not in n for n in payload["nodes"])      # the page rebuilds them
    # the model itself is untouched: the module briefs still read `excerpt`
    assert any(n["excerpt"] for n in data["nodes"])


def test_render_keeps_excerpts_for_files_that_missed_the_budget(fixture_impact_repo, tmp_path):
    cfg, conn = _idx(fixture_impact_repo, tmp_path)
    sizes = _sizes(model.build(conn, cfg))
    data = model.build(conn, cfg, max_source_bytes=min(sizes.values()))
    embedded = {f["path"] for f in data["files"] if str(f["fi"]) in data["sources"]}
    assert embedded and len(embedded) < len({n["file"] for n in data["nodes"]})

    payload = _payload(render.render(data))
    for n in payload["nodes"]:
        assert ("excerpt" in n) == (n["file"] not in embedded)


def test_render_with_no_sources_leaves_the_payload_alone(fixture_impact_repo, tmp_path):
    cfg, conn = _idx(fixture_impact_repo, tmp_path)
    data = model.build(conn, cfg, max_source_bytes=0)
    payload = _payload(render.render(data))
    assert payload["sources"] == {}
    assert all("excerpt" in n for n in payload["nodes"])


def test_page_carries_the_viewer_and_the_layer_switch(fixture_impact_repo, tmp_path):
    cfg, conn = _idx(fixture_impact_repo, tmp_path)
    html = render.render(model.build(conn, cfg))
    for hook in ("srcViewer", "highlightLines", "groupSwitch", "legendLayers", 'class: "srcpane"'):
        assert hook in html
    assert ".srcpane" in html and ".legend-seg" in html        # css inlined
    # the highlighter is local: the page still loads no script from anywhere
    assert "<script src=" not in html


def test_explore_json_omits_the_embedded_sources(fixture_impact_repo, tmp_path, capsys):
    cfg = config.load(fixture_impact_repo.path)
    cfg.codemap_dir.mkdir(exist_ok=True)
    dbc = db.connect(cfg.db_path)
    db.migrate(dbc)
    indexer.scan(dbc, cfg, until=fixture_impact_repo.sha("i3-sig-partial"))
    dbc.close()

    args = SimpleNamespace(
        path=str(fixture_impact_repo.path), out=None, json=True, emit_brief=False,
        max_symbols=0, open=False, quiet=True, if_enabled=False, no_progress=False,
    )
    assert cli.cmd_explore(args) == 0
    data = json.loads(capsys.readouterr().out)
    assert "nodes" in data and "sources" not in data
