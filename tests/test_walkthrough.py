"""Loader-contract tests for ``.codemap/walkthrough.json`` — mirrors the shape of
``test_libraries_json_absent_invalid_then_valid`` in ``tests/test_explore.py``.
"""

from __future__ import annotations

import json

from codemap import config
from codemap.site import walkthrough


def _write(cfg, data) -> None:
    cfg.codemap_dir.mkdir(exist_ok=True)
    wf = cfg.codemap_dir / walkthrough.WALKTHROUGH_FILE
    wf.write_text(json.dumps(data), encoding="utf-8")


def test_absent_file_returns_none(fixture_impact_repo, tmp_path):
    cfg = config.load(fixture_impact_repo.path)
    cfg.codemap_dir.mkdir(exist_ok=True)
    wf = cfg.codemap_dir / walkthrough.WALKTHROUGH_FILE
    wf.unlink(missing_ok=True)
    try:
        assert walkthrough.load(cfg) is None
    finally:
        wf.unlink(missing_ok=True)


def test_malformed_json_returns_none(fixture_impact_repo, tmp_path):
    cfg = config.load(fixture_impact_repo.path)
    cfg.codemap_dir.mkdir(exist_ok=True)
    wf = cfg.codemap_dir / walkthrough.WALKTHROUGH_FILE
    wf.write_text("{ not json", encoding="utf-8")
    try:
        assert walkthrough.load(cfg) is None
    finally:
        wf.unlink(missing_ok=True)


def test_full_valid_document_exact_shape(fixture_impact_repo, tmp_path):
    cfg = config.load(fixture_impact_repo.path)
    try:
        _write(cfg, {
            "intro": {
                "what": "  A tiny app that says hello.  ",
                "sides": {
                    "frontend": {
                        "root": "frontend",
                        "title": "The part people see",
                        "body": "Runs in the browser.",
                    },
                },
                "seam": {
                    "note": "The two sides meet over one HTTP call.",
                    "see": [
                        "frontend/src/services/api.js::sendContact",
                        "backend/app/routers/contact.py::create_contact",
                    ],
                },
            },
            "categories": [
                {
                    "title": "Website design",
                    "body": "The code that decides what the page looks like.",
                    "groups": [
                        {
                            "title": "Animation",
                            "see": ["frontend/src/hooks/useScrollReveal.js::useScrollReveal"],
                        },
                        {
                            "title": "Styling",
                            "see": {
                                "CSS files": ["frontend/src/index.css"],
                                "utility classes": ["tailwind.config.js"],
                            },
                        },
                    ],
                },
            ],
            "folders": {
                "frontend/src/components/ui": {
                    "title": "Small reusable pieces",
                    "purpose": "The buttons, cards and inputs the rest of the site is built out of.",
                    "read_first": "frontend/src/components/ui/Button.jsx",
                    "note": "Nothing in the app imports this folder -- that's expected for tests.",
                    "categories": ["look"],
                    "see": ["frontend/src/components/ui/Card.jsx::Card"],
                },
            },
            "glossary": {
                "contact form": "The box on the page where a visitor types a message to you.",
            },
        })

        loaded = walkthrough.load(cfg)
        assert loaded is not None

        assert loaded["intro"] == {
            "what": "A tiny app that says hello.",
            "sides": {
                "frontend": {
                    "root": "frontend",
                    "title": "The part people see",
                    "body": "Runs in the browser.",
                },
            },
            "seam": {
                "note": "The two sides meet over one HTTP call.",
                "see": [{
                    "group": None,
                    "keys": [
                        "frontend/src/services/api.js::sendContact",
                        "backend/app/routers/contact.py::create_contact",
                    ],
                }],
            },
        }

        assert loaded["categories"] == [{
            "id": "website-design",
            "title": "Website design",
            "body": "The code that decides what the page looks like.",
            "groups": [
                {
                    "title": "Animation",
                    "see": [{
                        "group": None,
                        "keys": ["frontend/src/hooks/useScrollReveal.js::useScrollReveal"],
                    }],
                },
                {
                    "title": "Styling",
                    "see": [
                        {"group": "CSS files", "keys": ["frontend/src/index.css"]},
                        {"group": "utility classes", "keys": ["tailwind.config.js"]},
                    ],
                },
            ],
        }]

        assert loaded["folders"] == {
            "frontend/src/components/ui": {
                "purpose": "The buttons, cards and inputs the rest of the site is built out of.",
                "title": "Small reusable pieces",
                "read_first": "frontend/src/components/ui/Button.jsx",
                "note": "Nothing in the app imports this folder -- that's expected for tests.",
                "categories": ["look"],
                "see": [{
                    "group": None,
                    "keys": ["frontend/src/components/ui/Card.jsx::Card"],
                }],
            },
        }

        assert loaded["glossary"] == {
            "contact form": "The box on the page where a visitor types a message to you.",
        }
    finally:
        (cfg.codemap_dir / walkthrough.WALKTHROUGH_FILE).unlink(missing_ok=True)


