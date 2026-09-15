# Remove INOTEX From Source — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Strip every INOTEX / اینوتکس brand reference from PadyarAIChatbot so the source is a neutral per-customer platform, while keeping `themes/`, the pet companion engine, and `migrations/` untouched.

**Architecture:** One PR, layered commits (path rename → deletions → code de-branding → data → templates/static → deploy → docs/meta → CI guard). Mechanical renames are done with exact `sed`/`rg` commands; behavioral edits are spelled out inline. A new CI guard job blocks the brand from returning.

**Tech Stack:** Python 3.10+ / FastAPI, vanilla JS, GitHub Actions, bash deploy scripts, pytest.

**Spec:** `docs/superpowers/specs/2026-09-15-remove-inotex-from-source-design.md`

## Global Constraints

- **Keep untouched:** `themes/` (including localStorage keys `inotex-light-mode`, `inotex-theme`), `migrations/` (checksum-guarded), `static/otp/pet/characters/inotex/`, `static/otp/pet/inotex-*.webp` asset filenames, all `--inotex-*` CSS custom properties, localStorage keys `inotex-pet-*`, the `active_theme` setting value `"inotex"`, and URL paths under `/themes/inotex/`.
- New admin path is exactly `/secure-panel-admin`.
- Neutral brand strings: «پردیار» for Persian UI, `Padyar` for Latin strings, `padyar.com` for `ALLOWED_ORIGINS`.
- `run_eval.py` must require `--golden` (the default dataset file is deleted).
- Do not edit applied migrations. Do not touch git history. Elecomp deploy surface stays.
- Commit style: conventional commits (`feat!:`, `fix:`, `chore:`, `test:`, `docs:`), matching `git log --oneline`.
- Every task ends green: targeted tests pass (or the stated verification command returns the stated result) before committing.

---

### Task 1: Branch and baseline

**Files:** none (git only)

**Interfaces:** Produces branch `chore/remove-inotex-from-source` that all later tasks commit to.

- [ ] **Step 1: Create the working branch from main**

```bash
cd /Users/sinashamsizadeh/projects/PadyarAIChatbot
git checkout main && git pull --ff-only
git checkout -b chore/remove-inotex-from-source
```

- [ ] **Step 2: Record the test-collection baseline**

```bash
.venv/bin/python -m pytest --collect-only -q | tail -2
```

Expected: roughly `2669 tests collected` (write the exact number into the PR description later; deleted suites in Task 4/6 will lower it — anything above ~2600 after those tasks is healthy).

- [ ] **Step 3: Confirm working tree is clean**

```bash
git status --porcelain
```

Expected: empty (except untracked spec/plan docs — leave them untracked for now; Task 10 commits them).

---

### Task 2: Rename the admin path `/secure-panel-inotex` → `/secure-panel-admin`

**Files:**
- Modify: every file matching `secure-panel-inotex` (~59 files: `app/main.py`, `app/auth/csrf.py`, `app/routers/{admin,public,backups,tts,otp}.py`, `static/admin/js/*`, `templates/admin/*`, `tests/*`, `scripts/smoke-live.py`, `scripts/capture_proposal_shots.py`, `.claude/**`, `remaining.md`, `deploy/README.md`, others)
- NOT: `docs/superpowers/` (spec text quotes the old path on purpose)

**Interfaces:** Produces the public admin URL prefix `/secure-panel-admin` used by every later task.

- [ ] **Step 1: List every match (sanity check before sed)**

```bash
rg -l 'secure-panel-inotex' --hidden -g '!node_modules' -g '!.git' -g '!docs/superpowers' | sort
```

Expected: ~59 paths, none under `docs/superpowers/`.

- [ ] **Step 2: Rename with sed (macOS syntax)**

```bash
rg -l 'secure-panel-inotex' --hidden -g '!node_modules' -g '!.git' -g '!docs/superpowers' \
  | xargs sed -i '' 's/secure-panel-inotex/secure-panel-admin/g'
```

- [ ] **Step 3: Verify zero leftovers outside the spec/plan**

```bash
rg -n 'secure-panel-inotex' --hidden -g '!node_modules' -g '!.git' -g '!docs/superpowers'
```

Expected: no output.

- [ ] **Step 4: Spot-check the security-critical sites**

```bash
rg -n 'secure-panel-admin' app/main.py app/auth/csrf.py app/routers/public.py
```

Expected: `app/main.py` header tuple `("/secure-panel-admin", "/admin/api")`; `app/auth/csrf.py` `PROTECTED_PREFIXES = ("/admin/", "/secure-panel-admin", "/api/synonyms")`; `public.py` routes/redirects on `/secure-panel-admin`.

