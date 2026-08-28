---
name: software-architecture
description: Use when writing code, designing architecture, or making architectural decisions in the PadyarAIChatbot. Covers the tiered intelligence pipeline, the module registry system, FastAPI router/service/db/auth layering, Jinja2 admin + vanilla-JS chat + theme partials, white-label settings, and the project's simplicity-first principles.
---

# Software Architecture for PadyarAIChatbot

Guidance for building in the PadyarAIChatbot — a **per-customer CMS** for an AI video chatbot (installed once per customer, **not** multi-tenant SaaS). Audience is anyone from a kid to an elderly person, so **simplicity is the #1 product principle** (CLAUDE.md "grandmother test"). Follow the existing patterns; when in doubt, simplify.

## Stack

Python 3.10+ · FastAPI + Uvicorn · Jinja2 (admin templates) · vanilla HTML/CSS/JS (chat UI) · Bootstrap 5 RTL (admin) · SQLite (`chat_history.db`) · pure-Python BM25 + model2vec local embeddings for retrieval, scikit-learn for the logistic-regression intent head · OpenAI via the GapGPT proxy. Persian/RTL, Vazirmatn font.

Entry point: `python main.py` (uvicorn with reload). The app object is `app.main:app` — a `FastAPI()` created at import time in `app/main.py` (**not** a factory). Routers are loaded through the **module registry**, filtered by the `ENABLED_MODULES` env var.

Run with the project venv: `.venv/bin/python`. Per CLAUDE.md, always `pip install` new deps and update `requirements.txt`.

## Project Structure

```
PadyarAIChatbot/
  main.py                  # uvicorn runner — entry point
  requirements.txt         # deps: fastapi, jinja2, uvicorn, scikit-learn, openai,
                           #       python-multipart, numpy, httpx, python-dotenv, bcrypt
  app/
    main.py                # FastAPI() app at import; lifespan, middleware, load_module_routers()
    config.py              # ALL config — env vars, paths, thresholds, module list
    models.py              # Pydantic request/response schemas (input validation)
    modules/
      registry.py          # ModuleDef catalogue + resolve/load logic
    routers/               # HTTP layer (thin)
      chat.py              # /chat — the core tiered pipeline
      admin.py             # admin login, stats, settings, export
      synonyms.py          # synonym CRUD  (the "search" module)
      dataset.py           # dataset + questions + video CRUD
      themes.py            # theme listing / switching
      voice.py             # /api/transcribe (Whisper) — optional module
      public.py            # public pages + health
    services/              # business logic (no HTTP)
      search.py            # retrieval orchestration, dataset loading, reindex
      bm25.py              # Okapi BM25 lexical retriever (pure Python)
      embeddings.py        # local model2vec sentence embeddings (no external API)
      rerank.py            # feature reranker fusing dense + lexical candidates
      openai.py            # GPT classification, chat, Whisper (via GapGPT)
      themes.py            # theme discovery
    db/
      connection.py        # get_db_connection(), init_db() (creates + seeds tables)
      queries.py           # get_setting/set_setting, log_chat, save_dataset, save_questions
    auth/
      security.py          # verify_admin, chat token, origin check, rate limit, hashing
    utils/
      normalizer.py        # Persian normalization + synonym expansion
  templates/admin/         # Jinja2 admin panel (Bootstrap 5 RTL)
  static/                  # chat/ (core.js, base.css), admin/css, admin/js, vendor/
  themes/                  # base/ + theme overrides (WordPress-style partials)
  data/                    # dataset.json, questions.json
  media/                   # runtime uploads (videos, uploads)
  scripts/                 # standalone dev/ops utilities
```

## Tiered Intelligence Pipeline

The heart of the app (`app/routers/chat.py` + `app/services/`). This is a summary. **`CLAUDE.md` under "Tiered Intelligence Pipeline" is the full, authoritative version — read it before changing any tier.**

```
User query
  → Pick tier: a number, an ordinal word or an offered title resolved against
    the ids stored on the last turn (zero AI calls)
  → Tier 0: (almost) exact hit in the curated questions index
  → Persian normalization + synonym expansion (app/utils/normalizer.py)
  → Tier 1: BM25 (bm25.py) + local model2vec embeddings (embeddings.py),
    fused by the feature reranker (rerank.py), orchestrated by search.py
      score >= TRUSTED_MATCH_THRESHOLD (0.70)?  → return matched video entry
  → Tier 1.5: this install's own logistic-regression intent head over the local
    embeddings, retrained on every reindex
      p >= INTENT_TRUST_THRESHOLD (0.6)?  → return that entry
  → Tier 2 selection (app/services/answer.py): the model sees the top
    ANSWER_TOPK records and returns JSON naming record ids. It CHOOSES; our
    renderer writes every fact string back out of the database.
  → Tier 2 legacy: GPT classification (app/services/openai.py), else a written
    answer, verified before it is served
  → AI unavailable: answer locally only above LOCAL_FALLBACK_THRESHOLD (0.45)
    / QUESTIONS_FALLBACK_THRESHOLD (0.60), else ask the visitor to rephrase
```

There is **no TF-IDF vectorizer** and **no `search_backend` setting**; both were removed. Keep the local tiers cheap; only fall through to the paid model tiers when local confidence is low. All thresholds live in `app/config.py`.

## Module System

**Every feature is a module.** A module is a `ModuleDef` in `app/modules/registry.py` with a router. There are two categories:

| Category   | Flag            | Behavior                                                      | Examples                          |
| ---------- | --------------- | ------------------------------------------------------------ | --------------------------------- |
| **Core**   | `is_core=True`  | Always loaded. Ships with every install. Cannot be disabled. | `chat`, `admin`, `search`, `dataset`, `theme` |
| **Optional** | `is_core=False` | Loaded only if listed in `ENABLED_MODULES` (empty = all on). | `voice`, `video`                  |

