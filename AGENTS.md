# AGENTS.md — PadyarAIChatbot

**This application must be usable by anyone — from a kid to an elderly person. No special knowledge required.**

This is the #1 product principle. It overrides everything else.

- If a feature needs an explanation, it's too complex.
- If a user has to think about what to do next, the UI is wrong.
- If a user has to read a manual to use the chatbot, the chatbot is wrong.
- Every screen must be understandable in under 3 seconds.
- Every action must be completable in under 3 clicks.
- No jargon. No technical terms in user-facing UI. No confusing settings.
- Default everything to "just work." Advanced options are optional and hidden.
- A user with zero AI knowledge should be able to use every feature immediately.
- The admin panel must be intuitive enough for non-technical staff.

**When in doubt: simplify. Remove. Hide. Auto-detect. Default.**

## Communication

Malik-e product (Sina) Finglish minevisi — farsi ba horuf-e latin. Jawab-ha
HAMESH Finglish ast. Hich vaght parsi script, hich vaght makhs. Faghat baraye
chat — code, commit, doc tu zaban-e khod.

---

## Project Overview

**PadyarAIChatbot** is a **CMS for AI chatbots** — installed per-customer. Each customer deploys the app, gets the features they ordered (core + selected optional modules), enters their own content (Q&A dataset, videos), customizes branding (name, logo, colors), and manages everything through the admin panel.

The chatbot uses a two-tier intelligence system — local knowledge base matching via TF-IDF, with AI fallback via OpenAI (GPT-5 Nano for classification, GPT-4.1 for free-text generation) through the GapGPT proxy.

- **Language:** Python 3.10+
- **Framework:** FastAPI + Uvicorn
- **Database:** PostgreSQL 16 (production). SQLite is the test backend and a rollback artifact only.
- **Frontend:** Vanilla HTML/CSS/JS (chat) + Bootstrap 5 RTL (admin)
- **AI:** OpenAI via GapGPT proxy (`https://api.gapgpt.app/v1`) — GPT-5 Nano (classification), GPT-4.1 (chat), Whisper-1 (voice)
- **Search:** scikit-learn TF-IDF + cosine similarity
- **Font:** Vazirmatn (Persian)

## Prerequisites

- Python 3.10+
- pip
- OpenAI API key (used via GapGPT proxy)

## Setup

```bash
# Interactive installer (recommended)
chmod +x setup.sh && ./setup.sh

# Manual
pip install -r requirements.txt
cp .env.example .env
# Edit .env — add OPENAI_API_KEY
python main.py
```

App starts at `http://127.0.0.1:8000`.

### Environment Variables

| Variable          | Required | Description                                                             |
| ----------------- | -------- | ----------------------------------------------------------------------- |
| `OPENAI_API_KEY`  | Yes      | API key for all AI operations via GapGPT proxy                          |
| `VIDEO_BASE_URL`  | No       | Base URL for videos (default: `/media/videos`) |
| `ENABLED_MODULES` | No       | Comma-separated optional modules to enable (empty = all)                |

## Commands

| Command                      | Description                               |
| ---------------------------- | ----------------------------------------- |
| `python main.py`             | Start dev server (port 8000, auto-reload) |
| `python scripts/change-admin.py`     | Change admin password             |
| `python scripts/debug_similarity.py` | Debug TF-IDF similarity matching  |
| `python scripts/net-diag.py`        | Network diagnostics               |
| `python scripts/gapgpt_test.py`      | Test GapGPT API connectivity      |

## Testing

**Tests run on GitHub, not on this machine.** `.github/workflows/ci.yml` runs
the full pytest suite plus the retrieval/safety eval on every push and PR —
that run is the pass/fail signal. Don't run the whole `pytest` suite locally
as a commit or merge gate: this machine has 15 tests that always fail here
and always pass on CI (env/network-only, e.g. tests needing a live
PostgreSQL), so a local full run is not a trustworthy signal.

```bash
gh run list --branch <branch> --limit 1
gh run watch
```

`python -m py_compile <file>` on files you touched is still fine as a quick
local syntax check before pushing. Running a single test file locally while
writing it (TDD red/green) is fine too — just don't treat a local full-suite
run as the merge gate.

## Project Structure