- [ ] **Step 5: Run the path-sensitive suites**

```bash
.venv/bin/python -m pytest tests/test_admin_navigation.py tests/test_admin_brand.py tests/test_api_docs_hidden.py tests/test_security_headers.py tests/test_csrf.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add -A && git commit -m "feat!: rename the obscured admin path to /secure-panel-admin"
```

---

### Task 3: Delete the inotex-only files and deploy/freshness surfaces

**Files:**
- Delete: `deploy/nginx/inotex.padyar.com.conf`, `deploy/env/inotex.env.template`, `deploy/systemd/padyar-inotex.service`, `deploy/systemd/padyar-freshness@.service`, `deploy/systemd/padyar-freshness@.timer`, `.github/workflows/freshness.yml`, `scripts/refresh-inotex-context.py`, `scripts/import-inotex-programs.py`, `data/eval/golden-inotex.json`, `content/sources.json`, `content/freshness-report.json`, `content/review-queue.md`
- Modify: `.github/workflows/ci.yml` (remove the inotex deploy step and its summary lines)

**Interfaces:** Consumes nothing. Produces: no file may reference the deleted paths (verified by rg).

- [ ] **Step 1: Delete the files**

```bash
git rm deploy/nginx/inotex.padyar.com.conf deploy/env/inotex.env.template \
  deploy/systemd/padyar-inotex.service deploy/systemd/padyar-freshness@.service \
  deploy/systemd/padyar-freshness@.timer .github/workflows/freshness.yml \
  scripts/refresh-inotex-context.py scripts/import-inotex-programs.py \
  data/eval/golden-inotex.json content/sources.json content/freshness-report.json \
  content/review-queue.md
rmdir content 2>/dev/null || true
```

- [ ] **Step 2: Remove the inotex deploy step from ci.yml**

In `.github/workflows/ci.yml`, delete the step block starting `- name: Deploy inotex to production` (around line 262, the `if` wrapper and the `/usr/local/bin/padyar-deploy inotex "${{ github.sha }}"` run block). In the final summary step, delete the line `echo "  - padyar-inotex  (inotex.padyar.com,  port 8001)"` and reword the comment above `Deploy elecomp to production` so it no longer explains interleaving with inotex (keep the note that elecomp failure leaves the job red and rolled back).

- [ ] **Step 3: Verify nothing references the deleted paths**

```bash
rg -n 'refresh-inotex-context|import-inotex-programs|golden-inotex|freshness@|padyar-inotex|inotex\.padyar\.com' \
  --hidden -g '!node_modules' -g '!.git' -g '!docs/superpowers' -g '!migrations'
```

Expected: no output (deploy scripts still mentioning `inotex` slugs are handled in Task 8; this check is only for the deleted file paths/domains — if deploy scripts show up here for domains, leave them for Task 8 and note it).

- [ ] **Step 4: Run the suites most likely to reference deleted files**

```bash
.venv/bin/python -m pytest tests/test_reset_script.py tests/test_prodcheck.py -q
```

Expected: PASS (if a test imports `data/eval/golden-inotex.json`, it is rewritten in Task 6 — move the fix here if it blocks collection).

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "chore(deploy): drop the inotex instance and its freshness pipeline"
```

---

### Task 4: Empty the default knowledge base

**Files:**
- Modify: `app/default_content.py` (741 lines → small module), `app/db/connection.py:583-610`, `scripts/reset-content-to-defaults.py`
- Test: `tests/test_default_seed.py`, `tests/test_import_no_seed.py`

**Interfaces:**
- Produces: `app.default_content.DEFAULT_DATASET`, `DEFAULT_QUESTIONS`, `DEFAULT_SYNONYMS` (all empty lists — names change from `INOTEX_*`), and unchanged functions `seed_default_content(cursor)`, `seed_default_synonyms(cursor)` (now no-ops on empty data).

- [ ] **Step 1: Rewrite the failing tests first**

In `tests/test_default_seed.py`, replace fact assertions with empty-seed assertions (keep the file's fixtures; the intent flips to "a fresh install starts empty"):

```python
def test_fresh_install_seeds_nothing(sqlite_conn):
    from app.default_content import DEFAULT_DATASET, DEFAULT_QUESTIONS, DEFAULT_SYNONYMS
    assert DEFAULT_DATASET == [] and DEFAULT_QUESTIONS == [] and DEFAULT_SYNONYMS == []
    rows = sqlite_conn.execute("SELECT COUNT(*) FROM dataset").fetchone()[0]
    assert rows == 0
