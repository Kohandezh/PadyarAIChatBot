-- Exhibition lead capture: field visitors, company contacts, one-time edit
-- invites, and the review queue that stands between a stranger's text and the
-- answer the chatbot gives the public.
--
-- WHY THESE TABLES AND NOT `otp_challenges`
-- -----------------------------------------
-- The visitor profile fields live on `otp_challenges` today (first_name,
-- last_name, job, position, interests). That table is keyed by a CHALLENGE and
-- built to expire — the row's whole purpose is to stop being valid after two
-- minutes. A lead has to outlive the code that proved it, be counted months
-- later, and be attributed to the visitor who captured it. Different lifetime,
-- different key, different table.
--
-- WHY AN INVITE AND A SESSION, NOT ONE TOKEN
-- ------------------------------------------
-- The invite must die the first time it is opened. But an invite that dies on
-- GET also kills the POST that follows it, so the contact would read the form
-- and be refused when they saved. `edit_invites` burns; `edit_sessions` carries
-- the same two hours forward in a cookie. Re-opening the link fails; finishing
-- the form does not.
--
-- WHY `dataset_edits` AND NOT A DIRECT WRITE
-- ------------------------------------------
-- `dataset.text` is what the chatbot says out loud. A row here is a proposal;
-- an administrator turns it into an answer. Without this table, whoever holds
-- an invite writes the product's public answers.
--
-- The application also creates these tables on demand (app/services/leads.py,
-- ensure_tables()) so the module stays copy-deployable, exactly as the OTP
-- module does. This file is the PostgreSQL-native version: real timestamps,
-- real booleans, real foreign keys.

BEGIN;

CREATE TABLE IF NOT EXISTS app.lead_visitors (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL DEFAULT '',
    -- The secret in the visitor's personal link. Stored readable on purpose:
    -- an operator must be able to re-show a visitor their own QR after a lost
    -- phone, and `active` is what actually revokes access.
    code        TEXT NOT NULL,
    active      BOOLEAN NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_visitor_code ON app.lead_visitors(code);

CREATE TABLE IF NOT EXISTS app.company_leads (
    id            TEXT PRIMARY KEY,
    dataset_id    TEXT NOT NULL,
    -- Denormalised on purpose: the name as it was AT CAPTURE. If the company
    -- row is later renamed or deleted, the lead still says who was signed up.
    company_name  TEXT NOT NULL DEFAULT '',
    visitor_id    TEXT NOT NULL DEFAULT '',
    first_name    TEXT NOT NULL DEFAULT '',
    last_name     TEXT NOT NULL DEFAULT '',
    position      TEXT NOT NULL DEFAULT '',
    -- Raw, because contacting these companies after the exhibition is the
    -- entire point. `phone_hash` is a keyed HMAC (same key as the OTP codes)
    -- and is what duplicate detection compares, so that path never needs the
    -- plaintext. See docs/engineering/SECURITY_MODEL.md.
    phone         TEXT NOT NULL DEFAULT '',
    phone_hash    TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'submitted',
    challenge_id  TEXT NOT NULL DEFAULT '',
    -- Flagged, never blocked. The first VERIFIED registration of a number
    -- wins; the rest are marked so the counts can ignore them.
    is_duplicate  BOOLEAN NOT NULL DEFAULT FALSE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    verified_at   TIMESTAMPTZ,
    ip            TEXT NOT NULL DEFAULT '',
    user_agent    TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_leads_visitor ON app.company_leads(visitor_id);
CREATE INDEX IF NOT EXISTS ix_leads_phone   ON app.company_leads(phone_hash);
CREATE INDEX IF NOT EXISTS ix_leads_dataset ON app.company_leads(dataset_id);

CREATE TABLE IF NOT EXISTS app.edit_invites (
    -- Keyed HMAC of the token. The raw token is returned to the caller once,
    -- rendered as a QR, and never written down: a database read cannot forge
    -- an invite.
    token_hash  TEXT PRIMARY KEY,
    lead_id     TEXT NOT NULL,
    dataset_id  TEXT NOT NULL,
    expires_at  TIMESTAMPTZ NOT NULL,
    used_at     TIMESTAMPTZ,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_invites_lead ON app.edit_invites(lead_id);

CREATE TABLE IF NOT EXISTS app.edit_sessions (
    id          TEXT PRIMARY KEY,
    lead_id     TEXT NOT NULL,
    dataset_id  TEXT NOT NULL,
    expires_at  TIMESTAMPTZ NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_edit_sessions_expiry ON app.edit_sessions(expires_at);

CREATE TABLE IF NOT EXISTS app.dataset_edits (
    id           TEXT PRIMARY KEY,
    dataset_id   TEXT NOT NULL,
    lead_id      TEXT NOT NULL DEFAULT '',
    -- The live text at the moment of submission, kept so a reviewer sees the
    -- change rather than only the replacement, and so an approval is undoable.
    old_text     TEXT NOT NULL DEFAULT '',
    new_text     TEXT NOT NULL DEFAULT '',
    status       TEXT NOT NULL DEFAULT 'pending',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    reviewed_at  TIMESTAMPTZ,
    reviewed_by  TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_edits_status ON app.dataset_edits(status);

COMMIT;
