-- Three-state lead status, the duplicate-override trail, release, and the
-- consent version stamped on every registration.
--
-- WHAT THIS DESTROYS (there is no downgrade; going back means restoring a
-- backup, see app/services/pg_backup.py and REL-006):
--
--   * `company_leads.is_duplicate` is DROPPED with its values. It was a
--     derived flag keyed on the phone number, and the control it stood for now
--     lives in three places that cannot be silently ignored: an owned company
--     leaves the search list, a repeated number raises an interactive warning,
--     and every override of that warning is recorded on the row itself.
--   * `edit_sessions` is DROPPED with every row in it. The invite no longer
--     burns when the link is opened, so nothing has to carry the window
--     forward. The token in the URL is the whole credential now.
--   * The old status vocabulary is rewritten IN PLACE. After this file,
--     `submitted`, `link_opened`, `edit_submitted`, `approved` and `duplicate`
--     do not exist on this table. `approved` and `rejected` were never
--     progress: they are a review outcome and they live on
--     `dataset_edits.status`, which this file does not touch.
--
-- Take a backup before running this.

BEGIN;

ALTER TABLE app.company_leads ADD COLUMN IF NOT EXISTS duplicate_override_of TEXT;
ALTER TABLE app.company_leads ADD COLUMN IF NOT EXISTS duplicate_override_at TIMESTAMPTZ;
-- Released, not deleted: the registration stays `verified` for the history and
-- stops being the live owner of the company, which puts the company back in
-- every visitor's search.
ALTER TABLE app.company_leads ADD COLUMN IF NOT EXISTS released_at TIMESTAMPTZ;
-- What the contact was actually told at the booth. Editing the script in the
-- settings mints a new version and leaves this one alone, so a later rewording
-- never rewrites what an earlier contact agreed to.
ALTER TABLE app.company_leads ADD COLUMN IF NOT EXISTS consent_script_version TEXT NOT NULL DEFAULT 'v1';

-- The visitor session that minted the invite. That session is refused when it
-- tries to submit the edit: the person who captured the lead must not be able
-- to write the company's own answer.
ALTER TABLE app.edit_invites ADD COLUMN IF NOT EXISTS issued_by_session TEXT NOT NULL DEFAULT '';

UPDATE app.company_leads SET status = CASE status
    WHEN 'submitted'      THEN 'unverified'
    WHEN 'link_opened'    THEN 'verified'
    WHEN 'edit_submitted' THEN 'completed'
    WHEN 'approved'       THEN 'completed'
    WHEN 'duplicate'      THEN 'unverified'
    ELSE status
END;

ALTER TABLE app.company_leads ALTER COLUMN status SET DEFAULT 'unverified';
ALTER TABLE app.company_leads DROP COLUMN IF EXISTS is_duplicate;

-- The funnel, the admin list and the company search all filter on status.
CREATE INDEX IF NOT EXISTS ix_leads_status ON app.company_leads(status);

DROP TABLE IF EXISTS app.edit_sessions;

COMMIT;
