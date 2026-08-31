-- Visit notes from the marketing field team, beside the lead pipeline.
--
-- WHY
-- ---
-- The booth flow (app/routers/leads.py /v panel) records a LEAD: an
-- OTP-verified contact plus the edit invite. But what the field team
-- actually observes at the booth is richer and looser than that —
-- «بشدت مشتاق همکاری با شرکت کهن سیستم فردا هستند» has no column in
-- company_leads, and a contact who will not do OTP on the spot had no
-- home at all. That observation was landing in the marketing WhatsApp
-- group and dying there (measured 2026-08-31, elecomp install).
--
-- marketing_notes is that home: an append-only timeline of what the
-- field agent saw, per company, with an optional UNVERIFIED contact
-- block. It never creates or claims a lead (company_leads' ownership
-- rule is untouched) — the organizer reads a note and formalizes it via
-- admin_add_contact when the contact is real.
CREATE TABLE IF NOT EXISTS app.marketing_notes (
    id               TEXT PRIMARY KEY,
    dataset_id       TEXT NOT NULL,
    company_name     TEXT NOT NULL DEFAULT '',
    visitor_id       TEXT NOT NULL DEFAULT '',
    visitor_name     TEXT NOT NULL DEFAULT '',
    warmth           TEXT NOT NULL DEFAULT 'medium',   -- low | medium | high
    note             TEXT NOT NULL DEFAULT '',
    contact_name     TEXT NOT NULL DEFAULT '',
    contact_position TEXT NOT NULL DEFAULT '',
    contact_phone    TEXT NOT NULL DEFAULT '',          -- UNVERIFIED, note-grade
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    ip               TEXT NOT NULL DEFAULT '',
    user_agent       TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS ix_marketing_notes_dataset ON app.marketing_notes(dataset_id);
CREATE INDEX IF NOT EXISTS ix_marketing_notes_created ON app.marketing_notes(created_at DESC);
