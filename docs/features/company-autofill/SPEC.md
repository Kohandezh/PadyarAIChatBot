# Company Autofill

**Slug:** `company-autofill` · **Status:** Implemented · **Domain:** leads/content
**Created:** 2026-08-31 (as activity-field-autofill) · **Widened:** 2026-08-31

## The scenario

Companies arrive with half-empty profile columns from both live sources: a
workbook import whose cells were blank, and a visitor's booth proposal an
admin approved (the text arrives, nothing else). A company with no facet
value is **invisible in every field-filtered chat list** («شرکت‌های فعال در
زمینهٔ …»), because `app/services/company_search.py` builds the facet
vocabulary from these values. Measured on the elecomp install, 2026-08-31:
28 of 670 rows were dark this way — including the one sponsor the organizer
had boosted and could not understand why the boost "didn't work".

The operator is not a typist. The fix is one button on the companies
page: **پر کردن خودکار اطلاعات** — it reads each company's own intro text
and fills every EMPTY field it can: the contact person and their title,
mobile, email, website, company phone, fax, address (fa/en), province,
booth number, hall, type, stage, participation, and the activity-field
labels. What the text does not mention stays empty — absence is the honest
answer, not an error.

The three English fields (`title_en`, `text_en`, `address_en`) cannot be
extracted from a Persian text, so the model TRANSLATES them instead
(آرمان تجارت مهرکالا → Arman Tejarat Mehrkala). That is generation, not
extraction — which is exactly why they are still length-capped and still
only ever written into an empty column.

## What ships

| Piece | Where |
| ----- | ----- |
| Service | `app/services/company_autofill.py` — `pending()`, `run()`, per-field validation |
| Endpoints | `GET`/`POST /admin/api/company-profiles/autofill` in `app/routers/leads.py` (declared BEFORE `/{dataset_id}` — FastAPI matches in order) |
| Button | `templates/admin/companies.html` toolbar + `static/admin/js/companies.js` (`initAutofill`, badge, progress loop) |
| Logs | category `content` — `companies.autofill.run` (info, per-run report naming the columns written per company), `companies.autofill.company_failed` (warning), `companies.autofill.ai_unavailable` (error). Visible under لاگ‌ها → محتوا و دیتاست |
| Tests | `tests/test_company_autofill.py` (8) |

## The contract

- The model is asked about **exactly the company's still-empty columns** —
  the prompt's JSON skeleton carries only those keys, and `_clean_fields`
  drops any other key unread. Reason (elecomp, 2026-08-31: 746 pending,
  zero filled): the model echoes what the text mentions — often
  already-full columns — while the company's real holes are fields the text
  never names; without the whitelist every write intersects nothing and the
  backlog never moves.
- The model **suggests**, the code decides: every value passes a per-field
  validator, re-validated in `run()` at the write layer, not only inside
  the AI call:
  - `email` — exactly one `@`, a dot in the domain, no spaces;
  - `contact_mobile`/`company_phone`/`fax` — digits (Persian or Latin)
    plus written separators, 7–15 digits;
  - `website` — no spaces, a real dot-bearing ASCII body after the scheme;
  - `activity_field` — same shape the facet reader accepts (≤ 8 tokens,
    ≤ 70 chars, no `|`), at most 3, joined with `" | "`;
  - every free-text field and the three English fields — a per-column
    length cap, REJECTED whole when over (never silently truncated).
- Fields the model returns for a column that is no longer empty are
  **dropped from that company's write** (the row is re-read at write time):
  the organizer's value stays AND the other fields still land. The UPDATE
  also carries `AND COALESCE(field,'')=''` per written column, so the run
  is re-runnable / idempotent and no organizer data is ever overwritten.
- One POST fills at most **10** companies and scans at most **40** — the
  batch counts FILLS, not companies examined, so a company whose text
  yields nothing for its holes does not strand the queue behind it (the UI
  loops with a progress line until `remaining` hits zero, and stops early
  if a pass fills nothing — the same failures would repeat forever).
- Rows with **no intro text are never guessed** — counted in the report
  (`no_text`), left for the organizer.
- The prompt shows the install's own existing labels (top 120 by frequency)
  so the taxonomy does not fork; the model may invent a short label only
  when nothing fits. It also forbids invention outright for every other
  field: extract what is written, leave the rest empty.
- AI completely unavailable → first failure aborts the run with **503**, a
  Persian message, and nothing written (each write is its own guarded
  UPDATE, so there is no half-written state).
- Model call rides the routed `chat` task (`padyar_ai.generate`,
  `response_format="json_object"`, `temperature=0.0`,
  `max_output_tokens=800`) — no new routing-table task, no admin change.

## Deliberately not done

- No automatic run on import/proposal — the button is the trigger, so AI
  spend is always a human choice.
- No guessing from a bare title (the `no_text` rows) — a wrong value puts a
  company in a list it does not belong to, which is worse than absence.
- No province/type/stage vocabulary locking — length caps only. The prompt
  forbids invention, so an empty text yields an empty field; the organizer
  corrects the rare imperfect-but-present value in the same modal.
- No repair of model output (no truncation, no reformatting): a value that
  fails its shape check is dropped, not fixed.

## Verification

`tests/test_company_autofill.py`: preview counts (a row whose only hole is
the English name is fillable too), wildcard-route ordering (fails if
`/autofill` is declared after `/{dataset_id}`), only-empty guard (fails if
the guard is removed — the pre-filled row must survive), hard per-field
validation (bad email/phone/website/labels rejected, valid ones kept),
full-field echoes dropped (the elecomp regression), scan-past-no-yield
companies, per-company failure reporting, AI-down → 503 + nothing written,
the mid-run organizer edit yielding precedence, and the button present with
its current label on the rendered companies page.
