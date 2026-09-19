"""The Architecture tab's arrows are drawn in the browser (``explore.js``, ``archTab``),
so the Python payload alone can't show whether an arrow lands where its tooltip says.
These tests render a real page in a headless Chrome/Edge and read the geometry back
out of the SVG. They are skipped when no browser is installed.

The bug they guard: an arrow between two layers used to be placed at the midpoint of
two *boxes* and titled with those two box names. With Views and API side by side in
one row, ``app.py -> api.py`` landed in the 18px seam between the two bands, touching
neither; a cap of two arrows per gap also hid whole layer-to-layer relationships."""

from __future__ import annotations

import html as _html
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from codemap.site import architecture, model, render
from tests.test_architecture import mk_file, run
from tests.test_explore import _idx


def _browser() -> str | None:
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome", "msedge"):
        found = shutil.which(name)
        if found:
            return found
    for path in (
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    ):
        if os.path.exists(path):
            return path
    return None


pytestmark = pytest.mark.skipif(_browser() is None, reason="needs Chrome, Chromium or Edge")


def _repo(spec, edges):
    """(path, deps) rows + (importer, imported) path pairs -> files/edges with dense indices"""
    files = [mk_file(i, path, deps=deps) for i, (path, deps) in enumerate(spec)]
    idx = {path: i for i, (path, _) in enumerate(spec)}
    return files, [(idx[a], idx[b]) for a, b in edges]


USERS, ORDERS_R = "app/routes/users.py", "app/routes/orders.py"
PAGES, EMAILS = "app/views/pages.py", "app/views/emails.py"
CLIENT, SCHEMAS = "app/api/client.py", "app/api/schemas.py"
BILLING, ORDERS_S = "app/services/billing.py", "app/services/orders.py"
USER_M, ORDER_M = "app/models/user.py", "app/models/order.py"
DATES, MONEY = "app/utils/dates.py", "app/utils/money.py"

# extra view folders (two files each: a one-file folder is folded into its parent)
MORE_VIEWS = [(f"app/views/{n}/{f}.py", ["jinja2"]) for n in "bcdef" for f in ("page", "base")]

# A repo with Views *and* API (they share a row), a side panel, and an API -> logic
# relationship heavier than Views -> logic (the one the old cap of two arrows hid).
# Views has many more parts than API, so the two bands are lopsided, like a real client
# plus API repo: only then does "midway between the two boxes" fall outside the API band.
SPLIT_ROW = _repo(
    [(USERS, ["flask"]), (ORDERS_R, ["flask"]), (PAGES, ["jinja2"]), (EMAILS, ["jinja2"]),
     (CLIENT, []), (SCHEMAS, []), (BILLING, ["stripe"]), (ORDERS_S, []),
     (USER_M, ["sqlalchemy"]), (ORDER_M, ["sqlalchemy"]), (DATES, []), (MONEY, [])] + MORE_VIEWS,
    [(USERS, CLIENT), (ORDERS_R, SCHEMAS),                # routes -> api
     (USERS, PAGES), (ORDERS_R, EMAILS),                  # routes -> views
     (PAGES, BILLING), (EMAILS, ORDERS_S),                # views -> logic
     (CLIENT, BILLING), (SCHEMAS, ORDERS_S), (CLIENT, ORDERS_S),   # api -> logic
     (BILLING, USER_M), (ORDERS_S, ORDER_M),              # logic -> data
     (PAGES, CLIENT), (EMAILS, SCHEMAS), (PAGES, SCHEMAS),          # views -> api: same rank
     (BILLING, DATES), (PAGES, MONEY)],                   # shared code
)

# no API layer: every row is a single full-width band
SINGLE_BAND = _repo(
    [(USERS, ["flask"]), (ORDERS_R, ["flask"]), (PAGES, ["jinja2"]), (EMAILS, ["jinja2"]),
     (BILLING, ["stripe"]), (ORDERS_S, []), (USER_M, ["sqlalchemy"]), (ORDER_M, ["sqlalchemy"]),
     (DATES, []), (MONEY, [])],
    [(USERS, PAGES), (ORDERS_R, EMAILS), (PAGES, BILLING), (EMAILS, ORDERS_S),
     (BILLING, USER_M), (ORDERS_S, ORDER_M), (BILLING, DATES)],
)


def _arch_page(fixture_impact_repo, tmp_path, files, edges):
    """A real page whose architecture payload is derived from the synthetic repo."""
    arch = run(files, edges, [{"kind": "route", "detail": "GET /users", "node": 0}])
    cfg, conn = _idx(fixture_impact_repo, tmp_path, "i3-sig-partial")
    data = model.build(conn, cfg)
    data["architecture"] = arch
    page = tmp_path / "explore.html"
    page.write_text(render.render(data), encoding="utf-8")
    return arch, page


def _dump_dom(page: Path) -> str:
    cmd = [_browser(), "--headless=new", "--disable-gpu", "--window-size=1400,900",
           "--virtual-time-budget=8000", "--dump-dom", page.resolve().as_uri() + "#/arch"]
    if hasattr(os, "geteuid") and os.geteuid() == 0:            # containers run as root
        cmd.insert(1, "--no-sandbox")
    done = subprocess.run(cmd, capture_output=True, timeout=120, encoding="utf-8", errors="replace")
    return done.stdout