```

Adapt the existing fixture/helper names in that file rather than inventing new ones; delete assertions that quote inotex facts. Keep any test that verifies seeding is idempotent — it now proves idempotence on empty data.

- [ ] **Step 2: Run to verify it fails**

```bash
.venv/bin/python -m pytest tests/test_default_seed.py -q
```

Expected: FAIL (imports `INOTEX_DATASET` or asserts seeded rows).

- [ ] **Step 3: Replace `app/default_content.py` content**

New file body (whole file):

```python
"""Default seed content for a fresh install: none.

This platform ships empty on purpose. Every fact a customer's assistant
knows arrives through the admin panel (dataset, questions, synonyms) or
its import scripts, so nothing here can go stale or leak one customer's
event into another's install. The seeding hooks below stay wired: they
are the mechanism a customer's own seed module plugs into, and tests
rely on them being idempotent no-ops while the lists are empty.
"""

DEFAULT_DATASET = []
DEFAULT_QUESTIONS = []
DEFAULT_SYNONYMS = []


def seed_default_content(cursor) -> None:
    """Insert DEFAULT_DATASET / DEFAULT_QUESTIONS if the tables are empty.

    Idempotent: safe on every boot. With empty lists it does nothing.
    """
    for row in DEFAULT_DATASET:
        cursor.execute(
            "INSERT OR IGNORE INTO dataset (id, title, text, title_en, text_en) "
            "VALUES (?, ?, ?, ?, ?)",
            (row["id"], row["title"], row["text"],
             row.get("title_en", ""), row.get("text_en", "")),
        )
    for row in DEFAULT_QUESTIONS:
        cursor.execute(
            "INSERT OR IGNORE INTO questions (text, answer) VALUES (?, ?)",
            (row["text"], row["answer"]),
        )


def seed_default_synonyms(cursor) -> None:
    """Insert DEFAULT_SYNONYMS if the synonyms table is empty."""
    for src, targets in DEFAULT_SYNONYMS:
        cursor.execute(
            "INSERT OR IGNORE INTO synonyms (source, target) VALUES (?, ?)",
            (src, targets),
        )
```

Then check the real column names before committing — open the original file's seed functions (lines 711-741) and copy their exact SQL and tuple shapes into this new body so the no-op stays schema-correct. If the originals differ, the originals win.

- [ ] **Step 4: Update `app/db/connection.py`**

- Line 583: keep `('active_theme', 'inotex')` (theme value — global constraint).
- Line 587: change the `knowledge_version` default to `('knowledge_version', 'kb-empty')`.
- Lines 599/605 comments: reword "Useful INOTEX synonym expansions …" → "Optional seed synonyms …" and "New installations open with useful INOTEX answers." → "New installations start empty; content arrives via the admin panel." (no logic change).

- [ ] **Step 5: Update `scripts/reset-content-to-defaults.py`**

- Imports: `INOTEX_DATASET, INOTEX_QUESTIONS, INOTEX_SYNONYMS` → `DEFAULT_DATASET, DEFAULT_QUESTIONS, DEFAULT_SYNONYMS`.
- Docstring/help text: "Reset DB content to the bundled INOTEX defaults" → "Reset DB content to the (empty) defaults — clears dataset/questions/synonyms with a backup." Replace the `scope.append("dataset, questions, synonyms  →  INOTEX defaults")` line with `→  empty defaults`.

- [ ] **Step 6: Run the content suites**

```bash
.venv/bin/python -m pytest tests/test_default_seed.py tests/test_import_no_seed.py tests/test_reset_script.py tests/test_suggestions.py -q
```

Expected: PASS. If `test_suggestions.py` assumed seeded suggestions, adapt it to seed its own fixture rows (do not re-add default content).

- [ ] **Step 7: Commit**

```bash
git add -A && git commit -m "feat!(content): ship an empty default knowledge base"
```

---

### Task 5: De-brand the Python services and `.env.example`

**Files:**
- Modify: `app/services/scope.py:48-49`, `app/services/openai.py` (lines 37-60, 118-126, 154, 245-252), `app/services/sms.py:166`, `app/services/branding.py:5,36-37`, `app/services/visit_plan.py` (docstring + lines 195-200), `app/services/pet_characters.py:30`, `app/config.py:326`, comment scrubbing in `app/services/{bm25,rerank,answer,embeddings,search,tts_lexicon,conversations}.py` and `app/routers/{public,chat,otp}.py`, `.env.example:1-2,70`
- Test: `tests/test_branding.py`, `tests/test_pet_characters.py`, `tests/test_answer_firewall.py`, `tests/test_named_entity_guard.py`, `tests/test_unknown_entity_guard.py`

**Interfaces:**
- Produces: `DEFAULT_DOMAIN_FA = "پردیار"`, `DEFAULT_DOMAIN_EN = "the event"` (scope.py); `DEFAULT_ASSISTANT_WEBSITE = "padyar.com"` (openai.py); `DEFAULT_CHARACTER = "elecomp"` (pet_characters.py).

- [ ] **Step 1: scope.py — neutral domain defaults**

```python
DEFAULT_DOMAIN_FA = "پردیار"
DEFAULT_DOMAIN_EN = "the event"
```

- [ ] **Step 2: openai.py — remove hardcoded brand**

- Line 41: `DEFAULT_ASSISTANT_WEBSITE = "padyar.com"`.
- Lines 43-47 (the `INOTEX INFORMATION:` block with `- Official website: https://inotex.com/`): delete the block; where a website line is structurally needed use `get_setting("assistant_website", DEFAULT_ASSISTANT_WEBSITE)` as line 186 already does.
- Line 60: `"You help visitors and exhibitors find clear, current INOTEX information.\n"` → `"You help visitors and exhibitors find clear, current {domain_en} information.\n"` (the `{domain}`/`{domain_en}` replacement machinery at lines 191-213 already fills it).
- Line 118: `...always direct the user to the official INOTEX website.` → `...always direct the user to the official {domain_en} website.`
- Line 121: `- Keep answers limited to the INOTEX exhibition and its services...` → `- Keep answers limited to {domain_en} and its services...`
- Line 154: `(INOTEX, AI, IoT, startup)` → `(event names, AI, IoT, startup)`.
- Lines 245-252 (classifier prompt): `"You are a classification engine for an INOTEX exhibition chatbot.\n"` → `...for an {domain_en} chatbot.\n`; `...news or any other INOTEX topic...` → `...news or any other {domain_en} topic...`; `completely unrelated to the INOTEX exhibition` → `completely unrelated to {domain_en}`. Then make sure the classifier prompt string goes through the same `.replace("{domain_en}", domain_en)` fill as line 213 (extend that call site if the classifier prompt is built separately).

