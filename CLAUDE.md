# CLAUDE.md — PadyarAIChatbot

**This application must be usable by anyone — from a kid to an elderly person. No special knowledge required.**

This is the #1 product principle. Every decision must pass this test.

- If a feature needs an explanation, it's too complex. Simplify or remove it.
- If a user has to think about what to do next, the UI is wrong. Redesign it.
- If a user has to read a manual to use the chatbot, the chatbot is wrong. Fix it.
- Every screen must be understandable in under 3 seconds.
- Every action must be completable in under 3 clicks.
- No jargon. No technical terms in user-facing UI. No confusing settings.
- Default everything to "just work." Advanced options are hidden behind a toggle.
- A user with zero AI knowledge must be able to use every feature immediately.
- The admin panel must be intuitive enough for non-technical staff.

**When building anything — code, UI, API, config — ask: "Would my grandmother understand this?" If no, simplify.**

### Simplicity Rules for Developers

1. **No unnecessary abstraction.** Three similar lines > a premature helper.
2. **No feature flags for simple features.** Just ship it.
3. **No config options for things that can be auto-detected.**
4. **No multi-step setup for things that can be zero-config.**
5. **Every new file/module/class must justify its existence.** If it doesn't make the code simpler, don't add it.
6. **Always use `pip install` for dependencies.** Update `requirements.txt` when adding packages.

### Communication Language

Malik-e product (Sina) Finglish minevisi — farsi ba horuf-e latin. Jawab-ha
HAMESH Finglish ast. Hich vaght parsi script, hich vaght makhs. Code, commit
message, test, doc tu zaban-e khodeshun mimunan — in ghaedeh faghat baraye
chat ast.

---

## What Is This Project?

**PadyarAIChatbot** is a **CMS (Content Management System) for AI chatbots** — installed per-customer. Each customer deploys the app, enters their own content (Q&A dataset, videos, branding), and manages it through the admin panel.

The chatbot answers through a **tiered pipeline**. Cheap local tiers run first, and the paid model tiers only run when the local ones are not confident:

1. **Local knowledge base:** matches the query against the customer's curated dataset with BM25 plus local model2vec embeddings, fused by a feature reranker. Returns the best-matching video response. There is no TF-IDF; it was removed on 2026-08-28.
2. **AI fallback:** when local confidence is low, routes to the configured models via the Padyar AI Wrapper. The model picks record ids and our renderer writes the facts back out of the database.

See "Tiered Intelligence Pipeline" below for every tier and its threshold. That diagram is the authoritative version.

### CMS Model

- **Installed once per customer** — not multi-tenant SaaS
- Each customer gets a tailored installation with the features they ordered
- **Core modules** ship with every installation — always enabled, cannot be turned off
- **Optional modules** are add-on features — enabled/disabled at install time via `ENABLED_MODULES` env var
- **When a customer orders a feature**, a new optional module is created and listed in their `ENABLED_MODULES`
- If `ENABLED_MODULES` is empty, all optional modules load (full-featured install)
- Customer manages their own content via the admin panel
- **White-label:** Customer customizes app name, logo, colors, welcome text, footer
- Settings stored in the `settings` table (key-value) with `whitelabel_` prefix
- Branding injected into all templates via Jinja2 context processors

---

## Tech Stack

| Layer            | Technology                                                     |
| ---------------- | -------------------------------------------------------------- |
| Language         | Python 3.10+                                                   |
| Web Framework    | FastAPI + Uvicorn                                              |
| Template Engine  | Jinja2 (admin panel)                                           |
| Frontend (Chat)  | Vanilla HTML/CSS/JS — no framework                             |
| Frontend (Admin) | Tabler UI (built on Bootstrap 5, RTL) + Chart.js                |
| Database         | **PostgreSQL 16** (production). SQLite = test backend + migration/rollback artifact only |
| ML/Search        | Pure-Python BM25, model2vec local embeddings, feature reranker. scikit-learn is used only for the logistic-regression intent head. No TF-IDF, no `search_backend` setting. |
| AI Provider      | **Padyar AI Control Plane** — 11 provider types behind the Padyar AI Wrapper (OpenAI, Anthropic, Gemini native; Z.AI, Kimi, DeepSeek, Qwen, xAI, Mistral, OpenAI-compatible; SAKOO/Rayen — live verification at deployment) |
| AI Models        | Per-route, configured in Admin -> AI -> Routing. Whisper-1 for voice (STT is outside the wrapper). |
| Font             | Vazirmatn (Persian web font)                                   |

---

## Prerequisites

- Python 3.10+
- pip
- An OpenAI API key (used via GapGPT proxy)

---

## Setup

```bash
# Option 1: Interactive installer
chmod +x setup.sh && ./setup.sh

# Option 2: Manual
pip install -r requirements.txt
cp .env.example .env
# Edit .env — add your OPENAI_API_KEY
python main.py
```

The app starts at `http://127.0.0.1:8000`.

### Environment Variables

See `.env.example` for the full list:

| Variable                 | Required | Description                                                                     |
| ------------------------ | -------- | ------------------------------------------------------------------------------- |
| `OPENAI_API_KEY`         | Yes      | API key for all AI operations (via GapGPT proxy)                                |
| `OPENAI_API_BASE`        | No       | Any OpenAI-compatible endpoint (default: `https://api.gapgpt.app/v1`)           |
| `VIDEO_BASE_URL`         | No       | Base URL for serving videos (default: `/media/videos`)                          |
| `ENABLED_MODULES`        | No       | Comma-separated optional modules to enable (empty = all)                        |
| `COOKIE_SECURE`          | No       | HTTPS-only admin cookie. Also the app's production marker (default: `false`)    |
| `SEED_DEFAULT_CONTENT`   | No       | Seed the bundled knowledge base into a new DB (default: `true`)                 |
| `INTENT_TRUST_THRESHOLD` | No       | Confidence floor for the trained intent classifier (default: `0.6`)             |
| `RETRIEVAL_RERANK`       | No       | Enable the hybrid BM25 + dense reranker (default: `true`)                       |
| `VISIT_TAXONOMY_PATH`    | No       | Path to the visit taxonomy JSON (default: `data/visit-taxonomy.json`)           |
| `OTP_*`                  | No       | Registration module: code length, TTL, cooldown, attempt/resend limits          |
| `ASANAK_*`               | No       | Asanak SMS gateway. Settings table wins over env — see `.env.example`           |

