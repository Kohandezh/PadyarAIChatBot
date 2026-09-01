-- The company contact edits their whole profile, not one textarea — and a
-- submission that changes nothing is a confirmation, not a draft.
--
-- WHAT `old_values`/`new_values` ARE
-- ----------------------------------
-- A submission now carries several fields (title, intro text, contact block,
-- address block — see EDITABLE_FIELDS in app/services/leads.py). The review
-- queue shows a per-field diff, and an approval writes every field back, so
-- the proposal keeps BOTH sides as one JSON object keyed by column name.
-- `old_text`/`new_text` stay and keep being filled for the `text` field:
-- they are the columns the existing queue, revert and CSV paths read, and
-- rewriting them would orphan every pending row on every install.
--
-- WHAT `edit_kind` IS
-- -------------------
-- 'change'  — the normal draft: fields differ from the live row, pending.
-- 'confirm' — the contact pressed "correct as-is": auto-approved, no review
--             needed, kept as the audit trail of WHO confirmed WHAT and WHEN.
-- The lead goes to `completed` on either kind.
--
-- WHAT `origin` IS
-- ----------------
-- 'booth'    — captured by a field visitor (the existing rows; the default).
-- 'admin'    — vouched by an operator (admin_add_contact).
-- 'campaign' — created by the organizer's bulk-confirm SMS campaign so the
--              company's one-time link has a lead to hang from.

BEGIN;

ALTER TABLE app.dataset_edits ADD COLUMN IF NOT EXISTS old_values JSONB;
ALTER TABLE app.dataset_edits ADD COLUMN IF NOT EXISTS new_values JSONB;
ALTER TABLE app.dataset_edits ADD COLUMN IF NOT EXISTS edit_kind TEXT NOT NULL DEFAULT 'change';

ALTER TABLE app.company_leads ADD COLUMN IF NOT EXISTS origin TEXT NOT NULL DEFAULT 'booth';

COMMIT;