- [ ] **Step 3: sms.py — neutral campaign default**

```python
_DEFAULT_CAMPAIGN_TEXT = ("برای نمایش درست اطلاعات شرکت شما به بازدیدکنندگان، "
                          "شمارهٔ خود را تأیید کنید.")
```

(Keep the original sentence structure/tail from line 166; only the `نمایشگاه INOTEX` head is replaced by the neutral wording above.)

- [ ] **Step 4: branding.py — neutral whitelabel defaults**

- Line 37: `"whitelabel_subtitle": "INOTEX"` → `"whitelabel_subtitle": "پردیار"`.
- Line 5 docstring: `Defaults here reproduce the INOTEX chat pixel-for-pixel` → `Defaults here give a new install the stock Padyar look`.
- Line 36 comment: `every theme header hardcoded "INOTEX" before this key existed` → `every theme header hardcoded a brand name before this key existed`.
- Lines 52-53 keep `/themes/inotex/static/bg-bricks.jpg` (theme asset — global constraint).

- [ ] **Step 5: visit_plan.py — de-brand fallbacks and docstring**

- Docstring lines 1-6: `match a visitor's profile to INOTEX sections` → `to the event's sections`; drop `INOTEX 2026` and `verified from inotex.com and listed in content/sources.json` (the file no longer exists) — reword to "listed in the taxonomy the admin maintains".
- Lines 197-200 fa/en fallback strings: remove `https://inotex.com/` and "official INOTEX 2026 sections" — the fa string becomes `"هنوز فهرست شرکت‌ها منتشر نشده؛ از پذیرش رویداد بپرسید."` and the en string `"These suggestions map to the event's official sections. The exhibitor directory is not published yet — ask at the reception."` (adjust to the exact wording style of the file).

- [ ] **Step 6: pet_characters.py — default character**

Line 30: `DEFAULT_CHARACTER = "inotex"` → `DEFAULT_CHARACTER = "elecomp"`. The `inotex` character directory stays selectable (global constraint).

- [ ] **Step 7: config.py + comment scrubbing**

- `app/config.py:326`: `Measured on data/eval/golden-inotex.json (2026-08-28, embedding + rerank):` → `Measured on the retired golden eval set (2026-08-28, embedding + rerank):` (keep the numeric values).
- In `bm25.py`, `rerank.py`, `answer.py`, `embeddings.py`, `search.py`, `tts_lexicon.py`, `conversations.py`, `public.py`, `chat.py`, `otp.py`: find brand mentions with `rg -n 'INOTEX|اینوتکس|inotex\.com' app/` and reword comments/examples to neutral ones. Special cases: `answer.py:393-400,545` uses `inotex.com` as the example hostname in URL-validation comments — switch the examples to `padyar.dev`/`example.com` without touching logic. `conversations.py:5-6` docstring keys `inotex_chat_history`/`inotex-visitor` describe removed PWA storage — rewrite the sentence to describe the server-side transcript only. `otp.py:502` docstring "official INOTEX sections" → "the event's official sections".

