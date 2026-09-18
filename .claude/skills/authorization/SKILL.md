---
name: authorization
description: Use when implementing authentication, authorization, or access control in PadyarAIChatbot (Python/FastAPI). Covers the admin session door (verify_admin), the visitor chat trio (HMAC chat token + origin allowlist + rate limit), the leads module's three separate doors, OTP, CSRF, and the kiosk threat model.
---

# Authorization Patterns for PadyarAIChatbot

This repo has no single login. It has several separate doors, and each one
answers a different question about who is calling. Pick the door that matches
the surface you are touching. Do not invent a new one.

Read `docs/engineering/SECURITY.md` and `docs/engineering/SECURITY_MODEL.md`
before you change any of this. `docs/engineering/ENGINEERING_CONSTITUTION.md`
outranks both.

## The doors

| Surface | Who is calling | How the server decides |
| --- | --- | --- |
| Admin panel and `/admin/api/*` | A staff operator | `Depends(verify_admin)`: admin session cookie, row in `admin_sessions`, sliding expiry |
| `POST /chat`, `/api/transcribe` | An anonymous booth visitor | HMAC chat token + Origin/Referer allowlist + two-tier rate limit, all three |
| Registered visitor features | A visitor who completed signup | `visitor_sessions` row behind an HttpOnly cookie, resolved by middleware |
| `/v/{code}` (leads) | A field staff member on their own link | The code itself is the credential, re-read from `lead_visitors` every request |
| `/edit/{token}` (leads) | A company contact with a one-time invite | HMAC of the token in `edit_invites`, burned on use, then a short edit session |
| `/verify` and `/api/auth/otp/*` | Someone proving a phone number | `otp_challenges`: HMAC of the code, expiry, attempt and resend caps |

No credential is shared between doors. A visitor cookie does not open the admin
panel, and an admin session does not open a lead edit page.

## The binding rules

These come from `docs/engineering/ENGINEERING_CONSTITUTION.md` and
`docs/engineering/API_STANDARDS.md`. They are not style. Break one and the
change is wrong.

1. **Every resource endpoint authenticates and authorizes on its own.** Do not
   rely on the page that linked to it, on a middleware you assume ran, or on a
   sibling endpoint that already checked.
2. **Never infer authorization from possession of a resource id.** Knowing a
   `dataset_id` is not permission to edit that row. The id says which row, the
   credential says whether you may touch it.
3. **Authorize the row you actually write.** If you look up a row by one
   identifier and then write a row selected by another, the check protected
   nothing. `API_STANDARDS.md` calls this Resource Binding.
4. **Any endpoint that returns a collection must paginate.** Take `limit` and
   `offset`, clamp `limit`, and never load the whole table into memory. See
   `admin_company_profiles` in `app/routers/leads.py` for the shape used here.
5. **Fail closed on the decision, and say so.** The only deliberate
   fail-open paths are documented in place (the login lockout table, the
   in-memory rate-limit fallback). Do not add a third without an entry in
   `docs/engineering/DECISIONS.md`.

## The kiosk threat model

This app runs on a shared booth browser. Strangers use the same tab one after
another. So the default bug in this codebase is:

> the next person inherits the last person's state.

Every session has to have an answer for that. `app/auth/visitor.py` shows the
pattern: two clocks, and a session must pass both.

- `expiry` slides on every request, so `VISITOR_SESSION_DAYS` (30) is really an
  inactivity window. On a kiosk that window never closes, because the next
  stranger's traffic keeps renewing it.
- `created_at` never moves, so `VISITOR_SESSION_MAX_HOURS` (12) is the bound a
  kiosk can actually reach. Past it the session dies no matter how busy the
  machine was.

The same reasoning caps the conversation context: `HISTORY_WINDOW_MINUTES` and
`PICK_WINDOW_MINUTES` (both 15, in `app/config.py`) stop one visitor's offered
list from being pickable by the next one.

When you add anything that remembers a visitor, state the kiosk answer in the
code comment. An inactivity timeout alone is not one.

## Admin door

