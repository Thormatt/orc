-- Orc v1 schema. schema_version = 1.
-- Per-workspace SQLite db at ~/.orc/workspaces/<name>/orc.db.

CREATE TABLE IF NOT EXISTS workspace (
    name           TEXT PRIMARY KEY,
    schema_version INTEGER NOT NULL,
    created_at     TEXT NOT NULL,
    embedding_model TEXT,
    corpus_version INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS evidence (
    evidence_id    TEXT PRIMARY KEY,
    source_path    TEXT NOT NULL,
    stored_path    TEXT NOT NULL,
    sha256         TEXT NOT NULL UNIQUE,
    mime_type      TEXT NOT NULL,
    title          TEXT,
    ingested_at    TEXT NOT NULL,
    corpus_version INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_evidence_sha256 ON evidence(sha256);

CREATE TABLE IF NOT EXISTS chunk (
    chunk_id       TEXT PRIMARY KEY,
    evidence_id    TEXT NOT NULL REFERENCES evidence(evidence_id) ON DELETE CASCADE,
    seq            INTEGER NOT NULL,
    text           TEXT NOT NULL,
    token_count    INTEGER NOT NULL,
    headings_path  TEXT,
    start_offset   INTEGER NOT NULL,
    end_offset     INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_chunk_evidence ON chunk(evidence_id);

CREATE VIRTUAL TABLE IF NOT EXISTS chunk_fts USING fts5(
    text,
    content='chunk',
    content_rowid='rowid',
    tokenize='porter unicode61 remove_diacritics 1'
);

CREATE TRIGGER IF NOT EXISTS chunk_ai AFTER INSERT ON chunk BEGIN
    INSERT INTO chunk_fts(rowid, text) VALUES (new.rowid, new.text);
END;

CREATE TRIGGER IF NOT EXISTS chunk_ad AFTER DELETE ON chunk BEGIN
    INSERT INTO chunk_fts(chunk_fts, rowid, text) VALUES('delete', old.rowid, old.text);
END;

CREATE TRIGGER IF NOT EXISTS chunk_au AFTER UPDATE ON chunk BEGIN
    INSERT INTO chunk_fts(chunk_fts, rowid, text) VALUES('delete', old.rowid, old.text);
    INSERT INTO chunk_fts(rowid, text) VALUES (new.rowid, new.text);
END;

-- chunk_vec is created lazily by storage/embeddings_store.py when embeddings are
-- enabled for a workspace. Schema:
--   CREATE VIRTUAL TABLE chunk_vec USING vec0(chunk_id TEXT PRIMARY KEY, embedding FLOAT[N]);

CREATE TABLE IF NOT EXISTS run (
    run_id               TEXT PRIMARY KEY,
    directive            TEXT NOT NULL,
    skill                TEXT NOT NULL,
    workspace            TEXT NOT NULL,
    corpus_version       INTEGER NOT NULL,
    started_at           TEXT NOT NULL,
    ended_at             TEXT,
    status               TEXT NOT NULL,
    model                TEXT,
    total_input_tokens   INTEGER NOT NULL DEFAULT 0,
    total_output_tokens  INTEGER NOT NULL DEFAULT 0,
    total_cache_read     INTEGER NOT NULL DEFAULT 0,
    total_cache_creation INTEGER NOT NULL DEFAULT 0,
    output_summary       TEXT,
    error_message        TEXT
);

CREATE INDEX IF NOT EXISTS idx_run_skill ON run(skill, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_run_started ON run(started_at DESC);

CREATE TABLE IF NOT EXISTS run_evidence (
    run_id     TEXT NOT NULL REFERENCES run(run_id) ON DELETE CASCADE,
    chunk_id   TEXT NOT NULL,
    role       TEXT NOT NULL,
    rank       INTEGER,
    score      REAL,
    PRIMARY KEY (run_id, chunk_id, role)
);

CREATE TABLE IF NOT EXISTS schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
