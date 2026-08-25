-- Security hardening pass (worktree: fix/security-hardening).
--
-- Cross-worker rate limiting for the public endpoints (chat, voice
-- transcription, OTP/leads form traffic). Until now the limiter was a
-- module-level dict: one copy per uvicorn worker and a clean slate on every
-- restart, so the effective limit was N x CHAT_RATE_LIMIT (the exact bug the
-- admin login lockout was moved into app.login_attempts to fix).
--
-- SLIDING window, one row per admitted request. A fixed-window counter was
-- tried first (single atomic upsert) and rejected: it resets to zero at
-- every window boundary, which admits a 2x burst around the boundary and
-- made the boundary-crossing CI test flake. Blocked attempts are not
-- recorded, so a tripped shared-NAT bucket drains from its admitted
-- timestamps instead of staying full while the caller keeps retrying.
--
-- `ts` is a unix epoch double — the window comparison stays plain numeric
-- on every backend, with no timestamp-format handling. The allowed path
-- prunes the bucket's own expired rows, so the table holds roughly
-- (active buckets x CHAT_RATE_LIMIT) rows and needs no pruning job.

BEGIN;

CREATE TABLE IF NOT EXISTS app.rate_limit_hits (
    key TEXT NOT NULL,
    ts DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_rate_limit_hits_key_ts
    ON app.rate_limit_hits(key, ts);

COMMIT;
