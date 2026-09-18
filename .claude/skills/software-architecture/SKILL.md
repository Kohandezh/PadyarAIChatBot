---
name: software-architecture
description: Use when writing code, designing a feature, or making an architectural decision in PadyarAIChatbot (Python/FastAPI/PostgreSQL). Covers the tiered answer pipeline, the module registry, the grounding rule, themes and white-label branding, and the required workflow with its stop conditions.
---

# Software Architecture for PadyarAIChatbot

This skill is the map of the system and the rules for changing it. Read it
before you write code, not after.

The authority order is: `docs/engineering/ENGINEERING_CONSTITUTION.md`, then the
topic standards (`API_STANDARDS.md`, `SECURITY.md`, `DATABASE.md`,
`TESTING.md`), then `CLAUDE.md`, then `AGENTS.md`. When a standard conflicts
with a shortcut, the standard wins.

## What the product is

A CMS for AI chatbots, installed once per customer. Not multi-tenant SaaS.
Each customer deploys the app, enters their own knowledge base and branding,
and runs it from the admin panel.

The one product rule that outranks everything else, from `CLAUDE.md`: the app
must be usable by anyone, from a child to an elderly person. Every screen
understandable in under 3 seconds, every action in under 3 clicks, no jargon in
user-facing text. The UI is Persian and RTL. "UX is engineering" is principle 8
of the constitution, so a feature a visitor cannot discover is not finished.

## Layout

```
main.py                 uvicorn runner
app/main.py             FastAPI app factory, middleware, module loading
app/config.py           every env var, path and threshold. Read this first
app/routers/            chat.py admin.py public.py dataset.py leads.py otp.py ...
app/services/           answer.py search.py bm25.py embeddings.py intent.py
                        rerank.py providers.py branding.py themes.py ai/ ...
app/db/                 connection.py (init_db, schema), queries.py
app/auth/               security.py, csrf.py, visitor.py
app/modules/registry.py module definitions
app/utils/normalizer.py Persian normalization and synonym expansion
migrations/             NNNN_name.sql, applied by scripts/apply_migrations.py
themes/                 base/ plus one directory per theme
static/chat/            core.js, base.css (shared by every theme)
templates/admin/        Jinja2 admin panel (Tabler on Bootstrap 5 RTL)
tests/                  pytest, with tests/postgres/ and tests/e2e/
docs/engineering/       the standards listed above
docs/features/<slug>/   one folder per feature
```

One Python package, one deployable app. Python 3.10+, pip, `requirements.txt`
for the app and `requirements-dev.txt` for test-only dependencies. There is no
JavaScript build step: the chat UI is vanilla JS served as files.

## The tiered answer pipeline

This is the core of the product. The gates live in `app/routers/chat.py` and
every threshold lives in `app/config.py`. Cheap local tiers run first, and a
paid model runs only when the local ones are not confident.

```
User query
  → normalize (app/utils/normalizer.py), strip leading greeting
  → Pick tier: a bare number, an ordinal word, or an offered title,
    resolved against the ids stored on the LAST turn. Zero AI calls.
  → Tier 0: (almost) exact hit in the curated questions index
  → Tier 1: local retrieval. BM25 + model2vec embeddings, fused by the
    feature reranker (app/services/rerank.py)
       score >= TRUSTED_MATCH_THRESHOLD (0.70)  → serve that record
  → Tier 1.5: this install's own trained intent classifier (logistic
    regression over local embeddings, retrained on every reindex)
       p >= INTENT_TRUST_THRESHOLD (0.6)        → serve that entry
  → Tier 2: selection. The model sees the top ANSWER_TOPK (8) records plus
    the last HISTORY_TURNS (5) turns and returns JSON naming record IDS
  → Tier 2 legacy: classify intent, else a written answer, verified by the
    grounding check before it is served
  → AI disabled or errored: answer locally only if
    score >= LOCAL_FALLBACK_THRESHOLD (0.45) or
    q_score >= QUESTIONS_FALLBACK_THRESHOLD (0.60)
  → otherwise a 200 with scope.no_answer_text(lang), never a 503
```

The live file has more local tiers than this sketch (company field, company
list, guide, booth, category overview, decline, affirm). They were each added
because a real production query was answered wrongly, and each carries a
comment saying which one. Read them before adding another.

