# Assessment Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Each task runs in its own git worktree on branch `task/<slug>`. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the fixable gaps found by the 2026 danesh-bonyan assessment audit, except local-model deployment (postponed by owner).

**Architecture:** 7 independent tasks executed in parallel worktrees (one agent each), merged sequentially into `main`, then one wave-2 doc task refreshes the evidence pack and top-level docs against the merged result.

**Tech Stack:** Python 3.10+ / FastAPI / PostgreSQL 16 (prod) + SQLite (test) / vanilla JS + Tabler RTL admin / Padyar AI Wrapper.

**Spec:** The audit findings (this conversation) + `docs/knowledge-based-evidence/` + `docs/engineering/ENGINEERING_CONSTITUTION.md`.

## Deferred (out of scope, by owner decision)

- Local proprietary model training/deployment — postponed by owner.
- Human review + named technical ownership — requires humans, not code.
- Live verification of the 11 AI providers — requires real API keys.
- Multi-node HA / replication — requires server infrastructure decisions.

## Global Constraints (binding for every task)

- GC1: Python 3.10+; follow existing code style exactly. No comments unless asked by existing patterns.
- GC2: DB access through the existing `app/db` layer (`?` placeholders, SQLite+PG compatible). No new migration unless a task explicitly requires a new table; if required: `migrations/NNNN_name.sql` + SQLite mirror in `app/db/connection.py` + runner script works.
- GC3: Every admin endpoint performs its own auth (admin session) — never infer from ID possession. Unbounded collections get pagination. CSRF conformance like sibling endpoints (see `tests/test_admin_js_csrf_conformance.py` pattern).
- GC4: ADR-017 anti-scaffold law: every param/endpoint/setting ships WITH its production caller in the same change. No unwired capability. Each feature test must FAIL when the wiring is removed (write it red first).
- GC5: All LLM/AI calls go ONLY through the Padyar AI Wrapper (`padyar_ai` in `app/services/ai/wrapper.py`). No direct SDK calls. AI-unavailable must degrade gracefully: clear Persian error to the admin UI, no crash, chat pipeline never blocked.
- GC6: New feature ⇒ spec folder `docs/features/{slug}/SPEC.md` (documents what IS shipping — doc-fiction is a defect) + a row in `docs/features/INDEX.md`.
- GC7: Testing: TDD red-first. Run ONLY your targeted test files:
  `/Users/sinashamsizadeh/projects/PadyarAIChatbot/.venv/bin/python -m pytest tests/test_<name>.py -q`
  from your worktree root. If a test touches embeddings, first `export HF_HOME=/Users/sinashamsizadeh/projects/PadyarAIChatbot/.hfcache`. Run `python -m py_compile` on every touched .py. Do NOT run the full local suite (CI is the gate; ~15 local tests fail for env-only reasons — ignore them).
- GC8: Do NOT edit `CLAUDE.md`, `AGENTS.md`, `README.md` (wave-2 owns them). Do NOT edit files owned by another task (boundaries below). Do NOT `git push`, do NOT tag, do NOT run graphify. Stay inside your worktree.
- GC9: Admin UI: Persian, RTL, Tabler/Bootstrap 5 patterns, one JS file per page. Usability law: understandable in 3 seconds, ≤3 clicks, no jargon.
- GC10: Commit locally on your branch, small conventional commits (`feat:`, `fix:`, `docs:`, `test:`, `chore:`). Never commit secrets.

## File Ownership Boundaries (merge-safety)

