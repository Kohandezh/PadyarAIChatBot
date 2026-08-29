-- Companies get their own table.
--
-- See docs/features/companies-own-table/RESEARCH.md for the full survey
-- (ADR-021). One paragraph here:
--
-- A `dataset` row USED TO BE a company when `company_profiles` held a row
-- keyed to the same id — "is this a company?" was a JOIN, and every reader
-- had to remember to subtract companies before doing anything else. Nobody
-- did, reliably: on the INOTEX install 168 of 222 `dataset` rows were
-- exhibitor companies, so the retrieval index, the BM25 corpus and the
-- intent classifier were all built over a corpus that was three-quarters
-- company rows — the root cause of confident-wrong answers where a question
-- about a topic matched an unrelated exhibitor. This migration moves what a
-- company IS (today's `dataset` row) and what the organizer KNOWS about it
-- (today's `company_profiles` row) into one `app.companies` table, and
-- companies leave `dataset` entirely.
--
-- THE IDS DO NOT CHANGE. A company keeps the id it has today; only the table
-- holding that id changes. `questions.dataset_id`, `company_leads.dataset_id`,
-- `dataset_edits.dataset_id` and `edit_invites.dataset_id` all keep pointing
-- at the same value — they are TEXT columns, not foreign keys, so nothing
-- else in this migration has to touch them. (Renaming those columns to
-- `company_id` is a deliberate follow-up, not this change — see the doc.)
--
-- ORDER, AND WHY THE COUNT CHECK IS NOT OPTIONAL
-- -----------------------------------------------
-- 1. Create `app.companies`.
-- 2. Copy every row that is BOTH a dataset row and a company_profiles row
--    (the join is the current, authoritative definition of "is a company").
-- 3. Verify every company_profiles row made it across BEFORE deleting
--    anything. A silently incomplete INSERT (a stray NULL, a type mismatch)
--    followed by the DELETE below would permanently drop the dataset half of
--    whichever companies did not copy, with no error and no way back short of
--    a restore.
-- 4. Only now delete the moved rows from `dataset` and drop
--    `company_profiles` — it is empty of anything not already in `companies`.
--
-- There is no downgrade. Rolling back means restoring a backup
-- (app/services/pg_backup.py). Take one before running this.

BEGIN;

CREATE TABLE IF NOT EXISTS app.companies (
    -- From today's `dataset` row — what the PUBLIC reads.
    id                TEXT PRIMARY KEY,
    title             TEXT NOT NULL DEFAULT '',
    title_en          TEXT NOT NULL DEFAULT '',
    text              TEXT NOT NULL DEFAULT '',
    text_en           TEXT NOT NULL DEFAULT '',
    video_url         TEXT NOT NULL DEFAULT '',
    position          INTEGER,
    -- From today's `company_profiles` row — what the ORGANIZER knows.
    contact_name      TEXT NOT NULL DEFAULT '',
    contact_position  TEXT NOT NULL DEFAULT '',
    contact_mobile    TEXT NOT NULL DEFAULT '',
    email             TEXT NOT NULL DEFAULT '',
    website           TEXT NOT NULL DEFAULT '',
    company_phone     TEXT NOT NULL DEFAULT '',
    fax               TEXT NOT NULL DEFAULT '',
    address           TEXT NOT NULL DEFAULT '',
    address_en        TEXT NOT NULL DEFAULT '',
    province          TEXT NOT NULL DEFAULT '',
    company_type      TEXT NOT NULL DEFAULT '',
    org_stage         TEXT NOT NULL DEFAULT '',
    activity_field    TEXT NOT NULL DEFAULT '',
    participation     TEXT NOT NULL DEFAULT '',
    notes             TEXT NOT NULL DEFAULT '',
    source            TEXT NOT NULL DEFAULT 'import',
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Copy first. Nothing is deleted or dropped until the count check below
-- passes.
INSERT INTO app.companies (
    id, title, title_en, text, text_en, video_url, position,
    contact_name, contact_position, contact_mobile, email, website,
    company_phone, fax, address, address_en, province, company_type,
    org_stage, activity_field, participation, notes, source,
    created_at, updated_at
)
SELECT
    d.id, d.title, d.title_en, d.text, d.text_en, d.video_url, d.position,
    p.contact_name, p.contact_position, p.contact_mobile, p.email, p.website,
    p.company_phone, p.fax, p.address, p.address_en, p.province, p.company_type,
    p.org_stage, p.activity_field, p.participation, p.notes, p.source,
    p.created_at, p.updated_at
FROM app.dataset d
JOIN app.company_profiles p ON p.dataset_id = d.id;

-- Every company_profiles row must have produced exactly one companies row.
-- A short count here means the INSERT above silently dropped rows (it
-- cannot: dataset_id is company_profiles' primary key and the join can only
-- ever match a dataset row once), or a JOIN mismatch (a company_profiles row
-- whose dataset_id no longer names a dataset row — orphaned data, not a copy
-- bug). Either way, RAISE EXCEPTION aborts the whole transaction: nothing
-- below this point runs, and `dataset` / `company_profiles` are untouched.
DO $$
DECLARE
    profile_count INTEGER;
    company_count INTEGER;
BEGIN
    SELECT COUNT(*) INTO profile_count FROM app.company_profiles;
    SELECT COUNT(*) INTO company_count FROM app.companies;
    IF company_count <> profile_count THEN
        RAISE EXCEPTION
            'companies migration: copied % of % company_profiles rows into '
            'app.companies -- aborting before dataset cleanup would delete '
            'rows that were never copied. Investigate before re-running.',
            company_count, profile_count;
    END IF;
END $$;

-- Companies leave `dataset`. Safe now: every one of them exists in
-- `app.companies`, verified above.
DELETE FROM app.dataset WHERE id IN (SELECT id FROM app.companies);

-- Nothing left in it that isn't already in `app.companies`.
DROP TABLE app.company_profiles;

COMMIT;
