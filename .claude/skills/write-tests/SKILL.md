---
name: write-tests
description: Generate focused pytest tests for Python code in this repo (services, utils, auth helpers, FastAPI routers). Use this to create unit tests, FastAPI TestClient integration tests, and real-PostgreSQL tests that cover critical functionality.
---

You are a test engineer for PadyarAIChatbot, a Python 3.10+ FastAPI app with a
pytest suite. Generate focused, essential tests that match the patterns already
in `tests/`.

There is no TypeScript, React, Vitest or Jest in this repo. Everything is
pytest: plain `def test_...` functions and bare `assert`. No `describe`, no
`it`, no `expect`.

Read `docs/engineering/TESTING.md` before you start. It is the repo's testing
standard and it wins over this file if the two disagree.

## Step 0: Read the existing tests first

`tests/` has over 130 files. Almost every shape you need is already in there.
Before writing anything, read:

- `tests/conftest.py` (the shared fixtures, all autouse, described below)
- one or two test files that cover code near your target

Match their idioms. Do not invent a fixture or a helper that does not exist.

## Step 1: Code Analysis

Identify:

- Purpose and functionality
- Dependencies (other services, the database, the AI wrapper, the network)
- Complexity level
- Code scope (pure util, service function, auth helper, router endpoint)
- Side effects (database writes, HTTP calls, file writes, module-level state)

Module-level state matters more here than in most codebases. Several services
keep process-wide caches (`app/services/search.py` index version,
`app/db/queries.py` settings cache, `app/services/applog.py` duplicate
suppression). If your target holds state between calls, the test has to reset
it or the next test inherits it.

## Step 2: Determine Test Strategy

| Target | Test type | How |
|---|---|---|
| Pure function, no DB, no network (`app/utils/normalizer.py`, `app/services/bm25.py`, scoring maths) | Unit | Import it, call it, assert. No fixture needed |
| Service function that reads or writes the database (`app/services/conversations.py`, `app/services/otp.py`) | Integration | Redirect `config.DB_PATH` to `tmp_path`, call the real function |
| A router endpoint in `app/routers/` | Integration through HTTP | FastAPI `TestClient`. See the `api-test` skill for the full pattern |
| Code whose bug only shows on PostgreSQL (booleans, JSONB, TIMESTAMPTZ, unique violations) | Real-PostgreSQL | Add it under `tests/postgres/` |
| Anything a visitor sees in a browser | Browser e2e | Playwright's ASYNC api. See the `e2e-test-gen` skill |

When unsure between unit and integration, prefer the integration test that
goes through the real database. The suite runs on throwaway SQLite files, so a
real-database test is still fast and hermetic.

## Step 3: Generate Tests

### Focus on essential scenarios

- **Core functionality** only. Test the main purpose and the business rule.
- **Critical error paths** that a real visitor or operator will hit.
- **Key edge cases** that represent real input. Persian text, an empty
  dataset, an expired token, a shared kiosk where the next person must not
  inherit the last person's state.
- **Security boundaries.** If the code guards access, test the denied path
  explicitly. The constitution requires it.
- **Important state changes** that affect what a visitor sees.

### Testing philosophy: quality over quantity

**DO test:**

- Business logic and visitor-facing behaviour
- Error handling that changes what the visitor sees
- The seam between two modules (router to service, service to database)
- The retrieval and selection rules (thresholds, tier gates, refusals)
- Denied access, expired sessions, and anything a kiosk visitor could inherit

**DO NOT test:**

- Private helpers that have no contract of their own
- Trivial getters or pass-through wrappers
- Third-party library behaviour (FastAPI, psycopg, model2vec have their own tests)
- Every possible input, only realistic ones
- The same happy path five times with different words

### Style rules that this suite follows

- One module docstring at the top saying WHY the file exists and what broke.
  Most files here do this and it is the reason the suite is readable.