| Task | Owns |
|---|---|
| T1 tfidf-cleanup | `scripts/run_eval.py`, `scripts/debug_similarity.py`, comment-only edits in `app/services/{embeddings,search,bm25}.py`, `app/utils/normalizer.py`, `app/routers/chat.py` (log label), `app/modules/registry.py` (line ~29 description), `app/config.py` (line ~37 comment only) |
| T2 metrics | `requirements.txt`, NEW `app/services/metrics.py`, NEW `app/routers/metrics.py`, `app/main.py` (middleware+router), minimal counter-hooks in `app/services/{health,pg_backup,ai/circuit}.py`, NEW `docs/engineering/MONITORING.md`, `docs/features/metrics-endpoint/` |
| T3 synonym-suggest | NEW `app/services/synonym_suggest.py`, `app/routers/synonyms.py`, `templates/admin/synonyms.html`, `static/admin/js/synonyms.js`, `docs/features/synonym-suggestions/` |
| T4 question-assist | NEW `app/services/question_assist.py`, `app/routers/dataset.py`, `templates/admin/dataset.html`, `static/admin/js/dataset.js`, `docs/features/question-assist/` |
| T5 ops-loops | `static/admin/js/dashboard.js`, `templates/admin/dashboard.html`, `app/routers/admin.py` (low_confidence payload only), NEW `deploy/systemd/padyar-freshness@.service` + `.timer`, `deploy/README.md`, NEW `.github/workflows/freshness.yml`, `docs/features/dashboard-fix-action/`, `docs/features/freshness-schedule/` |
| T6 offsite-backup-ci | NEW `app/services/backup_offsite.py`, `app/services/pg_backup.py` (hook), `app/services/backup.py` (scheduler hook, minimal), `app/config.py` (env vars), `.env.example`, `deploy/env/*.env.template`, `.github/workflows/ci.yml`, `requirements-dev.txt`, `docs/engineering/{POSTGRES_TESTING,DEPLOYMENT_RUNBOOK}.md`, `docs/features/offsite-backups/` |
| T7 release-process | NEW `VERSION`, `app/__init__.py`, `app/routers/public.py` (health payload only), NEW `CHANGELOG.md`, NEW `.github/workflows/release.yml`, NEW `docs/engineering/RELEASING.md`, `docs/features/release-process/` |
| T8 evidence-refresh (wave 2) | `docs/knowledge-based-evidence/**`, `CLAUDE.md`, `AGENTS.md`, `README.md`, `docs/features/INDEX.md`, `remaining.md` |

Known deliberate overlaps: `app/config.py` (T1 comment vs T6 env vars — different lines), `app/routers/public.py` (T7 health payload only; T2 must NOT touch public.py — use own router). Conflicts resolved by controller at merge.

---

### Task 1: TF-IDF remnant cleanup

TF-IDF was removed from retrieval on 2026-08-28 but dead code and stale text remain.

**Requirements:**
1. `scripts/run_eval.py`: remove the dead TF-IDF branch — `search.vectorizer`/`search.tfidf_matrix` reads (~lines 151-155) and the `--backend`/`tfidf` flag paths (~lines 29, 388, 457-458, 651). The eval must still run its BM25+embeddings pipeline; keep all non-TF-IDF behavior identical.
2. `scripts/debug_similarity.py`: replace the `TfidfVectorizer` usage with the current BM25/rerank pipeline so the debug tool reflects reality.
3. Fix misleading stale text (comments/docstrings/log labels ONLY — no behavior change): `app/services/embeddings.py:124,130` ("falling back to TF-IDF" → BM25), `app/routers/chat.py:1079` (`tfidf=` label → `score=`), `app/modules/registry.py:29` (module description → "local retrieval (BM25 + embeddings + rerank) + AI fallback"), `app/config.py:37` comment, `app/services/search.py:39-42,85-87,748-756`, `app/utils/normalizer.py:42`, `app/services/bm25.py:3,12`.
4. Grep the repo for remaining live TF-IDF references in code (exclude `docs/` history, ADRs, evidence pack — wave 2 owns docs). The `search_backend` setting written by ~15 test files is inert; do NOT remove those test writes, but if trivial, stop producing it in any non-test code.

**Acceptance:**
- `rg -i "tfidf|TfidfVectorizer"` over `app/` + `scripts/` returns only historical/none references; no functional code path references TF-IDF.
- `python -m py_compile` on all touched files passes.
- Targeted tests: run `tests/test_run_eval_conversations.py` and any tests importing the touched modules — pass.
- Report lists every removed reference with file:line.

### Task 2: Prometheus metrics endpoint

