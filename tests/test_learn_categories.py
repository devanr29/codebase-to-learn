"""A folder page in Learn pulls in the category groups the folder names.

`walkthrough-schema.md` tells authors to name a folder's categories by title, but
the page used to match only a category's id (a slug nobody sees), so every one of
those references was dropped without a word. Found by `codemap check` on real
authored content.
"""

from __future__ import annotations

import html as _html
import json
import re

import pytest

from codemap import config, db, indexer
from codemap.site import model, render
from tests.fixtures.build_repo import build_impact_repo
from tests.test_arch_arrows import _browser, _dump_dom

pytestmark = pytest.mark.skipif(_browser() is None, reason="needs Chrome, Chromium or Edge")

WALKTHROUGH = {
    "categories": [
        {"title": "Money movement", "groups": [{"title": "Billing", "see": ["svc/report.py::build_report"]}]},
    ],
    "folders": {"svc": {"purpose": "The report service."}},
}


def _page(tmp_path_factory, tmp_path, categories):
    repo = build_impact_repo(tmp_path_factory.mktemp("cat_repo"))
    cfg = config.load(repo.path)
    cfg.codemap_dir.mkdir(exist_ok=True)
    doc = json.loads(json.dumps(WALKTHROUGH))
    doc["folders"]["svc"]["categories"] = categories
    (cfg.codemap_dir / "walkthrough.json").write_text(json.dumps(doc), encoding="utf-8")
    conn = db.connect(cfg.db_path)
    db.migrate(conn)
    indexer.scan(conn, cfg, until=repo.sha("i3-sig-partial"))
    page = tmp_path / "explore.html"
    page.write_text(render.render(model.build(conn, cfg)), encoding="utf-8")
    conn.close()
    return page


@pytest.mark.parametrize("name", ["Money movement", "money movement", "money-movement"], ids=["title", "lowercase", "id"])
def test_a_folder_page_shows_the_category_it_names(tmp_path_factory, tmp_path, name):
    page = _page(tmp_path_factory, tmp_path, [name])
    text = _html.unescape(re.sub(r"<[^>]+>", " ", _dump_dom(page, "#/learn/svc")))
    assert "Money movement — Billing" in text


def test_a_category_that_matches_nothing_shows_nothing(tmp_path_factory, tmp_path):
    page = _page(tmp_path_factory, tmp_path, ["Something else"])
    text = _html.unescape(re.sub(r"<[^>]+>", " ", _dump_dom(page, "#/learn/svc")))
    assert "The report service." in text and "Money movement — Billing" not in text
