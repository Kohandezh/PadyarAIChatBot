# Synonym Suggestions

**Slug:** `synonym-suggestions` · **Status:** Implemented · **Domain:** search/content
**Created:** 2026-09-14

## The scenario

Synonyms were managed purely by hand, and the person who knows which words
visitors actually type is not the person editing the table. An operator on
the synonyms page clicks **«پیشنهاد هوشمند مترادف»**; the install asks the
model ONCE, fed with two signals it already owns — the curated questions
corpus's frequent terms (the vocabulary the knowledge base speaks) and the
real visitor queries the bot answered weakly or not at all from `chat_logs`
(the vocabulary the visitors speak). A checkbox list appears
(word ← suggestion + one-line reason); the operator picks rows and clicks
**«افزودن انتخاب‌شده‌ها»**. Two clicks plus the selections, nothing stored
until the second click.

## What ships

| Piece | Where |
| ----- | ----- |
| Service | `app/services/synonym_suggest.py` — `suggest_synonyms()`, `clean_pair()`, signal gathering, cooldown |
| Endpoints | `POST /admin/api/synonyms/suggest` and `POST /admin/api/synonyms/suggest/apply` in `app/routers/synonyms.py` (admin session required on both) |
| Shared write path | `_insert_synonym_pairs()` in `app/routers/synonyms.py` — now also the body of the manual add form; normalization reload + index-version bump live there once |
| Button + modal | `templates/admin/synonyms.html` (`#suggest-synonyms-btn`, `#suggestModal`) + `static/admin/js/synonyms.js` (`suggestSynonyms`, `applySuggestedSynonyms`) |
| Logs | category `content` — `synonyms.suggest.run` (info), `synonyms.suggest.ai_unavailable` (error) |
| Tests | `tests/test_synonym_suggest.py` (12) |

## The contract

- The model **suggests**, the code decides: every pair passes `clean_pair()`
  — both sides non-empty and ≤ 40 chars after whitespace folding, the two
  sides must normalize DIFFERENTLY under `normalize_persian(expand_synonyms=False)`
  (a pair differing only in Arabic ی/ي spelling is spelling advice, not a
  synonym), the pair must not already sit in the `synonyms` table (either
  direction), duplicates collapse on the normalized pair. The same function
  validates the apply payload — one implementation, two callers.
- Suggest **never writes**: the endpoint returns the validated list and the
  synonyms table is untouched. Apply inserts the operator's picks through
  `_insert_synonym_pairs()` — `INSERT OR IGNORE` per pair on one connection,
  then `load_synonyms_from_db()` + `bump_index_version()` exactly once per
  apply (ADR-017: reindex and normalization reload stay single-source with
  the manual add form).
- Signals: top 40 terms of the last 2 000 `questions` rows (normalized
  tokens ≥ 3 chars, digits dropped); the newest 40 distinct `chat_logs`
  queries with `confidence < 0.19` (the same bar
  `app/services/conversations.weak_answers` sets for the admin panel) or
  `source = 'no_answer'`, each clipped to 80 chars.
- One prompt, one paid call: `padyar_ai.generate` on the routed `chat`
  task, `response_format="json_object"`, `temperature=0.0`,
  `max_output_tokens=1500`, `timeout_s=45`. No new routing-table task, no
  admin change.
- Cost control: **one request per 60 s install-wide** (a settings-table
  timestamp, so the window holds across workers), consumed when the request
  is accepted — before the AI call — so a double-click or two operators
  clicking at once spend one call. Inside the window: 429 with
  «چند لحظه صبر کنید و دوباره امتحان کنید (حدود N ثانیه)». A settings
  fault fails open: the cooldown protects spend, it is not a gate.
- AI unavailable → `SynonymSuggestUnavailable` → **503** with a Persian
  message («هوش مصنوعی در دسترس نیست (…)»); nothing was written and the
  chat pipeline is never involved. An answer that is not valid JSON, or a
  JSON without a `suggestions` list, degrades to an empty list —
  «پیشنهاد تازه‌ای پیدا نشد.» — not an error.
- Apply bounds: at most 50 pairs per request; empty list, non-dict pair,
  or any pair failing `clean_pair()` → 400; an already-stored pair is a
  counted no-op (`added` counts rows actually inserted).
- CSRF: both mutations go through `fetchAuth()` (`X-CSRF-Token`), covered
  by `tests/test_admin_js_csrf_conformance.py`'s sweep.

## Deliberately not done

- No automatic suggestion runs — the button is the only trigger, so AI
  spend is always a human choice.
- No reverse pairs auto-added: `word → suggestion` is stored as typed; the
  expansion pass treats the pair as one direction, same as the manual form.
- No new table, no migration — signals come from existing `questions`,
  `chat_logs`, `settings` reads only.

## Verification

`tests/test_synonym_suggest.py`: strict parse of a valid model answer with
nothing saved; the filter set (duplicate / already-exists / self-normalizing
Arabic-ی pair / over-40-char side / empty side); the prompt carrying both
signals (corpus term + weak/unanswered query); AI-down → 503 + Persian
message; unparseable answer → empty list; the immediate second request →
429 cooldown; apply inserting rows AND bumping the index version exactly
once; apply rejecting every bad payload shape (empty, non-list, non-dict,
empty side, missing key, self-normalizing, over-cap); apply idempotence on
an existing pair; 401/403 without an admin session on both endpoints; and
the page carrying the button, the modal, and the apply label (wiring —
fails if the template button is removed).