```python
from fastapi import Depends
from app.auth.security import verify_admin

# Route needs the username:
@router.get("/admin/api/thing")
async def read_thing(username: str = Depends(verify_admin)):
    ...

# Route only needs the gate:
@router.get("/admin/api/thing", dependencies=[Depends(verify_admin)])
async def read_thing():
    ...
```

`verify_admin` (`app/auth/security.py`) reads the `ADMIN_COOKIE_NAME` cookie,
looks the token up in `admin_sessions`, deletes the row and raises 401 when it
has expired, then slides the expiry by `SESSION_TIMEOUT_HOURS` (1 hour). It
sets `request.state.slide_admin_cookie = True` so the `slide_admin_cookie`
middleware in `app/main.py` can re-issue the browser cookie. A dependency has
no handle on the response, which is why the work is split in two.

What the admin login actually checks (`app/routers/admin.py` plus
`app/auth/security.py`):

- password with **bcrypt** (`hash_password` / `verify_password`,
  `BCRYPT_ROUNDS` default 12), with a legacy salted SHA-256 verify path that
  upgrades the row on the next successful login;
- a **security answer**, hashed the same way through `hash_security_answer` and
  `verify_security_answer` with a domain separator;
- `timing_equalize(password, sec_answer)` on the unknown-username path, so the
  cost of a miss matches the cost of a hit and nobody can time which admin
  usernames exist. It spends **two** bcrypts, because the login checks two
  secrets. Keep them in step if you ever add a third factor;
- brute force counters in the `login_attempts` table (`login_block_active`,
  `record_failed_login`, `clear_login_attempts`), `MAX_LOGIN_ATTEMPTS` 5 then
  `BLOCK_TIME_MINUTES` 5. The table, not a dict: uvicorn runs several workers
  and a restart must not hand an attacker a clean counter.

### CSRF

Admin mutations are covered by the `csrf_protection` middleware in
`app/main.py`, not by a per-endpoint dependency. It is opt-out, so a new admin
mutation is protected the moment it exists.

- Token is `HMAC-SHA256(session_token, app secret)`, so it is bound to one
  session and needs no storage (`app/auth/csrf.py`).
- Transport is the `X-CSRF-Token` header, attached automatically by
  `fetchAuth()` in `static/admin/js/utils.js`. A `csrf_token` form field also
  works.
- Gated prefixes are `PROTECTED_PREFIXES` in `app/auth/csrf.py`:
  `("/admin/", "/secure-panel-admin", "/api/synonyms")`.
- Only `POST /admin/login` is exempt, because there is no session yet to bind
  to.
- `tests/test_csrf.py` walks every registered route and fails the build if a
  `verify_admin`-protected mutation sits outside those prefixes. If you mount
  an admin router on a new prefix, add the prefix there in the same change.

## Visitor chat door

`POST /chat` in `app/routers/chat.py` runs all three checks, in this order:

```python
validate_request_origin(http_request)          # Origin/Referer in ALLOWED_ORIGINS
nonce = validate_chat_token(http_request)      # HMAC token from X-Chat-Token
ip = client_ip(http_request) or "unknown"
security.check_rate_limits(http_request, [
    (f"chat:{nonce or 'ip:' + ip}", security.CHAT_RATE_LIMIT),
    (f"chatip:{ip}", security.CHAT_IP_RATE_LIMIT),
])
```

Why each one:

- **`validate_request_origin`** rejects a missing or short user-agent, a
  missing Origin and Referer, and any hostname outside `ALLOWED_ORIGINS`.
- **`validate_chat_token`** checks the HMAC over `{ts}.{nonce}` and the
  `CHAT_TOKEN_TTL` (3600s), then returns the nonce. The nonce is a per-token
  visitor identity that lives *inside* the signed payload, so the client cannot
  choose it. v1 tokens (`{ts}.{sig}`, no nonce) still verify and return `""`,
  so a deploy does not invalidate tokens visitors already hold.
- **Two-tier rate limit.** The tight bucket is keyed on the nonce, so one abuser
  behind the booth's shared NAT burns only their own budget. The loose per-IP
  bucket (`CHAT_IP_RATE_LIMIT`, 5x by default) bounds the trick of refreshing
  the page to mint a fresh identity. `check_rate_limits` checks every bucket in
  one connection and admits all or none, so a blocked identity cannot drain the
  shared backstop.