```
PadyarAIChatbot/
  main.py                        # Entry point — uvicorn runner
  setup.sh                       # Interactive installer
  requirements.txt               # 9 Python dependencies
  .env / .env.example            # Config

  app/                           # Application package
    main.py                      # FastAPI app factory, lifespan, middleware
    config.py                    # All config — env vars, paths, thresholds
    models.py                    # Pydantic schemas

    routers/                     # Route handlers
      public.py                  # Public pages + health check
      chat.py                    # /chat — core chatbot pipeline
      admin.py                   # Admin stats, settings, export
      voice.py                   # /api/transcribe (Whisper)
      synonyms.py                # Synonym CRUD
      dataset.py                 # Dataset + questions + video CRUD
      themes.py                  # Theme listing/activation

    services/                    # Business logic
      search.py                  # TF-IDF matching, dataset loading
      openai.py                  # GPT classification, chat, Whisper
      themes.py                  # Theme discovery

    db/                          # Database layer
      connection.py              # SQLite init, schema, seeding
      queries.py                 # All database operations

    auth/                        # Security
      security.py                # Rate limiting, HMAC tokens, admin auth

    utils/                       # Utilities
      normalizer.py              # Persian text normalization + synonyms

    modules/                     # Module system
      registry.py                # Module definitions, conditional loading

  templates/admin/               # Jinja2 admin templates (extends base.html > layout.html)
  static/admin/js/               # Admin JS modules (one per page)
  static/vendor/                 # Bootstrap, Chart.js, FontAwesome, Vazirmatn, marked.js

  themes/                        # Pluggable chat UI themes
    liquid-glass/                # Default — Apple-inspired frosted glass
    minimal/                     # Clean minimal theme

  data/                          # Knowledge base
    dataset.json                 # ~70 Q&A entries with video URLs
    questions.json               # ~800+ question-to-dataset mappings
    Videos/                      # Source video files

  media/                         # Runtime media
    videos/                      # Admin-uploaded videos
    uploads/                     # General uploads (YYYY/MM/)

  docs/                          # Documentation hub
  index.html                     # Root chat UI
  scripts/                       # Standalone dev/ops utilities (run from root)
```

## Dependencies (requirements.txt)

| Package            | Purpose                                  |
| ------------------ | ---------------------------------------- |
| `fastapi`          | Web framework                            |
| `jinja2`           | Template engine (admin panel)            |
| `uvicorn`          | ASGI server                              |
| `scikit-learn`     | TF-IDF vectorization + cosine similarity |
| `openai`           | OpenAI API client (via GapGPT proxy)     |
| `python-multipart` | File upload handling                     |
| `numpy`            | Numerical operations                     |
| `httpx`            | HTTP client                              |
| `python-dotenv`    | .env file loading                        |

## Architecture

### Two-Tier Intelligence

1. **Tier 0 — Curated questions (exact):** Jaccard-only match against the hand-mapped question index. Serves at ≥ 0.9.
2. **Tier 1 — Local Knowledge Base:** Persian normalization → synonym expansion → BM25 + local embeddings (TF-IDF is the fallback backend) → reranking. Trusted at `TRUSTED_MATCH_THRESHOLD` = **0.70**.
3. **Tier 1.5 — Per-install intent classifier:** logistic regression over local embeddings, retrained on every dataset edit. Serves at `INTENT_TRUST_THRESHOLD` = **0.6**.
4. **Tier 2 — AI Fallback (via GapGPT proxy):** GPT-5 Nano classifies intent → if a dataset match is found, return that entry; if out-of-domain, GPT-4.1 generates a conversational response. When AI is unavailable, only a strong local match answers (`LOCAL_FALLBACK_THRESHOLD` = 0.45, `QUESTIONS_FALLBACK_THRESHOLD` = 0.60), else 503.

`app/config.py` is authoritative for every threshold above.

### Module System

All features are modules. Each module has its own router and optional service layer.

**Two categories:**

| Category                               | Behavior                                                                                         | Examples                                      |
| -------------------------------------- | ------------------------------------------------------------------------------------------------ | --------------------------------------------- |
| **Core modules** (`is_core=True`)      | Always enabled. Ship with every installation. Cannot be disabled.                                | `chat`, `admin`, `search`, `dataset`, `theme` |
| **Optional modules** (`is_core=False`) | Enabled/disabled per installation via `ENABLED_MODULES` env var. Customer orders these features. | `voice`, `video`, `registration`              |

