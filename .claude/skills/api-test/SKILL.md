---
name: api-test
description: Generate pytest tests for PadyarAIChatbot FastAPI routes via TestClient. Covers admin cookie-session auth (login-and-reuse or dependency_overrides), the public chat endpoint's token + origin + rate-limit requirements, and the admin dataset/questions/backup endpoints. Uses a real temp SQLite DB — never mocks the database.
---

You are a test engineer for **PadyarAIChatbot** (FastAPI + SQLite). Generate route/endpoint tests using FastAPI's `TestClient`, following the patterns below exactly.

## Bootstrapping (no `tests/` dir exists yet)

Test deps are **already installed** and tracked in **`requirements-dev.txt`** (`pytest`, `pytest-asyncio`, `pytest-playwright`; `httpx` is already a runtime dep). `pytest.ini` enables asyncio auto-mode. You only need to create `tests/` + `tests/conftest.py` and write the tests. On a fresh checkout:

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
```

Run with:

```bash
.venv/bin/python -m pytest tests/test_admin_api.py -q
```

`app.config` raises `ValueError` at import unless `OPENAI_API_KEY` is set, so `tests/conftest.py` must set a dummy key **before** any `app.*` import.

## The FastAPI app

The ASGI app is `app.main:app` — a FastAPI instance created at import time in `app/main.py`. Its lifespan calls `init_db()`, and routers are loaded from the module registry based on `ENABLED_MODULES`. For tests, import it and wrap it:

```python
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)   # sync client; uses the already-installed httpx
```

`TestClient` runs the lifespan on context entry and **persists cookies** across requests on the same instance — that is what makes the admin login-and-reuse flow work.

## Critical rule: real temp DB, NEVER mock the DB

Use a real throwaway SQLite file. `app.config.DB_PATH` (= `BASE_DIR/chat_history.db`) is **not env-overridable**, but `get_db_connection()` and `init_db()` in `app/db/connection.py` re-import `DB_PATH` from `app.config` **inside the function body** on every call. So monkeypatching the attribute before requests run is sufficient and clean — and keeps tests off the real `chat_history.db`.

## conftest.py — fixtures

```python
# tests/conftest.py
import os
os.environ.setdefault("OPENAI_API_KEY", "test-key")   # before any app import
os.environ["ADMIN_USERNAME"] = "test@admin"
os.environ["ADMIN_PASSWORD"] = "test-password-123"
os.environ["ADMIN_SECURITY_ANSWER"] = "blue"

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    import app.config
    from app.db.connection import init_db
    db_file = tmp_path / "test.db"
    monkeypatch.setattr(app.config, "DB_PATH", str(db_file))
    init_db()   # creates tables + seeds the admin from the env vars above
    return str(db_file)


@pytest.fixture
def client(temp_db):
    from app.main import app
    with TestClient(app) as c:
        yield c


@pytest.fixture
def admin_client(client):
    """A TestClient already logged in as admin (session cookie set)."""
    resp = client.post("/admin/login", json={
        "username": "test@admin",
        "password": "test-password-123",
        "sec_answer": "blue",
    })
    assert resp.status_code == 200
    return client
```

Notes:
- The login body is `LoginRequest`: `{username, password, sec_answer}` (POST `/admin/login`, `app/routers/admin.py`).
- Setting `ADMIN_*` env vars before `init_db()` makes the seeded admin password deterministic so the login fixture can authenticate. Otherwise the seeded password is random.
- The admin session cookie is named `admin_session` (`config.ADMIN_COOKIE_NAME`); `verify_admin` reads it from the request cookies.

## Admin auth: two ways

### Option A — login and reuse the cookie (preferred; exercises real auth)
Use the `admin_client` fixture above. The same `TestClient` carries the `admin_session` cookie on every subsequent call.

### Option B — override the dependency (skip the login round-trip)
```python
def test_with_dep_override(client):
    from app.main import app
    from app.auth.security import verify_admin
    app.dependency_overrides[verify_admin] = lambda: "test-admin"
    try:
        resp = client.get("/admin/api/dataset")
        assert resp.status_code == 200
    finally:
        app.dependency_overrides.clear()
```
Admin routes declare `dependencies=[Depends(verify_admin)]`, so overriding `verify_admin` unblocks all of them. Always clear overrides in a `finally`.

Every admin route should also have a **401/unauthenticated** test: hit it with a plain `client` (no login, no override) and assert it is rejected.

## Public chat endpoint (`POST /chat`)

`/chat` (`app/routers/chat.py`) enforces three things, in order: `validate_request_origin`, `validate_chat_token`, `check_rate_limit`. To get a 200 you must satisfy all three:

- **Origin/Referer** in `config.ALLOWED_ORIGINS` — `localhost` and `127.0.0.1` are always allowed. Send `Origin: http://localhost`.
- **Chat token** in the `X-Chat-Token` header — mint one with `generate_chat_token()` from `app/auth/security.py`.
- **Rate limit** — `CHAT_RATE_LIMIT = 2` requests per `CHAT_RATE_WINDOW = 30s` per client IP. The 3rd quick request from the same IP is rejected.

