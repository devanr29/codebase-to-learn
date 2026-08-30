"""M6-M9: intent capture + hook, deterministic report, LLM gating, catch-up,
snapshot."""

from __future__ import annotations

import subprocess

import pytest

from codemap import config, db, digest, indexer, intent, narrate, report


def _idx(repo, tmp_path, until):
    cfg = config.load(repo.path)
    conn = db.connect(tmp_path / "index.db")
    db.migrate(conn)
    indexer.scan(conn, cfg, until=repo.sha(until))
    return cfg, conn


# ------------------------------------------------------------------- M6 intent


def test_intent_resolution_chain(tmp_path, monkeypatch):
    cfg = config.init(tmp_path)
    monkeypatch.delenv("CODEMAP_INTENT", raising=False)

    assert intent.resolve(cfg, "subject line", consume=True) == ("commit_message", "subject line")
    assert intent.resolve(cfg, "", consume=True) == ("inferred", None)

    intent.write_note(cfg, "from the note")
    assert intent.resolve(cfg, "msg", consume=True)[0] == "note"

    monkeypatch.setenv("CODEMAP_INTENT", "from the env")
    intent.write_note(cfg, "note again")
    assert intent.resolve(cfg, "msg", consume=True) == ("env", "from the env")

    (cfg.codemap_dir / intent.PENDING_FILE).write_text("do the thing\n", encoding="utf-8")
    assert intent.resolve(cfg, "msg", consume=True) == ("session", "do the thing")
    # session + note files are consumed
    assert not (cfg.codemap_dir / intent.PENDING_FILE).exists()
    assert not (cfg.codemap_dir / intent.NOTE_FILE).exists()


def test_intent_not_consumed_when_flag_false(tmp_path, monkeypatch):
    cfg = config.init(tmp_path)
    monkeypatch.delenv("CODEMAP_INTENT", raising=False)
    (cfg.codemap_dir / intent.PENDING_FILE).write_text("later\n", encoding="utf-8")
    assert intent.resolve(cfg, "m", consume=False) == ("commit_message", "m")
    assert (cfg.codemap_dir / intent.PENDING_FILE).exists()


def test_scan_captures_intent(fixture_repo, tmp_path, monkeypatch):
    monkeypatch.delenv("CODEMAP_INTENT", raising=False)
    _cfg, conn = _idx(fixture_repo, tmp_path, "c4-body")
    src, text = intent.load(conn, fixture_repo.sha("c4-body"))
    assert src == "commit_message" and "clean()" in text


def test_install_hook_creates_and_chains(tmp_path):
    root = tmp_path
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    hook = root / ".git" / "hooks" / "post-commit"

    assert intent.install_hook(root) == 0
    body = hook.read_text()
    assert body.startswith("#!/bin/sh")
    assert intent._HOOK_MARKER in body

    # idempotent
    intent.install_hook(root)
    assert body.count(intent._HOOK_MARKER) == 1

    # chaining: pre-existing hook content is preserved
    hook.write_text("#!/bin/sh\necho existing\n")
    intent.install_hook(root)
    chained = hook.read_text()
    assert "echo existing" in chained and intent._HOOK_MARKER in chained


# ------------------------------------------------------------------- M7 report


def test_report_sections_and_read_this_first(fixture_repo, tmp_path):
    cfg, conn = _idx(fixture_repo, tmp_path, "c3-signature")
    md = report.render_commit(conn, cfg, fixture_repo.sha("c3-signature"))
    assert md.startswith("## ")
    assert "**Intent:**" in md
    assert "**Structural:** `save()` signature changed, added `ttl`." in md
    assert "**Read this first:** app/util.py:" in md


def test_report_cosmetic_commit_stays_short(fixture_repo, tmp_path):
    cfg, conn = _idx(fixture_repo, tmp_path, "c5-cosmetic")
    md = report.render_commit(conn, cfg, fixture_repo.sha("c5-cosmetic"))
    assert "**Cosmetic:** 2 change(s)." in md
    assert "**Structural:**" not in md
    assert "**Behavioral:**" not in md


def test_report_files_written_during_scan(fixture_repo, tmp_path):
    cfg, _conn = _idx(fixture_repo, tmp_path, "c4-body")
    names = {p.name for p in cfg.changes_dir.glob("*.md")}
    # one <ts>-<sha7>.md per indexed commit c1..c4 (the shared fixture dir may
    # also hold entries from other tests' scans of the same repo)
    for tag in ("c1-initial", "c2-add-fn", "c3-signature", "c4-body"):
        sha7 = fixture_repo.sha(tag)[:7]
        assert any(n.endswith(f"-{sha7}.md") for n in names), tag


def test_report_impact_line_for_leaf_change(fixture_impact_repo, tmp_path):
    cfg, conn = _idx(fixture_impact_repo, tmp_path, "i2-leaf-body")
    md = report.render_commit(conn, cfg, fixture_impact_repo.sha("i2-leaf-body"))
    assert "**Impact:**" in md
    assert "worth checking" in md
    assert "**On path from:** GET /report ->" in md


# --------------------------------------------------------------- M8 narrate


def test_narrate_disabled_by_default(fixture_repo, tmp_path):
    cfg, conn = _idx(fixture_repo, tmp_path, "c4-body")
    assert cfg.llm.enabled is False
    assert narrate.narrate(conn, cfg, fixture_repo.sha("c4-body")) is None


def test_narrate_record_is_structured_and_sourceless(fixture_repo, tmp_path):
    cfg, conn = _idx(fixture_repo, tmp_path, "c4-body")
    rec = narrate.build_record(conn, cfg, fixture_repo.sha("c4-body"))
    assert set(rec) == {"intent", "structural", "behavioral", "cosmetic_count",
                        "new_dependencies", "impact"}
    assert rec["behavioral"] == ["`clean()` body changed"]
    # the record carries facts, never source text
    blob = repr(rec)
    assert "def clean" not in blob and "return text" not in blob


# --------------------------------------------------------------- M9 digest


def test_catchup_collapses_range(fixture_repo, tmp_path):
    cfg, conn = _idx(fixture_repo, tmp_path, "c4-body")
    db.set_meta(conn, "last_reviewed_commit", fixture_repo.sha("c1-initial"))
    out = digest.catchup(conn, cfg)
    assert "3 commit(s)" in out
    assert "`save()` signature" in out
    assert "`clean()` body changed" in out


def test_catchup_nothing_to_do(fixture_repo, tmp_path):
    cfg, conn = _idx(fixture_repo, tmp_path, "c4-body")
    db.set_meta(conn, "last_reviewed_commit", fixture_repo.sha("c4-body"))
    assert "Nothing to catch up" in digest.catchup(conn, cfg)


def test_snapshot_lists_modules_and_entry_points(fixture_impact_repo, tmp_path):
    cfg, conn = _idx(fixture_impact_repo, tmp_path, "i3-sig-partial")
    out = digest.snapshot(conn, cfg)
    assert "# Snapshot @" in out
    assert "`svc/report.py`" in out
    assert "[route] GET /report" in out
    assert "[main]" in out
    assert "## Hotspots" in out
