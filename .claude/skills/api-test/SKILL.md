---
name: api-test
description: Generate pytest tests for FastAPI endpoints in app/routers/, services in app/services/, and helpers in app/utils/ and app/auth/. Picks the test type (unit, TestClient endpoint test, real-PostgreSQL integration test) from the target. Uses the real database, never a mocked one.
---

You are a test engineer for the PadyarAIChatbot API. The app is FastAPI. Tests
are pytest. Generate tests that follow the patterns already in `tests/`.

There is no Hono, no Vitest, no Supabase, no Drizzle and no `apps/api`
directory. Endpoints live in `app/routers/*.py` and are tested through
FastAPI's `TestClient`.

Read `docs/engineering/TESTING.md` and `docs/engineering/API_STANDARDS.md`
before you start.

## Test Type Detection

Decide from the target:

**Endpoint tests** (the default for anything in `app/routers/`):

- The target is an HTTP route (for example "test the dataset create endpoint",
  "test POST /admin/api/dataset")
- Drives the whole request pipeline with `TestClient(app)`: middleware, CSRF,
  auth dependency, router, service, database
- Lives in `tests/test_<area>.py`

**Service tests** (`app/services/`):

- The target is business logic (`app/services/otp.py`, `app/services/leads.py`,
  `app/services/answer.py`)
- Calls the service function directly against a throwaway database
- Mocks only what leaves the machine (the AI wrapper, the SMS gateway)

**Unit tests** (`app/utils/`, `app/auth/`, scoring maths):

- The target is a pure function with no database and no network
- Import, call, assert. No fixture beyond `monkeypatch` if anything is patched

**Real-PostgreSQL tests** (`tests/postgres/`):

- The bug class only exists on PostgreSQL: int-for-boolean writes,
  `enabled = 1` comparisons, `json.loads()` on an already parsed JSONB dict,
  TIMESTAMPTZ compared as a string, `sqlite3.IntegrityError` that psycopg
  never raises
- Read `tests/postgres/conftest.py` before adding one

If unsure, write the **endpoint test**. It exercises the most real code per
line and catches the auth mistakes that matter most here.

## File Naming and Location

```
tests/test_<area>.py            # endpoint, service and unit tests, flat
tests/postgres/test_<area>.py   # needs a real PostgreSQL 16 server
tests/e2e/test_<flow>.py        # browser tests (see the e2e-test-gen skill)
tests/conftest.py               # the ONE shared conftest, already exists
tests/postgres/conftest.py      # schema isolation + the authenticated client
```

There is no `routes/`, `services/` or `lib/` subdirectory, and no
`.unit.`/`.integration.` suffix. Name the file after the feature a reader would
grep for.

## The three doors, and their fixtures

This app has three separate authentication surfaces. A test must use the right
one or it proves nothing.

| Door | What the client must send | Example file |
|---|---|---|
| Admin (`/admin/**`, `/secure-panel-admin/**`) | `admin_session` cookie plus `X-CSRF-Token` on POST/PUT/PATCH/DELETE | `tests/test_conversations_admin.py` |
| Chat (`/chat`) | `Origin` header plus `X-Chat-Token`, and the per-IP rate limit applies | `tests/test_chat_options_pick.py` |
| Public visitor (`/api/auth/otp/*`, `/verify`) | `Origin` and `User-Agent` | `tests/test_otp.py` |

### Admin endpoint test

Taken from `tests/test_conversations_admin.py`:

```python
import datetime
import secrets

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def app_db(tmp_path, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "myfeature.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    yield


@pytest.fixture
def anon(app_db):
    from app.main import app
    with TestClient(app) as c:
        yield c


@pytest.fixture
def client(app_db):
    from app.main import app
    with TestClient(app) as c:
        from app.db.connection import get_db_connection
        conn = get_db_connection()
        token = secrets.token_hex(16)
        conn.execute("INSERT OR IGNORE INTO admins (username, password_hash,"
                     " salt, security_question, security_answer_hash)"
                     " VALUES ('panel','x','y','q','z')")
        conn.execute("INSERT INTO admin_sessions (token, username, expiry)"
                     " VALUES (?,?,?)",
                     (token, "panel",
                      (datetime.datetime.now()
                       + datetime.timedelta(hours=1)).isoformat()))
        conn.commit()
        conn.close()
        c.cookies.set("admin_session", token)
        from app.auth.csrf import token_for_session
        c.headers["X-CSRF-Token"] = token_for_session(token)
        yield c
```

