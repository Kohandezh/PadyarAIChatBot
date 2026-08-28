-- The rolling summary of a long conversation.
--
-- WHY A SEPARATE FILE FROM 0010
-- -----------------------------
-- These two columns belong to the table 0010 created, and they were first
-- written INTO 0010. That was wrong and it would have broken the next deploy:
-- 0010 is already applied on production, and scripts/apply_migrations.py
-- records a checksum of every file it applies. Editing an applied file makes
-- it refuse to continue (exit 2), which aborts the deploy at step 4 and
-- resets the code. An applied migration is history; history gets appended to,
-- never edited.
--
-- WHY THE SUMMARY LIVES ON THE CONVERSATION ROW
-- ---------------------------------------------
-- A chat that runs long cannot keep sending every turn to the model: the
-- prompt grows without limit and the oldest turns are the least useful part
-- of it. So the old part is folded into one short paragraph and only the
-- recent turns stay word for word (app/services/conversations.update_summary).
--
-- It is one column on the conversation and not its own table because there is
-- exactly one summary per conversation and it is rewritten in place. Nothing
-- joins to it and nothing keeps its history.
--
-- `summary_upto_id` is the highest `messages.id` already folded in. It is what
-- makes the work INCREMENTAL: each refresh reads only the messages written
-- since the last one and appends them to the paragraph, instead of re-reading
-- a conversation that only gets longer.
--
-- THE SUMMARY IS CONTEXT, NEVER CONTENT. It is written by a model, so it is
-- not evidence. It is never shown to a visitor and it never reaches the one
-- call that writes prose a visitor reads. It is handed only to the selection
-- call, whose entire output is record ids the code then renders from the
-- database. See app/services/answer.py's header for the rule.
--
-- ADD COLUMN IF NOT EXISTS: an install whose 0010 was applied from the short
-- lived edited copy already has both columns, and must not fail here.

BEGIN;

ALTER TABLE app.conversations
    ADD COLUMN IF NOT EXISTS summary TEXT NOT NULL DEFAULT '';
ALTER TABLE app.conversations
    ADD COLUMN IF NOT EXISTS summary_upto_id BIGINT NOT NULL DEFAULT 0;

COMMIT;