def test_side_missing_root_is_dropped(fixture_impact_repo, tmp_path):
    cfg = config.load(fixture_impact_repo.path)
    try:
        _write(cfg, {
            "intro": {
                "what": "Still here.",
                "sides": {
                    "frontend": {"title": "No root here"},
                },
            },
        })
        loaded = walkthrough.load(cfg)
        assert loaded is not None
        assert loaded["intro"] == {"what": "Still here."}
        assert "sides" not in loaded["intro"]
    finally:
        (cfg.codemap_dir / walkthrough.WALKTHROUGH_FILE).unlink(missing_ok=True)


def test_category_with_no_groups_is_dropped(fixture_impact_repo, tmp_path):
    cfg = config.load(fixture_impact_repo.path)
    try:
        _write(cfg, {
            "categories": [
                {"title": "Empty category", "groups": []},
                {"title": "Also empty"},
                {"title": "Has content", "groups": [
                    {"title": "G", "see": ["a::b"]},
                ]},
            ],
        })
        loaded = walkthrough.load(cfg)
        assert loaded is not None
        titles = [c["title"] for c in loaded["categories"]]
        assert titles == ["Has content"]
    finally:
        (cfg.codemap_dir / walkthrough.WALKTHROUGH_FILE).unlink(missing_ok=True)


def test_folder_without_purpose_is_dropped(fixture_impact_repo, tmp_path):
    cfg = config.load(fixture_impact_repo.path)
    try:
        _write(cfg, {
            "folders": {
                "frontend/src": {
                    "title": "No purpose here",
                    "see": ["frontend/src/App.jsx::App"],
                },
            },
        })
        assert walkthrough.load(cfg) is None
    finally:
        (cfg.codemap_dir / walkthrough.WALKTHROUGH_FILE).unlink(missing_ok=True)


def test_duplicate_category_titles_get_suffixed_ids(fixture_impact_repo, tmp_path):
    cfg = config.load(fixture_impact_repo.path)
    try:
        _write(cfg, {
            "categories": [
                {"title": "Setup", "groups": [{"title": "G1", "see": ["a::b"]}]},
                {"title": "Setup", "groups": [{"title": "G2", "see": ["c::d"]}]},
            ],
        })
        loaded = walkthrough.load(cfg)
        ids = [c["id"] for c in loaded["categories"]]
        assert ids == ["setup", "setup-2"]
    finally:
        (cfg.codemap_dir / walkthrough.WALKTHROUGH_FILE).unlink(missing_ok=True)


def test_norm_see_non_list_non_dict_is_none():
    assert walkthrough._norm_see("frontend/src/App.jsx::App") is None
    assert walkthrough._norm_see(42) is None
    assert walkthrough._norm_see(None) is None


def test_group_see_that_is_a_bare_string_drops_the_group(fixture_impact_repo, tmp_path):
    cfg = config.load(fixture_impact_repo.path)
    try:
        _write(cfg, {
            "categories": [
                {"title": "Broken see", "groups": [
                    {"title": "Bad", "see": "not-a-list-or-dict"},
                    {"title": "Good", "see": ["a::b"]},
                ]},
            ],
        })
        loaded = walkthrough.load(cfg)
        assert loaded is not None
        groups = loaded["categories"][0]["groups"]
        assert [g["title"] for g in groups] == ["Good"]
    finally:
        (cfg.codemap_dir / walkthrough.WALKTHROUGH_FILE).unlink(missing_ok=True)


def test_everything_malformed_returns_none_not_empty_dict(fixture_impact_repo, tmp_path):
    cfg = config.load(fixture_impact_repo.path)
    try:
        _write(cfg, {
            "intro": "not a dict",
            "categories": "not a list",
            "folders": ["not", "a", "dict"],
            "glossary": None,
        })
        assert walkthrough.load(cfg) is None
    finally:
        (cfg.codemap_dir / walkthrough.WALKTHROUGH_FILE).unlink(missing_ok=True)


def test_does_not_depend_on_libraries_module():
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(walkthrough))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.update(alias.name for alias in node.names)
    assert "libraries" not in imported
    assert not hasattr(walkthrough, "libraries")
