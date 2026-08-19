---
name: write-tests
description: Generate focused pytest tests for the PadyarAIChatbot (Python + FastAPI). Use this to create unit tests for services/utils/auth and integration tests via FastAPI's TestClient, covering the TF-IDF search pipeline, Persian normalization, auth/rate-limit, and dataset/questions import-export.
---

You are an expert Python testing engineer generating tests for **PadyarAIChatbot** — a FastAPI + SQLite app with a scikit-learn TF-IDF search pipeline and OpenAI (via the GapGPT proxy) fallback. Tests use **pytest**.

## Project Testing Reality (read this first)

- There is **no `tests/` dir yet**, so writing the first tests means creating `tests/` and a `tests/conftest.py`. CLAUDE.md says: "The project doesn't currently have a formal test suite — if adding one, use pytest."
- The test deps are **already installed** and tracked in **`requirements-dev.txt`** (kept separate from `requirements.txt` so customer installs don't pull in pytest/Playwright): `pytest`, `pytest-asyncio`, `pytest-playwright`. `httpx` is already a runtime dependency (TestClient uses it). A fresh checkout sets up with:
  ```bash
  .venv/bin/python -m pip install -r requirements-dev.txt
  ```
- `pytest-asyncio` runs in **auto** mode (configured in `pytest.ini`), so `async def test_*` works without a per-test marker.
- Run tests with the project interpreter:
  ```bash
  .venv/bin/python -m pytest
  .venv/bin/python -m pytest tests/test_search.py -q
  ```
- `app.config` raises `ValueError` at import if `OPENAI_API_KEY` is missing. The test process must have it set (a dummy value is fine since real network calls are mocked). Set it in `tests/conftest.py` **before** anything imports `app.*`, or export it in the environment.
- Before committing, honor the CLAUDE.md mandatory check:
  ```bash
  python -m py_compile app/main.py app/routers/chat.py
  ```

## File Layout

All tests live in a top-level `tests/` directory:

```
tests/
├── conftest.py            # shared fixtures: temp DB, TestClient, admin login, chat token
├── test_search.py         # TF-IDF matching (app/services/search.py)
├── test_normalizer.py     # Persian normalization + synonyms (app/utils/normalizer.py)
├── test_security.py       # tokens, password hashing, rate limit (app/auth/security.py)
├── test_openai.py         # classify_intent / get_openai_response with the OpenAI client mocked
├── test_chat_api.py       # POST /chat integration (token + origin + rate limit)
└── test_admin_api.py      # admin login + dataset/questions/backup endpoints
```

## Step 1: Analyze the target

Identify: purpose, dependencies (DB? OpenAI network? HTTP layer?), and side effects. Then pick a strategy.

## Step 2: Choose UNIT vs INTEGRATION

**UNIT tests** (no HTTP, call the function directly) for:

- `app/utils/normalizer.py` — `normalize_persian(text)`, synonym expansion. Pure-ish (synonyms load from DB, so use the temp-DB fixture).
- `app/services/search.py` — `find_best_match(query)` returns `(best_match, score)`; `find_similar_question(query)`. Threshold `SIMILARITY_THRESHOLD = 0.20`. Needs dataset rows in the temp DB.
- `app/auth/security.py` — `hash_password` / `verify_password`, `generate_chat_token`, `validate_chat_token`, `check_rate_limit`.
- `app/services/openai.py` — `classify_intent`, `get_openai_response`. **Mock the OpenAI/GapGPT client — never hit the real API.**

**INTEGRATION tests** (via `TestClient`) for anything that goes through a FastAPI route: `/chat`, `/admin/*`. See the `api-test` skill for the full route-testing playbook.

## Step 3: The conftest temp-DB pattern (critical)

`app/config.DB_PATH` is computed at import time as `BASE_DIR/chat_history.db` and is **NOT env-overridable**. `get_db_connection()` and `init_db()` (in `app/db/connection.py`) both do `from app.config import DB_PATH` **inside** the function body — they re-read `app.config.DB_PATH` on every call. That makes it cleanly monkeypatchable: set `app.config.DB_PATH` to a temp path *before* the code runs, and every connection opens against the throwaway DB. **Never let tests touch the real `chat_history.db`.**

```python
# tests/conftest.py
import os
os.environ.setdefault("OPENAI_API_KEY", "test-key")  # must precede any app import

import pytest


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    """Point the app at a throwaway SQLite DB and initialize the schema."""
    import app.config
    from app.db.connection import init_db

    db_file = tmp_path / "test_chat_history.db"
    # get_db_connection()/init_db() re-import DB_PATH from app.config each call,
    # so patching the attribute is enough.
    monkeypatch.setattr(app.config, "DB_PATH", str(db_file))
    init_db()  # creates all tables + seeds default synonyms/settings/admin
    return str(db_file)
```

Notes:
- `init_db()` seeds the `synonyms`, `settings`, and `admins` tables. The seeded admin password is random unless you set `ADMIN_PASSWORD`/`ADMIN_SECURITY_ANSWER` env vars before `init_db()` — set them in the fixture when a test needs to log in (see the `api-test` skill).
- To seed dataset/questions rows for search tests, use `app.db.queries.save_dataset([...])` and `save_questions([...])` after `init_db()`.

## Step 4: pytest idioms (replacing Vitest)

| Vitest | pytest |
| --- | --- |
| `describe`/`it` | plain `def test_*` functions (group by file/module) |
| `expect(x).toBe(y)` | `assert x == y` |
| `expect(fn).toThrow()` | `with pytest.raises(SomeError): fn()` |
| `vi.mock(...)` / `vi.fn()` | `monkeypatch.setattr(...)` / `unittest.mock.MagicMock` |
| `beforeEach` | a `@pytest.fixture` passed as an argument |
| table-driven cases | `@pytest.mark.parametrize` |
| async test | `@pytest.mark.asyncio` (needs `pytest-asyncio`) |

## What to cover (this project's high-value tests)

### TF-IDF search pipeline (`test_search.py`)
- A query that closely matches a seeded dataset entry returns that entry with `score >= 0.20`.
- An unrelated/gibberish query returns a score below threshold (so the route would fall through to the AI tier).
- `find_best_match` returns the tuple shape `(dict-or-None, float)`.

```python
def test_find_best_match_returns_relevant_entry(temp_db):
    from app.db.queries import save_dataset, save_questions
    from app.services.search import find_best_match, load_dataset_internal

    save_dataset([{"id": "lasik", "title": "لیزیک", "text": "توضیح لیزیک", "video_url": ""}])
    save_questions([{"question": "عمل لیزیک چیست", "dataset_id": "lasik", "video_url": ""}])
    load_dataset_internal()  # refresh the in-memory TF-IDF index from the DB

    best, score = find_best_match("عمل لیزیک")

    assert best is not None
    assert best["id"] == "lasik"
    assert score >= 0.20
```

(Confirm the exact in-memory refresh entrypoint in `app/services/search.py` before relying on `load_dataset_internal()`.)

### Persian normalization (`test_normalizer.py`)
Edge cases that matter for Persian text:
- Arabic vs Persian characters: `ي → ی`, `ك → ک`.
- Diacritics/zero-width chars stripped; extra whitespace collapsed.
- Synonym expansion uses the seeded `synonyms` table (e.g. `لیزیک → لیزر لیزیک`).

```python
import pytest

@pytest.mark.parametrize("raw, expected_substr", [
    ("كيف", "کیف"),          # Arabic kaf/ya normalized to Persian
    ("  سلام  ", "سلام"),     # trimmed
])
def test_normalize_persian(temp_db, raw, expected_substr):
    from app.utils.normalizer import normalize_persian
    assert expected_substr in normalize_persian(raw)
```

### Auth & rate limit (`test_security.py`)
- `verify_password(p, hash_password(p))` is True; wrong password is False.
- `validate_chat_token` accepts a token from `generate_chat_token()` and rejects a missing/garbage token.
- `check_rate_limit` allows `CHAT_RATE_LIMIT` (2) calls per IP in the window, then raises on the next. Build a fake `Request` with the client IP you want, or drive it through `/chat` (see `api-test`).

### OpenAI fallback (`test_openai.py`)
Mock the client — do **not** call the network. `app/services/openai.py` builds module-level `AsyncOpenAI` clients (`_classification_client`) and ad-hoc clients inside `get_openai_response`/`_transcribe_sync`. Monkeypatch the relevant client (or its `.chat.completions.create`) to return a canned response object, then assert the parsing/branching logic.

```python
import pytest

@pytest.mark.asyncio
async def test_classify_intent_parses_model_output(monkeypatch):
    import app.services.openai as oai
    # patch the client's create() to return a stub matching what the code reads
    ...  # assert classify_intent("...") returns the expected intent/branch
```

### Dataset/questions import-export
Unit-test `save_dataset` / `save_questions` round-trips against the temp DB; integration-test the `/admin/api/dataset/import|export` and `/admin/api/questions/import|export` routes via `api-test`.

## Best practices

- **AAA**: Arrange (fixtures/seed data) → Act (call) → Assert. One behavior per test.
- Test **behavior and outcomes**, not implementation details.
- Use `@pytest.mark.parametrize` instead of copy-pasting near-identical happy paths.
- Mock only what is **external** (OpenAI/GapGPT network, the system clock if needed). **Never mock SQLite** — use the real temp DB.
- Keep tests independent: each gets a fresh `temp_db` (function-scoped fixture).
- Do **not** add inline comments to generated test code unless a setup step is genuinely non-obvious.

## Workflow

1. Read the source file(s) to understand inputs/outputs and dependencies.
2. Decide unit vs integration; for integration defer to the `api-test` skill.
3. Ensure `tests/conftest.py` exists with the `temp_db` (and, for routes, `client`) fixtures.
4. Write the tests under `tests/` with descriptive `test_*` names.
5. Test deps are already installed (`requirements-dev.txt`) — run `.venv/bin/python -m pip install -r requirements-dev.txt` on a fresh checkout.
6. Run `.venv/bin/python -m pytest -q` and iterate until green.
7. Run `python -m py_compile app/main.py app/routers/chat.py` before committing.