- [ ] **Step 8: `.env.example`**

- Lines 1-2 header: `# INOTEX Chatbot Configuration` → `# Padyar Chatbot Configuration`.
- Line 70: `ALLOWED_ORIGINS=inotex.com` → `ALLOWED_ORIGINS=padyar.com`.

- [ ] **Step 9: Run the service suites**

```bash
.venv/bin/python -m pytest tests/test_branding.py tests/test_pet_characters.py tests/test_answer_firewall.py \
  tests/test_named_entity_guard.py tests/test_unknown_entity_guard.py tests/test_scope* tests/test_intent_routing.py -q
```

Expected: PASS. Where a test asserts the old default strings (e.g. `the INOTEX exhibition`), update the assertion to the new defaults from this task's Interfaces block.

- [ ] **Step 10: Commit**

```bash
git add -A && git commit -m "feat: neutral brand defaults across services"
```

---

### Task 6: Eval data, smoke scripts, run_eval CLI

**Files:**
- Modify: `data/eval/personas.json`, `data/eval/smoke-options.json`, `data/visit-taxonomy.json`, `scripts/run_eval.py`, `scripts/smoke-live.py`
- Test: whatever imports these (`rg -l 'smoke-options|personas.json|visit-taxonomy' tests/ scripts/`)

**Interfaces:**
- Produces: `run_eval.py` CLI where `--golden PATH` is required (exits with usage error if missing); `--out` default becomes `data/eval/retrieval-eval.json`.

- [ ] **Step 1: Neutralize the eval data**

- `data/eval/personas.json:60`: `{"say": "من شرکت های فعال در حوزه هوش مصنوعی که در اینوتکس شرکت کرده اند رو ...", "expect": "list"}` → `{"say": "شرکت‌های فعال در حوزه هوش مصنوعی رو معرفی کن", "expect": "list"}`.
- `data/eval/smoke-options.json:31,33`: replace `اینوتکس چیست؟` / `محل برگزاری اینوتکس کجاست؟` with option-shape questions that do not need facts: `سلام، خوش امدی؟` and `دستیار تو چه زمینه‌ای کمک می‌کنه؟` (keep `"expect": "answer"`, `"cat": "named"` values; the smoke checks response shape, not facts).
- `data/visit-taxonomy.json:337`: `"fa": "اینوتکس پیچ و بتل"` → `"fa": "پیچ و بتل"`; line 456: `"fa": "استیج اینوتکس"` → `"fa": "استیج اصلی"`.

- [ ] **Step 2: run_eval.py — require --golden**

- Line 76-77: delete `GOLDEN = ROOT / "data" / "eval" / "golden-inotex.json"`; change `DEFAULT_OUT` to `ROOT / "data" / "eval" / "retrieval-eval.json"`.
- Line ~378: `p.add_argument("--golden", required=True, help="Path to a golden JSON file")` and `p.add_argument("--out", default=str(DEFAULT_OUT))`; every later use of `args.golden`/`GOLDEN` follows from the argparse value (rename references accordingly).
- Docstring line 4: `Runs the golden INOTEX dataset (...)` → `Runs a golden dataset (passed with --golden) against the ...`.
- Line 377 description: `Run the INOTEX retrieval benchmark.` → `Run the retrieval benchmark.`
- Line 556 comment `CURRENT INOTEX information` → `current event information`.

- [ ] **Step 3: smoke-live.py — neutral probes**

- Lines 81/96/105: `{"message": "اینوتکس چیست", "lang": "fa"}` → `{"message": "سلام", "lang": "fa"}` (all three sites); the assertions around them check status/response shape — keep them, adjust only if they assert answer text.
- Line 167 `get(f"{base}/themes/inotex/static/style.css")` stays (theme asset).

- [ ] **Step 4: Verify consumers**

```bash
rg -n 'golden-inotex|اینوتکس|INOTEX' scripts/ data/ -g '!data/chat*' | head
.venv/bin/python scripts/run_eval.py 2>&1 | head -3
```

Expected: rg — no output; run_eval — usage error mentioning `--golden` is required.

- [ ] **Step 5: Run related tests and commit**

```bash
.venv/bin/python -m pytest tests/test_rerank_coverage_query.py tests/test_synonym_suggest.py tests/test_taxonomy_admin.py tests/test_visit_plan.py -q
git add -A && git commit -m "chore(eval): neutral golden data and require --golden"
```

Expected: PASS.

---

### Task 7: Templates and static files