Two rules about the bottom of the ladder:

- **"We have nothing" is not an outage.** A 503 makes `static/chat/core.js`
  say the AI service is unavailable, which is a lie when we simply have no
  record. The only honest 503 left is: the AI tier was asked, it is genuinely
  down, and no local match was strong enough.
- **Refusal wording is a setting, not a Python literal.** It lives in
  `app/services/scope.py`, read from the same settings the prompt is built
  from, so a customer changes it without a deploy. Read that module before you
  touch any refusal.

## The grounding rule

`app/services/answer.py`. This is the rule that keeps the bot from making
things up, and it is not negotiable.

> The model CHOOSES records. It never AUTHORS facts.

The selection tier shows the model up to `ANSWER_TOPK` retrieved records and
the recent turns. The model replies with one JSON object:

```
{"mode": "answer"|"options"|"converse"|"none", "ids": [...], "lead": "", "reason": ""}
```

Everything the visitor reads is then re-read from the database by the renderer
in that module. An answer is the record's own `text`. An option line is the
record's own `title`. The count is computed in Python, the numbering is
`enumerate()`.

Two firewalls hold the line:

1. **A set intersection in Python** drops any id the model returns that
   retrieval never proposed. So the model cannot reach a record outside the
   candidate list, and therefore cannot reach one outside the corpus.
2. **`frame_is_grounded` and `generated_prose_is_grounded`** check the one
   string the model may write (a short `lead` sentence above a list, capped at
   `LEAD_MAX_CHARS`, 160) against the record body and the query.

The single exception is `mode: "converse"` for greetings, thanks, small talk
and yes/no replies, inside its own tighter firewall: the assistant's own
identity plus facts already in history, no record facts, no digits, two short
sentences.

What this does not promise: the model can still pick the wrong id out of the
allowlist. That is bounded upstream by the named-entity anchor and the
unknown-entity gate in `app/routers/chat.py`, which run first.

If you are about to let a model produce a fact string, stop. Every fabrication
incident this product has had came from exactly that.

## The module system

Every feature is a module. `app/modules/registry.py` is the authoritative list.

```python
@dataclass
class ModuleDef:
    name: str
    description: str
    is_core: bool = False        # True = always on, False = per-install toggle
    router_module: str = ""      # e.g. "app.routers.voice"
    router_var: str = "router"
```

- **Core** (`is_core=True`) always loads: `chat`, `admin`, `search`, `dataset`,
  `theme`, `conversations`.
- **Optional** (`is_core=False`) loads per install through `ENABLED_MODULES`:
  `voice`, `video`, `infra`, `backups`, `ops`, `logs`, `tts`, `registration`,
  `leads`.
- An empty `ENABLED_MODULES` means every optional module loads, which is the
  full-featured install.
- `resolve_enabled_modules()` always adds the core names, then the listed
  optional ones. An unknown name is logged and ignored.
- `load_module_routers()` imports each router at startup from `app/main.py`.

**A new feature starts optional.** Promote to core only when every single
customer needs it. `conversations` is the example of a justified promotion:
the core `chat` module already writes visitors, transcripts and the
wrong-answer queue to the database, so an install able to switch the reading
end off would still collect names and phone numbers with nobody able to read,
check or export them.

To add one:

1. Add the `ModuleDef` to `MODULES` in `app/modules/registry.py`.
2. Create `app/routers/<name>.py` with an `APIRouter` named `router`.
3. Put the business logic in `app/services/<name>.py`. Routers stay thin.
4. Add the module name to the customer's `ENABLED_MODULES`.

Code that must behave differently when a module is off asks the registry, via
`config.is_module_enabled("<name>")`. Never ask an `ImportError`: a module that
fails to import is broken, not absent.

A module that owns a table creates it on demand (`ensure_table()` in
`app/services/otp.py`, `ensure_tables()` in `app/services/leads.py`), so an
install without the module never grows the table.

## The provider seam

`app/services/providers.py` keeps business logic independent of any AI vendor.
Two layers, in the order the pipeline consults them: `LocalRetrievalProvider`
(curated KB, local embeddings, trained intent head, zero external calls), then
`OpenAICompatProvider` (any OpenAI-compatible endpoint, base URL and key are
per-install settings).

Rules the module enforces:

