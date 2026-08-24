-- Security hardening pass (worktree: fix/security-hardening).
--
-- Cross-worker rate limiting for the public endpoints (chat, voice
-- transcription, OTP/leads form traffic). Until now the limiter was a
-- module-level dict: one copy per uvicorn worker and a clean slate on every
-- restart, so the effective limit was N x CHAT_RATE_LIMIT (the exact bug the
-- admin login lockout was moved into app.login_attempts to fix).
--
-- Fixed-window counters, one row per bucket, updated by a single atomic
-- upsert in app/auth/security.py::_db_rate_limit. Rows self-heal: a stale row
-- is simply overwritten when its bucket's next window opens, so no pruning
-- job is needed.

BEGIN;

CREATE TABLE IF NOT EXISTS app.rate_limit_buckets (
    key          TEXT PRIMARY KEY,
    window_start TIMESTAMPTZ NOT NULL,
    count        INTEGER NOT NULL DEFAULT 0
);

COMMIT;
