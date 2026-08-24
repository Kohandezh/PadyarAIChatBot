-- The visitor session becomes a row, and the visitor's code stops being
-- readable.
--
-- WHY A SESSION TABLE (F8)
-- -----------------------
-- The `padyar_visitor` cookie carried `lead_visitors.id` and a 12 hour
-- `max_age`, so the 12 hours were enforced by the BROWSER and by nothing else:
-- a client that kept the cookie kept access for good. Worse, the cookie value
-- was the primary key, and that key is printed in the admin UI, returned by
-- `/admin/api/leads`, and written into applog metadata. Any screenshot or log
-- line was a session takeover.
--
-- `lead_visitor_sessions` is shaped like `app.admin_sessions` and for the same
-- reasons: an opaque token in the cookie, the identity behind it, and an
-- `expiry` the SERVER reads on every request.
--
-- WHY `code_hash` (F9)
-- -------------------
-- `lead_visitors.code` was a permanent credential stored in the clear,
-- indexed, and present in every backup that `/admin/api/backups/download` can
-- hand out. The comment in 0005 justified it by an operator needing to re-show
-- a visitor their QR after a lost phone. That is the wrong answer to a lost
-- phone: re-showing leaves the lost phone working. Rotating is the answer, the
-- panel already has that button, and `edit_invites` four tables away was
-- already storing only an HMAC.
--
-- WHAT THIS DESTROYS (there is no downgrade; going back means restoring a
-- backup, see app/services/pg_backup.py and REL-006):
--
--   * `lead_visitors.code` is DROPPED with every value in it, so EVERY
--     PERSONAL LINK HANDED OUT BEFORE THIS MIGRATION STOPS WORKING. The rows
--     survive with their names and their counts; only the credential dies.
--     The codes are not carried over as hashes because they cannot be: the
--     hash is a keyed HMAC whose key lives in the application (SECRET_KEY, or
--     `settings.app_secret_key`), not in the database, so no statement in this
--     file can compute it. After running this, the operator opens the leads
--     page and presses «ساخت لینک تازه» for each visitor, then hands the new
--     link over the same way the first one was handed over. There are as many
--     visitors as there are people wearing a badge, so this is minutes of
--     work, once.
--   * Existing rows get `expires_at = now()`, which is already past. They are
--     dead twice over: an empty `code_hash` never matches a real digest
--     either. Nothing has to be cleaned up by hand.
--
-- Take a backup before running this.

BEGIN;

CREATE TABLE IF NOT EXISTS app.lead_visitor_sessions (
    -- The whole cookie value. Opaque, and nothing else in the system knows it:
    -- it names no row an admin screen prints.
    token       TEXT PRIMARY KEY,
    visitor_id  TEXT NOT NULL REFERENCES app.lead_visitors(id) ON DELETE CASCADE,
    -- Slid forward on activity, and never past the code's own expiry. A
    -- visitor works a whole exhibition day on one phone, so an idle timeout
    -- that signs them out between two booths costs a real registration.
    expiry      TIMESTAMPTZ NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_visitor_sessions_visitor ON app.lead_visitor_sessions(visitor_id);
CREATE INDEX IF NOT EXISTS ix_visitor_sessions_expiry  ON app.lead_visitor_sessions(expiry);

ALTER TABLE app.lead_visitors ADD COLUMN IF NOT EXISTS code_hash TEXT NOT NULL DEFAULT '';
-- The link is handed out before the show and is useless after it. An expiry
-- means a code lifted from an old backup is dead by the next exhibition, and
-- it gives the session its ceiling.
ALTER TABLE app.lead_visitors ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ;
UPDATE app.lead_visitors SET expires_at = now() WHERE expires_at IS NULL;

DROP INDEX IF EXISTS app.ix_visitor_code;
ALTER TABLE app.lead_visitors DROP COLUMN IF EXISTS code;
CREATE INDEX IF NOT EXISTS ix_visitor_code_hash ON app.lead_visitors(code_hash);

COMMIT;
