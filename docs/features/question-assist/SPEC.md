# Question Assist (question-variant suggestions)

**Slug:** `question-assist` · **Status:** Implemented · **Domain:** content/admin
**Created:** 2026-09-14

## The scenario

A knowledge entry is matched by its curated questions (Tier 0/1 of the chat
pipeline), but visitors phrase the same need many ways and nobody types ten
paraphrases by hand. The result is an entry that answers a question nobody
asks in exactly those words.

The admin is already in the dataset page, editing the entry. One button
inside the entry edit modal — **پیشنهاد سوال** — asks the model (through the
Padyar AI Wrapper, the only AI path) for question variants grounded in that
entry's own title, text and existing questions. The suggestions arrive as a
checkbox list (all checked by default), the admin unchecks what they do not
want, and **افزودن انتخاب‌شده‌ها** inserts the rest. Two clicks; nothing is
saved before the second one.

## What ships

| Piece | Where |
| ----- | ----- |
| Service | `app/services/question_assist.py` — `suggest_questions(entry_id, count=10)`, validation, `QuestionAssistUnavailable` |
| Endpoints | `POST /admin/api/dataset/{id}/suggest-questions` (admin auth, no saving) and `POST /admin/api/dataset/{id}/apply-questions` in `app/routers/dataset.py` |
| Shared insert | `_insert_question()` in `app/routers/dataset.py` — the same INSERT the single question-create endpoint uses (single-source; ADR-017) |
| UI | `templates/admin/dataset.html` (`#ds-suggest-block` in the edit modal, edit mode only — a suggestion needs a saved entry id) + `static/admin/js/dataset.js` (`suggestQuestions`, `applySuggestedQuestions`) |
| Tests | `tests/test_question_assist.py` (12) |

## The contract

- **The model suggests; the code decides** (company_autofill's contract).
  Every suggestion is validated before the admin ever sees it, in order:
  1. a string, whitespace-collapsed, **5..120 chars** — over-cap items are
     dropped whole, never truncated;
  2. **Persian** — an English suggestion survives only when the entry
     already carries an English question (checked on the existing rows:
     ASCII letters, no Persian script);
  3. **normalized-unique** against the entry's existing questions AND
     against suggestions already accepted in this batch (dedupe), using
     `normalize_persian(..., expand_synonyms=False)` — two questions that
     differ only by a synonym ARE different questions;
  4. capped at `count` (default 10).
- **Suggest saves nothing.** Only apply-questions writes, and it inserts
  through `_insert_question` — the same insert statement the single
  question-create endpoint uses — with **one reindex for the whole batch**
  (the bulk-delete precedent), so validation, write shape and reindex stay
  single-source.
- **AI-unavailable degrades gracefully:** `QuestionAssistUnavailable`
  (typed, Persian message, company_autofill pattern) → HTTP 503 with a
  Persian `detail`; the chat pipeline is never involved, nothing is
  half-written.
- Unparsable or length-truncated model output yields an empty list («پیشنهاد
  تازه‌ای پیدا نشد»), not an error — absence is the honest answer.
- Model call rides the routed `chat` task (`padyar_ai.generate`,
  `response_format="json_object"`, `temperature=0.3`,
  `max_output_tokens=800`, `timeout_s=45`) — no new routing-table task, no
  admin change.
- Both endpoints are admin-session-gated (`verify_admin`), CSRF-protected
  like every admin mutation (fetchAuth), unknown `entry_id` → 404, apply
  payload validated (list of non-empty strings, ≤ 20).

## Deliberately not done

- No apply-time dedupe against questions added in the meantime — the manual
  add path allows duplicates too, and Tier-0 matching is unaffected (both
  rows map to the same entry).
- No background batch over all entries — AI spend is always a human click.
- No repair of model output: a suggestion that fails a rule is dropped,
  not fixed.

## Verification

`tests/test_question_assist.py`: validation filters (duplicate vs existing
normalized, length bounds, English gate, in-batch dedupe, cap), English
allowed when the entry already has English questions, unknown entry → 404,
AI down → 503 + Persian message + nothing written, apply inserts rows mapped
to the entry with exactly one reindex, apply payload validation, 401 anon /
403 missing-CSRF on both endpoints, and the modal carries the two buttons
(fails when the wiring is removed).
