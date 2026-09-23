"""A stand-in for a codebase-memory-mcp index, for tests.

The four tables codemap reads, with the columns and types of CBM's own DDL
(`src/store/store.c`, `init_schema`), minus the generated columns and indexes
codemap never touches -- so the tests do not depend on a recent SQLite.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

DDL = """
CREATE TABLE projects (name TEXT PRIMARY KEY, indexed_at TEXT NOT NULL, root_path TEXT NOT NULL);
CREATE TABLE file_hashes (
  project TEXT NOT NULL, rel_path TEXT NOT NULL, sha256 TEXT NOT NULL,
  mtime_ns INTEGER NOT NULL DEFAULT 0, size INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (project, rel_path));
CREATE TABLE nodes (
  id INTEGER PRIMARY KEY AUTOINCREMENT, project TEXT NOT NULL, label TEXT NOT NULL,
  name TEXT NOT NULL, qualified_name TEXT NOT NULL, file_path TEXT DEFAULT '',
  start_line INTEGER DEFAULT 0, end_line INTEGER DEFAULT 0, properties TEXT DEFAULT '{}',
  UNIQUE(project, qualified_name));
CREATE TABLE edges (
  id INTEGER PRIMARY KEY AUTOINCREMENT, project TEXT NOT NULL,
  source_id INTEGER NOT NULL, target_id INTEGER NOT NULL, type TEXT NOT NULL,
  properties TEXT DEFAULT '{}', UNIQUE(source_id, target_id, type));
"""


def make_cbm_db(
    cache_dir: Path,
    root: Path | str,
    *,
    nodes: list[tuple],           # (label, name, file_path, start_line[, properties dict])
    edges: list[tuple],           # (source index, target index, type[, properties dict]) -- indexes into `nodes`
    hashes: dict[str, str],       # rel_path -> sha256 hex, as CBM recorded it
    project: str = "fake-project",
    user_version: int = 1,
    after_sql: str = "",          # e.g. an ALTER TABLE, to simulate a schema drift
) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"{project}.db"
    path.unlink(missing_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.executescript(DDL)
        conn.execute("INSERT INTO projects VALUES (?, '2026-01-01T00:00:00Z', ?)", (project, str(root)))
        for rel, sha in hashes.items():
            conn.execute("INSERT INTO file_hashes (project, rel_path, sha256) VALUES (?, ?, ?)", (project, rel, sha))
        for i, node in enumerate(nodes, start=1):
            label, name, file, line, *rest = node
            props = json.dumps(rest[0]) if rest else "{}"
            conn.execute(
                "INSERT INTO nodes (id, project, label, name, qualified_name, file_path, start_line, end_line, properties) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (i, project, label, name, f"{project}.{file}.{name}.{i}", file, line, line, props),
            )
        for src, dst, etype, *rest in edges:
            props = json.dumps(rest[0]) if rest else "{}"
            conn.execute(
                "INSERT INTO edges (project, source_id, target_id, type, properties) VALUES (?, ?, ?, ?, ?)",
                (project, src + 1, dst + 1, etype, props),
            )
        if after_sql:
            conn.executescript(after_sql)
        conn.execute(f"PRAGMA user_version = {int(user_version)}")
        conn.commit()
    finally:
        conn.close()
    return path


# ---- a ready-made index for the impact fixture repo (tests/fixtures/build_repo.py) ----
# codemap's own resolved edges there (all confident): main -> build_report,
# build_report -> fetch, build_report -> render, fetch -> db_query, report_view -> build_report

IMPACT_NODES = [  # (label, name, file, start line) -- the fixture's real symbol locations
    ("Function", "main", "svc/cli.py", 4),               # 0
    ("Function", "fetch", "svc/data.py", 1),             # 1
    ("Function", "db_query", "svc/data.py", 5),          # 2
    ("Function", "build_report", "svc/report.py", 4),    # 3
    ("Function", "render", "svc/report.py", 9),          # 4
    ("Function", "report_view", "svc/web.py", 5),        # 5
    ("File", "cli.py", "svc/cli.py", 4),                 # 6  a File node must never map to `main`
    ("Route", "/api/report", "", 0, {"method": "GET"}),  # 7
]
MAIN, FETCH, DBQ, BUILD, RENDER, VIEW = range(6)
FILE_NODE, ROUTE = 6, 7


def call_props(score, strategy="import_map"):
    return {"callee": "x", "confidence": score, "strategy": strategy, "candidates": 1}


IMPACT_EDGES = [
    (MAIN, RENDER, "CALLS", call_props(0.95)),                 # new, resolved by imports -> EXTRACTED
    (VIEW, FETCH, "CALLS", call_props(0.75, "unique_name")),   # new, by unique name -> INFERRED
    (FETCH, RENDER, "CALLS", call_props(0.55, "suffix_match")),  # below CBM's own "high" band -> ignored
    (MAIN, BUILD, "CALLS", call_props(0.95)),                  # codemap already has it -> untouched
    (FILE_NODE, RENDER, "CALLS", call_props(0.95)),            # source is a File node -> ignored
    (MAIN, ROUTE, "HTTP_CALLS", {"callee": "fetch", "url_path": "/api/report", "method": "GET"}),
    (VIEW, ROUTE, "HANDLES", {"handler": "svc.web.report_view"}),
]