---

## Commands

| Command                      | Description                                 |
| ---------------------------- | ------------------------------------------- |
| `python main.py`             | Start dev server (auto-reload; `HOST`/`PORT` env-overridable, default 127.0.0.1:8000) |
| `.venv/bin/python -m pytest` | Run the test suite                          |
| `python scripts/run_eval.py`         | Offline retrieval evaluation against the golden set |
| `python scripts/reset-content-to-defaults.py` | Back up the DB and restore bundled content |
| `python scripts/change-admin.py`     | Change admin password interactively |
| `python scripts/reset-admin-password.py` | Reset the admin password non-interactively |
| `python scripts/debug_similarity.py` | Debug similarity matching           |
| `python scripts/net-diag.py`        | Network diagnostic tool             |
| `python scripts/gapgpt_test.py`      | Test GapGPT API connectivity        |
| `python scripts/openai_test.py`      | Test OpenAI API connectivity        |

---

## Project Structure

```
PadyarAIChatbot/
  main.py                        # Entry point — uvicorn runner (HOST/PORT env-overridable)
  setup.sh                       # Interactive installer script
  requirements.txt               # Python dependencies (11 packages)
  .env / .env.example            # Environment config

  app/                           # Application package
    main.py                      # FastAPI app factory, lifespan, middleware
    config.py                    # All env vars, paths, module config
    models.py                    # Pydantic request/response schemas
    default_content.py           # Bundled INOTEX knowledge base + synonym seed

    routers/                     # Route handlers
      public.py                  # Public pages + health check
      chat.py                    # /chat endpoint (core chatbot logic)
      admin.py                   # Admin login, stats, settings, export
      admin_ai.py                # Admin API for the AI provider control plane
      voice.py                   # /api/transcribe (Whisper)
      synonyms.py                # Synonym CRUD
      dataset.py                 # Dataset + questions + video CRUD
      conversations_admin.py     # Admin: visitors, transcripts, wrong-answer queue
      dbadmin.py                 # Admin API for Infrastructure -> Database + Storage
      backups.py                 # Admin API for Infrastructure -> Backups
      ops.py                     # Admin Operations & Control Center
      logs.py                    # Admin API for the central log store
      tts.py                     # Admin panel -> AI -> Text to speech (proxy to Chatterbox)
      otp.py                     # /verify page, /api/auth/otp/*, /api/visit-plan
      leads.py                   # Exhibition lead capture: visitor, company edit, admin queue
      themes.py                  # Theme listing and activation

    services/                    # Business logic
      answer.py                  # Selection tier: the model picks record ids, we render
      scope.py                   # What this assistant is about, and its refusal wording
      search.py                  # Retrieval orchestration, dataset loading, reindex
      bm25.py                    # Okapi BM25 lexical retriever (pure Python)
      embeddings.py              # Local sentence embeddings (model2vec), no external API
      intent.py                  # Trained intent classifier over local embeddings
      rerank.py                  # Feature reranker fusing dense + lexical candidates
      providers.py               # Model-provider seam (local → OpenAI-compatible)
      ai/                        # AI provider control plane (per-provider clients)
      openai.py                  # GPT classification, chat, Whisper
      conversations.py           # Write side: visitors, conversations, messages
      otp.py                     # OTP issue/verify/resend, otp_challenges table
      signup.py                  # Server-owned signup flow: validation + steps
      sms.py                     # SMS gateway providers (Asanak)
      taxonomy.py                # Loads/validates data/visit-taxonomy.json (hot-reload)
      visit_plan.py              # Matches a visitor profile to INOTEX sections
      leads.py                   # Lead capture business logic (visitor/company/admin doors)
      company_profiles.py        # Company records behind the leads module
      company_search.py          # Company lookup for the leads module
      themes.py                  # Theme discovery from themes/ dir
      branding.py                # White-label defaults (WL_DEFAULTS) + branding context
      menu_settings.py           # Hamburger-drawer row visibility (admin-toggleable)
      idle_video.py              # Avatar idle-loop setup: main + up to 3 random extras
      pet_characters.py          # Companion (pet) character registry: which mascot an install ships
      backup.py                  # DB backup scheduler + operations
      backup_center.py           # Infrastructure -> Backups business logic
      pg_backup.py                # PostgreSQL backup/restore primitives
      pg_admin.py                 # PostgreSQL diagnostics for Infrastructure -> Database
      dbadmin.py                  # Infrastructure -> Database/Storage business logic
      storage.py                  # Storage usage/diagnostics
      resources.py                # System resource diagnostics (Ops)
      health.py                   # Health checks surfaced in Ops
      service_control.py          # Allowlisted service actions (Ops)
      maintenance.py              # Maintenance-mode enforcement
      applog.py                   # Central log/audit writer behind the logs module
      secure_store.py             # Encrypted-at-rest secrets (`enc:` Fernet tokens)
      tts_lexicon.py               # Pronunciation lexicon for the tts module

    db/                          # Database layer
      connection.py              # get_db_connection() routing, init_db(), seeding
      queries.py                 # log_chat, get/set settings, save data

    auth/                        # Security
      security.py                # Rate limiting, HMAC tokens, admin auth

    utils/                       # Utilities
      normalizer.py              # Persian text normalization + synonym expansion

    modules/                     # Module system
      registry.py                # Module definitions, conditional loading

  templates/                     # Jinja2 templates
    admin/                       # Admin panel
      base.html                  # Base layout (Tabler UI / Bootstrap 5 RTL)
      layout.html                # Sidebar + main content wrapper
      login.html, dashboard.html, dataset.html, questions.html,
      synonyms.html, themes.html,
      settings_account.html, settings_ai.html, settings_backup.html,
      settings_sms.html            # Asanak gateway credentials (registration module)
      settings_taxonomy.html       # Registration form options editor (registration module)
    otp/                         # Registration module
      verify.html                # /verify — phone entry, code, profile step

  static/                        # Static assets
    chat/                        # Shared chat assets (core.js, base.css)
    admin/css/                   # Admin styles (variables, base, login)
    admin/js/                    # Admin JS modules (auth, dashboard, dataset, etc.)
    otp/                         # Verify-page assets (otp.css, otp.js, pet/ sprites)
    companion/                   # On-page companion UI (companion.js, companion-ui.js,
                                 #   registration.js, button/ art)
    vendor/                      # Third-party: Tabler, Bootstrap, Chart.js, FontAwesome,
                                 #   Vazirmatn, marked.js, liquid-glass background/switcher

  themes/                        # Pluggable chat UI themes (WordPress-style partials)
    base/                        # Base theme — default partials all themes inherit
      partials/                  # index.html, head.html, header.html, messages.html, video.html, input.html, footer.html
    inotex/                      # Default theme — official INOTEX palette, modular brick layout
      partials/                  # Overrides: header, messages, video, input, footer
    liquid-glass/                # Apple-inspired frosted glass
      partials/                  # Overrides: header (switcher), messages (glass bubbles), input (glass wrapper), footer (JS overrides)
    minimal/                     # Minimal clean theme
      partials/                  # Override: footer only (uses all base defaults)
    haj/                         # Hajj & Ziyarat Organization — calm blue, large type, light/dark toggle
      partials/                  # Overrides all 7 base partials, plus 3 own: pattern, chips, security
      static/                    # style.css + hero/logo art and the companion sprite atlas
    (each theme has: theme.json, partials/ (optional overrides), static/style.css, screenshot.png)

  data/                          # Runtime data files
    visit-taxonomy.json          # Jobs/interests/flags/sections for registration + planner
    frame-vocabulary.json        # Connector words a model-written lead sentence may use
    eval/golden-inotex.json      # Golden set for the retrieval evaluation harness
    eval/smoke-options.json      # Live-install smoke set for the selection tier
    models/                      # Cached local embedding model (first download)
    otp-dev-outbox.log           # Dev-only OTP outbox (gitignored)

  content/                       # Source-of-truth INOTEX context
    sources.json, snapshots/, freshness-report.json, review-queue.md

  media/                         # Runtime media storage
    videos/                      # Admin-uploaded videos
    uploads/                     # General media uploads (YYYY/MM/ structure)

  docs/                          # Documentation
    README.md, ARCHITECTURE.md
    engineering/                 # architecture, decisions, security, runbook, AI log
    features/                    # one folder per feature (RESEARCH.md, INDEX.md)
    knowledge-based-evidence/    # دانش‌بنیان technical evidence package
    _other-product-padyar-ai/    # ⚠️ a DIFFERENT product's docs — reference only
    features/otp-verification/, features/targeted-visit/

  index.html                     # Root-level chat UI (redirects to active theme)

  tests/                         # pytest suite (config in pytest.ini, asyncio auto-mode)
    conftest.py                  # Shared fixtures
    test_smoke.py, test_public_ui.py, test_dataset_sync.py, test_default_seed.py,
    test_db_upgrade.py, test_embedding_search.py, test_intent_routing.py,
    test_reset_script.py, test_otp.py, test_profile_edit.py, test_sms_settings.py,
    test_taxonomy.py, test_visit_plan.py

  scripts/                       # Standalone dev/ops utilities (run from project root)
    change-admin.py              # Change admin password interactively
    reset-admin-password.py      # Reset the admin password non-interactively
    reset-content-to-defaults.py # Restore the bundled knowledge base
    debug_similarity.py          # Debug similarity matching
    run_eval.py                  # Retrieval evaluation harness (--recall-k for the recall@K table)
    smoke_options.py             # Selection-tier smoke run against a RUNNING install
    refresh-inotex-context.py    # Refresh content/ snapshots from sources.json
    migrate_json_to_db.py        # One-off JSON → SQLite content migration
    export-otp-module.py         # Package the registration module for another install
    capture_chat_shots.py / capture_proposal_shots.py  # Screenshot capture
    net-diag.py                  # Network diagnostic tool
    gapgpt_test.py / openai_test.py  # API connectivity tests
  backup_db.py                   # DB backup primitives (used by app + as a CLI)
```

