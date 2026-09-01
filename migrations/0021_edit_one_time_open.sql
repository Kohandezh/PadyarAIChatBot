-- The one-time edit link, made actually one-time — plus the session that
-- keeps the open page alive after the link itself is gone.
--
-- WHAT CHANGES
-- ------------
-- The invite now burns when the contact PRESSES the start button on the link's
-- page (POST), not when the page is merely fetched. A plain GET cannot burn
-- anything, because messengers (Telegram, WhatsApp) prefetch URLs server-side
-- before the human taps; a link that died on GET was a link the contact never
-- had. The page served on GET is a shell with no data; the burn and the data
-- both arrive with the button press.
--
-- WHAT THE SESSION IS FOR
-- -----------------------
-- Once the invite is burned, the open page still needs credentials to load its
-- state and to submit. `edit_sessions` carries that window forward in an
-- HttpOnly cookie (2 hours), exactly the arrangement migrations/0005 shipped
-- and 0006 removed when the burn moved to the submit. The burn moved back —
-- to the button — so the table comes back with it.
--
-- `edit_invites.opened_at` records when the link was consumed; `used_at` is
-- set at the same moment now (it stays the "this invite is dead" flag every
-- existing query already reads). Existing rows are compatible: a NULL used_at
-- is a live invite either way, a set one is dead either way.
--
-- WHAT A BOOTH PHONE MEETS NOW
-- ----------------------------
-- Only the contact may consume the link. A browser carrying a /v visitor
-- cookie is refused at the button press (the invite is NOT burned), which
-- extends the old submit-time guard: the person who captured the lead may
-- not spend the company's one opening either.

BEGIN;

ALTER TABLE app.edit_invites ADD COLUMN IF NOT EXISTS opened_at TIMESTAMPTZ;

CREATE TABLE IF NOT EXISTS app.edit_sessions (
    -- Keyed HMAC of the cookie's secret. The cookie carries the raw secret;
    -- a database read cannot forge a session.
    session_hash       TEXT PRIMARY KEY,
    invite_hash        TEXT NOT NULL,
    lead_id            TEXT NOT NULL,
    dataset_id         TEXT NOT NULL,
    issued_by_session  TEXT NOT NULL DEFAULT '',
    expires_at         TIMESTAMPTZ NOT NULL,
    submitted_at       TIMESTAMPTZ,
    ip                 TEXT NOT NULL DEFAULT '',
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_edit_sessions_expiry ON app.edit_sessions(expires_at);

COMMIT;