The assessment flagged "no monitoring". Add a minimal, secure Prometheus metrics surface.

**Requirements:**
1. Add `prometheus-client` to `requirements.txt`.
2. NEW `app/services/metrics.py`: a dedicated `CollectorRegistry`; HTTP request counter (method, route template, status), request latency histogram, in-flight gauge; chat tier counter (tier label, hooked in `app/routers/chat.py`? NO — hook where the tier is already recorded: log/turn-recording point; find the existing single point where source/tier is known and increment there); AI wrapper counters (provider, outcome) + circuit state gauge (hook in `app/services/ai/circuit.py` transitions); backup outcome counter (hook in `app/services/pg_backup.py` success/fail); health score gauge (from `app/services/health.py` computed score).
3. NEW `app/routers/metrics.py`: `GET /metrics`. Security: if `METRICS_TOKEN` env set → require `Authorization: Bearer <token>`, else admin-session-gated (403 otherwise). Follow repo auth patterns. 403 negative test required.
4. Wire middleware + router in `app/main.py` following existing middleware patterns (route template, not raw path — avoid label cardinality).
5. NEW `docs/engineering/MONITORING.md`: metric list, security model, nginx/Cloudflare scrape note (metrics must NOT be public), retention guidance pointer.
6. Spec folder `docs/features/metrics-endpoint/` + INDEX.md row.

**Acceptance:**
- Red-first tests (NEW `tests/test_metrics.py`): requests increment counters (route template label), 403 without auth, token auth works, tier counter increments on a chat turn (use existing chat-test fixtures), backup counter hook fires (mock).
- `python -m py_compile` on touched files.
- No chat-path latency regression: instrumentation is counter/histogram only, no I/O in middleware beyond in-memory ops.

### Task 3: Synonym suggestion engine (AI-assisted, human-approved)

Assessment: "synonyms are managed manually". Evidence doc claim: auto-alias suggestion is "planned" — ship it.

**Requirements:**
1. NEW `app/services/synonym_suggest.py`: `suggest_synonyms(limit=20)` — gather signals (top frequent terms in `questions` corpus + low-confidence/unmatched queries from `chat_logs` via existing weak-query patterns — reuse the query used by `app/services/conversations.py` weak store where sensible), build ONE prompt, call `padyar_ai` chat route, parse strict JSON, validate every suggestion: normalizes differently from its word (use `app/utils/normalizer.py`), does not already exist in `synonyms` table, both sides non-empty ≤ 40 chars, dedupe. Return list of `{word, suggestion, reason}`. AI-unavailable → typed error surfaced as Persian message.
2. `app/routers/synonyms.py`: `POST /admin/api/synonyms/suggest` (admin auth) returns suggestions WITHOUT saving; `POST /admin/api/synonyms/suggest/apply` accepts the selected pairs and inserts through the EXISTING add-synonym code path (so reindex + normalization reuse stay single-source). Rate-limit generously (this calls a paid model; e.g. reuse per-admin cooldown pattern if one exists, else simple in-DB cooldown setting).
3. UI (`templates/admin/synonyms.html` + `static/admin/js/synonyms.js`): one button «پیشنهاد هوشمند مترادف» → modal with checkbox list (word → suggestion, reason) → «افزودن انتخاب‌شده‌ها». ≤3 clicks. Shows Persian error on AI failure. CSRF conformance like sibling pages.
4. Spec folder `docs/features/synonym-suggestions/SPEC.md` (scenario: who triggers, from where, wire list) + INDEX.md row.

**Acceptance:**
- Red-first tests (NEW `tests/test_synonym_suggest.py`): mocked `padyar_ai` — parse OK, invalid suggestions filtered (duplicate/exists/self-normalizing/too long), apply inserts rows AND triggers reindex (assert index version bump or reindex call), suggest endpoint 403 without admin, apply endpoint validates payload.
- No migration (uses existing tables).

### Task 4: Question-variant assistant for dataset entries

Assessment: knowledge entry is manual. Ship AI-suggested question paraphrases with human approval.