**Files:**
- Modify: `templates/admin/{login,base,layout,dashboard,conversations,ai_providers,settings_ai,settings_backup,settings_sms,infra_backups}.html`, `templates/otp/verify.html`, `static/chat/base.css`, `static/chat/core.js`, `static/companion/*.js` (comments only)

**Interfaces:** Produces the neutral admin/OTP chrome strings: title suffix «| پردیار», login heading «مدیریت چت‌بات پردیار».

- [ ] **Step 1: Titles and headings**

- `templates/admin/base.html:7`: `<title>{% block title %}پنل مدیریت{% endblock %} | اینوتکس</title>` → `... | پردیار</title>`.
- `templates/admin/login.html:15`: `<h4>مدیریت چت‌بات اینوتکس</h4>` → `<h4>مدیریت چت‌بات پردیار</h4>`.
- `templates/otp/verify.html:7`: `<title>تأیید کد | اینوتکس</title>` → `<title>تأیید کد | پردیار</title>`.
- `templates/admin/settings_ai.html:94` placeholder `e.g. the INOTEX exhibition` → `e.g. the exhibition`.

- [ ] **Step 2: Remaining template mentions**

```bash
rg -n 'INOTEX|اینوتکس' templates/
```

Reword each hit neutrally (e.g. dashboard/ai_providers copy that says the bot serves the INOTEX exhibition → "the event"). Do not touch `/themes/inotex/` asset URLs.

- [ ] **Step 3: Static comment scrub (no identifier renames)**

```bash
rg -n 'INOTEX|اینوتکس|Pet-INOTEX' static/ | rg -v 'inotex-(pose|fallback)|--inotex-|inotex-pet'
```

For each hit: `static/chat/base.css:1` `/* ── INOTEX Chat Base CSS ── */` → `/* ── Padyar Chat Base CSS ── */`; `static/companion/companion.js` + `companion-ui.js` comments referencing "Pet-INOTEX"/"Pet-Inotex module" → "the standalone pet module" (keep all `inotex-*` key/atlas identifiers untouched); `static/otp/otp.css:2` `INOTEX OTP verification` → `Padyar OTP verification`; `static/chat/core.js` header/brand mentions → neutral. CSS custom properties `--inotex-*` stay (global constraint).

- [ ] **Step 4: Run UI suites and commit**

```bash
.venv/bin/python -m pytest tests/test_public_ui.py tests/test_admin_brand.py tests/test_tts_admin.py tests/test_ai_admin_ui.py tests/test_otp_input_autofill.py -q
git add -A && git commit -m "chore(ui): neutral chrome strings and comments"
```

Expected: PASS.

---

### Task 8: Deploy surface — elecomp only

**Files:**
- Modify: `deploy/padyar-deploy.sh`, `deploy/{00-bootstrap-server,05-create-databases,10-install-app,15-nginx-and-ssl,17-watchdog,30-verify,40-cloudflare-tunnel,45-prerender,50-install-github-runner}.sh`, `deploy/watchdog/watchdog.py`, `deploy/README.md`, `deploy/tts/*.py`, `setup.sh`, `scripts/{net-diag,make-handover-zip,export-otp-module,compress-videos,import-content,capture_proposal_shots}.py`

**Interfaces:** Produces deploy tooling that only knows the `elecomp` instance (port 8002) — `{inotex|elecomp}` choices collapse to `elecomp`.

- [ ] **Step 1: Instance selectors — drop the inotex case**

- `deploy/padyar-deploy.sh:44,46`: delete the `inotex) PORT=8001 ;;` line; usage becomes `Usage: sudo $0 elecomp <commit-sha>`.
- `deploy/10-install-app.sh:4,15,17`: same pattern; usage `sudo bash $0 elecomp`.
- `deploy/45-prerender.sh:15-16`: `elecomp) ;;` only; usage `{elecomp}`.
- `deploy/00-bootstrap-server.sh:38,48`, `deploy/05-create-databases.sh:21,71,75`, `deploy/17-watchdog.sh:16,41,53,61,71`, `deploy/30-verify.sh:10,17,33,39,62,84`: remove `padyar-inotex`/`inotex` from every loop/array/title-map; `17-watchdog.sh:41` title map loses its inotex entry; `30-verify.sh:39` drops the `padyar-inotex.service` grep.
- `deploy/15-nginx-and-ssl.sh:3,18`: `DOMAINS=(elecomp.padyar.com)`; reword the header comment.
- `deploy/40-cloudflare-tunnel.sh:81-85,141,160`: remove the inotex ingress/hostname blocks and the inotex host in cert/verify loops; final log line references `elecomp.padyar.com`.

- [ ] **Step 2: README, watchdog, tts, setup, misc scripts**