---

## Architecture

### Tiered Intelligence Pipeline

The tier gates live in `app/routers/chat.py`; the thresholds are in `app/config.py`.

```
User Query
    │
    ▼
┌─────────────────────────────┐
│  Pick tier (zero AI calls)  │
│  A bare number, an ordinal  │
│  word, or an offered title, │
│  resolved against the ids   │
│  stored on the LAST turn.   │
│  "more" pages the same list.│
└────────────┬────────────────┘
             │ not a pick
             ▼
┌─────────────────────────────┐
│  Tier 0: Questions index    │
│  (Almost) exact hit in the  │
│  curated questions list     │
└────────────┬────────────────┘
             │ no hit
             ▼
┌─────────────────────────────┐
│  Tier 1: Local Retrieval    │
│  Persian text normalization │
│  Synonym expansion          │
│  BM25 + local embeddings    │
│  Feature reranker (fusion)  │
└────────────┬────────────────┘
             │
   score ≥ TRUSTED_MATCH_THRESHOLD (0.70)?
             │
        Yes ─┤─── Return matched video response
             │
        No ──┤
             ▼
┌─────────────────────────────┐
│  Tier 1.5: Trained intent   │
│  This install's own logistic│
│  classifier over local      │
│  embeddings, retrained on   │
│  every reindex              │
└────────────┬────────────────┘
             │
   p ≥ INTENT_TRUST_THRESHOLD (0.6)?
             │
        Yes ─┤─── Return that entry
             │
        No ──┤
             ▼
┌─────────────────────────────┐
│  Tier 2: Selection          │
│  The model is shown the top │
│  ANSWER_TOPK records + the  │
│  last 5 turns and returns   │
│  JSON naming record IDS:    │
│    answer  → serve that row │
│    options → numbered list, │
│              "which one?"   │
│    none    → written answer │
│  It CHOOSES; our renderer   │
│  writes every fact string   │
│  back out of the database.  │
└────────────┬────────────────┘
             │ no usable decision
             ▼
┌─────────────────────────────┐
│  Tier 2 (legacy, untouched) │
│  classify intent → entry,   │
│  else a written answer —    │
│  now verified before it is  │
│  served (see answer.py)     │
└────────────┬────────────────┘
             │ AI disabled or errored
             ▼
  Answer locally only if score ≥ LOCAL_FALLBACK_THRESHOLD (0.45)
  / QUESTIONS_FALLBACK_THRESHOLD (0.60); otherwise ask the visitor
  to rephrase rather than show an unrelated video.
```