**Requirements:**
1. NEW `app/services/question_assist.py`: `suggest_questions(entry_id, count=10)` — build prompt from the dataset entry (title + text + existing questions), call `padyar_ai`, validate: normalized-unique vs the entry's existing questions, length 5..120 chars, Persian (allow English only if the entry already has English questions), dedupe, cap count. Return `{question, is_new}` list. AI-unavailable → typed error.
2. `app/routers/dataset.py`: `POST /admin/api/dataset/{id}/suggest-questions` (admin auth) → suggestions; `POST /admin/api/dataset/{id}/apply-questions` → bulk insert via the EXISTING question-create path (single-source validation + reindex).
3. UI (`templates/admin/dataset.html` + `static/admin/js/dataset.js`): in the entry edit modal, button «پیشنهاد سوال» → checkbox list → «افزودن انتخاب‌شده‌ها». Persian error on AI failure. CSRF conformance.
4. Spec folder `docs/features/question-assist/SPEC.md` + INDEX.md row.

**Acceptance:**
- Red-first tests (NEW `tests/test_question_assist.py`): mocked `padyar_ai` — validation filters (dup vs existing, length, cap), apply inserts rows mapped to the entry AND triggers reindex, 403 without admin, unknown entry_id → 404.

### Task 5: Ops loops — dashboard fix-action + freshness scheduler

Two small shipped-loop gaps: the low-confidence dashboard is read-only, and the freshness checker is never scheduled.

**Requirements:**
1. Dashboard fix-action: `static/admin/js/dashboard.js` low-confidence table gets an «اصلاح» action per row that opens the SAME fix flow as the conversations weak view (`static/admin/js/conversations.js` `openFix()` pattern — extract a shared helper module or replicate minimally per repo JS pattern) and posts to the existing `/admin/api/questions` endpoint. If `/admin/api/low_confidence` (`app/routers/admin.py` ~1015) does not already return `entry_id` where derivable, extend the payload (it is logged in `chat_logs`). Keep read-only stats elsewhere untouched. ≤3 clicks from row to saved fix.
2. Freshness scheduling: NEW `deploy/systemd/padyar-freshness@.service` + `padyar-freshness@.timer` (weekly, `Persistent=true`, read-only script — script never writes to DB). Follow the existing `padyar-watchdog@` unit pattern for install-path parameterization. Add a `deploy/README.md` section (enable commands). NEW `.github/workflows/freshness.yml`: weekly schedule, runs `python scripts/refresh-inotex-context.py`, `continue-on-error` (advisory — network-dependent), does NOT touch `ci.yml`.
3. Spec folders `docs/features/dashboard-fix-action/SPEC.md` + `docs/features/freshness-schedule/SPEC.md` + INDEX.md rows.

**Acceptance:**
- Red-first test: `/admin/api/low_confidence` payload includes `entry_id` when the log row has one (extend/add `tests/test_admin_stats*` or new file). Dashboard JS change follows the page's existing fetch/CSRF pattern.
- Unit files parse (`systemd-analyze verify` if available, else YAML/lint sanity + review).

### Task 6: Off-site backup copy + PG tests in CI + coverage

Assessment: "backups live on the same host", "PG tests not in CI", "no coverage measurement".

**Requirements:**
1. NEW `app/services/backup_offsite.py`: after a successful backup+verify in `app/services/pg_backup.py`, if `OFFSITE_BACKUP_TARGET` is configured, copy the verified dump: target forms `rsync:user@host:/path` → `rsync -a --chmod=F600` subprocess; `dir:/mounted/path` → copy + fsync. Record result (target, sha256 of source, exit code, duration) in `manifest.json` offsite block + `app/services/applog.py` service event. Failure is NON-fatal: local backup stays valid, event logged, admin-visible error surfaced by existing backups page only if free. Config: `OFFSITE_BACKUP_TARGET`, `OFFSITE_BACKUP_TIMEOUT` in `app/config.py` + `.env.example` + both `deploy/env/*.env.template` (placeholder).
2. `.github/workflows/ci.yml`: add job `postgres-tests` — `services: postgres:16-alpine`, env `RUN_POSTGRES_TESTS=1` (+ the DSN env the suite expects — read `tests/postgres/conftest.py` for exact vars), runs `pytest tests/postgres -q`; blocking. Add `pytest-cov` (advisory coverage report, no threshold) to the existing `test` job + `requirements-dev.txt`.
3. Docs: update `docs/engineering/POSTGRES_TESTING.md` (now in CI), `docs/engineering/DEPLOYMENT_RUNBOOK.md` (off-site copy step + note the `/health` ×3 vs 12×5s drift — fix the drift line to match `deploy/padyar-deploy.sh`). Spec folder `docs/features/offsite-backups/SPEC.md` + INDEX.md row.