- `deploy/README.md`: delete the INOTEX row (line 7) and every `inotex` step/example (lines 24-29, 70, 230 reword, 249); `elecomp` becomes the running example.
- `deploy/watchdog/watchdog.py`, `deploy/tts/*.py`, `setup.sh`, `scripts/net-diag.py:48` (`INOTEX Assistant Network Diagnostic` → `Padyar Assistant Network Diagnostic`), `scripts/compress-videos.py:4` (reword to "the 2026 event batch"), `scripts/import-content.py:52` (reword comment), `scripts/make-handover-zip.py:252` (`inotex-chatbot-handover-` → `padyar-chatbot-handover-`), `scripts/export-otp-module.py:103-105` (doc table: `INOTEX` default → `پردیار`; "INOTEX hexagon SVG" → "brand hexagon SVG"; "INOTEX atlas path" → "companion atlas path").

- [ ] **Step 3: Syntax-check every touched script**

```bash
for f in deploy/*.sh setup.sh; do bash -n "$f" || echo "FAIL: $f"; done
.venv/bin/python -m py_compile deploy/watchdog/watchdog.py deploy/tts/*.py scripts/net-diag.py scripts/make-handover-zip.py scripts/export-otp-module.py scripts/compress-videos.py scripts/import-content.py scripts/capture_proposal_shots.py
```

Expected: no FAIL, no py_compile output.

- [ ] **Step 4: Verify and commit**

```bash
rg -n 'INOTEX|اینوتکس|inotex' deploy/ setup.sh --glob '!deploy/env/elecomp.env.template' | rg -v 'migrations'
git add -A && git commit -m "chore(deploy): elecomp-only deploy surface"
```

Expected: rg — only allowed identifiers if any (there should be none in deploy/ after this task; `inotex.env.template` was deleted in Task 3).

---

### Task 9: `.claude/` skills, launch.json, spikes

**Files:**
- Modify: `.claude/launch.json`, `.claude/skills/**` (~12 files), `spikes/*.py`

**Interfaces:** none (tooling docs only).

- [ ] **Step 1: Scrub brand mentions (paths already renamed in Task 2)**

```bash
rg -n 'INOTEX|اینوتکس|inotex\.com|inotex\.padyar' .claude/ spikes/ --hidden
```

Reword each hit: skills' examples referencing the INOTEX bot become "the exhibition bot"/"the event install"; `inotex.com` URLs → `padyar.com`; keep `/themes/inotex/` and CSS-var/storage-key identifiers if any appear.

- [ ] **Step 2: Verify and commit**

```bash
rg -n 'INOTEX|اینوتکس|inotex\.com|inotex\.padyar' .claude/ spikes/ --hidden
git add -A && git commit -m "docs(claude): neutral agent tooling references"
```

Expected: rg — no output.

---

### Task 10: Docs scrub (full committed-tree scope), CHANGELOG, remaining.md