`app/modules/registry.py` is authoritative. Note `whitelabel` is **not** a module — branding is a set of `whitelabel_*` rows in the `settings` table (see below).

**How it works:**

- At install time, set `ENABLED_MODULES=voice,video` to enable specific optional modules
- If `ENABLED_MODULES` is empty → all optional modules load (full-featured install)
- Core modules always load regardless of the env var
- Every **new feature** must be implemented as an optional module (`is_core=False`) — only promote to core if every customer needs it

Module definition pattern in `app/modules/registry.py`:

```python
@dataclass
class ModuleDef:
    name: str
    description: str
    is_core: bool = False        # True = always on, False = per-install toggle
    router_module: str = ""      # e.g. "app.routers.voice"
    router_var: str = "router"
```

Core modules always load. Optional modules load only when listed in `ENABLED_MODULES` env var.

### White-Label / Branding

This is a CMS installed per-customer — branding customization is a first-class feature.

**Storage:** Key-value `settings` table with `whitelabel_` prefix. Follows WordPress `wp_options` pattern.

| Key                        | Default           | Description          |
| -------------------------- | ----------------- | -------------------- |
| `whitelabel_app_name`      | `پادیار ویدیو چت` | App display name     |
| `whitelabel_logo_url`      | `/LOGO/logo.jpg`  | Logo image URL       |
| `whitelabel_favicon_url`   | (none)            | Favicon URL          |
| `whitelabel_primary_color` | `#4f46e5`         | Primary brand color  |
| `whitelabel_accent_color`  | `#10b981`         | Accent color         |
| `whitelabel_sidebar_color` | `#1e1b4b`         | Admin sidebar color  |
| `whitelabel_welcome_text`  | (default)         | Chat welcome message |
| `whitelabel_footer_text`   | (default)         | Footer text          |
| `whitelabel_custom_css`    | (empty)           | Extra CSS override   |

**Template injection:** Starlette `context_processors` inject branding into all Jinja2 templates automatically. No need to pass variables to every `render()` call.

**Dynamic CSS:** A `/theme.css` endpoint (FastAPI `Response` with `media_type="text/css"`) generates CSS custom properties from DB values. Both admin and chat UI use `var(--brand-primary)` etc.

**Color picker:** Native `<input type="color">` — zero dependencies, outputs `#rrggbb` hex.

**Public chat UI:** String-replacement pattern (`html.replace("<!-- APP_NAME -->", app_name)`) since themes use raw HTML, not Jinja2.

### Database (PostgreSQL 16 — schemas `app` + `observability`)

| Table            | Purpose                                                       |
| ---------------- | ------------------------------------------------------------- |
| `chat_logs`      | Chat interactions (query, response, confidence, tokens, cost) |
| `settings`       | Key-value runtime settings (includes `whitelabel_*` keys)     |
| `dataset`        | Knowledge base entries (title, text, video_url)               |
| `questions`      | Question-to-dataset mappings                                  |
| `synonyms`       | Persian synonym mappings                                      |
| `admins`         | Admin credentials (SHA-256 + salt)                            |
| `admin_sessions` | Active sessions with sliding expiry                           |
| `media`          | Uploaded file metadata                                        |

### Security

- HMAC-signed chat tokens (validated on every `/chat` **and** `/api/transcribe` request)
- Origin/Referer validation against allowlist
- Rate limiting: `CHAT_RATE_LIMIT` requests per `CHAT_RATE_WINDOW` seconds per IP (defaults: 20 / 60, env-overridable) — sliding-window counters live in the `rate_limit_hits` table so they are shared across workers and survive restarts
- Request body ceiling: `MAX_BODY_BYTES` (default 512 MB) via the Content-Length header, plus per-endpoint read caps where uploads are buffered in memory
- Admin: bcrypt passwords (legacy SHA-256 rows upgrade on next login), bcrypt security answers (legacy rows upgrade too), session cookies, brute-force lockout (5 attempts → 5 min, stored in the `login_attempts` table so it survives a restart and is shared across workers)
- Sliding admin sessions (1 hour); a password change revokes every other session for that admin
- Secrets stored encrypted at rest (`enc:` Fernet tokens via `app/services/secure_store.py`, including the legacy `ai_api_key`) — `get_setting()` decrypts transparently
- CSV exports neutralize spreadsheet formula injection; admin-editable branding injected into public HTML is escaped
- `data/` is NOT served over HTTP; static mounts cover `/static`, `/media`, `/LOGO`, `/themes/*` only

