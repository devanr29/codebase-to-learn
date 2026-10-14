"""Web-request links: the Architecture tab's request connectors, Simulate's
"request crosses to the server" hop, and the brief.

The call graph has no edge across an HTTP request, so a client calling `/report`
and the handler serving it were never joined. `model.build` carries codemap's own
request -> route links (`enrich/pyfacts.py` for Python, `jsfacts.py` for JS/TS) as
`routes`; these tests follow one into each surface.
"""

from __future__ import annotations

import html as _html
import json
import re
from types import SimpleNamespace

import pytest

from codemap import config, db, indexer
from codemap.site import brief, model, render
from tests.fixtures.build_repo import _git, build_impact_repo
from tests.test_arch_arrows import (
    CLIENT, PAGES, SPLIT_ROW, USERS, _arch_page, _browser, _dump_dom, _numbers,
)
from tests.test_architecture import mk_file, run

GET_REPORT = "GET /report"

# svc/cli.py at the extra commit: main() asks the running web app for the
# report instead of building it in-process -- a request to svc/web.py's
# `@app.get("/report")` handler, which only request -> route linking can join.
CLI_WITH_REQUEST = '''import requests

from svc.report import build_report


def main():
    print(build_report("cli"))
    print(requests.get("http://localhost:8000/report").text)


if __name__ == "__main__":
    main()
'''


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


def _index(repo_path, conn_path, until):
    cfg = config.load(repo_path)
    cfg.codemap_dir.mkdir(exist_ok=True)
    conn = db.connect(conn_path)
    db.migrate(conn)
    indexer.scan(conn, cfg, until=until)
    return cfg, conn


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    """The impact repo plus one commit in which main() sends a real request."""
    repo = build_impact_repo(tmp_path_factory.mktemp("route_repo"))
    plain_cfg, plain = _index(repo.path, tmp_path_factory.mktemp("plain") / "index.db",
                              until=repo.sha("i3-sig-partial"))
    (repo.path / "svc" / "cli.py").write_text(CLI_WITH_REQUEST, encoding="utf-8", newline="\n")
    _git(repo.path, "add", "-A")
    _git(repo.path, "commit", "-q", "-m", "cli: ask the web app for the report")
    cfg, conn = _index(repo.path, config.load(repo.path).db_path, until=_git(repo.path, "rev-parse", "HEAD"))
    yield SimpleNamespace(repo=repo, cfg=cfg, conn=conn, plain=plain, plain_cfg=plain_cfg)
    conn.close()
    plain.close()


def _with_request(env):
    return model.build(env.conn, env.cfg)


def test_the_model_draws_the_request_only_when_the_code_sends_one(env):
    without = model.build(env.plain, env.plain_cfg)
    assert without["architecture"]["requests"] == []
    # the served route is still listed ("this address exists"), with nobody calling it
    assert all(not r["callers"] for r in without.get("routes", []))

    data = _with_request(env)
    assert data["architecture"]["requests"] == [
        {"s": "entry:svc/cli.py", "t": "entry:svc/web.py", "routes": [GET_REPORT], "n": 1}
    ]
    json.dumps(data)


def test_a_derived_brief_tree_crosses_the_request_and_the_overview_lists_it(env, tmp_path):
    cfg = config.load(env.repo.path)
    cfg.codemap_dir.mkdir(exist_ok=True)
    # no request in the code: main's tree never crosses to the server
    plain = model.build(env.plain, env.plain_cfg)
    brief.emit(env.plain, cfg, plain)
    derived = json.loads((cfg.codemap_dir / "briefs" / "scenarios-derived.json").read_text(encoding="utf-8"))
    tree = next(s for s in derived["scenarios"] if s["root_key"] == "svc/cli.py::main")["steps"]
    assert not any(st.get("route") for st in tree)

    data = _with_request(env)
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
def test_simulate_walks_from_the_client_call_into_the_handler(env, tmp_path):
    data = _with_request(env)
    page = tmp_path / "explore.html"
    page.write_text(render.render(data), encoding="utf-8")
    dom = _dump_dom(page, "#/sim/derive:svc/cli.py::main/0")
    text = _html.unescape(re.sub(r"<[^>]+>", " ", dom))
    assert f"main sends {GET_REPORT}" in text
    assert f"{GET_REPORT} arrives at report_view." in text
    assert "travels over the network to the server" in text

    # ...and nothing of the kind appears when request linking is off
    off = config.load(env.repo.path)
    off.engine.native = "off"
    page.write_text(render.render(model.build(env.conn, off)), encoding="utf-8")
    dom = _dump_dom(page, "#/sim/derive:svc/cli.py::main/0")
    text = _html.unescape(re.sub(r"<[^>]+>", " ", dom))
    assert f"{GET_REPORT} arrives at" not in text and "main sends" not in text   # (the JS source itself has the bare words)
    assert "main calls build_report" in text                                      # the ordinary tree still plays