**Files:**
- Delete: `docs/knowledge-based-evidence/` (entire directory — the retired event's evidence package; measurements cannot be neutralized), `docs/superpowers/plans/2026-09-14-assessment-remediation.md` (finished plan artifact), `docs/engineering/ai-providers/research/_git-baseline-before-ai-phase.txt` if it is a frozen historical capture whose inotex mentions cannot be reworded without falsifying it — assess and delete rather than falsify
- Modify (scrub in place, mechanical rewording): `CHANGELOG.md` (2 hits), `remaining.md` (3 hits), `README.md`, `AGENTS.md`, `CLAUDE.md`, `docs/engineering/{AI_ASSISTANCE_LOG,ARCHITECTURE,CODE_OWNERSHIP,DECISIONS,DEPLOYMENT_RUNBOOK,HUMAN_REVIEW_CHECKLIST}.md`, `docs/engineering/ai-providers/research/kimi.md`, every `docs/features/**` file matching `rg -li 'INOTEX|اینوتکس|inotex\.com|secure-panel-inotex'` — replace brand words with neutral ones («رویداد»/"the event"), old admin path already renamed by Task 2's sed, drop stale freshness-workflow references (AGENTS.md/CLAUDE.md describe the deleted `padyar-freshness@` units and `scripts/refresh-inotex-context.py` as live — reword or remove those sentences; do not describe machinery that no longer exists)
- Add: `docs/superpowers/specs/2026-09-15-remove-inotex-from-source-design.md`, `docs/superpowers/plans/2026-09-15-remove-inotex-from-source.md`

**Interfaces:** none.

- [ ] **Step 1: CHANGELOG**

```bash
rg -n -i 'inotex' CHANGELOG.md
```

Rewrite the two lines to neutral wording (e.g. "official exhibition chatbot" / "the event install"). Keep dates and semantics; only the brand word changes.

- [ ] **Step 2: remaining.md**

- `/secure-panel-inotex/...` occurrences were already renamed by Task 2's sed; verify with `rg -n 'inotex' remaining.md` and reword any remaining brand mentions (e.g. "inotex.com opens from this machine" → "the exhibition site opens from this machine", `inotex_chat_history` if present → describe neutrally).

- [ ] **Step 3: Commit docs**

```bash
git add CHANGELOG.md remaining.md docs/superpowers/
git commit -m "docs: scrub inotex mentions; record the removal spec and plan"
```

---

### Task 11: CI guard + final verification

**Files:**
- Modify: `.github/workflows/ci.yml` (new job `identity-guard`)

**Interfaces:** Produces the guard that keeps the brand out (allowlist: `themes/`, `migrations/`, `static/otp/pet/characters/inotex/`, `docs/superpowers/`).

- [ ] **Step 1: Add the guard job to ci.yml**

Append after the existing jobs:

```yaml
  identity-guard:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: The retired event brand must not return
        run: |
          sudo apt-get update -qq && sudo apt-get install -y -qq ripgrep >/dev/null
          rg -n 'INOTEX|اینوتکس|inotex\.com|inotex\.padyar|padyar-inotex|secure-panel-inotex' \
            --hidden -g '!node_modules' -g '!.git' \
            -g '!themes/**' -g '!migrations/**' \
            -g '!static/otp/pet/characters/inotex/**' \
            -g '!docs/superpowers/**' \
            && { echo "::error::Retired event brand found outside the allowlist"; exit 1; } \
            || echo "clean"
```

(Ubuntu runners ship `rg` preinstalled in newer images; the apt line is belt-and-braces and harmless if already present.)

- [ ] **Step 2: Run the same check locally**

```bash
rg -n 'INOTEX|اینوتکس|inotex\.com|inotex\.padyar|padyar-inotex|secure-panel-inotex' \
  --hidden -g '!node_modules' -g '!.git' \
  -g '!themes/**' -g '!migrations/**' \
  -g '!static/otp/pet/characters/inotex/**' \
  -g '!docs/superpowers/**'
```

Expected: no output. If hits remain, fix them (they belong to an earlier task's scope — fix and amend that layer's commit if unpushed, otherwise a follow-up fix commit).

- [ ] **Step 3: Review the intentional lowercase leftovers**

```bash
rg -n -i 'inotex' --hidden -g '!node_modules' -g '!.git' -g '!docs/superpowers' -l
```

Expected: only `themes/**`, `migrations/**`, `static/otp/pet/characters/inotex/`, files containing `--inotex-` CSS vars / `inotex-pet-*` / `inotex-light-mode` / `inotex-theme` / `inotex_chat` storage keys / `/themes/inotex/` asset URLs / `active_theme` value `"inotex"` / `inotex-pose-atlas`/`inotex-fallback` asset names. Anything else gets scrubbed.

- [ ] **Step 4: Full local suite + collection count**

```bash
.venv/bin/python -m pytest --collect-only -q | tail -2
.venv/bin/python -m pytest -q -x --ignore=tests/postgres 2>&1 | tail -5
```

Expected: collection within ~50 of the Task 1 baseline minus deleted suites; suite PASS or only the known environment-sensitive failures this machine already had before the change (compare against baseline notes; CI is the merge signal).

- [ ] **Step 5: Commit and open the PR**

```bash
git add -A && git commit -m "ci: guard against the retired event brand returning"
git push -u origin chore/remove-inotex-from-source
gh pr create --title "feat!: remove the retired event brand from source" \
  --body-file - <<'EOF'
## What
Strips every INOTEX / اینوتکس brand reference so the source is a neutral per-customer platform. Implements docs/superpowers/specs/2026-09-15-remove-inotex-from-source-design.md.

## Kept on purpose
themes/ (incl. inotex theme + localStorage keys), migrations/ (checksum-guarded), pet character inotex/ (default is now elecomp), --inotex-* CSS vars, /themes/inotex/ asset URLs.

## Breaking
- Admin URL: /secure-panel-inotex → /secure-panel-admin (all deployments, incl. elecomp bookmarks).
- Fresh installs seed no content.
- inotex.padyar.com stops deploying from this repo.
- run_eval.py now requires --golden.

## Verification
- Local: full suite green (minus pre-existing env-sensitive tests), bash -n on all deploy scripts, identity-guard pattern clean.
- CI: identity-guard job added; blocks the brand outside the allowlist.
EOF
```

(Push/PR only after Sina confirms — commits were approved as part of the one-PR approach; pushing is the explicit final gate.)
