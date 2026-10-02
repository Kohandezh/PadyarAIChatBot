-- Knowledge ingestion: an admin gives a file or a web page, the server cuts it
-- into proposals, and nothing reaches `dataset` until a human approves one
-- (docs/features/knowledge-ingestion/SPEC.md, section 7; ADR-023).
--
-- WHY TWO TABLES AND NO FOREIGN KEY
-- ---------------------------------
-- ingest_jobs is one uploaded file or fetched page and its state machine.
-- ingest_proposals is one chunk of it, reviewed on its own. They are joined by
-- job_id only, like questions.dataset_id: an approved proposal becomes an
-- ordinary `dataset` row that does not depend on either table, so the 30-day
-- purge of old jobs (REQ-041) never touches live knowledge.
--
-- THE TWO PARTIAL UNIQUE INDEXES ARE THE CONTROLS, NOT A SELECT
-- -------------------------------------------------------------
-- ux_ingest_jobs_active_hash: the same bytes cannot have two active jobs; a
-- second upload hits the index and gets the first job back (REQ-027).
-- ux_ingest_jobs_one_proposing: at most one job per install talks to the model
-- at a time, and `cancelling` still holds the slot because a call may be in
-- flight (REQ-037, REQ-039). Three web workers run at once, so a SELECT before
-- the UPDATE would race; the index cannot. Both behave the same on SQLite and
-- PostgreSQL 16 (measured in the SPEC, section 7).
--
-- WHAT THIS DESTROYS: nothing. Additive only. An install without the ingest
-- module gets two empty tables.

BEGIN;

CREATE TABLE IF NOT EXISTS app.ingest_jobs (
    id            TEXT PRIMARY KEY,
    source_kind   TEXT NOT NULL,
    source_name   TEXT NOT NULL,
    content_hash  TEXT NOT NULL,
    byte_size     INTEGER NOT NULL,
    format        TEXT NOT NULL DEFAULT '',
    encoding_note TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL,
    error_code    TEXT NOT NULL DEFAULT '',
    chunk_count   INTEGER NOT NULL DEFAULT 0,
    ai_stopped_at INTEGER,
    tmp_path      TEXT NOT NULL DEFAULT '',
    created_by    TEXT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    heartbeat_at  TIMESTAMPTZ,
    finished_at   TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS app.ingest_proposals (
    id            TEXT PRIMARY KEY,
    job_id        TEXT NOT NULL,
    seq           INTEGER NOT NULL,
    source_text   TEXT NOT NULL,
    heading       TEXT NOT NULL DEFAULT '',
    title         TEXT NOT NULL,
    text          TEXT NOT NULL,
    title_source  TEXT NOT NULL,
    questions     TEXT NOT NULL DEFAULT '[]',
    synonyms      TEXT NOT NULL DEFAULT '[]',
    ai_state      TEXT NOT NULL,
    similar_kind  TEXT NOT NULL DEFAULT '',
    similar_to    TEXT NOT NULL DEFAULT '',
    same_title_as TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'pending',
    reject_reason TEXT NOT NULL DEFAULT '',
    edited        INTEGER NOT NULL DEFAULT 0,
    seen_at       TIMESTAMPTZ,
    seen_by       TEXT NOT NULL DEFAULT '',
    reviewed_at   TIMESTAMPTZ,
    reviewed_by   TEXT NOT NULL DEFAULT '',
    dataset_id    TEXT NOT NULL DEFAULT '',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_ingest_proposals_job_seq ON app.ingest_proposals (job_id, seq);
CREATE INDEX IF NOT EXISTS ix_ingest_proposals_job_status ON app.ingest_proposals (job_id, status);
CREATE INDEX IF NOT EXISTS ix_ingest_jobs_created ON app.ingest_jobs (created_at);
CREATE UNIQUE INDEX IF NOT EXISTS ux_ingest_jobs_active_hash ON app.ingest_jobs (content_hash)
    WHERE status IN ('queued', 'extracting', 'extracted', 'proposing', 'cancelling', 'ready');
CREATE UNIQUE INDEX IF NOT EXISTS ux_ingest_jobs_one_proposing ON app.ingest_jobs ((1))
    WHERE status IN ('proposing', 'cancelling');

COMMIT;
