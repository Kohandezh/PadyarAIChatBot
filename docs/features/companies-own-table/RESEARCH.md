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

**Backend divergence that would silently wipe test data.** In PostgreSQL
(`migrations/0001_initial.sql:52-58`), `app.questions.dataset_id` is a plain
`TEXT NOT NULL` with an index — no foreign key. In SQLite
(`app/db/connection.py:252-258`), the same column IS a foreign key with
`ON DELETE CASCADE`. Step 3 of the migration (`DELETE FROM dataset WHERE id
IN (SELECT id FROM companies)`) is safe on production — nothing references
`dataset` by FK — but mirrored verbatim against the current SQLite schema it
would CASCADE-DELETE every one of the 840-equivalent company-linked
`questions` rows in the test backend the moment the dataset cleanup runs.
Tests would then pass locally while quietly testing a `questions` table that
no longer has any company rows in it — the opposite of what section 2 above
just spent a trace establishing. **Drop the `ON DELETE CASCADE` FK (or the FK
entirely) from the SQLite `questions.dataset_id` definition in the same
change**, so both backends behave identically: a dataset/company row's
removal never touches `questions`.

**There is no downgrade.** Rolling back means restoring a backup
(`app/services/pg_backup.py`). Take one before the deploy.

### 2. `questions` rows that point at a company — RESOLVED, 2026-08-29

Measured on inotex production: **840 rows**
(`SELECT COUNT(*) FROM questions WHERE dataset_id IN (SELECT dataset_id FROM company_profiles)`).

**They are not dead weight — this was the wrong assumption to check first.**
`_intent_training_set()` excludes them from the CLASSIFIER's training set,
true, but that is one of three readers of `questions_data`, not all of them.
Traced `app/services/search.py:485-901` and `app/routers/chat.py:449-586`:

- `load_dataset_internal()` loads every `questions` row, company or not, into
  `questions_data` / `questions_embedding_index` / `questions_bm25_index` — no
  filter.
- `find_similar_question()` (Tier 0) scores the visitor's query against ALL of
  them and, on a hit, resolves the answer with
  `dataset_lookup.get(dataset_id)` (`search.py:896`).
- `chat.py:580-585` treats an exact_score ≥ 0.9 Tier-0 hit as **authoritative,
  outranking the company tiers** ("Tier 0 stays authoritative: a near-exact
  hit on a hand-curated question... never overridden by the anchor").

So a curated question like "شماره تماس شرکت دکیو چیست؟" is answered by Tier 0
TODAY, live, at booth traffic — not a training-time artifact. Deleting these
840 rows would delete real curated answers. Decision: **keep them, unchanged,
in `questions`.**

**Why no migration is needed for this table.** THE IDS DO NOT CHANGE (see
above) — a `questions.dataset_id` that names a company keeps naming that same
id after the move; only the table holding that id changes. The `questions`
row itself does not need to move or be rewritten.

**What DOES need to change — a second choke point, not in the reads-table
below because it isn't a company-profile join, it's a plain
`dataset_lookup.get(id)` that goes stale the moment companies leave
`dataset`:**

| File | What |
| ---- | ---- |
| `app/services/search.py:896` | `find_similar_question()` — Tier 0's answer resolution. A curated company question would keep winning the match and then silently resolve to `None` (company id no longer in `dataset_lookup`), falling through to a worse tier with no error. |
| `app/services/search.py:813-825` | `get_entry()` — the pick/offer tier's id→entry lookup, called from `chat.py:351,378,380,401,713,750,762` and `answer.py:324`. The Tier 2 selection tier can legitimately offer a company as one of the numbered options (`ANSWER_TOPK` draws from the same corpus), so a visitor picking "2" for a company hits this exact gap. |

Fix: add a module-level `companies_lookup: Dict[str, dict]` in `search.py`,
built in `load_dataset_internal()` from `SELECT ... FROM companies` the same
way `dataset_lookup` is built, and change exactly those two reads to
`dataset_lookup.get(id) or companies_lookup.get(id)`. Ship this in the same
change as the migration — it is a live-traffic regression, not a slow-burn
one, if it lags behind.

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
that should improve. Recall@8 was 0.952 on 2026-08-28, reconfirmed unchanged
on 2026-08-29 before starting the migration.

Then add company queries to the golden set, which is on the backlog anyway,
and measure both halves separately. If FAQ recall does not improve, the
storage change did not buy what it was supposed to buy, and that is worth
knowing before the follow-up work.

---

## Order of work

1. ~~Count the company-owned `questions` rows on production. Decide their
   fate.~~ Done 2026-08-29 — 840 rows, keep them, see section 2.
2. Baseline `run_eval.py --recall-k`.
3. Write `0013_companies.sql` plus the `init_db()` mirror. Test the migration
   against a restored copy of the production dump, not against a fresh DB.
4. Move the readers, one file at a time, tests going green as you go —
   including the `companies_lookup` fallback in `get_entry()` and
   `find_similar_question()` from section 2. Do not ship the migration
   without it; that pairing is what keeps Tier 0 and the pick tier answering
   companies at all.
5. Delete `_company_dataset_ids()` and the subtraction. This is the moment the
   change pays for itself.
6. Re-run the eval and record both numbers in
   `docs/knowledge-based-evidence/`.
7. Record the decision in `docs/engineering/DECISIONS.md` as ADR-019, in
   Persian, matching that file.