- Test names are sentences: `test_a_duplicate_id_is_a_controlled_409_not_a_500`,
  `test_every_api_route_refuses_an_anonymous_caller`. A name should say the
  rule, not the function under test.
- Arrange, Act, Assert inside the body.
- Independent tests. Never rely on a previous test having run.
- `assert x == y, "why this matters"` when the failure would otherwise be a
  mystery.

### pytest syntax you will actually use

| Need | Use |
|---|---|
| A test | `def test_name():` or `async def test_name():` (asyncio auto mode, no marker) |
| Setup and teardown | `@pytest.fixture` with `yield` |
| Replace a function or attribute | `monkeypatch.setattr(module, "name", replacement)` |
| Replace by dotted path | `monkeypatch.setattr("app.db.queries.get_setting", fake)` |
| Temp files and databases | the built-in `tmp_path` fixture |
| Table-driven cases | `@pytest.mark.parametrize("route", ROUTES)` |
| Expect an error | `with pytest.raises(HTTPException) as exc:` |
| Skip when an optional dependency is missing | `pytest.importorskip("playwright.async_api")` |
| Skip on a condition | `@pytest.mark.skipif(not embeddings.available(), reason="...")` |

`monkeypatch` replaces what `vi.mock` did elsewhere. It undoes itself after
each test, which is why the suite uses it instead of manual patching.

### The shared fixtures in `tests/conftest.py`

All of these are autouse. They already run for your test, so do not repeat
them:

- `.env` is redirected to a throwaway file (`config.ENV_FILE`)
- the log store is redirected to a throwaway file (`config.LOGS_DB_PATH`)
- the settings TTL cache is cleared before and after
- the search index refresh window is reopened
- the background SMS poller is stubbed out
- applog's duplicate suppression is cleared

`tests/conftest.py` also pins process-wide environment before `app.*` imports:
`DB_BACKEND=sqlite`, `ENABLED_MODULES=""` (all optional modules load),
`BCRYPT_ROUNDS=4`, `OTP_DEST_HOURLY_LIMIT=50`.

Never create a second top-level `conftest.py`. Add a shared fixture to the
existing one only when more than one file needs it. Otherwise keep the fixture
in your own test file.

### The database redirect idiom

Every test that touches the database points `config.DB_PATH` at `tmp_path`
first. Without it the test writes into the developer's real
`chat_history.db`.

```python
import pytest


@pytest.fixture
def app_db(tmp_path, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "myfeature.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    yield
```

`SEED_DEFAULT_CONTENT = False` keeps the bundled knowledge base out of the
test database, so your assertions see only the rows you inserted.

### Unit test example (pure function)

Shape taken from `tests/test_embedding_search.py`:

```python
"""Semantic retriever unit tests.

The calibration contract is exercised without the model, so CI stays
hermetic.
"""
from app.services import embeddings


def test_calibration_maps_noise_to_zero_and_matches_high():
    assert embeddings._calibrate(0.30) == 0.0
    assert embeddings._calibrate(0.72) > 0.70
    assert embeddings._calibrate(0.95) == 1.0


def test_build_index_empty_returns_none():
    assert embeddings.build_index([]) is None


def test_search_degrades_to_bm25_only_when_embeddings_unavailable(monkeypatch):
    import app.services.search as search

    monkeypatch.setattr(embeddings, "available", lambda: False)
    monkeypatch.setattr(
        "app.db.queries.get_setting",
        lambda key, default=None: default,
    )
    search.load_dataset_internal()
    assert search.dataset_embedding_index is None
```

### Integration test example (service plus database)

```python
import pytest


@pytest.fixture
def app_db(tmp_path, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "conversations.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    yield


def test_a_visitor_is_attached_to_their_conversation(app_db):
    from app.services import conversations

    conversations.get_or_create_conversation("c-1", lang="fa", ip="10.0.0.1",
                                             user_agent="kiosk")
    conversations.append_visitor_message("c-1", "ساعت کاری چند است؟")
    visitor_id = conversations.upsert_visitor(
        first_name="علی", last_name="رضایی", phone="09121112233",
        job="دانشجو", position="مدیر", interests="هوش مصنوعی")
    conversations.attach_visitor("c-1", visitor_id)

    rows = conversations.list_conversations()
    assert rows and rows[0]["visitor_id"] == visitor_id
```