### Module System

All features are implemented as **modules**. Each module has its own router, service, and optional admin page.

**Two categories:**

| Category                               | Behavior                                                                                         | Members (see `app/modules/registry.py`)             |
| -------------------------------------- | ------------------------------------------------------------------------------------------------ | --------------------------------------------------- |
| **Core modules** (`is_core=True`)      | Always enabled. Ship with every installation. Cannot be disabled.                                | `chat`, `admin`, `search`, `dataset`, `theme`, `conversations` |
| **Optional modules** (`is_core=False`) | Enabled/disabled per installation via `ENABLED_MODULES` env var. Customer orders these features. | `voice`, `video`, `infra`, `backups`, `ops`, `logs`, `tts`, `registration`, `leads` |

The registry is the authoritative list — `MODULES` in `app/modules/registry.py`:

| Module          | Core | Router                            | What the customer gets                                          |
| --------------- | ---- | ---------------------------------- | --------------------------------------------------------------- |
| `chat`          | Yes  | `app.routers.chat`                 | Chatbot engine (local retrieval + GPT fallback)                  |
| `admin`         | Yes  | `app.routers.admin`                | Admin dashboard and API                                          |
| `search`        | Yes  | `app.routers.synonyms`             | Synonym management API                                           |
| `dataset`       | Yes  | `app.routers.dataset`              | Dataset and questions CRUD                                       |
| `theme`         | Yes  | `app.routers.themes`               | Theme management and switching                                   |
| `conversations` | Yes  | `app.routers.conversations_admin`  | Read side of who visited, what they said, and where the bot was wrong |
| `voice`         | No   | `app.routers.voice`                | Voice input via Whisper API                                      |
| `video`         | No   | `app.routers.dataset`              | Video upload and serving (endpoints live in the dataset router)  |
| `infra`         | No   | `app.routers.dbadmin`              | Infrastructure centre: database diagnostics and storage tools    |
| `backups`       | No   | `app.routers.backups`              | Infrastructure -> Backups: create, download, restore (typed confirmation) |
| `ops`           | No   | `app.routers.ops`                  | Admin operations centre: dashboard, service health, session control |
| `logs`          | No   | `app.routers.logs`                 | Central log store and audit trail                                |
| `tts`           | No   | `app.routers.tts`                  | Persian text-to-speech control panel + voice cloning              |
| `registration`  | No   | `app.routers.otp`                  | Visitor registration + SMS verification, and the targeted visit plan |
| `leads`         | No   | `app.routers.leads`                | Exhibition lead capture: field visitor, company contact, admin queue |

**The `conversations` module** is the only way a human sees what the `chat` module (core, always on) already writes to the database — visitors, conversation transcripts, and the wrong-answer queue. It is core, not optional: an install able to switch it off would still collect names, phone numbers and everything people typed, with nobody able to read, check or export any of it.

**The `registration` module** bundles the visitor-facing signup flow: the `/verify` page, the OTP endpoints (`/api/auth/otp/request|verify|resend|status`), the profile step (`/api/auth/profile`), the taxonomy-driven form options (`/api/registration/options`), and the targeted visit planner (`/api/visit-plan`). It owns the `otp_challenges` table, reads its form/planner vocabulary from `data/visit-taxonomy.json` via `app/services/taxonomy.py`, and delivers codes through `app/services/sms.py`. Gateway credentials come from the `settings` table first (entered in the admin panel), then env — see `.env.example`.

**The `leads` module** covers exhibition lead capture through three separate doors with no shared credential: `/v/{code}` for a field visitor identified by their own link, `/edit/{token}` for a company contact holding a one-time invite from the booth, and the admin queue for review.

**The `infra`, `backups`, `ops`, `logs` and `tts` modules** are the operator-facing side of running an install: database/storage diagnostics, backup create/restore (restore requires the operator to type `RESTORE BACKUP <id>`, compared exactly), a dashboard with allowlisted service actions and session revocation, a central log/audit store, and a thin authenticated proxy to the loopback-only Chatterbox TTS service. All are admin-only.

**How it works:**

- At install time, set `ENABLED_MODULES=voice,video,registration` to enable specific optional modules (add more names, comma-separated, as needed)
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

Modules are conditionally loaded at startup via `load_module_routers()` in `app/main.py`.

### White-Label / Branding System

Since this is a **CMS installed per-customer**, branding customization is a first-class feature.

#### Database: Key-Value with `whitelabel_` Prefix

White-label settings live in the `settings` table with prefixed keys (WordPress `wp_options` pattern). **`app/services/branding.py` is the single source of truth** — `WL_DEFAULTS` holds every key and its shipped default, `WL_FIELD_TO_KEY` maps the admin form's field names onto the keys, and an install that never opens the form renders exactly what it shipped with (an empty saved value collapses back to the default).

