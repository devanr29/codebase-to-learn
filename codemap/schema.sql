-- codemap SQLite schema. See CODEMAP_SPEC.md section 7.
-- SCHEMA_VERSION is tracked in codemap/db.py and mirrored into meta(schema_version).
-- Every table that varies over time is keyed by commit_sha.

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
-- keys: schema_version, last_indexed_commit, last_reviewed_commit

CREATE TABLE IF NOT EXISTS commits (
    sha        TEXT PRIMARY KEY,
    parent_sha TEXT,
    ts         INTEGER,
    author     TEXT,
    message    TEXT,
    indexed_at INTEGER
);

CREATE TABLE IF NOT EXISTS files (
    id   INTEGER PRIMARY KEY,
    path TEXT UNIQUE,
    lang TEXT,
    tier INTEGER
);

CREATE TABLE IF NOT EXISTS file_versions (
    file_id      INTEGER,
    commit_sha   TEXT,
    content_hash TEXT,
    loc          INTEGER,
    status       TEXT,   -- added|modified|deleted|unchanged
    PRIMARY KEY (file_id, commit_sha)
);

CREATE TABLE IF NOT EXISTS symbols (
    id             INTEGER PRIMARY KEY,
    file_id        INTEGER,
    key            TEXT UNIQUE,   -- relative/path.ext::Qualified.Name
    kind           TEXT,
    name           TEXT,
    qualified_name TEXT
);

CREATE TABLE IF NOT EXISTS symbol_versions (
    symbol_id  INTEGER,
    commit_sha TEXT,
    signature  TEXT,
    start_line INTEGER,
    end_line   INTEGER,
    body_hash  TEXT,          -- normalized, see semdiff / normalize
    decorators TEXT,
    docstring  TEXT,
    PRIMARY KEY (symbol_id, commit_sha)
);

CREATE TABLE IF NOT EXISTS refs (
    id               INTEGER PRIMARY KEY,
    commit_sha       TEXT,
    from_symbol_id   INTEGER,
    target_name      TEXT,
    target_symbol_id INTEGER,   -- NULL when unresolved
    resolved         INTEGER,
    tier             INTEGER,
    line             INTEGER
);

CREATE TABLE IF NOT EXISTS imports (
    id               INTEGER PRIMARY KEY,
    commit_sha       TEXT,
    file_id          INTEGER,
    raw              TEXT,
    resolved_file_id INTEGER,
    external         INTEGER,
    line             INTEGER
);

CREATE TABLE IF NOT EXISTS entry_points (
    id         INTEGER PRIMARY KEY,
    commit_sha TEXT,
    symbol_id  INTEGER,
    kind       TEXT,
    detail     TEXT
);

CREATE TABLE IF NOT EXISTS changes (
    id           INTEGER PRIMARY KEY,
    commit_sha   TEXT,
    symbol_id    INTEGER,
    file_id      INTEGER,
    change_type  TEXT,
    severity     TEXT,
    details_json TEXT
);

CREATE TABLE IF NOT EXISTS intents (
    commit_sha TEXT PRIMARY KEY,
    source     TEXT,   -- session|note|env|commit_message|inferred
    text       TEXT
);

-- Impact analysis is a hot path.
CREATE INDEX IF NOT EXISTS idx_refs_target_id   ON refs (target_symbol_id, commit_sha);
CREATE INDEX IF NOT EXISTS idx_refs_target_name ON refs (target_name, commit_sha);
CREATE INDEX IF NOT EXISTS idx_symver_commit    ON symbol_versions (commit_sha);
CREATE INDEX IF NOT EXISTS idx_symbols_file     ON symbols (file_id);
CREATE INDEX IF NOT EXISTS idx_fileversions_commit ON file_versions (commit_sha);
CREATE INDEX IF NOT EXISTS idx_changes_commit   ON changes (commit_sha);
