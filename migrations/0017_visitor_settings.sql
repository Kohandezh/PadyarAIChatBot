-- A JSONB scratch column for per-visitor client state the PWA owns —
-- calendar picks, contact connections, language — none of which is a fact
-- the server needs to reason about today. See docs/features/pwa-api/SPEC.md
-- REQ-013.
ALTER TABLE app.visitors
    ADD COLUMN IF NOT EXISTS visitor_settings JSONB NOT NULL DEFAULT '{}'::jsonb;
