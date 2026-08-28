-- Conversation memory: three columns on the table that already stores turns.
--
-- WHY THREE COLUMNS AND NOT A NEW TABLE
-- -------------------------------------
-- `chat_logs` already holds every answered turn: the visitor's question, the
-- answer text, the tier and the confidence. A second `conversation_turns`
-- table would write the same two strings a second time, need its own
-- retention, and give an operator two places to look when an answer went
-- wrong. CLAUDE.md requires a new table to justify its existence and this one
-- cannot. `conversation_id` is the padyar_conv cookie the app has been setting
-- since 2026-08-27 and nothing ever stored; `entry_id` is WHICH record
-- produced the answer (today a row cannot be traced back to it at all);
-- `offer_state` is the JSON list of records OFFERED on this turn, so the next
-- turn's "3" resolves against ids and never against re-parsed answer text.
--
-- WHY ORDER BY id AND NOT created_at
-- ----------------------------------
-- The readers sort by `id`. SQLite's CURRENT_TIMESTAMP has one-second
-- resolution, so two turns written in the same second tie and the order
-- becomes whatever the planner felt like. `id` is monotonic on both backends,
-- which is why both indexes below are keyed on (conversation_id, id DESC).
--
-- WHAT THIS DESTROYS: nothing. All three columns default to '' and every
-- existing row keeps its meaning. There is no downgrade path — rolling back
-- means restoring a backup (app/services/pg_backup.py).
--
-- Take a backup before running this.

BEGIN;

ALTER TABLE app.chat_logs ADD COLUMN IF NOT EXISTS conversation_id TEXT NOT NULL DEFAULT '';
ALTER TABLE app.chat_logs ADD COLUMN IF NOT EXISTS entry_id TEXT NOT NULL DEFAULT '';
ALTER TABLE app.chat_logs ADD COLUMN IF NOT EXISTS offer_state TEXT NOT NULL DEFAULT '';

CREATE INDEX IF NOT EXISTS ix_chat_logs_conversation ON app.chat_logs(conversation_id, id DESC);
CREATE INDEX IF NOT EXISTS ix_chat_logs_offer ON app.chat_logs(conversation_id, id DESC) WHERE offer_state <> '';

COMMIT;
