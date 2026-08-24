-- The reviewer writes down WHY, and the contact gets to read it.
--
-- WHAT WAS BROKEN
-- ---------------
-- `dataset_edits` recorded `reviewed_by` and `reviewed_at` and nothing else.
-- So a rejection reached a company manager as "your text was not approved",
-- they opened their page, saw the same words they had written, and had nothing
-- to act on. The likely next move is to send the identical text again, which
-- rejects again. The loop has no exit that does not involve a phone call.
--
-- WHY ONE COLUMN AND NOT A TABLE
-- ------------------------------
-- A review happens once per edit row and is never amended: re-reviewing an
-- already-reviewed edit is refused (SPEC REQ-027), and a corrected text is a
-- NEW row, not a second verdict on the old one. So the note is an attribute of
-- the review that is already stored here beside `reviewed_by` and
-- `reviewed_at`, and a second table would only add a join.
--
-- WHY IT IS NOT NOT-NULL-WITHOUT-DEFAULT
-- --------------------------------------
-- Approvals carry no note and never will: an approved text appears on the
-- chatbot, which is the notification (SPEC REQ-025). Every row written before
-- today also has none. `DEFAULT ''` is what makes "no note" one value instead
-- of two, so nothing downstream has to distinguish NULL from empty before
-- deciding whether there is a reason to show.
--
-- The requirement that a REJECTION carries one is enforced in
-- app/services/leads.py, not here: it is a rule about one verdict, and a
-- CHECK constraint on the column would also refuse every historical rejection
-- this migration is carrying forward.

BEGIN;

ALTER TABLE app.dataset_edits ADD COLUMN IF NOT EXISTS review_note TEXT NOT NULL DEFAULT '';

COMMIT;
