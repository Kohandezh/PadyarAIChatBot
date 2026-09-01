-- The organizer's bulk-confirm campaign: one SMS, every company with a
-- mobile on file, each with its own one-time link.
--
-- WHY A CAMPAIGN ROW AND NOT JUST THE MESSAGES
-- --------------------------------------------
-- The report an operator needs is per CAMPAIGN: "we asked 84 companies, 61
-- delivered, 9 skipped because their draft was already pending, 3 failed,
-- 11 still queued". `sms_messages` (migrations/0023) holds the per-company
-- truth including delivery; this row holds the launch — who started it, with
-- what text, for how many — and the counters that make the report one read.
--
-- STATUS
-- ------
-- running - the background loop is still sending (paced, ~1/second).
-- done    - every audience company got its verdict (sent/skipped/failed row).
-- stopped - halted mid-way: the daily budget ran out or the sender line
--           refused links (Asanak 1014). The already-sent messages stand;
--           the operator restarts by launching a fresh campaign later.
--
-- The per-company verdicts live on sms_messages rows with
-- campaign_id = this campaign, kind = 'campaign': 'queued'/'delivered' from
-- the gateway, 'skipped' for a pending-draft company, 'send_failed' for a
-- refusal. One table is the whole report; there is no campaign_items table
-- to keep in step.

BEGIN;

CREATE TABLE IF NOT EXISTS app.sms_campaigns (
    id            TEXT PRIMARY KEY,
    text_template TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'running',
    audience      INTEGER NOT NULL DEFAULT 0,
    sent          INTEGER NOT NULL DEFAULT 0,
    skipped       INTEGER NOT NULL DEFAULT 0,
    failed        INTEGER NOT NULL DEFAULT 0,
    stop_reason   TEXT NOT NULL DEFAULT '',
    created_by    TEXT NOT NULL DEFAULT '',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at   TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS ix_sms_campaigns_created ON app.sms_campaigns(created_at);

COMMIT;
