-- Company profiles: the richer data the organizer already has about each
-- exhibitor, keyed 1:1 on dataset.id.
--
-- WHY A SECOND TABLE AND NOT MORE COLUMNS ON `dataset`
-- ---------------------------------------------------
-- `dataset` is the CHATBOT'S ANSWER sheet: id/title/text(+en)/video are the
-- six fields the review flow and the contact's edit page are built around,
-- and it also carries non-company entries (general exhibition Q&A). Twelve
-- operator-facing exhibitor fields would sit empty on every non-company row
-- and drag the dataset editor away from its three-second test.
--
-- WHY `company_leads` IS WRONG FOR THIS TOO
-- -----------------------------------------
-- A lead is a VERIFIED CAPTURE EVENT: OTP'd at the booth or vouched for by an
-- admin. A row there owns its company (it leaves the booth search) and is the
-- audit record of consent. Spreadsheet data is none of that — importing it as
-- leads would silently claim every company and lock the booth out of all 169
-- of them. So what we merely KNOW about a company lives here, and the first
-- real registration still creates the lead that owns it.
--
-- `dataset_id` is the PRIMARY KEY: one profile per company, upsertable by
-- import. Contact fields (name/position/mobile) are the "incomplete data" the
-- organizer holds — display-only, never a credential.

BEGIN;

CREATE TABLE IF NOT EXISTS app.company_profiles (
    dataset_id       TEXT PRIMARY KEY,
    contact_name     TEXT NOT NULL DEFAULT '',
    contact_position TEXT NOT NULL DEFAULT '',
    contact_mobile   TEXT NOT NULL DEFAULT '',
    email            TEXT NOT NULL DEFAULT '',
    website          TEXT NOT NULL DEFAULT '',
    company_phone    TEXT NOT NULL DEFAULT '',
    fax              TEXT NOT NULL DEFAULT '',
    address          TEXT NOT NULL DEFAULT '',
    address_en       TEXT NOT NULL DEFAULT '',
    province         TEXT NOT NULL DEFAULT '',
    company_type     TEXT NOT NULL DEFAULT '',
    org_stage        TEXT NOT NULL DEFAULT '',
    activity_field   TEXT NOT NULL DEFAULT '',
    participation    TEXT NOT NULL DEFAULT '',
    notes            TEXT NOT NULL DEFAULT '',
    source           TEXT NOT NULL DEFAULT 'import',
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMIT;
