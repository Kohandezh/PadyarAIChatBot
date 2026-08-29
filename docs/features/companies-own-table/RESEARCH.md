# Companies get their own table

**Status:** planned, not started. Approved by the product owner on 2026-08-29.
**Written by:** the session that surveyed the surface, so the next session does
not have to survey it again.

---

## The problem, in one sentence

A `dataset` row IS a company when `company_profiles` holds a row with the same
id. So "is this a company?" is a JOIN, and every consumer has to remember to
subtract companies before it does anything else.

Nobody remembers every time. On the INOTEX install **168 of 222 `dataset` rows
are exhibitor companies**, so the retrieval index, the BM25 corpus and the
intent classifier were all built over a corpus that is three-quarters company
rows. That is the root cause of the confident-wrong answers: a question about
a topic matched an unrelated exhibitor because the exhibitor was in the same
index as the answer sheet.

The pipeline already grew dedicated company tiers to work around it
(`local_company_search`, `local_company_field` in `app/routers/chat.py`), and
`app/services/search.py::_company_dataset_ids()` exists purely to subtract
companies back out of the training set. Those are patches on a storage
decision.

## The decision

**One `companies` table.** It holds what a company IS, in one row:

| From | Columns |
| ---- | ------- |
| today's `dataset` row | `id`, `title`, `title_en`, `text`, `text_en`, `video_url`, `position` |
| today's `company_profiles` row | `contact_name`, `contact_position`, `contact_mobile`, `email`, `website`, `company_phone`, `fax`, `address`, `address_en`, `province`, `company_type`, `org_stage`, `activity_field`, `participation`, `notes`, `source`, `created_at`, `updated_at` |

Companies leave `dataset`. `company_profiles` is dropped after the move.

Rejected alternatives, and why:

- **A `dataset.kind` column.** Five percent of the cost and it does fix
  retrieval. It does not fix the thing the owner actually asked for: reading
  one company is still two tables. Rejected.
- **Renaming the `dataset_id` foreign keys to `company_id` in the same
  change.** Correct eventually, but it doubles the blast radius inside the
  leads module for a naming win. Do it as a separate follow-up. Note honestly
  in the migration that those columns now name a `companies.id`.

### THE IDS DO NOT CHANGE

This is what makes the change affordable. A company keeps the id it has today.
Every `dataset_id` value in every other table stays byte-identical; only the
table it points at is renamed in the reader's head. `chat_logs.entry_id` and
the stored `offer_state` ids are log/text columns, not foreign keys, so
history keeps rendering.

---

## What has to change

### 1. The migration: `migrations/0013_companies.sql`

Next free number is 0013 (0012 is the visitor sessions one, already applied).

**Never edit an applied migration.** `scripts/apply_migrations.py` stores a
sha256 and exits 2 on a changed file, which aborts step 4 of six in
`deploy/padyar-deploy.sh`. This bit us once already; see the rule in CLAUDE.md.

Order inside the one transaction:

1. `CREATE TABLE IF NOT EXISTS app.companies (...)` with the merged columns.
2. `INSERT INTO app.companies SELECT d.*, p.* FROM dataset d JOIN company_profiles p ON p.dataset_id = d.id`.
3. `DELETE FROM app.dataset WHERE id IN (SELECT id FROM app.companies)`.
4. `DROP TABLE app.company_profiles`.

Verify the counts before and after in the same transaction, or the delete
silently removes rows the insert did not copy.

Then mirror the table in `init_db()` in `app/db/connection.py` for the SQLite
test backend, and delete the `company_profiles` block from `_TABLES` in
`app/services/leads.py` (around line 218).

**There is no downgrade.** Rolling back means restoring a backup
(`app/services/pg_backup.py`). Take one before the deploy.

### 2. `questions` rows that point at a company

Decide before writing the migration: `questions.dataset_id` may hold company
ids. `_intent_training_set()` already throws those away, so they are dead
weight in the index today. Count them on production first
(`SELECT COUNT(*) FROM questions WHERE dataset_id IN (SELECT dataset_id FROM company_profiles)`),
then either move them beside the company or delete them. Do not guess.

