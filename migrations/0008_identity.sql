-- The identity layer: an account, a session, and an explicit grant of
-- ownership over one row of the knowledge base.
--
-- WHY AN ACCOUNT AND NOT THE PHONE NUMBER
-- ---------------------------------------
-- The obvious design was to hang ownership off `company_leads.phone_hash`. It
-- was rejected. A phone number proves one thing at one moment: whoever held
-- that handset was willing to read six digits out loud. A recycled SIM hands
-- the number, and with it every row it owned, to a stranger months later. An
-- employee who leaves and keeps their number keeps the company's public
-- answer, and that is the common case rather than the edge one. So `users.id`
-- is the identity, the number is a factor that binds to it, and ownership is a
-- separate grant that expires and can be revoked without touching either.
--
-- WHY `phone_hash_key_version` EXISTS BEFORE IT IS NEEDED
-- ------------------------------------------------------
-- `phone_hash` is a keyed HMAC. Rotating that key means rehashing every row,
-- and a table that cannot say which key a value was computed under cannot be
-- rehashed in halves, so the rotation can never be done online. The column is
-- written from the very first INSERT, when there is nothing to backfill.
--
-- WHY `dataset_owners.status` AND `expires_at`
-- --------------------------------------------
-- Neither is in PRD 5.2 and both are load-bearing. `status` carries the rule
-- that capture at a booth never silently escalates an EXISTING account: that
-- grant lands `pending` and only the holder, from a session they started
-- themselves, turns it into `active`. Without it the attack runs backwards
-- (sign up with a number, have a colleague capture it at the target booth, own
-- the target). `expires_at` carries the rule that ownership ends with the
-- exhibition and renewing it takes a fresh admin decision.
--
-- The application also creates these tables on demand (app/services/identity.py,
-- ensure_tables()), the same way the leads and OTP modules do, so an install
-- without the module never grows them. This file is the PostgreSQL-native
-- version: real timestamps and real foreign keys.
--
-- WHAT THIS DESTROYS
-- ------------------
-- Nothing. Three new tables, no column dropped, no value rewritten. There is
-- still no downgrade (REL-006): removing them again means restoring a backup
-- (app/services/pg_backup.py), because anything written into them between the
-- two points is gone with the DROP.

BEGIN;

CREATE TABLE IF NOT EXISTS app.users (
    id                     TEXT PRIMARY KEY,
    -- Raw, for the same reason `company_leads.phone` is raw: reaching these
    -- people after the exhibition is the point of collecting them at all. It
    -- is also what makes a key rotation possible, since the hash can be
    -- recomputed.
    phone                  TEXT NOT NULL DEFAULT '',
    -- The lookup key, and the only one login ever searches on. UNIQUE is what
    -- makes `INSERT ... ON CONFLICT` a race-free find-or-create: two people
    -- registering the same contact in the same second produce ONE account.
    phone_hash             TEXT NOT NULL UNIQUE,
    phone_hash_key_version INTEGER NOT NULL DEFAULT 1,
    first_name             TEXT NOT NULL DEFAULT '',
    last_name              TEXT NOT NULL DEFAULT '',
    position               TEXT NOT NULL DEFAULT '',
    job                    TEXT NOT NULL DEFAULT '',
    interests              TEXT NOT NULL DEFAULT '',
    -- 'active' or 'blocked'. Blocking also DELETEs the account's sessions
    -- (SEC-006): a flag alone only works where somebody remembered to read it.
    status                 TEXT NOT NULL DEFAULT 'active',
    -- Where the account came from: 'login', 'booth', 'chat'. Read by nothing
    -- that decides access; it is there so an operator can tell an account that
    -- someone created for themselves from one a booth created for them.
    source                 TEXT NOT NULL DEFAULT '',
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_login_at          TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS app.user_sessions (
    -- The whole cookie value, and it names nothing else. Shaped like
    -- app.admin_sessions and app.lead_visitor_sessions for the same reason:
    -- the expiry that matters is the one the SERVER reads on every request.
    token      TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL REFERENCES app.users(id) ON DELETE CASCADE,
    -- Two hours, fixed. It does not slide the way a visitor's does: a visitor
    -- works a whole exhibition day on one phone, while a contact signs in,
    -- fixes one paragraph and leaves.
    expiry     TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_user_sessions_user   ON app.user_sessions(user_id);
CREATE INDEX IF NOT EXISTS ix_user_sessions_expiry ON app.user_sessions(expiry);

CREATE TABLE IF NOT EXISTS app.dataset_owners (
    id         TEXT PRIMARY KEY,
    -- No foreign key to `dataset`: a company row can be deleted or restored by
    -- the reset script and the seed path, and a grant that blocks that is a
    -- grant that turns into an operations problem. A grant pointing at a row
    -- that no longer exists opens nothing, because every read joins `dataset`.
    dataset_id TEXT NOT NULL,
    user_id    TEXT NOT NULL REFERENCES app.users(id) ON DELETE CASCADE,
    -- The admin username, or 'booth' when a verified capture created it.
    granted_by TEXT NOT NULL DEFAULT '',
    granted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- The end of the exhibition. Extending it is a fresh admin decision, not
    -- something the passage of time does on its own (SEC-012).
    expires_at TIMESTAMPTZ,
    -- 'pending' until the holder accepts it, then 'active'.
    status     TEXT NOT NULL DEFAULT 'pending',
    -- Revoked, never deleted: who was allowed to speak for a company, and
    -- until when, is a question that gets asked after the show.
    revoked_at TIMESTAMPTZ,
    -- One grant per (company, person). The rule that a company has only ONE
    -- live owner is a different constraint and is enforced in code, because it
    -- has to allow the revoked and expired rows this one keeps.
    UNIQUE (dataset_id, user_id)
);
CREATE INDEX IF NOT EXISTS ix_dataset_owners_dataset ON app.dataset_owners(dataset_id);
CREATE INDEX IF NOT EXISTS ix_dataset_owners_user    ON app.dataset_owners(user_id);

COMMIT;