- no module outside `app/services` imports an AI SDK directly;
- chat messages are the only payload that ever leaves the host. Admin
  credentials, settings and logs never do. `classify_data_policy` states this
  contract in code;
- health checks are on demand, never in the request path.

## Themes and white-label branding

Two separate systems, and they meet in the rendered page.

**Themes** are WordPress-style partials over Jinja2. `themes/base/partials/`
holds the defaults (`index.html`, `head.html`, `header.html`, `menu.html`,
`messages.html`, `video.html`, `input.html`, `footer.html`). A theme overrides
only the partials it needs by putting a file with the same name in its own
`partials/`. `FileSystemLoader` resolves the child first, then the parent named
in `theme.json`, then base. The active theme is the `active_theme` settings
row.

`static/chat/core.js` holds all the chat JavaScript and `static/chat/base.css`
holds the structural layout. A theme's `style.css` overrides visual properties
only, and a theme overrides at most three JS functions through `ChatConfig`
callbacks before calling `initChat()`. Do not fork `core.js` or edit `base.css`
for one theme: `base`, `minimal` and `liquid-glass` all depend on its layout
assumptions.

**Branding** is the per-customer white label. `app/services/branding.py` is the
single source of truth: `WL_DEFAULTS` holds every `whitelabel_*` key and its
shipped default, `WL_FIELD_TO_KEY` maps the admin form's fields onto those
keys, and an empty saved value collapses back to the default. Values reach the
page through `chat_branding_context()` as `--wl-*` CSS custom properties, so
Settings > Branding controls the colors of every theme.

Rendered pages are cached, so `wl_cache_key()` (a tuple of all brand values)
extends the cache key in `app/services/themes.py`. An admin save flips the key
on the next request. Add a brand value and you add it to `WL_DEFAULTS`, or the
cache will serve the old page forever.

## Database

PostgreSQL 16 in production, schemas `app` and `observability`. SQLite is the
test backend and a rollback artifact only.

1. Add a numbered file in `migrations/` and apply it with
   `.venv/bin/python scripts/apply_migrations.py`.
2. Mirror the change in `init_db()` in `app/db/connection.py` for the SQLite
   test backend.
3. Put queries and mutations in `app/db/queries.py`.

**Never edit a migration that has already been applied.**
`apply_migrations.py` stores a sha256 of every file it applies. Change an
applied file and it prints `REFUSING TO CONTINUE` and exits 2, which aborts the
next deploy at step 4 of `deploy/padyar-deploy.sh`. It fails safe, but nothing
ships until someone works out why. A migration on `main` is history. Add a new
numbered file and use `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`.

There is no downgrade path. Rolling back means restoring a backup
(`app/services/pg_backup.py`).

Both backends use `?` placeholders and go through `app/db/timeutil.py` for
timestamps, because PostgreSQL returns aware datetimes where SQLite returned
text. Never compare `utcnow()` against a column value directly.

## API and authorization

Full detail is in the `authorization` skill. The short version:

- admin surface: `Depends(verify_admin)` from `app/auth/security.py`;
- visitor chat: HMAC chat token, Origin/Referer allowlist and a two-tier rate
  limit, all three together;
- every resource endpoint authenticates and authorizes independently, and never
  infers authorization from possession of a resource id;
- any endpoint returning an unbounded collection paginates;
- the kiosk is the threat model, so "the next person inherits the last person's
  state" is the default bug.

## Code quality in this repo

- **Routers stay thin.** Business logic goes in `app/services/`. A rule shared
  by two routers has one implementation, not two.
- **Early return** over nested conditions.
- **Comments explain why, not what.** This codebase's comments record the
  production incident that forced the current shape. That is deliberate and it
  is why the next person does not undo the fix. Keep writing them that way.
- **Every new file, module or class justifies its existence.** Three similar
  lines beat a premature helper. No config option for something that can be
  auto-detected, no feature flag for a simple feature, no multi-step setup for
  something that can be zero-config.
- **No speculative abstraction.** Constitution principle 3: the smallest
  architecture that safely supports the real requirement.

## Required workflow

From `CLAUDE.md`. Do not skip to implementation.

1. **Understand.** Read the relevant source and tests, inspect the schema,
   search for an existing implementation of the same pattern, find the callers.
   Change nothing in this phase.
