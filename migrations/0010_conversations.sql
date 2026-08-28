-- Visitors, conversations and messages: the transcript moves out of the
-- browser and into the database.
--
-- WHY THESE TABLES
-- ----------------
-- The product's most valuable data lives in localStorage today.
-- `static/chat/core.js` keeps the whole transcript under 'inotex_chat_history'
-- and `static/companion/registration.js` keeps the registered person under
-- 'inotex-visitor'. Clear a browser and the owner has lost who visited, what
-- they asked and what the bot answered. A kiosk browser gets cleared often.
--
-- The registration profile (first_name, last_name, job, position, interests)
-- is written to `otp_challenges`, which is keyed by a CHALLENGE and BUILT TO
-- EXPIRE — migrations/0005_leads.sql already says this about the same five
-- fields, and promoted them for company leads for the same reason. This
-- promotes them for the visitor.
--
-- WHAT HAPPENS TO `chat_logs`: IT STAYS, AND BOTH ARE WRITTEN
-- ----------------------------------------------------------
-- 0009's header argued against a second table that would hold the same two
-- strings, and that argument was right for what 0009 was doing: it needed
-- three fields, not a schema. This is a different need, and the duplication
-- is the price of not breaking the panel that is live today.
--
-- `chat_logs` is one flat row per ANSWERED TURN and it is what
-- app/routers/admin.py already reads five times over: the totals and the
-- 7-day chart (`/admin/api/stats`), the tier mix, the weak-answer report
-- (`/admin/api/low_confidence`), the CSV export, and the three reset buttons.
-- app/db/queries.py reads it twice more for the pick tier
-- (`recent_turns`, `last_offer_state`). Making `messages` the only write
-- target empties every one of those on the day it ships.
--
-- `messages` is one row per MESSAGE, related to a conversation, which is
-- related to a person. That is what `chat_logs` cannot become by adding
-- columns: it has no row for a question that was never answered, no role, no
-- visitor, and no conversation record to hang `started_at`, `ip` or
-- `user_agent` on. "Read this whole session back" and "who was this" are not
-- questions a flat per-turn analytics table answers.
--
-- So the two coexist on purpose and with different jobs: `chat_logs` is the
-- turn-level telemetry the dashboard aggregates, `messages` is the durable
-- transcript. The chat router writes both, through one service call each
-- (app/services/conversations.py). If the dashboard is ever ported onto
-- `messages`, `chat_logs` can be dropped in a later migration — that is a
-- separate change, with the panel edited in the same commit.
--
-- WHY `answers` IS ONE JSONB COLUMN
-- ---------------------------------
-- The chatbot will collect more from a visitor than the registration form
-- asks. One mechanism was picked and this is it: a single JSONB bag on the
-- visitor row. A column per question means a migration per question, which is
-- the thing this is meant to avoid. A key/value `visitor_answers` table means
-- a join and a second retention story for a handful of short strings per
-- person. The five fields the registration form already collects stay real
-- columns because they are filtered and exported on every screen; anything
-- the bot picks up later goes in the bag. It defaults to '{}' and never NULL,
-- so a reader never has to test for both.
--
-- WHY `visitor_id` IS '' AND NOT NULL
-- -----------------------------------
-- Most people at a booth kiosk never register. Their conversations are the
-- bulk of the data and losing them would defeat the purpose, so a
-- conversation starts with no visitor and gets one if and when the person
-- registers mid-session. '' rather than NULL is the convention this codebase
-- already uses for exactly this (`company_leads.visitor_id`), and it is why
-- there is no foreign key here: '' is not a visitor id.
--
-- WHY MESSAGES ARE ORDERED BY id
-- ------------------------------
-- Same reason as 0009. SQLite's CURRENT_TIMESTAMP has one-second resolution,
-- so two messages written in the same second tie on created_at and the order
-- becomes whatever the planner felt like. `id` is monotonic on both backends,
-- so the conversation index is keyed (conversation_id, id).
--
-- RETENTION
-- ---------
-- `chat_log_retention_days` (0 = keep forever) is the one dial, and
-- app/db/queries.py purge_chat_logs() now prunes `messages` and
-- `conversations` on the same rule and in the same cycle. It does NOT prune
-- `visitors`. A visitor row is a registration the person gave on purpose and
-- is the lead data the product exists to capture; the dial is about
-- unredacted conversation text, and it already leaves `company_leads` alone
-- for the same reason. Deleting a conversation leaves its visitor.
--
-- WHAT THIS DESTROYS: nothing. Three new tables, no existing table touched,
-- no existing row changed. There is no downgrade path — rolling back means
-- restoring a backup (app/services/pg_backup.py).
--
-- Take a backup before running this.