**Acceptance:**
- Red-first tests (NEW `tests/test_backup_offsite.py`): mocked subprocess/copy — success records manifest block + service event; absent config → no-op; rsync failure → non-fatal, event logged, no exception raised to caller.
- CI YAML valid (`python -c "import yaml,sys; yaml.safe_load(open('.github/workflows/ci.yml'))"` — install pyyaml into venv if needed).
- `python -m py_compile` on touched files.

### Task 7: Release process (version + changelog + release workflow)

Assessment: "no tags/releases/versioned artifacts".

**Requirements:**
1. NEW `VERSION` file (`0.1.0`) at repo root; `app/__init__.py` reads it (`__version__`); `GET /api/health` payload includes `version` (read current payload first — additive field only).
2. NEW `CHANGELOG.md` — Keep a Changelog format, seeded: 0.1.0 (unreleased summary of current state, 5 bullets max, from `docs/CHANGELOG-2026-06-27-fa.md` + recent git log themes).
3. NEW `.github/workflows/release.yml`: on `push: tags: ['v*']` — checkout, install requirements, run `pytest -q` (same pattern as ci.yml test job), then `softprops/action-gh-release` (or `gh release create`) with notes extracted from CHANGELOG's matching version section. No other jobs.
4. NEW `docs/engineering/RELEASING.md`: when to bump (semver for this product), tag + push commands, what the workflow does, rollback note (deploy script already auto-rolls back). Spec folder `docs/features/release-process/SPEC.md` + INDEX.md row. Do NOT create or push any tag yourself.

**Acceptance:**
- Red-first test: `/api/health` includes `version` matching VERSION file (extend existing health test file — read `tests/test_health_surface.py` first and follow its style).
- Workflow YAML valid; `python -m py_compile` on touched files.

### Task 8 (wave 2 — only after T1..T7 merged): Evidence pack + top-level docs refresh

**Requirements:**
1. `docs/knowledge-based-evidence/`: refresh `00-executive-summary-fa.md`, `14-innovation-claims-and-evidence-fa.md`, `15-limitations-and-roadmap-fa.md` against the merged repo: PG live (not planned), recount test functions (`rg "def test_" tests | wc -l`), CI jobs list (now includes postgres-tests + freshness + release), new claims for T2-T7 features with evidence pointers (status labels: implemented_and_verified requires the feature's tests as evidence), TF-IDF baseline framing corrected (historical, pre-removal), limitations updated (drop fixed items — off-site backups partial, monitoring partial with metrics endpoint; keep: no HA, no off-site? update accordingly, human review pending, local model postponed).
2. Update `AGENTS.md` + `CLAUDE.md`: tech-stack/deps tables (prometheus-client, pytest-cov dev), new services/routers rows, module tables if touched, Testing section (PG tests now in CI), new feature list pointers.
3. `README.md`: features/architecture deltas only (metrics endpoint, synonym suggestions, question assist, off-site backups, release process, PG tests in CI). Keep tone/structure.
4. `docs/features/INDEX.md`: verify all new rows present (T2-T7 added theirs); `remaining.md`: mark items done that now are (off-site, monitoring partial, freshness scheduled, release process), keep human-review item open.

**Acceptance:** every numeric claim in the evidence pack is re-derived from the merged repo (commands listed in the report); no stale "planned" for shipped things; no doc-fiction.