### 3. Code that has to move

**Reads a company (the `dataset` × `company_profiles` join):**

| File | What |
| ---- | ---- |
| `app/services/company_search.py:288` | `_load_companies()`, the join. Becomes one `SELECT ... FROM companies`. |
| `app/services/company_search.py:475` | `public_profile` use |
| `app/services/company_profiles.py:141,241` | the whole service. Most of it collapses; keep `PUBLIC_PROFILE_FIELDS` and `_WITHHELD_WORDS`, they are the privacy allowlist. |
| `app/services/answer.py:592,615,739` | "a dataset row IS one of the collection when it has a company_profiles row" |
| `app/services/leads.py:601,606,618,795,862,968,1134,1183` | capture, sync, search, delete |
| `app/routers/leads.py:365-390` | the admin profile endpoints |
| `app/routers/chat.py:508,572,592` | the two company tiers |

**Deletes outright:**

| File | What |
| ---- | ---- |
| `app/services/search.py:96-116` | `_company_dataset_ids()`. Gone. |
| `app/services/search.py:119-156` | `_intent_training_set()`'s company subtraction. `dataset` no longer holds any, so there is nothing to subtract. **This is the payoff: keep the assertion, drop the filter.** |

**Reads `dataset` and must now ask which table it wants:** the 27 sites listed
by

```bash
grep -rn --include='*.py' -iE "(from|join|into|update)[[:space:]]+dataset\b" app/
```

Most are the admin dataset CRUD (`app/routers/dataset.py`, 8 sites) and are
correct as-is once companies are not in `dataset` any more. Check each; do not
sweep.

**Scripts:** `scripts/import-content.py` (writes both tables today),
`scripts/reset-content-to-defaults.py`, `scripts/import-inotex-programs.py`,
`scripts/debug_similarity.py`.

### 4. Admin

`templates/admin/companies.html` and its route
(`app/routers/leads.py:545`, `/secure-panel-inotex/companies`) already exist,
so there is a screen. It reads `company_profiles.list_companies()`; point it at
the new table. The dataset screen should stop showing companies, which it will
do for free.

### 5. Tests that will move

21 test files mention `company_profiles`:

```
test_answer_firewall test_chat_options_pick test_chat_transcript
test_company_field test_company_list_phrasing test_company_profiles
test_company_search test_conversation_memory test_grounded_selection
test_named_entity_guard test_offer_roundtrip test_pick_resilience
```

Note: `test_company_profiles.py` (4 tests) is one of the 15 that already fail
on a developer Mac for environment reasons. Do not read a red run there as
your change breaking something. See `local-suite-has-15-env-failures`.

---

## The measurement that says whether it worked

`scripts/run_eval.py --recall-k` against `data/eval/golden-inotex.json`.

Take a BASELINE READING BEFORE the change, because the golden set today has
**60 FAQ questions and zero company queries**, so it measures exactly the half
that should improve. Recall@8 was 0.952 on 2026-08-28.

Then add company queries to the golden set, which is on the backlog anyway,
and measure both halves separately. If FAQ recall does not improve, the
storage change did not buy what it was supposed to buy, and that is worth
knowing before the follow-up work.

---

## Order of work

1. Count the company-owned `questions` rows on production. Decide their fate.
2. Baseline `run_eval.py --recall-k`.
3. Write `0013_companies.sql` plus the `init_db()` mirror. Test the migration
   against a restored copy of the production dump, not against a fresh DB.
4. Move the readers, one file at a time, tests going green as you go.
5. Delete `_company_dataset_ids()` and the subtraction. This is the moment the
   change pays for itself.
6. Re-run the eval and record both numbers in
   `docs/knowledge-based-evidence/`.
7. Record the decision in `docs/engineering/DECISIONS.md` as ADR-019, in
   Persian, matching that file.