BEGIN;

-- One durable row per registered person.
CREATE TABLE IF NOT EXISTS app.visitors (
    id            TEXT PRIMARY KEY,
    first_name    TEXT NOT NULL DEFAULT '',
    last_name     TEXT NOT NULL DEFAULT '',
    -- Raw, because contacting these people after the exhibition is the point.
    -- `phone_hash` is the keyed HMAC that migrations/0005_leads.sql already
    -- established as the dedupe key (app/services/leads.py _digest(), same key
    -- as the OTP codes), so duplicate detection never needs the plaintext.
    phone         TEXT NOT NULL DEFAULT '',
    phone_hash    TEXT NOT NULL DEFAULT '',
    job           TEXT NOT NULL DEFAULT '',
    position      TEXT NOT NULL DEFAULT '',
    interests     TEXT NOT NULL DEFAULT '',
    -- Everything the bot learns later. See the header.
    answers       JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- Partial, because a visitor captured without a phone has nothing to dedupe
-- on and '' would collide with every other such visitor.
CREATE UNIQUE INDEX IF NOT EXISTS ux_visitors_phone_hash
    ON app.visitors(phone_hash) WHERE phone_hash <> '';
CREATE INDEX IF NOT EXISTS ix_visitors_created ON app.visitors(created_at DESC);

-- One row per chat session, keyed by the padyar_conv cookie the chat router
-- has been setting since 2026-08-27 (app/routers/chat.py).
CREATE TABLE IF NOT EXISTS app.conversations (
    id               TEXT PRIMARY KEY,
    visitor_id       TEXT NOT NULL DEFAULT '',
    started_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_message_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    message_count    INTEGER NOT NULL DEFAULT 0,
    lang             TEXT NOT NULL DEFAULT 'fa',
    ip               TEXT NOT NULL DEFAULT '',
    user_agent       TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_conversations_started
    ON app.conversations(started_at DESC);
-- Partial: the admin's "registered people only" filter, and anonymous
-- conversations are the majority, so they are kept out of the index.
CREATE INDEX IF NOT EXISTS ix_conversations_visitor
    ON app.conversations(visitor_id) WHERE visitor_id <> '';

-- One row per turn. The diagnostic columns are the ones chat_logs records,
-- carried here so a bad answer can be read in the context that produced it.
CREATE TABLE IF NOT EXISTS app.messages (
    id               BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    conversation_id  TEXT NOT NULL
                     REFERENCES app.conversations(id) ON DELETE CASCADE,
    role             TEXT NOT NULL DEFAULT 'visitor',
    text             TEXT NOT NULL DEFAULT '',
    -- Assistant rows only. NULL confidence means "no score", which is not the
    -- same as 0.0 and must not be read as a bad answer.
    source           TEXT NOT NULL DEFAULT '',
    confidence       DOUBLE PRECISION,
    entry_id         TEXT NOT NULL DEFAULT '',
    video_url        TEXT NOT NULL DEFAULT '',
    tokens           INTEGER NOT NULL DEFAULT 0,
    cost             NUMERIC(12, 6) NOT NULL DEFAULT 0,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- The two reads that will actually happen. First: give me this conversation
-- in order — id, not created_at, see the header.
CREATE INDEX IF NOT EXISTS ix_messages_conversation
    ON app.messages(conversation_id, id);
-- Second: the recent weak answers, newest first. Partial on the assistant
-- rows because a visitor's question has no confidence to be low.
CREATE INDEX IF NOT EXISTS ix_messages_weak
    ON app.messages(created_at DESC, confidence) WHERE role = 'assistant';

COMMIT;
