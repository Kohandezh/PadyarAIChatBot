# Design: Remove all INOTEX references from source

Date: 2026-09-15 · Repo: PadyarAIChatbot · Status: approved in chat, pending implementation

## Goal

Strip every INOTEX / اینوتکس reference from the repository so the source is a
neutral, per-customer platform. Elecomp (elecomp.padyar.com, port 8002) remains
a supported deployment. Deliverable: one PR, layered commits, plus a CI guard
that blocks the identity from returning.

## Decisions (locked with Sina)

1. Scope: this repo only. `Padyar.com` (portfolio) and `padyar-wt-crm` (stale
   copy) are out of scope.
2. Deploy surface for inotex.padyar.com is removed: CI deploy job, nginx conf,
   env template, systemd unit. Elecomp deploy stays.
3. Admin path `/secure-panel-inotex` renames to `/secure-panel-admin`.
   Breaking for bookmarks on every deployment (including elecomp).
4. Historical docs are scrubbed too (CHANGELOG lines, `remaining.md`).
5. `themes/` stays untouched — the inotex theme is a design asset, including
   its localStorage keys (`inotex-light-mode`, `inotex-theme`).
6. Pet character `static/otp/pet/characters/inotex/` stays; the default
   character switches to `elecomp`.
7. `migrations/` stays untouched. Migrations are checksum-guarded and applied
   in production; editing them breaks `apply_migrations`. Comments there keep
   their historical wording.
8. `scripts/run_eval.py` loses its default golden set (the file is deleted);
   `--golden` becomes a required argument.

## Work layers

1. **Path rename** — `/secure-panel-inotex` → `/secure-panel-admin` across
   ~59 files (app, csrf, static admin JS, tests, docs, `.claude/` skills).
2. **Delete files** — `deploy/nginx/inotex.padyar.com.conf`,
   `deploy/env/inotex.env.template`, `deploy/systemd/padyar-inotex.service`,
   `.github/workflows/freshness.yml`, `scripts/refresh-inotex-context.py`,
   `scripts/import-inotex-programs.py`, `data/eval/golden-inotex.json`,
   `content/` (sources.json, freshness-report.json, review-queue.md — the
   inotex instance's content-pipeline data). CI: drop the inotex deploy step
   and summary line.
3. **De-brand code** — `app/default_content.py` keeps the seeding module but
   ships empty records; `app/services/openai.py` hardcoded "INOTEX" strings
   move to the existing `{domain}` / `{domain_en}` slots;
   `scope.py` DEFAULT_DOMAIN_EN, `sms.py` campaign default, `branding.py`
   whitelabel defaults ("INOTEX" → «پردیار» for Persian UI strings, "Padyar"
   for Latin ones), `visit_plan.py`
   fallback URLs, `pet_characters.py` DEFAULT_CHARACTER → `elecomp`; comments
   and code examples in the remaining services use neutral wording.
   Theme asset URLs (`/themes/inotex/static/...`) stay — the theme stays.
4. **Data** — `data/eval/personas.json`, `data/eval/smoke-options.json`,
   `data/visit-taxonomy.json`: replace inotex questions/program names with
   neutral ones. Eval/smoke questions assert routing and response shape, not
   inotex facts (the default knowledge base is gone).
5. **Tests** — mechanical path rename plus rewrites of assertions that quote
   inotex facts; `test_default_seed` flips to assert the empty seed.
   Target: full suite green, collection count ~unchanged minus deleted suites.
6. **Docs/meta** — CHANGELOG (2 lines), `remaining.md` (3), `deploy/README.md`,
   `.claude/` skills + `launch.json`, `setup.sh`, `.env.example`
   (`ALLOWED_ORIGINS=inotex.com` → `padyar.com`), `deploy/padyar-deploy.sh`
   and the numbered deploy scripts (elecomp-only), watchdog, deploy TTS
   scripts.
7. **CI guard** — new job: `rg -i 'inotex|اینوتکس'` fails the build outside an
   explicit allowlist: `themes/`, `migrations/`,
   `static/otp/pet/characters/inotex/`, `docs/superpowers/specs/`.

## Breaking changes

- Admin URL changes on every deployment (bookmarks, operator muscle memory).
- Fresh-install seed content is empty; content arrives via admin import.
- The retired inotex production server stops receiving deploys from this repo.

## Verification

- `.venv/bin/python -m pytest` green (CI runs the rest; postgres job blocking).
- `bash -n` on every touched deploy script; CI guard job green.
- `rg -i 'inotex|اینوتکس'` outside the allowlist returns zero matches.

## Out of scope

Git history rewrite, live server data, Padyar.com marketing site,
`padyar-wt-crm` copy, renaming the inotex theme or its assets.
