"""Glossary/tooltip data pipeline: bundled-table merge and project overrides."""

from __future__ import annotations

import json

from codemap import config
from codemap.site import glossary


def test_bundled_merge_case_folds_and_dedupes(fixture_impact_repo):
    items = glossary._bundled()

    assert "webpack" in items
    assert sum(1 for k in items if k.lower() == "webpack") == 1  # exactly once

    assert "c" not in items
    assert "go" not in items

    assert "aws" in items
    assert "rag" in items

    assert len(items) > 150


def test_project_glossary_load_absent_invalid_then_valid(fixture_impact_repo, tmp_path):
    cfg = config.load(fixture_impact_repo.path)
    cfg.codemap_dir.mkdir(exist_ok=True)
    gf = cfg.codemap_dir / "glossary.json"
    gf.unlink(missing_ok=True)
    try:
        assert glossary.load(cfg) == {}                      # absent -> {}

        gf.write_text("{ not json", encoding="utf-8")
        assert glossary.load(cfg) == {}                       # malformed -> {}

        gf.write_text(json.dumps({"items": {
            "widget": {"display": "Widget", "definition": "  A thing this repo builds.  "},
            "gadget": {"definition": ""},                     # empty definition -> dropped
            "bad": "not an object",                           # dropped
        }}), encoding="utf-8")

        loaded = glossary.load(cfg)
        assert set(loaded) == {"widget"}
        assert loaded["widget"]["display"] == "Widget"
        assert loaded["widget"]["definition"] == "A thing this repo builds."  # trimmed
    finally:
        gf.unlink(missing_ok=True)


def test_build_filters_to_prose(fixture_impact_repo):
    cfg = config.load(fixture_impact_repo.path)
    cfg.codemap_dir.mkdir(exist_ok=True)
    gf = cfg.codemap_dir / "glossary.json"
    gf.unlink(missing_ok=True)

    result = glossary.build(cfg, ["We use React for the frontend."])
    assert result is not None
    assert "React" in result["terms"]
    assert "Django" not in result["terms"]
    assert "Kubernetes" not in result["terms"]


def test_build_project_overrides_bundled(fixture_impact_repo):
    cfg = config.load(fixture_impact_repo.path)
    cfg.codemap_dir.mkdir(exist_ok=True)
    gf = cfg.codemap_dir / "glossary.json"
    try:
        gf.write_text(json.dumps({"items": {
            "react": {"display": "React", "definition": "This repo's own take on React."},
        }}), encoding="utf-8")

        result = glossary.build(cfg, ["We use React for the frontend."])
        assert result is not None
        assert result["terms"]["React"] == "This repo's own take on React."
    finally:
        gf.unlink(missing_ok=True)


def test_build_returns_none_when_nothing_matches(fixture_impact_repo):
    cfg = config.load(fixture_impact_repo.path)
    cfg.codemap_dir.mkdir(exist_ok=True)
    gf = cfg.codemap_dir / "glossary.json"
    gf.unlink(missing_ok=True)

    assert glossary.build(cfg, ["Nothing interesting happens in this sentence."]) is None
    assert glossary.build(cfg, []) is None


def test_build_extra_terms_sit_between_bundled_and_project(fixture_impact_repo):
    """`extra_terms` (walkthrough.json's own inline `glossary`, folded in by
    model.py) is visible when nothing else defines the term, but the
    dedicated `.codemap/glossary.json` file still wins a real conflict."""
    cfg = config.load(fixture_impact_repo.path)
    cfg.codemap_dir.mkdir(exist_ok=True)
    gf = cfg.codemap_dir / "glossary.json"
    gf.unlink(missing_ok=True)

    # not covered by any bundled table or a project glossary.json
    result = glossary.build(cfg, ["The side quest is a fun detour."], extra_terms={
        "side quest": "An optional, just-for-fun section of the site.",
    })
    assert result is not None
    assert result["terms"]["side quest"] == "An optional, just-for-fun section of the site."

    try:
        gf.write_text(json.dumps({"items": {
            "side quest": {"display": "side quest", "definition": "The project's own definition wins."},
        }}), encoding="utf-8")
        result = glossary.build(cfg, ["The side quest is a fun detour."], extra_terms={
            "side quest": "An optional, just-for-fun section of the site.",
        })
        assert result["terms"]["side quest"] == "The project's own definition wins."
    finally:
        gf.unlink(missing_ok=True)