| Key                                | Default                             | Description                                        |
| ---------------------------------- | ----------------------------------- | -------------------------------------------------- |
| `whitelabel_app_name`              | `دستیار پادیار`                     | Display name in admin sidebar and chat UI          |
| `whitelabel_subtitle`              | `INOTEX`                            | Small line under the chat title                    |
| `whitelabel_logo_url`              | (empty = built-in SVG mark)         | Logo image URL                                     |
| `whitelabel_primary_color`         | `#2D5CA7`                           | Primary brand color (feeds `--wl-primary`)         |
| `whitelabel_accent_color`          | `#FCB715`                           | Accent color (feeds `--wl-accent`)                 |
| `whitelabel_yellow_light_color`    | `#FEBE27`                           | Hover yellow (`--wl-yellow-light`)                 |
| `whitelabel_navy_color`            | `#1E2D52`                           | Panels navy (`--wl-navy`)                          |
| `whitelabel_teal_color`            | `#04A584`                           | Success teal (`--wl-teal`)                         |
| `whitelabel_dark_teal_color`       | `#00644F`                           | Dark teal (`--wl-dark-teal`)                       |
| `whitelabel_background_color`      | `#000000`                           | Page background (`--wl-background`)                |
| `whitelabel_white_color`           | `#FFFFFF`                           | Main text (`--wl-white`)                           |
| `whitelabel_welcome_text`          | (default Persian greeting)          | Chat welcome message                               |
| `whitelabel_chat_background_url`   | `/themes/inotex/static/bg-bricks.jpg` | Background image behind the chat tab (`--wl-chat-background`, painted on `.view-container`) |
| `whitelabel_video_background_url`  | `/themes/inotex/static/bg-bricks.jpg` | Background image behind the video tab (`--wl-video-background`, painted on `.view-container` in `body.video-mode`) |
| `whitelabel_footer_text`           | `قدرت گرفته از سکوی ملی متن باز هوش مصنوعی` | Powered-by credit under the composer (empty = default) |
| `whitelabel_footer_color`          | `#B8C4DE`                           | Credit text colour (`--wl-footer-color`)             |

Colors are stored as **hex strings** (`#RRGGBB`); the two background URLs follow the logo rule (site-relative or absolute `http(s)`, validated server-side).

#### How the values reach the page

`branding.chat_branding_context()` (merged into the theme render in `app/routers/public.py`) emits, pre-escaped for the theme env's `autoescape=False`:

- **`wl_style`** — a `<style>` tag with one `--wl-*` custom property per palette token plus the two `url("…")` background tokens. Themes map their own `--{theme}-*` tokens onto these with the official palette as `var()` fallback (see `themes/inotex/static/style.css`), so Settings > Branding controls every theme color and both tab backgrounds.
- **`wl_brand_script`** — `window.PADYAR_BRAND = {app_name, welcome}` for `core.js` (json + `</` guard, never html.escape inside `<script>`).
- Text positions (`app_title`, `wl_subtitle`, `wl_welcome`, `wl_logo_url`) are html-escaped.

Brand values are baked into the rendered-page cache, so `wl_cache_key()` (a tuple of all values) extends the cache key in `app/services/themes.py` — an admin save flips the key on the next request.

The admin surface is `Settings > Branding` (`/secure-panel-inotex/settings/branding`): native `<input type="color">` pickers (always `#rrggbb`, zero dependencies) and URL fields with an upload button for logo and both backgrounds (uploads land in `/media/uploads` via `/admin/api/upload_logo`, magic-byte validated, then the operator presses «ذخیره برندینگ»).

### Database (PostgreSQL 16)

Production runs PostgreSQL 16 with schemas `app` and `observability`.
SQLite remains only as the test backend and a rollback artifact.
Schema changes go through `migrations/*.sql`, applied by
`scripts/apply_migrations.py` (checksummed, idempotent).

Core `app` tables:

