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

-- chunk_vec is created lazily by storage/embeddings_store.py (ensure_chunk_vec)
-- when embeddings are enabled for a workspace. Requires the sqlite-vec extension.
-- The vector dimension N is stamped in schema_meta under 'chunk_vec_dim'. Schema:
--   CREATE VIRTUAL TABLE chunk_vec USING vec0(
--       chunk_id TEXT PRIMARY KEY, embedding FLOAT[N], corpus_version INTEGER);

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

-- Approval queue: directives produce proposals; humans accept/reject.
-- Source-of-truth for "things the runtime wants to do but won't do without a human."
-- Article 14 §5 of the EU AI Act requires two natural persons for some Annex III
-- systems; approvers_required defaults to 1 for backward compat, can be set on enqueue.
CREATE TABLE IF NOT EXISTS approval (
    approval_id        TEXT PRIMARY KEY,
    workspace          TEXT NOT NULL,
    directive          TEXT NOT NULL,
    skill              TEXT NOT NULL,
    source_run_id      TEXT NOT NULL,
    status             TEXT NOT NULL,             -- pending | approved | rejected | expired
    summary            TEXT NOT NULL,             -- one-line for list output
    payload            TEXT NOT NULL,             -- JSON: full proposal context
    proposed_action    TEXT,                      -- JSON: what would happen if accepted
    approvers_required INTEGER NOT NULL DEFAULT 1,
    created_at         TEXT NOT NULL,
    decided_at         TEXT,                      -- when status last flipped from pending
    decided_by         TEXT,                      -- the natural person whose decision flipped status
    decision_note      TEXT
);

CREATE INDEX IF NOT EXISTS idx_approval_status     ON approval(status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_approval_source_run ON approval(source_run_id);

-- Per-decision audit log. Article 14 §5 requires named natural persons; one row per
-- decision per approver. UNIQUE (approval_id, decided_by) prevents a single person
-- from voting twice on the same approval.
CREATE TABLE IF NOT EXISTS approval_decision (
    decision_id     TEXT PRIMARY KEY,
    approval_id     TEXT NOT NULL REFERENCES approval(approval_id) ON DELETE CASCADE,
    decision        TEXT NOT NULL,                -- accept | reject
    decided_by      TEXT NOT NULL,
    decided_at      TEXT NOT NULL,
    note            TEXT,
    UNIQUE (approval_id, decided_by)
);

CREATE INDEX IF NOT EXISTS idx_approval_decision_approval ON approval_decision(approval_id);

-- v2: gold set, eval runs, and tiered-verification calibration.
CREATE TABLE IF NOT EXISTS gold_claim (
    gold_id            TEXT PRIMARY KEY,
    workspace          TEXT NOT NULL,
    claim              TEXT NOT NULL,
    expected_label     TEXT NOT NULL,
    corpus_version     INTEGER NOT NULL,
    relevant_chunk_ids TEXT,
    source             TEXT NOT NULL,
    source_run_id      TEXT,
    note               TEXT,
    added_at           TEXT NOT NULL,
    added_by           TEXT
);
CREATE INDEX IF NOT EXISTS idx_gold_claim_workspace ON gold_claim(workspace);

CREATE TABLE IF NOT EXISTS eval_run (
    eval_id      TEXT PRIMARY KEY,
    workspace    TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    config_json  TEXT NOT NULL,
    metrics_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tiered_policy (
    workspace                  TEXT PRIMARY KEY,
    tier1_model                TEXT NOT NULL,
    tier2_model                TEXT NOT NULL,
    top_judge_model            TEXT,
    escalation_threshold       REAL NOT NULL,
    target                     REAL NOT NULL,
    calibrated_at              TEXT NOT NULL,
    calibrated_against_eval_id TEXT,
    n_gold                     INTEGER NOT NULL
);
