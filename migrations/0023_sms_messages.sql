-- Every gateway send, kept with the handle that can prove delivery.
--
-- WHY A TABLE (and not another applog row)
-- -----------------------------------------
-- A 200 from Asanak means QUEUED, not delivered — a message can sit
-- undelivered for hours behind a perfectly successful send response. Until
-- now the msgid was written to an applog row and never read again: the only
-- thread back to a message that never arrived existed for an operator willing
-- to grep logs and call the gateway by hand. This table is that thread,
-- queryable: one row per send, the msgid beside it, and a status column the
-- poller keeps current (see app/services/sms_outbox.py).
--
-- STATUS VOCABULARY
-- -----------------
-- queued   - accepted by the gateway, no final word yet.
-- delivered- the gateway's msgstatus answered success (code 6 on Asanak).
-- unknown  - no final status within the poll window (24h), or a send with no
--            msgid at all (the dev outbox has none).
-- failed   - reserved for an explicit failure word from the gateway.
--
-- `destination` is stored MASKED (applog.mask_phone): the raw number already
-- lives where the flow needs it (company_leads, otp_challenges) and this
-- table is read by the admin panel and exports.

BEGIN;

CREATE TABLE IF NOT EXISTS app.sms_messages (
    id                TEXT PRIMARY KEY,
    provider          TEXT NOT NULL DEFAULT '',
    kind              TEXT NOT NULL DEFAULT '',
    msgid             TEXT NOT NULL DEFAULT '',
    destination       TEXT NOT NULL DEFAULT '',
    reference         TEXT NOT NULL DEFAULT '',
    campaign_id       TEXT NOT NULL DEFAULT '',
    status            TEXT NOT NULL DEFAULT 'queued',
    status_detail     TEXT NOT NULL DEFAULT '',
    status_checked_at TIMESTAMPTZ,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_sms_messages_status ON app.sms_messages(status);
CREATE INDEX IF NOT EXISTS ix_sms_messages_campaign ON app.sms_messages(campaign_id);
CREATE INDEX IF NOT EXISTS ix_sms_messages_created ON app.sms_messages(created_at);

COMMIT;