Call the real service function. Do not mock `get_db_connection`. The database
in a test is a throwaway SQLite file, so there is nothing to protect by
mocking, and a mock would hide exactly the SQL bugs the test exists to catch.

### Endpoint tests

Endpoints get their own skill. Use `api-test` for routers under
`app/routers/`. It has the working `TestClient` fixtures for the three doors
(admin session plus CSRF, chat token plus Origin, public visitor).

### Browser tests

Anything a visitor sees goes through the `e2e-test-gen` skill. The one rule
you must not break: Playwright's ASYNC api only, with your own `browser`
fixture. `tests/test_suite_isolation.py` fails the suite with an AST check if
a test asks for pytest-playwright's sync `page`, `browser`, `context`,
`browser_context` or `playwright` fixtures.

### AI and network calls

Never let a test reach the network. Patch the seam the router imports:

```python
def _mock_ai(monkeypatch, generated="پاسخ"):
    import app.routers.chat as chat

    async def fake_generate(query, lang="fa"):
        return generated, 2, 0.0

    monkeypatch.setattr(chat, "get_openai_response", fake_generate)
```

Patch the name on the module that USES it (`app.routers.chat`), not the module
that defines it. The router did `from ... import get_openai_response` at import
time, so patching the source module would not change what the router calls.

## Step 4: File location and naming

The suite is flat. One file per feature or area:

```
tests/test_<area>.py          # unit and integration, the default home
tests/postgres/test_<area>.py # needs a real PostgreSQL server
tests/e2e/test_<flow>.py      # browser test for a whole visitor flow
```

There is no `unit/` or `integration/` directory and no `.unit.` or
`.integration.` suffix. Pick the name a reader would grep for:
`test_visitor_session.py`, `test_chat_options_pick.py`,
`test_conversations_admin.py`.

A browser test that covers one defect in one feature may live next to that
feature's other tests instead of `tests/e2e/` (`tests/test_kiosk_privacy.py`
does this). Both are collected by the default run, which is the point.

## Step 5: Run and verify

Run only what you wrote:

```bash
.venv/bin/python -m pytest tests/test_myfeature.py -q
```

Set up the test environment once, if it is not there yet:

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m playwright install chromium   # browser tests only
```

**Do not run the full suite locally and do not treat it as a gate.** This Mac
has 15 tests that always fail here and always pass on CI, because they need a
live PostgreSQL or the network (`test_company_profiles`,
`test_leads_company_tools`, `test_leads_contacts_admin`,
`test_leads_sms_channel`, `test_sms_production_guard`). Chasing them wastes
time.

The local pre-commit check is only a syntax check on the files you touched:

```bash
python -m py_compile app/main.py app/routers/chat.py
```

**CI on GitHub is the pass/fail gate.** `.github/workflows/ci.yml` runs the
full suite (`test` job) and the real-PostgreSQL suite (`postgres-tests` job,
blocking). After pushing:

```bash
gh run list --branch <branch> --limit 1
gh run watch
```

## Output Format

### 1. Analysis Summary

Brief description of the code and its key characteristics.

### 2. Test Strategy

State the type (unit, integration, real-PostgreSQL, browser) and why.

### 3. Test File Location

The exact path under `tests/`.

### 4. Test Implementation

The test code.

### 5. Coverage Notes

Which scenarios are covered, and which known gaps you left on purpose.
`docs/engineering/TESTING.md` asks you to record a limitation rather than
silently downgrade confidence.

Now analyse the target and write focused tests, essential functionality first.

DO NOT add inline comments to generated test code unless the setup is genuinely
hard to follow. Put the reasoning in the module docstring instead, the way the
existing files do.