Limits are read off the `security` module at call time
(`security.CHAT_RATE_LIMIT`, not a value imported once), because the enforcing
module is the one place tests and operators tune.

The limiter's real store is the `rate_limit_hits` table. The in-memory dict in
`app/auth/security.py` is only the fail-open path for when the database is
unreachable, for the same reason the lockout moved to a table: N workers meant
N independent copies of the limit.

### Who the visitor is

`client_ip(request)` in `app/auth/security.py` is the single place this app
decides which address a request came from. It reads `CF-Connecting-IP` only
when `TRUST_CLOUDFLARE` is set, and counts `X-Forwarded-For` from the **right**
using `TRUSTED_PROXY_HOPS`. Reading that header left to right reads the entry
the client controls, which turns a rate limit into no limit. With nothing
configured the headers are ignored and the socket address wins.

Identity comes from `app/auth/visitor.py` through the `resolve_visitor`
middleware in `app/main.py`, which reads `request.cookies` and nothing else.
Downstream code reads `request.state.visitor_id` and `request.state.visitor`.

**Never read identity from a request body, header, query string or path
segment.** `ChatRequest.visitor` used to carry a self-asserted profile in the
POST body, which meant anyone could be anyone. There is deliberately no second
resolver: one that also accepted a header would be the whole hole, back.

Use `require_visitor(request)` when a route needs a registered visitor. It
raises on state the middleware already resolved, so it never touches storage.

Storage faults in `app/auth/visitor.py` degrade to anonymous, they do not
raise. "No session" is the least privileged answer, so that is still failing
closed on the security decision while keeping `GET /` alive during a database
blip.

## Leads module: three doors, no shared credential

`app/routers/leads.py` is the best worked example in the repo of separating
audiences. Read its module docstring before changing it.

| Door | Credential | Rule |
| --- | --- | --- |
| `/v/{code}` | The visitor's own code | Redirects to `/v` so the code leaves the URL, then keeps it in an HttpOnly cookie |
| `/edit/{token}` | A one-time invite token | GET never burns it, the POST to `/api/leads/edit/{token}/begin` does |
| `/secure-panel-admin/leads` | The existing admin session | `Depends(verify_admin)` like every other admin route |

Three details worth copying:

- **The cookie carries the code, not the row id.** `current_visitor()` re-reads
  `lead_visitors` by code on every request, so revoking a visitor or rotating
  their link takes effect on the next tap. The old cookie held `id`, which
  never changes, so a lost phone survived a rotation for the rest of its
  session. A row id the client hands back is not a credential.
- **GET does not spend a one-time link.** Messengers prefetch URLs server-side
  before a human taps, so a link that died on GET was a link the contact never
  had. The landing page is a gate with one button, and the button POSTs.
- **The invite token names the company.** The row the token hashes to names the
  single company it may touch, so no company id is ever read from a request
  body. That is rule 2 and rule 3 above, implemented.

`/edit/{token}` is the one route an unauthenticated stranger can hammer, so it
calls `check_rate_limit(request)` and audits with `client_ip(request)`. A
guessing run has to be visible in the log with an address beside it.

## OTP door

`app/services/otp.py` owns the whole lifecycle. Its security contract:

- codes come from `secrets`, never from `random`;
- the raw code is never stored and never logged. The database keeps
  `HMAC-SHA256(challenge_id:code)` under the app secret. An unkeyed hash of a
  six digit code is brute-forceable in milliseconds;
- comparison is `hmac.compare_digest`;
- expiry (`OTP_TTL_SECONDS`, 120), attempt cap (`OTP_MAX_ATTEMPTS`, 5), resend
  cooldown and cap (`OTP_RESEND_COOLDOWN`, `OTP_MAX_RESENDS`) and a
  per-destination hourly limit (`OTP_DEST_HOURLY_LIMIT`) are all enforced
  server-side. The countdown in the UI is presentation, not a control;
- a challenge is bound to an unguessable `challenge_id`. Sending a phone number
  alone proves nothing;
