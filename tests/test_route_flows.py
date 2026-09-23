"""Web-request links from the optional codebase-memory engine: the Architecture tab's
request connectors, Simulate's "request crosses to the server" hop, and the brief.

The call graph has no edge across an HTTP request, so a screen calling `/api/x` and
the handler serving it were never joined. `model.build` now carries the engine's
route links as `routes`; these tests follow them into each surface.
"""

from __future__ import annotations

import hashlib
import html as _html
import json
import re
from types import SimpleNamespace

import pytest

from codemap import config, db, indexer
from codemap.site import brief, model, render
from tests.cbm_fake import IMPACT_EDGES, IMPACT_NODES, make_cbm_db
from tests.fixtures.build_repo import build_impact_repo
from tests.test_arch_arrows import (
    CLIENT, PAGES, SPLIT_ROW, USERS, _arch_page, _browser, _dump_dom, _numbers,
)
from tests.test_architecture import mk_file, run

GET_REPORT = "GET /api/report"


# --------------------------------------------------------------------------- architecture.build


def _cid(arch, fi):
    return next(c["id"] for c in arch["components"] if fi in c["files"])


def _three_part_app():
    # two files per folder: folders.py folds a one-file folder into its parent
    return [
        mk_file(0, "web/pages/home.py", deps=["jinja2"]), mk_file(1, "web/pages/about.py", deps=["jinja2"]),
        mk_file(2, "api/routes/users.py", deps=["flask"]), mk_file(3, "api/routes/orders.py", deps=["flask"]),
        mk_file(4, "api/services/billing.py"), mk_file(5, "api/services/mailer.py"),
    ]


def test_a_route_links_the_component_that_calls_it_to_the_one_that_serves_it():
    route = {"url": "/api/users", "method": "GET", "callers": [0], "handlers": [2]}
    arch = run(_three_part_app(), (), [], routes=[route])
    assert arch["requests"] == [
        {"s": _cid(arch, 0), "t": _cid(arch, 2), "routes": ["GET /api/users"], "n": 1}
    ]
    assert _cid(arch, 0) != _cid(arch, 2)
    assert arch["links"] == []          # imports are untouched: a request is not an import


def test_requests_group_by_pair_and_skip_what_cannot_be_drawn():
    routes = [
        {"url": "/api/users", "method": "GET", "callers": [0], "handlers": [2]},
        {"url": "/api/users", "method": "POST", "callers": [1], "handlers": [2]},      # same pair: grouped
        {"url": "/health", "method": "ANY", "callers": [0], "handlers": [4]},          # bare url when no method
        {"url": "/inside", "method": "GET", "callers": [2], "handlers": [3]},          # same component: no arrow
        {"url": "/ghost", "method": "GET", "callers": [0], "handlers": [99]},          # unknown node: ignored
        {"url": "/nobody", "method": "GET", "callers": [], "handlers": [2]},           # no caller: ignored
    ]
    arch = run(_three_part_app(), (), [], routes=routes)
    c = [_cid(arch, fi) for fi in range(6)]
    assert c[2] == c[3] and len({c[0], c[2], c[4]}) == 3          # the layout this test relies on
    assert arch["requests"] == [
        {"s": c[0], "t": c[2], "routes": ["GET /api/users", "POST /api/users"], "n": 2},   # heaviest pair first
        {"s": c[0], "t": c[4], "routes": ["/health"], "n": 1},
    ]


def test_no_routes_means_no_requests():
    assert run(_three_part_app(), (), [])["requests"] == []
    assert run(_three_part_app(), (), [], routes=[])["requests"] == []


# --------------------------------------------------------------------------- through the model


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    repo = build_impact_repo(tmp_path_factory.mktemp("route_repo"))
    cfg = config.load(repo.path)
    cfg.codemap_dir.mkdir(exist_ok=True)
    conn = db.connect(cfg.db_path)
    db.migrate(conn)
    indexer.scan(conn, cfg, until=repo.sha("i3-sig-partial"))
    hashes = {p: hashlib.sha256((repo.path / p).read_bytes()).hexdigest()
              for p in ("svc/cli.py", "svc/data.py", "svc/report.py", "svc/web.py")}
    yield SimpleNamespace(repo=repo, cfg=cfg, conn=conn, hashes=hashes)
    conn.close()


@pytest.fixture
def cache(tmp_path, monkeypatch):
    d = tmp_path / "cbm-cache"
    d.mkdir()
    monkeypatch.setenv("CBM_CACHE_DIR", str(d))
    return d


def _with_engine(env, cache):
    make_cbm_db(cache, env.repo.path, nodes=IMPACT_NODES, edges=IMPACT_EDGES, hashes=env.hashes)
    return model.build(env.conn, env.cfg)


def test_the_model_draws_the_request_only_when_the_engine_found_a_route(env, cache):
    without = model.build(env.conn, env.cfg)
    assert without["architecture"]["requests"] == [] and "routes" not in without

    data = _with_engine(env, cache)
    assert data["architecture"]["requests"] == [
        {"s": "entry:svc/cli.py", "t": "entry:svc/web.py", "routes": [GET_REPORT], "n": 1}
    ]
    json.dumps(data)


