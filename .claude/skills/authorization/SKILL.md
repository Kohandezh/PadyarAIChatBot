---
name: authorization
description: Use when implementing authorization, access control, or endpoint security in the PadyarAIChatbot. Covers admin cookie-session auth, the chat-token + origin + rate-limit trio for the public chat endpoint, password hashing, parameterized SQLite queries, and fail-closed security principles.
---

# Authorization & Security for PadyarAIChatbot

This skill provides guidance for securing endpoints in the PadyarAIChatbot. The app is a **per-customer CMS** (installed once per customer, not multi-tenant). There are **no workspaces, spaces, or roles** — only a single **admin** role plus token-gating for the public chat endpoint.

All security primitives live in `app/auth/security.py`. Reuse them — do not roll your own.

## Two Security Domains

| Domain               | Who                        | How it's protected                                            |
| -------------------- | -------------------------- | ------------------------------------------------------------- |
| **Admin panel**      | The customer's staff       | Cookie session validated by `verify_admin` dependency         |
| **Public chat API**  | End-users on the chat page | HMAC chat token + origin check + per-IP rate limit            |

## Protecting Admin Routes

Admin routes use a FastAPI dependency. `verify_admin(request)` is async — it reads the `admin_session` cookie (`config.ADMIN_COOKIE_NAME`), validates the token against the `admin_sessions` table (expiry + username), and **slides** the expiry forward by `SESSION_TIMEOUT_HOURS` (1h) on each request. It returns the admin username, or raises `HTTPException(401)`.

Attach it to a whole router or per-route:

```python
from fastapi import APIRouter, Depends
from app.auth.security import verify_admin

router = APIRouter(
    prefix="/admin",
    dependencies=[Depends(verify_admin)],  # protects every route on this router
)

@router.get("/stats")
def stats():
    ...  # only reachable with a valid admin session

# Or, if you need the username inside the handler:
@router.post("/settings")
def update_settings(username: str = Depends(verify_admin)):
    ...
```

**Never** check the cookie by hand. Always go through `verify_admin`.

### Admin Login Flow

Login is `POST /admin/login` with JSON `{username, password, sec_answer}`.

- **Passwords:** bcrypt via `hash_password` / `verify_password`. `verify_password` also accepts a **legacy salted SHA-256** hash so old installs keep working; on a successful login with `is_legacy_hash(stored) == True`, re-hash with bcrypt and update the `admins` row (upgrade-on-login).
- **Security answer:** stored as `hashlib.sha256(answer.encode()).hexdigest()`.
- **Brute force:** the in-memory `login_attempts` dict tracks failures per IP. After `MAX_LOGIN_ATTEMPTS` (5) the IP is blocked for `BLOCK_TIME_MINUTES` (5). This state is **in-memory only** — it clears on restart and is per-process. Don't rely on it as a hard guarantee across workers.

```python
from app.auth.security import hash_password, verify_password, is_legacy_hash

if verify_password(password, row["password"], row.get("salt", "")):
    if is_legacy_hash(row["password"]):
        new_hash = hash_password(password)
        # UPDATE admins SET password = ? WHERE username = ?  (bcrypt upgrade)
```

## Protecting the Public Chat Endpoint

The `/chat` endpoint must pass **all three** checks. They are independent dependencies in `app/auth/security.py`:

```python
from fastapi import Depends
from app.auth.security import (
    validate_chat_token, validate_request_origin, check_rate_limit,
)

@router.post(
    "/chat",
    dependencies=[
        Depends(validate_chat_token),
        Depends(validate_request_origin),
        Depends(check_rate_limit),
    ],
)
def chat(...):
    ...
```

| Check                     | What it enforces                                                                                                                                                  |
| ------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `validate_chat_token`     | Header `X-Chat-Token`, format `ts.sig` where `sig = sha256(f"{ts}.{key}")[:32]`. TTL = `CHAT_TOKEN_TTL` (3600s). Token is minted by `generate_chat_token` and injected into the chat HTML. |
| `validate_request_origin` | Requires a User-Agent and an `Origin`/`Referer` whose hostname is in `config.ALLOWED_ORIGINS`. Otherwise 403.                                                       |
| `check_rate_limit`        | `CHAT_RATE_LIMIT` (2) requests per `CHAT_RATE_WINDOW` (30s) per IP, in-memory. Otherwise 429.                                                                       |

The HMAC signing key comes from `config.SECRET_KEY` (env) if set, otherwise a stable `app_secret_key` auto-generated and stored in the `settings` table (`INSERT OR IGNORE`, race-safe across workers). Never log or expose this key.

## SQLite Security

Database access goes through `app/db/connection.py` (`get_db_connection()` — sqlite3 with `Row` factory) and `app/db/queries.py`.

**Always use parameterized queries.** Never string-format user input into SQL.

```python
from app.db.connection import get_db_connection

conn = get_db_connection()
try:
    # CORRECT — ? placeholders
    row = conn.execute(
        "SELECT * FROM dataset WHERE id = ?", (entry_id,)
    ).fetchone()
finally:
    conn.close()

# WRONG — never do this
# conn.execute(f"SELECT * FROM dataset WHERE id = {entry_id}")
```

For settings, use the helpers rather than raw SQL: `get_setting(key, default)` and `set_setting(key, value)` from `app/db/queries.py` (WordPress-style key-value, includes `whitelabel_*` keys).

## Input Validation

Validate every request body with a **Pydantic model** from `app/models.py`. Declare the model as the typed parameter so FastAPI rejects malformed input at the boundary (422) before your handler runs.

```python
from app.models import ChatRequest  # define/extend models in app/models.py

@router.post("/chat")
def chat(payload: ChatRequest):
    text = payload.message  # already validated and typed
```

## Security Principles

1. **Fail closed.** On any error or missing credential, deny (raise `HTTPException`), never fall through to granting access.
2. **Least information.** Use generic messages ("Forbidden.", "Unauthorized") — don't reveal whether a token was missing vs. expired vs. invalid beyond what the existing handlers already do.
3. **Never trust client input.** Don't trust client-supplied identity, admin flags, or origin claims — derive identity from `verify_admin`, not from the request body.
4. **Parameterize all SQL.** `?` placeholders only.
5. **Reuse the primitives.** All auth lives in `app/auth/security.py`. Don't duplicate token, hashing, or rate-limit logic elsewhere.
6. **Keep it simple (grandmother test).** Per CLAUDE.md, no extra auth knobs end-users would have to understand. Security must be invisible to the end-user.

## Important Files

| Path                     | Purpose                                                                       |
| ------------------------ | ----------------------------------------------------------------------------- |
| `app/auth/security.py`   | All auth: `verify_admin`, chat token, origin check, rate limit, password hashing |
| `app/config.py`          | Security constants: `ADMIN_COOKIE_NAME`, `ALLOWED_ORIGINS`, `SECRET_KEY`, TTLs, limits |
| `app/db/connection.py`   | `get_db_connection()`, `init_db()` — schema for `admins`, `admin_sessions`, `settings` |
| `app/db/queries.py`      | `get_setting` / `set_setting`, `log_chat`, parameterized data mutations        |
| `app/models.py`          | Pydantic request/response schemas for input validation                         |
| `CLAUDE.md`              | "Security" section — the authoritative summary of the security model           |