- success consumes the challenge. Resend replaces the HMAC and the expiry
  atomically, so the old code dies;
- the code is never in any API response. `OTP_DELIVERY=dev` is refused when
  `COOKIE_SECURE=true`, this project's production marker.

## Secrets at rest

`app/services/secure_store.py`. `protect()` writes a Fernet token with an
`enc:` prefix, `reveal()` reads it and passes anything without the prefix
straight through, so values written before encryption existed keep working.

The key is derived with HKDF-SHA256 from the secret the install already has
(`SECRET_KEY`, else the generated `app_secret_key` row, via
`app.auth.security.get_app_secret`). Do not add a second secret for an operator
to manage, and do not write the derived key into `.env`: a key sitting next to
its own ciphertext protects nothing.

`reveal()` fails closed and returns `""` rather than sending garbage to a
gateway.

## Checklist for a new endpoint

1. Which door? Pick one from the table at the top. A new door needs an
   architecture decision recorded in `docs/engineering/DECISIONS.md`
   (`SECURITY.md`: no new authentication mechanism without one).
2. Add the dependency or the explicit check. Never assume a caller checked.
3. Take the resource id from the request, take the identity from the
   credential, and verify they belong together before you write.
4. Returning a list? Add `limit` and `offset`, clamp `limit`, and order
   deterministically.
5. Mutating and under `/admin/`, `/secure-panel-admin` or `/api/synonyms`?
   CSRF already covers it. Mounting somewhere else? Update
   `PROTECTED_PREFIXES` in `app/auth/csrf.py`.
6. Public and unauthenticated? Add `check_rate_limit(request)` and log the
   client IP.
7. Kiosk answer: what stops the next person at this browser from inheriting
   this state?
8. Tests. `docs/engineering/SECURITY.md` requires both the allowed and the
   denied path. See `tests/test_security_hardening.py`,
   `tests/test_admin_lockout.py`, `tests/test_csrf.py`,
   `tests/test_visitor_session.py`, `tests/test_leads_edit_session.py`.
   Integration tests use FastAPI's `TestClient`, browser tests use Playwright's
   **async** API only.

## Errors

`API_STANDARDS.md`: errors are actionable for clients, safe for users, and leak
no secrets, stack traces or internal resource detail.

The convention already in the code:

- `401` no credential or an expired one (`verify_admin`);
- `403` a credential that does not open this door (`validate_chat_token`,
  `validate_request_origin`, the leads visitor gate);
- `410` a one-time link that has been spent (`/edit/{token}`);
- `429` rate limited, with a short human sentence;
- `413` body over `MAX_BODY_BYTES`.

Visitor-facing text is Persian and written for someone with no technical
background. That is the product rule in `CLAUDE.md`, and it applies to refusals
too.

## Important files

| Path | Purpose |
| --- | --- |
| `app/auth/security.py` | Passwords, chat tokens, origin check, rate limits, lockout, `verify_admin` |
| `app/auth/csrf.py` | CSRF token, `PROTECTED_PREFIXES` |
| `app/auth/visitor.py` | Visitor session mint, resolve, revoke, the two clocks |
| `app/main.py` | `csrf_protection`, `resolve_visitor`, `slide_admin_cookie`, body size middleware |
| `app/services/otp.py` | OTP lifecycle and the `otp_challenges` table |
| `app/services/leads.py` | Invite burn, edit sessions, the review queue |
| `app/services/secure_store.py` | Fernet `enc:` secrets and the `.env` writer |
| `app/config.py` | `CHAT_RATE_LIMIT`, `CHAT_TOKEN_TTL`, `SESSION_TIMEOUT_HOURS`, `VISITOR_SESSION_*`, `TRUST_CLOUDFLARE`, `TRUSTED_PROXY_HOPS` |
| `docs/engineering/ENGINEERING_CONSTITUTION.md` | The binding principles |
| `docs/engineering/SECURITY.md` | Authentication target and current-state exceptions |
| `docs/engineering/SECURITY_MODEL.md` | Attack surface table (Persian) |
| `docs/engineering/API_STANDARDS.md` | Resource binding, errors, required tests |
