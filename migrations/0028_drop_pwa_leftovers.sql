-- Drop the PWA-only leftovers: visitors.visitor_settings (0017) and
-- messages.feedback (0025). Their only readers and writers were the pwa_api
-- module, removed in this same change. Nothing under app/ references either
-- column any more.
--
-- WHAT THIS DESTROYS: any per-visitor PWA client state (calendar picks,
-- contact connections, language) and any recorded thumbs up/down. After the
-- pwa_api removal none of it is reachable, so this is garbage collection,
-- not a feature change. There is no downgrade path: rolling back means
-- restoring a backup (app/services/pg_backup.py).
--
-- Take a backup before running this.

ALTER TABLE app.visitors DROP COLUMN IF EXISTS visitor_settings;
ALTER TABLE app.messages DROP COLUMN IF EXISTS feedback;