### Theme System

Self-contained themes in `/themes/{name}/` — each has `theme.json`, `index.html`, `static/style.css`, `screenshot.png`. Auto-discovered at startup. Active theme stored in DB settings. Current: `liquid-glass` (default), `minimal`.

## Key Files

| File                      | Purpose                                   |
| ------------------------- | ----------------------------------------- |
| `app/config.py`           | All configuration — read this first       |
| `app/routers/chat.py`     | Core chatbot pipeline — the main endpoint |
| `app/services/search.py`  | TF-IDF matching engine                    |
| `app/services/openai.py`  | All AI integration                        |
| `app/db/connection.py`    | Database schema and seeding               |
| `app/auth/security.py`    | All security logic                        |
| `app/utils/normalizer.py` | Persian text processing                   |
| `app/modules/registry.py` | Module definitions                        |

## How a Feature Ships (the anti-scaffold rule)

A 2026-08 audit of this repo found the same defect class seven times over: a
capability **built but never wired to its production call-site** — a rate
limiter with a per-identity `key` param no route ever passed, a conversation
cookie read on every request but never set, a maintenance mode fully enforced
with no UI to toggle it, a documented white-label system that did not exist in
code (PR #17 fixed all seven). The rule below exists so that class of defect
cannot merge again.

**A feature is a scenario, not a capability.** Before building, name the
scenario: who triggers it, from where, under what real conditions (a NAT'd
booth sharing one IP, a visitor an hour into a conversation, a non-technical
operator at 3 clicks, two installs deploying from one branch). If the scenario
cannot be named, the feature is not specified. The scenario — not the
mechanism — is what gets tested and reviewed.

### Flow: spike → prototype → spec → wire → verify

1. **Spike** (optional, timeboxed) — de-risk the unknown. Throwaway code, no
   commit to main.
2. **Prototype** — the thinnest vertical slice that exercises the scenario
   end-to-end. If the slice cannot reach the scenario, the design is wrong.
3. **Spec** (`docs/features/{slug}/`) — written AFTER the prototype, from what
   it proved. A spec documents what IS shipping. A spec describing machinery
   that does not exist in code is a defect (doc-fiction), not a roadmap —
   planned items live in the feature folder, clearly marked.
4. **Wire** — every param, function, endpoint, setting, cookie or table this
   feature introduces has its production caller **in the same change**. A
   `key=` param with zero callers, an endpoint with zero consumers, a reader
   with no writer: all are review-blocking defects, not forward compatibility.
5. **Verify** — the scenario has a test that **fails when the wiring is
   removed**. Write it first and watch it fail (red), then make it pass. A
   test that asserts the unwired behavior (e.g. per-IP limiting when the
   design says per-identity) is an approval of the bug, not a guard.

### Reader–writer pairs must close

The audit's failures were one-sided mechanisms. These pairs must BOTH ship or
neither does: cookie read ↔ cookie set; token validated ↔ token refreshable;
secret saved ↔ secret reachable by its consumer; admin page ↔ its data API
(module-gated together); sidebar link ↔ route exists; docs ↔ code. Health and
panel status must reflect whether the feature can actually serve (routes
exist, wiring live), never merely whether config is present — green with zero
routes is a defect.

### Scale

Bugfix / small change: no phases — just the checklist above at review time.
New feature or optional module: the full flow, and the spec folder is the
record of the scenario.

## Patterns to Follow

### New Module (required for all features)

1. Define in `app/modules/registry.py` as a `ModuleDef` — always `is_core=False` (optional) unless every customer needs it
2. Create `app/routers/{name}.py` with `APIRouter`
3. Create `app/services/{name}.py` for business logic
4. Router auto-loads at startup via `load_module_routers()` when listed in `ENABLED_MODULES`
5. For the customer's installation, add the module name to their `ENABLED_MODULES` env var

### New Admin Page

1. Create `templates/admin/{name}.html` extending `layout.html`
2. Create `static/admin/js/{name}.js` for page logic
3. Add route in appropriate router
4. Add sidebar link in `templates/admin/layout.html`

### New White-Label Setting

1. Add key to `settings` table (prefix with `whitelabel_`) + default in `WL_DEFAULTS` (`app/services/branding.py`)
2. Escape it in `chat_branding_context()` (theme env is `autoescape=False`)
3. Add field to Settings → برندینگ (`templates/admin/settings_branding.html` + `initBranding()`)
4. Extend the theme page-cache key (`themes.py`) if the value is baked into the chat shell

### New Theme

1. Create `/themes/{name}/` with `theme.json`, `index.html`, `static/style.css`, `screenshot.png`
2. Auto-discovered at startup — no registration needed

### Database Changes

1. Add a new versioned file `migrations/NNNN_name.sql` (PostgreSQL owns the schema)
2. Apply with `python scripts/apply_migrations.py` (idempotent, checksum-guarded)
3. Mirror test-suite needs in the SQLite DDL (`app/db/connection.py` and the `ensure_*` helpers)
4. Add queries in `app/db/queries.py` — `?` placeholders and `INSERT OR IGNORE` are translated by `app/db/pg.py`

## Configuration Reference

All config in `app/config.py`:

| Setting                 | Default | Purpose                        |
| ----------------------- | ------- | ------------------------------ |
| `SIMILARITY_THRESHOLD`  | 0.20    | Min confidence for local match |
| `MAX_LOGIN_ATTEMPTS`    | 5       | Admin brute-force limit        |
| `BLOCK_TIME_MINUTES`    | 5       | Admin lockout duration         |
| `SESSION_TIMEOUT_HOURS` | 1       | Admin session lifetime         |
| `CHAT_RATE_LIMIT`       | 2       | Max requests per window        |
| `CHAT_RATE_WINDOW`      | 30      | Rate limit window (seconds)    |
| `CHAT_TOKEN_TTL`        | 3600    | HMAC token lifetime (seconds)  |

## Documentation

`docs/` is the knowledge base. Keep it current.

| When                  | Update                                  |
| --------------------- | --------------------------------------- |
| New feature           | `docs/features/{slug}/RESEARCH.md`      |
| New/changed service   | the Tech Stack + module tables in `CLAUDE.md` |
| Setup changes         | the Setup section in `CLAUDE.md`, and `README.md` |
| Feature status change | `docs/features/INDEX.md`                |
| Architectural decision| `docs/engineering/DECISIONS.md`         |

> `docs/_other-product-padyar-ai/` documents a DIFFERENT product and must never
> be updated for work done in this repository.

One feature, one folder in `docs/features/{slug}/`.

---

## Session Handoff — 2026-08-14 (INOTEX instance, Padyar platform)

The product instance is now **INOTEX** (پانزدهمین نمایشگاه بین‌المللی نوآوری و
فناوری — INOTEX 2026). The reusable platform layer is named **Padyar**.

- **Identity:** all previous-event identity was removed from the working tree.
  Canonical names: display "INOTEX Chatbot", package `inotex-chatbot`,
  admin route prefix `/secure-panel-inotex`.
- **Content:** the knowledge seed (`app/default_content.py`) carries facts
  verified against https://inotex.com/ on 2026-08-14. The machine-readable
  source manifest is `content/sources.json`; conflicts pending human review
  live in `content/review-queue.md`. Freshness checking:
  `python3 scripts/refresh-inotex-context.py`.
- **Mascot policy:** the Pet-INOTEX companion is **back on** (owner request,
  2026-08-24) via `themes/inotex/partials/footer.html` +
  `static/companion/companion{,-ui}.js` — desktop/tablet only, hidden below a
  640px viewport by the theme CSS. The old pet iframe, its `/assets` mount and
  `static/pet/` remain removed.
- **Design:** the INOTEX theme uses the official palette
  (#FCB715, #FEBE27, #2D5CA7, #1E2D52, #04A584, #00644F, #000000, #FFFFFF)
  as design tokens. The frontend skeleton (routes, partial hierarchy,
  chat/video tabs, input region) is preserved — do not restructure it.
- **Reset path:** `scripts/reset-content-to-defaults.py` (backs up the DB,
  then seeds INOTEX defaults).