def test_a_derived_brief_tree_crosses_the_request_and_the_overview_lists_it(env, cache, tmp_path):
    cfg = config.load(env.repo.path)
    cfg.codemap_dir.mkdir(exist_ok=True)
    # plain repo: no route section, no hop
    plain = model.build(env.conn, cfg)
    brief.emit(env.conn, cfg, plain)
    overview = (cfg.codemap_dir / "briefs" / "00-overview.md").read_text(encoding="utf-8")
    assert "Route links" not in overview

    data = _with_engine(env, cache)
    brief.emit(env.conn, cfg, data)
    overview = (cfg.codemap_dir / "briefs" / "00-overview.md").read_text(encoding="utf-8")
    assert "## Route links" in overview and f"`{GET_REPORT}`" in overview
    assert "`svc/cli.py::main`" in overview and "`svc/web.py::report_view`" in overview

    derived = json.loads((cfg.codemap_dir / "briefs" / "scenarios-derived.json").read_text(encoding="utf-8"))
    tree = next(s for s in derived["scenarios"] if s["root_key"] == "svc/cli.py::main")["steps"]
    hop = next(i for i, st in enumerate(tree) if st.get("route") == GET_REPORT)
    assert tree[hop]["t"] == "note" and tree[hop]["node"] == "svc/cli.py::main"
    assert tree[hop + 1] == {"node": "svc/web.py::report_view", "t": "call"}   # the handler is walked into
    assert tree[hop]["code"].startswith(f"sends {GET_REPORT}")


# --------------------------------------------------------------------------- in a real browser


needs_browser = pytest.mark.skipif(_browser() is None, reason="needs Chrome, Chromium or Edge")


def _box_edges(svg: str) -> dict[str, tuple[float, float, float, float]]:
    """component path -> (x0, x1, y0, y1) of its drawn box"""
    boxes = {}
    for path, x, y, w, h in re.findall(
        r'<g class="arch-item arch-box[^"]*"[^>]*><title>([^\n<]+)[^<]*</title>'
        r'<rect x="([\d.]+)" y="([\d.]+)" width="([\d.]+)" height="([\d.]+)"', svg):
        boxes[_html.unescape(path)] = (float(x), float(x) + float(w), float(y), float(y) + float(h))
    return boxes


def _request_connectors(dom: str):
    start = dom.find('<svg class="arch"')
    assert start >= 0, "no architecture diagram in the page (did archTab throw?)"
    svg = dom[start: dom.find("</svg>", start)]
    out = []
    for title, body in re.findall(r'<g class="arch-req"><title>(.*?)</title>(.*?)</g>', svg, re.S):
        curve = re.search(r'<path d="([^"]+)"', body).group(1)
        head = re.search(r'<path class="head" d="([^"]+)"', body).group(1)
        out.append((_html.unescape(title), _numbers(curve)[0], _numbers(head)[0]))
    return svg, out


def _on_edge(pt, box, slack=1.5) -> bool:
    x, y = pt
    inside_x = box[0] - slack <= x <= box[1] + slack
    return inside_x and (abs(y - box[2]) <= slack or abs(y - box[3]) <= slack)


@needs_browser
@pytest.mark.parametrize(
    "caller, handler",
    [(PAGES, USERS), (PAGES, CLIENT)],
    ids=["down-a-row", "same-row-across-the-seam"],
)
def test_the_request_connector_leaves_the_caller_and_lands_on_the_handler(
    fixture_impact_repo, tmp_path, caller, handler
):
    files, edges = SPLIT_ROW
    fi = {f["path"]: f["fi"] for f in files}
    route = {"url": "/api/users", "method": "GET", "callers": [fi[caller]], "handlers": [fi[handler]]}
    arch, page = _arch_page(fixture_impact_repo, tmp_path, files, edges, routes=[route])
    assert len(arch["requests"]) == 1

    svg, connectors = _request_connectors(_dump_dom(page))
    assert len(connectors) == 1
    title, start, tip = connectors[0]
    src = next(c for c in arch["components"] if c["id"] == arch["requests"][0]["s"])
    dst = next(c for c in arch["components"] if c["id"] == arch["requests"][0]["t"])
    assert title.startswith(f"1 web request from {src['title']} to {dst['title']}:")
    assert "GET /api/users" in title

    boxes = _box_edges(svg)
    assert _on_edge(start, boxes[src["path"]]), f"connector does not leave {src['path']}: {start}"
    assert _on_edge(tip, boxes[dst["path"]]), f"connector does not land on {dst['path']}: {tip}"


@needs_browser
def test_simulate_walks_from_the_client_call_into_the_handler(env, cache, tmp_path):
    data = _with_engine(env, cache)
    page = tmp_path / "explore.html"
    page.write_text(render.render(data), encoding="utf-8")
    dom = _dump_dom(page, "#/sim/derive:svc/cli.py::main/0")
    text = _html.unescape(re.sub(r"<[^>]+>", " ", dom))
    assert f"main sends {GET_REPORT}" in text
    assert f"{GET_REPORT} arrives at report_view." in text
    assert "travels over the network to the server" in text

    # ...and nothing of the kind appears when the engine wasn't there
    off = config.load(env.repo.path)
    off.engine.codebase_memory = "off"
    page.write_text(render.render(model.build(env.conn, off)), encoding="utf-8")
    dom = _dump_dom(page, "#/sim/derive:svc/cli.py::main/0")
    text = _html.unescape(re.sub(r"<[^>]+>", " ", dom))
    assert f"{GET_REPORT} arrives at" not in text and "main sends" not in text   # (the JS source itself has the bare words)
    assert "main calls build_report" in text                                      # the ordinary tree still plays
