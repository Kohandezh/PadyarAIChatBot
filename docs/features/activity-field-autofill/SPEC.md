# Activity-Field Autofill

**Slug:** `activity-field-autofill` · **Status:** Implemented · **Domain:** leads/content
**Created:** 2026-08-31

## The scenario

Companies arrive with an empty `activity_field` from both live sources: a
workbook import whose cell was blank, and a visitor's booth proposal an admin
approved (the text arrives, nothing else). A company with no facet value is
**invisible in every field-filtered chat list** («شرکت‌های فعال در زمینهٔ …»),
because `app/services/company_search.py` builds the facet vocabulary from
these values. Measured on the elecomp install, 2026-08-31: 28 of 670 rows
were dark this way — including the one sponsor the organizer had boosted and
could not understand why the boost "didn't work".

The operator is not a taxonomist. The fix is one button on the companies
page: **پر کردن خودکار حوزهٔ خالی**.

## What ships

| Piece | Where |
| ----- | ----- |
| Service | `app/services/company_autofill.py` — `pending()`, `run()`, hard label validation |
| Endpoints | `GET`/`POST /admin/api/company-profiles/autofill` in `app/routers/leads.py` (declared BEFORE `/{dataset_id}` — FastAPI matches in order) |
| Button | `templates/admin/companies.html` toolbar + `static/admin/js/companies.js` (`initAutofill`, badge, progress loop) |
| Logs | category `content` — `companies.autofill.run` (info, per-run report), `companies.autofill.company_failed` (warning), `companies.autofill.ai_unavailable` (error). Visible under لاگ‌ها → محتوا و دیتاست |
| Tests | `tests/test_company_autofill.py` (7) |

## The contract

- The model **suggests**, the code decides: every label passes `_clean_labels`
  — same shape the facet reader accepts (≤ 8 tokens, ≤ 70 chars, no `|`),
  at most 3 per company — validated in `run()` at the write layer, not only
  inside the AI call.
- The UPDATE carries `AND COALESCE(activity_field,'')=''`: organizer data is
  never overwritten, and the run is re-runnable / idempotent.
- One POST fills at most **25** companies (proxy-timeout bound); the UI loops
  with a progress line until `remaining` hits zero.
- Rows with **no intro text are never guessed** — counted in the report
  (`no_text`), left for the organizer.
- The prompt shows the install's own existing labels (top 120 by frequency)
  so the taxonomy does not fork; the model may invent a short label only when
  nothing fits.
- AI completely unavailable → first failure aborts the run with **503**, a
  Persian message, and nothing written (each write is its own guarded UPDATE,
  so there is no half-written state).
- Model call rides the routed `chat` task (`padyar_ai.generate`,
  `response_format="json_object"`, `temperature=0.0`) — no new routing-table
  task, no admin change.

## Deliberately not done

- No automatic run on import/proposal — the button is the trigger, so AI
  spend is always a human choice.
- No `province`/`company_type` autofill — the intro text essentially never
  states them; guessing writes wrong facts.
- No guessing from a bare title (the `no_text` rows) — a wrong facet puts a
  company in a list it does not belong to, which is worse than absence.

## Verification

`tests/test_company_autofill.py`: preview counts, wildcard-route ordering
(fails if `/autofill` is declared after `/{dataset_id}`), only-empty guard
(fails if the guard is removed — the pre-filled row must survive), hard label
validation, per-company failure reporting, AI-down → 503 + nothing written,
and the button present on the rendered companies page.