2. **Architecture.** Answer: current behavior, root cause, existing pattern,
   proposed design, security, reliability, data and migration, compatibility.
3. **Implement** the smallest architecturally correct change, not the smallest
   possible patch.
4. **Test.** Success, invalid input, unauthorized access, missing resource,
   duplicate request, concurrent request, regression. A security boundary needs
   its negative case tested explicitly.
5. **Self review.** Read your own diff as a skeptical reviewer and try to
   reject it.
6. **Verify.** Say plainly which checks PASSED, FAILED, were NOT RUN or were
   NOT APPLICABLE. Never claim a check you did not run.

## Stop conditions

Stop and reassess the architecture if you are about to:

- add a special-case conditional or a client-specific branch;
- duplicate business logic, validation or authorization;
- introduce a pattern that already exists elsewhere;
- add a flag to compensate for an abstraction that does not fit;
- bypass a validation or disable a security check;
- add a second way of doing something that already has a standard way;
- make a destructive database change;
- modify a shared contract without checking its consumers;
- let a model write a fact string.

When one of these appears necessary, the underlying abstraction is probably
wrong. Fix that instead.

## Verification and CI

Tests run on GitHub, not on this machine. `.github/workflows/ci.yml` runs the
pytest suite (`test`) and the PostgreSQL integration suite (`postgres-tests`,
blocking, against a `postgres:16` service container), plus `secret-scan` and
`identity-guard`. That run is the pass/fail signal.

This Mac has 15 tests that always fail here and always pass on CI, because they
need a live PostgreSQL or network. A local full run is not a trustworthy gate,
so do not run one before committing.

Local pre-commit check:

```bash
python -m py_compile app/main.py
python -m py_compile app/routers/chat.py
```

After pushing:

```bash
gh run list --branch <branch> --limit 1
gh run watch
```

Every browser test uses Playwright's **async** API. `pytest.ini` sets
`asyncio_mode = auto`, and one sync browser test keeps an event loop alive for
the whole run, which turned 15 failures into 141 when it was measured.
`tests/test_suite_isolation.py` bans the sync fixtures with an AST check.

## Documentation

Keep `docs/` describing reality. Constitution principle 7: current state,
target state and planned work must stay distinguishable, and documentation must
never make an implementation look more complete than it is.

| When this happens | Update |
| --- | --- |
| Starting a feature | `docs/features/<slug>/RESEARCH.md` |
| New or changed service | the tables in `CLAUDE.md` |
| Structure or setup changed | `CLAUDE.md` and `README.md` |
| Feature status changed | `docs/features/INDEX.md` |
| An architectural decision | `docs/engineering/DECISIONS.md` |
| Cutting a release | `CHANGELOG.md` and `docs/features/release-process/SPEC.md` |

`docs/_other-product-padyar-ai/` describes a different product and is reference
only. Never update it for work done here.

## Important files

| Path | Purpose |
| --- | --- |
| `app/config.py` | Every setting and threshold. Read this first |
| `app/routers/chat.py` | The tier gates, in order |
| `app/services/answer.py` | Selection tier, the grounding firewalls, the list renderer |
| `app/services/scope.py` | Domain and refusal wording |
| `app/services/search.py` | Retrieval orchestration, dataset loading, reindex |
| `app/services/rerank.py` | How hybrid candidates are fused and scored |
| `app/services/providers.py` | The model-provider seam |
| `app/modules/registry.py` | Module definitions, core vs optional |
| `app/services/branding.py` | `WL_DEFAULTS`, the white-label source of truth |
| `app/services/themes.py` | Theme discovery, render cache, `wl_cache_key` |
| `app/db/connection.py` | Schema and seeding |
| `app/auth/security.py` | Tokens, rate limits, admin auth |
| `app/utils/normalizer.py` | Persian normalization and synonyms |
| `static/chat/core.js` | All chat JS, shared by every theme |
| `docs/engineering/ENGINEERING_CONSTITUTION.md` | The binding principles |
| `docs/engineering/API_STANDARDS.md` | Endpoint rules and required tests |
| `docs/engineering/DATABASE.md` | Migration and integrity rules |
| `docs/engineering/TESTING.md` | What to test and how deep |
| `docs/engineering/DECISIONS.md` | Where an architectural decision is recorded |