def _numbers(d: str) -> list[tuple[float, float]]:
    nums = [float(n) for n in re.findall(r"-?\d+\.?\d*", d)]
    return list(zip(nums[0::2], nums[1::2], strict=False))


def _geometry(dom: str):
    """-> (bands, arrows): bands {title: (x0, x1, y0, y1)}; arrows [(title, (x0, x1, y0, y1))]"""
    start = dom.find('<svg class="arch"')
    assert start >= 0, "no architecture diagram in the page (did archTab throw?)"
    svg = dom[start: dom.find("</svg>", start)]
    bands = {}
    for x, y, w, h, label in re.findall(
        r'<rect x="([\d.]+)" y="([\d.]+)" width="([\d.]+)" height="([\d.]+)" rx="10"[^>]*></rect>'
        r'<text[^>]*class="arch-lt[^"]*"[^>]*>([^<]+)<title>', svg):
        bands[_html.unescape(label)] = (float(x), float(x) + float(w), float(y), float(y) + float(h))
    arrows = []
    for title, body in re.findall(r'<g class="arch-arrow"><title>(.*?)</title>(.*?)</g>', svg, re.S):
        pts = [p for d in re.findall(r' d="([^"]+)"', body) for p in _numbers(d)]
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        arrows.append((_html.unescape(title), (min(xs), max(xs), min(ys), max(ys))))
    return bands, arrows


def _inside(box, band) -> bool:
    return band[0] <= box[0] and box[1] <= band[1]


@pytest.mark.parametrize("repo", [SPLIT_ROW, SINGLE_BAND], ids=["views-beside-api", "single-band-rows"])
def test_every_band_arrow_sits_over_the_bands_it_names(fixture_impact_repo, tmp_path, repo):
    arch, page = _arch_page(fixture_impact_repo, tmp_path, *repo)
    bands, arrows = _geometry(_dump_dom(page))
    title = {layer["id"]: layer["title"] for layer in arch["layers"]}
    layer_of = {c["id"]: c["layer"] for c in arch["components"]}
    if repo is SPLIT_ROW:                       # the precondition that makes this test bite
        assert list(layer_of.values()).count("views") >= 5 and list(layer_of.values()).count("api") == 1
        assert bands["Views & UI"][1] - bands["Views & UI"][0] > 1.4 * (bands["API"][1] - bands["API"][0])

    drawn, sideways = {}, {}
    for text, box in arrows:
        head = text.split("\n")[0]
        m = re.match(r"(.+) → (.+) · (\d+) imports?", head)
        if not m or "entry point" in head:
            continue                                        # an actor's arrow, checked below
        src, dst, n = m.group(1), m.group(2), int(m.group(3))
        assert src in bands and dst in bands, f"tooltip names a layer that has no band: {head!r}"
        in_a_band = any(y0 <= box[2] and box[3] <= y1 for _, _, y0, y1 in bands.values())
        if in_a_band:                                        # sideways: must sit in the seam
            left, right = sorted((bands[src], bands[dst]))
            assert left[1] <= box[0] and box[1] <= right[0], f"sideways arrow is not in the seam: {head!r}"
            sideways[(src, dst)] = n
        else:                                                # down a row gap: over BOTH bands
            assert _inside(box, bands[dst]), f"arrow is not over the band it points into: {head!r} at x={box[:2]}"
            assert _inside(box, bands[src]), f"arrow is not under the band it leaves: {head!r} at x={box[:2]}"
            assert box[3] <= bands[dst][2] and box[2] >= bands[src][3] - 1, f"arrow leaves its row gap: {head!r}"
            drawn[(src, dst)] = n

    # nothing is silently dropped, and the count is the whole layer pair's
    want_down, want_side = {}, {}
    for link in arch["links"]:
        ls, lt = layer_of[link["s"]], layer_of[link["t"]]
        if link["dir"] == "down" and architecture.RANK[lt] - architecture.RANK[ls] == 1:
            key = (title[ls], title[lt])
            want_down[key] = want_down.get(key, 0) + link["n"]
        elif link["dir"] == "same" and ls != lt:
            key = (title[ls], title[lt])
            want_side[key] = want_side.get(key, 0) + link["n"]
    assert drawn == want_down
    if want_side:
        assert sum(sideways.values()) >= max(want_side.values())      # the dominant direction is drawn
        assert set(sideways) <= set(want_side)
    else:
        assert not sideways

    # the Internet actor names the band it really drives
    actor = [t for t, _ in arrows if t.startswith("Internet")]
    assert actor and actor[0].startswith("Internet → Routes & entry")


def test_views_and_api_are_drawn_sideways_not_dropped(fixture_impact_repo, tmp_path):
    """Views and API share a rank, so architecture.py tags their imports `same`; the page
    used to ignore that tag and draw nothing for the biggest relationship in a
    client-plus-API repo."""
    arch, page = _arch_page(fixture_impact_repo, tmp_path, *SPLIT_ROW)
    _, arrows = _geometry(_dump_dom(page))
    heads = [t.split("\n")[0] for t, _ in arrows]
    assert any(h.startswith("Views & UI → API · 3 imports") for h in heads), heads
    assert any(h.startswith("API → Logic") for h in heads), heads