| Table             | Created by                       | Purpose                                                     |
| ----------------- | -------------------------------- | ----------------------------------------------------------- |
| `chat_logs`       | `app/db/connection.py`           | All chat interactions with confidence, tokens, cost, plus `conversation_id` / `entry_id` / `offer_state` (migration 0009) |
| `settings`        | `app/db/connection.py`           | Key-value runtime settings (includes `whitelabel_*` keys)   |
| `dataset`         | `app/db/connection.py`           | Knowledge base entries (title, text, video_url, `*_en`)     |
| `questions`       | `app/db/connection.py`           | Question-to-dataset mappings                                |
| `synonyms`        | `app/db/connection.py`           | Persian synonym mappings                                    |
| `admins`          | `app/db/connection.py`           | Admin credentials (hashed)                                  |
| `admin_sessions`  | `app/db/connection.py`           | Active admin sessions with sliding expiry                   |
| `login_attempts`  | `app/db/connection.py`           | Brute-force counters per IP: attempts, block_until, last_attempt |
| `otp_challenges`  | `app/services/otp.py` (`ensure_table()`) | OTP challenges: HMAC of the code, expiry, attempt/resend counters, and the profile fields (name, job, position, interests) |
| `edit_invites` / `edit_sessions` | `app/services/leads.py` (`ensure_tables()`) | One-time edit links (HMAC of the token, burn on the contact's button press, migrations 0021) and the 2-hour session cookie rows that carry the open page afterwards |
| `dataset_edits`   | `app/services/leads.py` (`ensure_tables()`) | The review queue: multi-field proposals (`old_values`/`new_values` JSON + `edit_kind` change/confirm, migration 0022) between a company contact and the live `companies` row |
| `sms_messages`    | `app/services/sms_outbox.py` (`ensure_table()`) | One row per gateway send with the msgid and polled delivery status (migration 0023); also carries campaign verdict rows (`skipped`/`send_failed`) |
| `sms_campaigns`   | `app/services/campaigns.py` (`ensure_table()`) | Bulk confirm campaigns: text, audience, sent/skipped/failed counters, stop reason (migration 0024) |

`init_db()` in `app/db/connection.py` creates the first eight at startup. `otp_challenges` is created on demand by the registration module's `ensure_table()`, so an install without `registration` never grows the table.

### Security

- **Chat tokens:** HMAC-signed tokens injected into HTML, validated on every `/chat` request
- **Origin validation:** Checks `Origin`/`Referer` against allowlist
- **Rate limiting:** `CHAT_RATE_LIMIT` requests per `CHAT_RATE_WINDOW` seconds per IP (default 20 per 60s — a whole exhibition hall can share one NAT'd address)
- **Admin auth:** SHA-256 + salt password hashing, session cookies, brute-force protection (5 attempts → 5 min block, counted in the `login_attempts` table so a restart or a second worker does not reset it)
- **Sliding sessions:** 1-hour admin sessions, extended on activity

### Theme System

Themes use a WordPress-style partial template system with Jinja2. The `themes/base/` directory provides default partials (header, messages, video, input, footer, head). Each theme overrides specific partials by placing files with the same name in its own `partials/` directory. Jinja2's `FileSystemLoader` resolves overrides automatically — child theme first, then base.

**Shared assets:**

- `static/chat/core.js` — All chat JS (send, type, video, voice, accessibility). Themes override 3 functions via `ChatConfig` callbacks before calling `initChat()`.
- `static/chat/base.css` — Structural layout CSS. Theme CSS files override only visual properties (colors, backgrounds, borders, shadows).

**Template partials** (`themes/base/partials/`):
| Partial | Purpose |
|---------|---------|
| `index.html` | Master template — assembles all partials via `{% include %}` |
| `head.html` | `<head>` with CSS/JS links (uses `{{ theme_name }}`, `{{ chat_token }}`) |
| `header.html` | Tab switcher, accessibility controls (no logo — inotex/base moved the logo into `menu.html`, see below) |
| `menu.html` | Hamburger drawer. Below 992px: a fixed overlay opened by the header's hamburger button. At 992px and up: a persistent sidebar next to the chat, collapsible to a 76px icon rail via `#menu-sidebar-toggle` (`static/chat/base.css`) |
| `messages.html` | Text chat view, welcome message, loading bubble |
| `video.html` | Video view, avatar container, action buttons |
| `input.html` | Textarea, mic button, send button |
| `footer.html` | Loads core.js, theme-specific JS overrides, calls `initChat()` |

Active theme is stored in the `settings` table (key `active_theme`) and switchable via admin panel. Selectable themes: `inotex` (default), `liquid-glass`, `minimal`, `haj`; `base` is marked `"selectable": false` and exists only to supply the default partials. Theme inheritance: if `theme.json` has a `"parent"` field, the parent's partials are searched before base.

**`menu.html`'s sidebar header — logo placement differs by theme.** The base shell (`static/chat/base.css`) assumes two separate elements: `.menu-sidebar-logo` (a small logo next to the title, shown only when the sidebar is expanded) and `.menu-sidebar-toggle-btn` (a compact stand-in logo used only on the collapsed rail, swapping to the collapse icon on hover). `base`, `minimal`, and `liquid-glass` all follow this two-element pattern.

`inotex` does not. It has no `.menu-sidebar-logo`. Instead, the full 38px brand mark lives inside `.menu-sidebar-toggle-btn` (`.menu-sidebar-toggle-logo`, a `<div>`) at all times — expanded or collapsed — and `themes/inotex/static/style.css` overrides the base shell so that element stays visible in the expanded state too (base.css hides it there by default). `.logo-container` in inotex holds only the two-line title (`.the-slogan`), which hides on collapse (`.menu-drawer.collapsed .menu-sidebar-header .logo-container .the-slogan { display: none; }`) instead of the whole container. If another theme wants this "logo lives in the toggle button" look, add the override in that theme's own `style.css` — do not change `base.css`, since `base`/`minimal`/`liquid-glass` still rely on the two-element layout.

---

## Key Files to Know

| File                      | What It Does                                                    |
| ------------------------- | --------------------------------------------------------------- |
| `app/config.py`           | All configuration — read this first when looking for a setting  |
| `app/routers/chat.py`     | Core chatbot endpoint — the main pipeline                       |
| `app/services/answer.py`  | Selection tier + the two grounding firewalls + the list renderer |
| `app/services/scope.py`   | Domain and refusal wording — read this before changing a refusal |
| `app/services/search.py`  | Retrieval orchestration — dataset loading, reindex, scoring      |
| `app/services/rerank.py`  | Feature reranker — how hybrid candidates are fused and scored    |
| `app/services/providers.py` | Model-provider seam — swap AI vendors without touching logic   |
| `app/services/openai.py`  | All AI integration — classification, chat, transcription        |
| `app/services/otp.py`     | OTP lifecycle + `otp_challenges` schema — read before touching auth |
| `app/services/sms.py`     | SMS gateways — credential precedence (settings → env → default) |
| `app/services/taxonomy.py`| Registration/planner vocabulary — validates + hot-reloads the JSON |
| `app/services/menu_settings.py` | Hamburger-drawer row visibility (language/theme/text-size/logout), admin-toggleable |
| `app/services/idle_video.py` | Avatar idle-loop setup — main + up to 3 random extras, gated on the `video` module |
| `app/db/connection.py`    | Database schema — table definitions, seeding                    |
| `app/auth/security.py`    | All security — tokens, rate limits, admin auth                  |
| `app/utils/normalizer.py` | Persian text processing — normalization, synonyms               |
| `app/modules/registry.py` | Module definitions — what's enabled, what's optional            |
| `static/chat/core.js`     | Shared chat JS — all chat logic, themes override via ChatConfig |
| `static/chat/base.css`    | Shared structural CSS — layout, positioning, animations         |
| `themes/base/partials/`   | Default template partials all themes inherit                    |

---

## Patterns to Follow

### Adding a New Module

Every feature must be a module. Follow this pattern:

1. **Define the module** in `app/modules/registry.py` — always as optional (`is_core=False`) unless every single customer needs it:

   ```python
   "mymodule": ModuleDef(
       name="mymodule",
       description="What it does",
       is_core=False,  # Optional — customer enables at install time
       router_module="app.routers.mymodule",
   ),
   ```

2. **Create the router** at `app/routers/mymodule.py` with an `APIRouter`.

3. **Create the service** at `app/services/mymodule.py` for business logic.

4. **Register in `app/main.py`** — it auto-loads via `load_module_routers()` when listed in `ENABLED_MODULES`.

5. **For the customer's installation**, add the module name to their `ENABLED_MODULES` env var.

### Adding a New Admin Page

1. Create `templates/admin/{name}.html` extending `layout.html`
2. Create `static/admin/js/{name}.js` for page logic
3. Add a route in `app/routers/public.py` (for page serving) or the appropriate router
4. Add sidebar link in `templates/admin/layout.html`

### Adding a New Theme

1. Create `/themes/{name}/` with `theme.json`, `screenshot.png`, and `static/style.css`
2. Optionally create `partials/` directory with override files for any partial that differs from base
3. If extending another theme (not base), add `"parent": "theme-name"` to `theme.json`
4. It's auto-discovered at startup — no registration needed
5. For JS overrides, set `ChatConfig.addMessageFn`, `ChatConfig.switchTabFn`, or `ChatConfig.playVideoTransitionFn` in the theme's `footer.html` before calling `initChat()`

### Database Changes

1. Add a numbered file in `migrations/` and apply it with
   `.venv/bin/python scripts/apply_migrations.py` (PostgreSQL, production)
2. Mirror the change in `app/db/connection.py` (`init_db()`) for the SQLite
   test backend
3. Add queries/mutations in `app/db/queries.py`

**Never edit a migration that has already been applied.** `apply_migrations.py`
stores a sha256 of every file it applies. Change an applied file and it prints
`REFUSING TO CONTINUE` and exits 2. That is step 4 of six in
`deploy/padyar-deploy.sh`, so the next deploy aborts there and resets the code.
It fails safe, but nothing ships until someone finds out why. A migration on
`main` is history: add a new numbered file, use
`ALTER TABLE ... ADD COLUMN IF NOT EXISTS`, and leave the old one alone.

There IS a migration system now. It has no downgrade path: rolling back means
restoring a backup (`app/services/pg_backup.py`).

### Adding White-Label Settings

1. Add the key to the `settings` table (key-value, prefix with `whitelabel_`)
2. Add default in `get_setting("whitelabel_key", default_value)` calls
3. Add the field to the admin white-label settings page
4. Inject into templates via the `branding_context` context processor

### Adding a New Dataset Entry

Via the admin panel — that is now the only supported path. The knowledge base lives in the `dataset`/`questions` tables; the bundled defaults are Python literals in `app/default_content.py`, seeded into a brand-new DB only (never over existing content). The old `data/dataset.json` / `data/questions.json` files are gone. To restore the bundled defaults on an existing install, run `python scripts/reset-content-to-defaults.py` (it backs up the DB first and preserves admins, chat logs and settings).

---

## Use the Tooling That Ships With This Repo

This repo carries 18 skills in `.claude/skills/` and 9 agents in
`.claude/agents/`. Two plugins add more: `engineering:*` and
`product-management:*`. They were sitting unused while avoidable bugs shipped.

Two things went wrong and both are fixable by habit:

1. A skill read AFTER the code is written changes nothing. Load it first.
2. A skill that lies makes the code wrong. On 2026-08-29 three skills still
   said the main branch was `main-noor`, that retrieval used TF-IDF, and that
   the database was SQLite. All three were false. An agent that follows a
   stale skill writes stale code.

### Which source wins

| Source | Wins on |
| ------ | ------- |
| `.claude/skills/`, `.claude/agents/` | Anything about THIS codebase: the tiered pipeline, the module registry, Persian normalization, themes, the admin panel |
| `engineering:*` | How to think about it: decisions, test depth, incidents, deploys |
| `product-management:*` | Before any code exists: the problem, the spec, the order of work |

A project skill beats a plugin skill whenever they overlap. The project skill
knows our file layout; the plugin skill does not.

### What to load, by what you are about to do

| You are about to | Load first |
| ---------------- | ---------- |
| Touch auth, sessions, cookies, tokens, rate limits, or any endpoint's access control | `authorization`, then `/security-review` before you call it done |
| Add an endpoint or a route | `api-test` |
| Add a service, a util, or an auth function | `write-tests` |
| Change anything a visitor sees in a browser | `e2e-test-gen`, `playwright-cli` |
| Chase a bug, a failing test, or behaviour you cannot explain | `systematic-debugging` or `engineering:debug`. Find the root cause BEFORE proposing a fix |
| Choose between two technical approaches | `engineering:architecture`. Record the decision in `docs/engineering/DECISIONS.md` |
| Design a new subsystem | `engineering:system-design` |
| Decide what to test and how deep | `engineering:testing-strategy` |
| Ship a release | `engineering:deploy-checklist` |
| Handle production being broken | `engineering:incident-response` |
| Refactor with no new behaviour | `engineering:tech-debt` |
| Branch, commit, or open a PR | `scoped-pr`, `commit` |
| Review a change | `code-review`, plus the `code-review-specialist` agent |
| Build a non-trivial feature or refactor | `implement` |
| Turn a request into a spec | `product-management:write-spec` |
| Shape a vague idea | `product-management:product-brainstorming` |
| Decide what ships next | `product-management:roadmap-update` |

### The two rules that make it stick

**Load the skill before the code, not after.** A review skill run on finished
code catches some of what a design skill would have prevented, and costs a
rewrite.

**A skill that contradicts the code is a bug in the skill.** Fix the skill in
the same change. Do not work around it and do not leave it for later: the next
agent reads it and repeats your bug.

## Mandatory Checks Before Every Commit

**Tests run on GitHub, not on this machine.** `.github/workflows/ci.yml` runs
the full pytest suite (`test` job) and the retrieval/safety eval
(`evaluation` job) on every push and every PR — that run is the pass/fail
signal, not a local one. This machine has 15 tests that always fail here and
always pass on CI (env/network-only, see below) — a local `pytest` run is not
a trustworthy gate on this box, so don't run the full suite locally before
committing.

After every code change, before committing:

```bash
python -m py_compile app/main.py          # Verify Python syntax
python -m py_compile app/routers/chat.py  # Verify core router
```

After pushing, check CI instead of re-running tests locally:

```bash
gh run list --branch <branch> --limit 1
gh run watch
```

### Keep the Code Graph Current

This repo uses [Graphify](https://github.com/safishamsi/graphify) to keep a
local, queryable knowledge graph of the codebase. Output lives in
`graphify-out/` (gitignored, regenerated, never committed — see the
`.gitignore` entry and `app/services/storage.py`, which surfaces it in the
admin Infrastructure page as "خروجی نقشهٔ دانش").

After finishing a feature, before opening a PR, refresh it:

```bash
graphify update .
```

No LLM or API key needed — it re-parses changed files with tree-sitter and
rewrites `graphify-out/graph.json`, `graph.html` and `GRAPH_REPORT.md`. This
doesn't touch anything CI checks; it exists so the next agent (or the next
session of you) can query an up-to-date map of the codebase instead of
grepping cold. Useful follow-ups against the refreshed graph:

```bash
graphify query "<question>"        # BFS traversal for a question
graphify explain "<file-or-symbol>" # plain-language neighbors of one node
graphify affected "<file-or-symbol>" # what a change to this would impact
```

### Testing

The setup below is for local debugging (reproducing a CI failure, running one
test file while writing it) — it is not a required pre-commit step; CI is the
gate. The project uses **pytest**. Test-only dependencies (`pytest`, `pytest-asyncio`, `pytest-playwright`) live in **`requirements-dev.txt`** — kept separate from `requirements.txt` so customer installs don't pull in Playwright + browser binaries. Set up a dev/test environment with:

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m playwright install chromium   # only needed for browser e2e tests
```

Tests live under `tests/` (config in `pytest.ini`, asyncio auto-mode) — **1860 collected, 1702 passing, 143 skipped as of 2026-08-28**. The 15 remaining failures all need a live PostgreSQL or network and fail the same way on a clean checkout: `test_company_profiles` (4), `test_leads_company_tools` (3), `test_leads_contacts_admin` (4), `test_leads_sms_channel` (1), `test_sms_production_guard` (3). The suite is growing, so treat those numbers as a snapshot and let the command be the source of truth:

```bash
.venv/bin/python -m pytest --collect-only -q | tail -2
```

Write unit tests for services/utils/auth and integration tests via FastAPI's `TestClient` (see the `write-tests` and `api-test` skills); browser e2e tests go under `tests/e2e/` with pytest-playwright (see the `playwright-cli` and `e2e-test-gen` skills).

**Every browser test uses Playwright's ASYNC API.** This is not a style choice. `pytest.ini` sets `asyncio_mode = auto`, and the sync API refuses to start inside a running event loop; worse, `pytest-playwright`'s sync fixtures are session-scoped, so one sync browser test keeps a loop alive for the whole run and every later test that calls `asyncio.run()` fails. Measured 2026-08-28: a single sync browser test turned 15 failures into 141. So write `async def test_...(page: Page)` with `async_playwright()`, never the `page`/`browser`/`context` sync fixtures. `tests/test_suite_isolation.py` bans them with an AST check and separately asserts that `tests/e2e/` is still collected by the default run, so nobody can quietly re-hide the browser tests instead of fixing one.

Browser tests need the binary:

```bash
.venv/bin/python -m playwright install chromium
```

---

## Configuration Reference

All config lives in `app/config.py`. Key thresholds:

| Setting                     | Default | Purpose                                                                    |
| --------------------------- | ------- | -------------------------------------------------------------------------- |
| `TRUSTED_MATCH_THRESHOLD`   | 0.70    | At/above this a local match is served outright                             |
| `LOCAL_FALLBACK_THRESHOLD`  | 0.45    | Lowest local score answered when the AI fallback is unavailable            |
| `QUESTIONS_FALLBACK_THRESHOLD` | 0.60 | Same floor for the questions index                                          |
| `SIMILARITY_THRESHOLD`      | 0.45    | Deprecated alias of `LOCAL_FALLBACK_THRESHOLD` — no longer an answer floor  |
| `INTENT_TRUST_THRESHOLD`    | 0.6     | Trained intent classifier's confidence floor (env-overridable)             |
| `MAX_LOGIN_ATTEMPTS`        | 5       | Admin brute-force limit                                                    |
| `BLOCK_TIME_MINUTES`    | 5       | Admin lockout duration             |
| `SESSION_TIMEOUT_HOURS` | 1       | Admin session lifetime             |
| `CHAT_RATE_LIMIT`       | 20      | Max chat requests per window per IP (env-overridable) |
| `CHAT_RATE_WINDOW`      | 60      | Rate limit window in seconds (env-overridable) |
| `CHAT_TOKEN_TTL`        | 3600    | HMAC chat token lifetime (seconds) |
| `VISITOR_SESSION_DAYS`  | 30      | Days a visitor stays signed in; slides on use, so this is inactivity |
| `VISITOR_SESSION_MAX_HOURS` | 12  | Hard cap from when the session was minted; nothing renews it. The bound a shared kiosk can reach |
| `ANSWER_TOPK`           | 8       | Records shown to the selection tier (recall@8 = 0.952, measured) |
| `HISTORY_TURNS`         | 5       | Prior turns handed to the model as context |
| `HISTORY_WINDOW_MINUTES` | 15     | How far back those turns are read (shared-kiosk bound) |
| `OPTIONS_MAX`           | 5       | Most records offered as a numbered choice on one turn |
| `OPTIONS_MARGIN`        | 0.15    | Top-vs-second gap that collapses "options" back to one answer |
| `PICK_WINDOW_MINUTES`   | 15      | How long a stored list stays pickable (shared-kiosk bound) |
| `OFFER_IDS_MAX`         | 50      | Ids kept in one offer for paging |
| `LEAD_MAX_CHARS`        | 160     | Longest model-written lead above a numbered list |

Settings rows (admin panel, no deploy) that the selection tier reads:
`options_shown` (1..15, the list-length kill switch), `collection_noun_fa` /
`collection_noun_en`, `assistant_domain` / `assistant_domain_en`,
`refusal_text_fa` / `refusal_text_en` (password-gated), and
`chat_log_retention_days` (0 = keep forever).

---

## Documentation Rules

The `docs/` folder is the project's knowledge base. Keep it current.

| When this happens...           | Update this file                        |
| ------------------------------ | --------------------------------------- |
| Starting a new feature         | `docs/features/{slug}/RESEARCH.md`      |
| New or changed service         | the Tech Stack + module tables here     |
| App structure or setup changes | the Setup + Project Structure here, and `README.md` |
| Feature status changes         | `docs/features/INDEX.md`                |
| An architectural decision      | `docs/engineering/DECISIONS.md`         |
| A measured claim changes       | `docs/knowledge-based-evidence/`        |
| AI-assisted work in a session  | `docs/engineering/AI_ASSISTANCE_LOG.md` |

One feature, one folder in `docs/features/{slug}/`.

> `docs/_other-product-padyar-ai/` describes a DIFFERENT product (a pnpm/Next.js
> monorepo) and is kept only for reference — never update it for work done here,
> and never follow its setup instructions. See its README.

---

## Working Principles

- Be structured and execution-focused
- Prioritize real-world deployable outputs
- Follow existing patterns in the codebase
- When in doubt, simplify
- Every feature is a module — no exceptions
- The chatbot serves end-users — every change must improve their experience