Two details that break tests if you skip them:

- The session row is inserted directly. There is no login helper fixture, and
  going through `POST /admin/login` would drag the brute-force lockout into
  every test.
- `X-CSRF-Token` is `token_for_session(session_token)`. The CSRF middleware
  gates `POST`, `PUT`, `PATCH` and `DELETE` under `/admin/`,
  `/secure-panel-admin` and `/api/synonyms`. Only `POST /admin/login` is
  exempt. Without the header the middleware answers 403 before your route runs
  and the failure looks like a routing bug.

### The gate test every admin router needs

The constitution says each endpoint authenticates and authorizes on its own.
One route that forgets `Depends(verify_admin)` publishes visitor names, phone
numbers and everything people typed. Prove it in one parametrized test:

```python
API_ROUTES = [
    "/admin/api/conversations",
    "/admin/api/conversations/weak",
    "/admin/api/visitors",
]


@pytest.mark.parametrize("route", API_ROUTES)
def test_every_api_route_refuses_an_anonymous_caller(anon, route):
    assert anon.get(route).status_code in (401, 403), route
```

Admin PAGE routes redirect instead of returning a status code. Assert the
redirect, not a 401:

```python
@pytest.mark.parametrize("route", PAGE_ROUTES)
def test_pages_send_an_anonymous_visitor_to_the_login(anon, route):
    res = anon.get(route, follow_redirects=False)
    assert res.status_code == 303, route
    assert "/login" in res.headers.get("location", "")
```

### Chat endpoint test

From `tests/test_chat_options_pick.py`:

```python
@pytest.fixture
def client(tmp_path, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "options.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    from app.main import app
    from app.auth import security
    security._chat_rate_limits.clear()
    with TestClient(app) as c:
        from app.auth.security import generate_chat_token
        c.headers.update({"Origin": "http://localhost",
                          "X-Chat-Token": generate_chat_token()})
        yield c
    security._chat_rate_limits.clear()
```

Clear `security._chat_rate_limits` on both sides. The limiter is per-IP
process state and every test client shares one IP, so a file with more than 20
chat calls throttles itself.

### Public visitor endpoint test

From `tests/test_otp.py`. A real browser always sends `Origin` and
`User-Agent`, `TestClient` sends neither, and `validate_request_origin` runs on
the public surface:

```python
with TestClient(app) as c:
    c.headers.update({"Origin": "http://localhost",
                      "User-Agent": "pytest-agent/1.0"})
    yield c
```

When a test file fires dozens of requests, disable only the per-IP limiter and
leave the product limits that the file is asserting:

```python
@pytest.fixture(autouse=True)
def _no_ip_throttle(monkeypatch):
    import app.routers.otp as otp_router
    monkeypatch.setattr(otp_router, "check_rate_limit", lambda request: None)
```

## Real-PostgreSQL tests (`tests/postgres/`)

`tests/conftest.py` pins `DB_BACKEND=sqlite` so the main suite is fast and
hermetic. Production is PostgreSQL 16. `tests/postgres/` closes that gap
against a real server.

How it works, so you use it correctly:

- Opt-in with `RUN_POSTGRES_TESTS=1`. Without it every test in the directory
  skips, so a machine with no server still runs green.
- The session fixture creates two throwaway schemas
  (`padyar_test_<pid>_<rand>` and `..._obs`), applies the real
  `migrations/*.sql` into them with the `app.` and `observability.` prefixes
  rewritten, and drops them at the end.
- `app` is deliberately NOT on the test `search_path`, so a table the harness
  forgot to create fails loudly instead of silently hitting the operator's live
  table.
- `_live_data_is_untouched` snapshots the live `app.*` row counts before and
  after the session and fails if anything moved.

Fixtures available there:

| Fixture | What it gives you |
|---|---|
| `client` | `TestClient` with an admin session, CSRF header and data in the test schemas |
| `conn` | A connection through the real adapter (`app/db/pg.py`), which is where placeholder translation and `lastrowid` emulation live |
| `raw` | A raw psycopg connection, for the few assertions that must bypass the adapter (for example "what type did the column actually get?") |
| `pg_clean` | Autouse. TRUNCATEs every test table with `RESTART IDENTITY CASCADE` before each test |