Request body is `ChatRequest`: `{"message": "..."}`. Response is `ChatResponse`: `{type, text, video_url, confidence, source}`.

```python
def test_chat_returns_response(client):
    from app.auth.security import generate_chat_token
    token = generate_chat_token()
    resp = client.post(
        "/chat",
        json={"message": "سلام"},
        headers={"X-Chat-Token": token, "Origin": "http://localhost"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["source"] in ("local", "openai", "gpt")  # confirm valid sources in chat.py
    assert "text" in body


def test_chat_rejects_missing_token(client):
    resp = client.post(
        "/chat",
        json={"message": "سلام"},
        headers={"Origin": "http://localhost"},
    )
    assert resp.status_code in (401, 403)   # confirm exact code in validate_chat_token


def test_chat_rate_limited(client):
    from app.auth.security import generate_chat_token
    token = generate_chat_token()
    h = {"X-Chat-Token": token, "Origin": "http://localhost"}
    client.post("/chat", json={"message": "a"}, headers=h)
    client.post("/chat", json={"message": "b"}, headers=h)
    third = client.post("/chat", json={"message": "c"}, headers=h)
    assert third.status_code == 429   # confirm code raised by check_rate_limit
```

If you do not want the AI tier to fire (no real network), seed a high-confidence dataset match first (`save_dataset`/`save_questions` + refresh the search index) or monkeypatch `app.routers.chat.classify_intent` / `get_openai_response`. The chat module imports those names directly, so patch them on `app.routers.chat`, not on `app.services.openai`. **Never let a test hit the real GapGPT API.**

## Admin dataset / questions / backup endpoints

Verified routes (all under `Depends(verify_admin)`):

| Method & path | Source |
| --- | --- |
| `GET /admin/api/dataset` | `app/routers/dataset.py` |
| `POST /admin/api/dataset` | create entry |
| `PUT /admin/api/dataset/{item_id}` | update |
| `DELETE /admin/api/dataset/{item_id}` | delete |
| `GET /admin/api/dataset/export` / `POST /admin/api/dataset/import` | export/import |
| `GET /admin/api/questions` + `POST/PUT/DELETE` + `/export` + `/import` | questions CRUD |
| `GET /admin/api/backups`, `POST /admin/api/backups/create`, `GET /admin/api/backups/download/{name}`, `DELETE /admin/api/backups/{name}`, `POST /admin/api/backups/restore/{name}` | `app/routers/admin.py` |
| `POST /admin/login`, `POST /admin/logout`, `GET /admin/check_auth`, `GET /admin/api/stats` | `app/routers/admin.py` |

Always re-grep `app/routers/` to confirm a route, its method, and its request body before asserting on it — do not invent endpoints or payload shapes.

```python
def test_dataset_crud(admin_client):
    resp = admin_client.post("/admin/api/dataset", json={
        "id": "lasik", "title": "لیزیک", "text": "توضیح", "video_url": "",
    })   # confirm the exact request schema in dataset.py first
    assert resp.status_code in (200, 201)

    listed = admin_client.get("/admin/api/dataset")
    assert listed.status_code == 200
    assert any(item["id"] == "lasik" for item in listed.json())


def test_dataset_requires_auth(client):
    assert client.get("/admin/api/dataset").status_code == 401
```

## Critical rules

1. **NEVER mock the database** — use a real temp SQLite DB via the `temp_db` fixture and the `DB_PATH` monkeypatch. Off-limits: the real `chat_history.db`.
2. **Mock only external network** (OpenAI/GapGPT). Patch `app.routers.chat.classify_intent` / `get_openai_response` (the chat router imports them by name).
3. **Always test auth** — every admin route gets a 401/unauthenticated test; `/chat` gets a missing-token rejection test.
4. **Reuse one TestClient per logged-in flow** — cookies persist on the instance; that is how login-and-reuse works.
5. **Clear `app.dependency_overrides`** in a `finally` whenever you use Option B.
6. **Verify before asserting** — grep `app/routers/` for the real route, method, status codes, and request/response schema. Confirm exact status codes (401 vs 403, 200 vs 201) against the handler rather than guessing.
7. **Function-scoped temp DB** — each test starts from a freshly initialized DB.

## Workflow

1. Read the target router/handler and its request/response models (`app/models.py`).
2. Pick the auth approach (login-and-reuse via `admin_client`, or `dependency_overrides`).
3. Ensure `tests/conftest.py` has `temp_db`, `client`, and `admin_client`.
4. Write tests under `tests/` (e.g. `tests/test_admin_api.py`, `tests/test_chat_api.py`) with descriptive `test_*` names: a happy path, an auth-rejection path, and a validation/error path.
5. Run `.venv/bin/python -m pytest tests/<file> -q` until green.
6. Run `python -m py_compile app/main.py app/routers/chat.py` before committing.

Do NOT add inline comments to generated test code unless a setup step is genuinely non-obvious.