`resolve_enabled_modules()` always includes core modules; if `ENABLED_MODULES` is blank, all optional modules load too (backward-compatible full install). `load_module_routers()` imports each enabled module's router and `app/main.py` mounts them. **New features should be optional modules** unless every customer needs them.

## Layering Rules

- **Routers (`app/routers/`)** stay thin: parse/validate the request (Pydantic), call a service, return a response. Attach auth via `dependencies=[Depends(verify_admin)]` (admin) or the chat-token/origin/rate-limit trio (public chat). See the `authorization` skill.
- **Services (`app/services/`)** hold business logic — retrieval, OpenAI calls, theme discovery, media handling. No HTTP concerns here.
- **DB (`app/db/`)** owns all SQLite access. Use `get_db_connection()` and the `queries.py` helpers. Always parameterize (`?`) — never string-format user input into SQL.
- **Auth (`app/auth/`)** owns all security primitives. Reuse, don't duplicate.
- **Utils (`app/utils/`)** for shared helpers like Persian normalization.

## Admin UI vs. Chat UI vs. Themes

- **Admin panel:** server-rendered **Jinja2** templates (`templates/admin/`) extending `layout.html`, styled with Bootstrap 5 RTL; per-page JS in `static/admin/js/`.
- **Chat UI:** **vanilla** HTML/CSS/JS. All chat logic lives in `static/chat/core.js`; structural CSS in `static/chat/base.css`. No framework.
- **Themes:** WordPress-style partial system under `themes/`. `themes/base/partials/` provides defaults; each theme overrides individual partials by name. Active theme is stored in the `settings` table and switched from the admin panel. Themes override 3 JS hooks via `ChatConfig` callbacks before calling `initChat()`.

## White-Label / Settings

Branding is key-value in the `settings` table with a `whitelabel_` prefix (WordPress `wp_options` pattern). Read via `get_setting("whitelabel_app_name", default)` — defaults live in Python, not the DB. Inject into Jinja2 templates via a `branding_context` context processor; the public chat HTML uses string replacement. Dynamic colors are served from a `/theme.css` endpoint using CSS custom properties.

## Database

SQLite, **no migration system** — `init_db()` in `app/db/connection.py` creates and seeds all tables on first boot. Tables: `chat_logs`, `settings`, `dataset`, `questions`, `synonyms`, `admins`, `admin_sessions`, `media`. To change the schema, edit `init_db()` and add queries to `queries.py`.

## Patterns to Follow

**Add a module**
1. Add a `ModuleDef` to `app/modules/registry.py` (`is_core=False` unless every customer needs it).
2. Create `app/routers/<name>.py` with an `APIRouter` named `router`.
3. Put business logic in `app/services/<name>.py`.
4. It auto-loads via `load_module_routers()` when listed in `ENABLED_MODULES`.

**Add an admin page**
1. `templates/admin/<name>.html` extending `layout.html`.
2. `static/admin/js/<name>.js` for page logic.
3. Route in `app/routers/public.py` (page) or the relevant router (API), protected with `Depends(verify_admin)`.
4. Add the sidebar link in `templates/admin/layout.html`.

**Add a theme**
1. Create `themes/<name>/` with `theme.json`, `static/style.css`, `screenshot.png`.
2. Override only the partials that differ from `themes/base/partials/`.
3. Auto-discovered at startup — no registration.

**Change the database**
1. Edit `init_db()` in `app/db/connection.py`.
2. Add queries/mutations in `app/db/queries.py`. No migrations — the DB auto-creates.

## Simplicity Principles (CLAUDE.md)

- No premature abstraction — three similar lines beat a speculative helper.
- No feature flags or config knobs for simple things; auto-detect where possible.
- Every new file/module/class must justify its existence by making the code simpler.
- Every feature is a module — no exceptions.
- The grandmother test: if a user-facing thing needs explaining, simplify it.

## Mandatory Pre-Commit Check

Git main branch is `main`. Before committing, run the syntax checks from CLAUDE.md:

```bash
.venv/bin/python -m py_compile app/main.py
.venv/bin/python -m py_compile app/routers/chat.py
```

## Important Files

| Path                      | Purpose                                                          |
| ------------------------- | ---------------------------------------------------------------- |
| `app/config.py`           | All configuration — read this first when looking for a setting   |
| `app/modules/registry.py` | Module catalogue, core vs. optional, `ENABLED_MODULES` resolution |
| `app/main.py`             | `FastAPI()` app, lifespan, `load_module_routers()`               |
| `app/routers/chat.py`     | Core tiered chatbot pipeline                                     |
| `app/services/search.py`  | Retrieval orchestration, dataset loading, reindex                |
| `app/services/bm25.py`    | Okapi BM25 lexical retriever (pure Python)                       |
| `app/services/embeddings.py` | Local model2vec sentence embeddings                           |
| `app/services/rerank.py`  | Feature reranker fusing dense + lexical candidates                |
| `app/services/openai.py`  | GPT classification, chat, Whisper (via GapGPT)                   |
| `app/db/connection.py`    | `get_db_connection()`, `init_db()` — schema + seeding            |
| `app/auth/security.py`    | All auth/security primitives (see the `authorization` skill)     |
| `static/chat/core.js`     | All chat JS — themes override via `ChatConfig`                   |
| `themes/base/partials/`   | Default theme partials all themes inherit                        |
| `CLAUDE.md`               | Project principles, architecture, and conventions               |