Example, from `tests/postgres/test_dataset_api.py`:

```python
def test_a_duplicate_id_is_a_controlled_409_not_a_500(client):
    assert _create(client, "pg-dup").status_code == 200
    res = _create(client, "pg-dup", title="دیگر")
    assert res.status_code == 409
    assert res.json()["detail"] == "ID already exists"
```

That test exists because `app/routers/dataset.py` caught
`sqlite3.IntegrityError`, which PostgreSQL never raises. On the production
backend a duplicate id was a 500 with a traceback. The SQLite suite could not
see it.

CI runs this directory as a separate blocking job (`postgres-tests` in
`.github/workflows/ci.yml`, with a `postgres:16` service container).

## Unit test example

```python
from app.utils.normalizer import normalize_persian


def test_arabic_letters_are_folded_onto_their_persian_forms():
    arabic = normalize_persian("كتاب عربي", expand_synonyms=False)
    persian = normalize_persian("کتاب عربی", expand_synonyms=False)
    assert arabic == persian
```

`expand_synonyms=False` keeps the test off the database. The default is True
and reads the `synonyms` table, which a unit test should not depend on.

Patch dependencies with `monkeypatch`, not a mocking library:

```python
def test_reads_the_default_when_the_setting_is_absent(monkeypatch):
    monkeypatch.setattr("app.db.queries.get_setting",
                        lambda key, default=None: default)
    ...
```

## Critical Rules

1. **Never mock the database.** Tests run against a throwaway SQLite file
   (`tmp_path`) or a throwaway PostgreSQL schema. A mocked database hides the
   SQL bugs the test exists to catch.
2. **Always redirect `config.DB_PATH` to `tmp_path`.** Otherwise the test
   writes into the developer's real `chat_history.db`. Set
   `SEED_DEFAULT_CONTENT = False` too, so only your rows are there.
3. **Always test the anonymous caller** for every admin route. 401/403 for
   API routes, a 303 to `/login` for page routes.
4. **Send `X-CSRF-Token`** on every admin mutation, built with
   `app.auth.csrf.token_for_session`.
5. **Test pagination** on any endpoint that returns a collection. The
   constitution forbids unbounded collections, so assert the limit is applied
   and that a second page is reachable.
6. **Patch the importing module, not the defining one.** `app/routers/chat.py`
   did `from ... import get_openai_response` at import time, so
   `monkeypatch.setattr(chat, "get_openai_response", fake)` is what works.
7. **Never reach the network.** Patch the AI wrapper
   (`app.services.ai.wrapper.padyar_ai.generate`) and the SMS provider seam.
8. **Reset process-wide state you disturb:** `security._chat_rate_limits`,
   `search._last_version_check`, the settings cache. `tests/conftest.py`
   already handles the common ones.
9. **Kiosk is the threat model.** This runs on a shared booth browser. For any
   session, cookie or history feature, write the test that proves the next
   person does not inherit the last person's state.
10. **Never add a second top-level `conftest.py`.** Extend `tests/conftest.py`
    only when more than one file needs the fixture.
11. **Never use pytest-playwright's sync fixtures** (`page`, `browser`,
    `context`, `browser_context`, `playwright`). `tests/test_suite_isolation.py`
    fails the suite if one appears. See the `e2e-test-gen` skill.

## Workflow

1. Read the router or service you are testing, and the dependency that guards
   it (`Depends(verify_admin)`, chat token, visitor session).
2. Read an existing test file that covers a nearby area and copy its fixtures.
3. Decide the test type from the table above.
4. Write the tests: success, invalid input, unauthorized, missing resource,
   duplicate request, and the regression you actually came for.
5. Run just your file:

   ```bash
   .venv/bin/python -m pytest tests/test_myfeature.py -q
   ```

6. Do not run the full suite locally. This machine has 15 tests that always
   fail here (they need a live PostgreSQL or the network) and always pass on
   CI. The local pre-commit check is only
   `python -m py_compile app/main.py app/routers/chat.py`.
7. After pushing, CI is the gate:

   ```bash
   gh run list --branch <branch> --limit 1
   gh run watch
   ```

DO NOT add inline comments to generated test code unless the setup is genuinely
hard to follow. Put the reasoning in the module docstring, the way the existing
files do.
