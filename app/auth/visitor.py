"""The registered visitor's session: mint it, resolve it, revoke it.

THE RULE THIS FILE EXISTS TO ENFORCE
------------------------------------
A visitor is whoever the SERVER says they are. Identity is a random token this
module mints, stored in `visitor_sessions` and carried in an HttpOnly cookie.
It is never read from a request header, a request body field, a query
parameter or a path segment. Before this, `ChatRequest.visitor` carried a
profile in the POST /chat body and `challenge_id` carried identity in the body
of /api/auth/profile — both self-asserted, so anyone who could type a request
could be anyone. Passing four extra fields is not authentication.

`resolve()` is called from ONE place, the `resolve_visitor` middleware in
app/main.py, which reads `request.cookies` and nothing else. Everything
downstream reads `request.state.visitor_id`. There is deliberately no second
door: a resolver that also accepted a header would be the whole hole, back.

WHY A TABLE AND NOT A SIGNED TOKEN
----------------------------------
See the header of migrations/0012_visitor_sessions.sql. Short version: a
session has to be revocable the second someone asks, and a signature cannot be
un-signed.

FAILURES DEGRADE TO ANONYMOUS, THEY DO NOT RAISE
------------------------------------------------
Every function here swallows storage faults and returns the safe value —
`resolve()` returns None, `mint()` returns "". This follows the rule
app/services/conversations.py states for the visitor hot path: a database blip
must not become a site-wide 500 on `GET /`. Failing closed is still the
security outcome, because "no session" means "anonymous", which is the least
privileged answer.

`require_visitor` is the ONE exception. Its whole job is to raise, and it
raises on state the middleware already resolved, so it never touches storage.

BOTH BACKENDS
-------------
`?` placeholders, and timestamps written through `_stamp()` so the text SQLite
stores sorts correctly against `datetime('now')` while PostgreSQL still reads
an unambiguous UTC instant. See `_stamp`.
"""
import datetime
import secrets

from fastapi import HTTPException, Request

from app.config import (logger, COOKIE_SECURE, VISITOR_COOKIE_NAME,
                        VISITOR_SESSION_DAYS, VISITOR_SESSION_MAX_HOURS)

# Marker the frontend branches on when /chat refuses an unregistered visitor.
# A code, not a sentence: the signup modal must not be wired to Persian prose
# that a copy edit can silently break. The human-readable half rides in the
# same object so nothing needs a second translation table.
REGISTRATION_REQUIRED = "registration_required"


def _stamp(moment: datetime.datetime) -> str:
    """A UTC timestamp both backends read the same way.

    Space separated with the offset kept: '2026-08-29 10:00:00+00:00'.

    PostgreSQL needs the offset — a naive string is read in the server's own
    timezone, which is the bug app/auth/security.py documents after it expired
    every admin's second request on a non-UTC host.

    SQLite needs the SPACE, not isoformat()'s 'T'. It stores this column as
    text and compares it as text, and `datetime('now')` produces
    'YYYY-MM-DD HH:MM:SS'. 'T' > ' ', so an isoformat() string would sort after
    every same-day CURRENT_TIMESTAMP value and `purge_expired()` would quietly
    skip the rows it exists to delete.
    """
    return moment.astimezone(datetime.timezone.utc).isoformat(
        sep=" ", timespec="seconds")


def _now() -> datetime.datetime:
    """Aware and UTC. Never `utcnow()` — see the comment in verify_admin."""
    return datetime.datetime.now(datetime.timezone.utc)


# ── Lifecycle ────────────────────────────────────────────────────────────

def mint(visitor_id: str) -> str:
    """Open a session for `visitor_id`. Returns the token, or '' on failure.

    Called once, from the OTP verify endpoint, right after the code checks out
    and the visitor row exists. 32 bytes of `secrets.token_urlsafe`, which is
    the same generator admin login uses; the token is the entire credential so
    it is never derived from anything the client sent.

    Returning '' rather than raising keeps a storage fault from turning a
    successful registration into an error the visitor sees. They are signed up;
    they are just not signed in, and the next verify fixes it.
    """
    visitor_id = (visitor_id or "").strip()
    if not visitor_id:
        return ""

    from app.db.connection import get_db_connection
    token = secrets.token_urlsafe(32)
    expiry = _now() + datetime.timedelta(days=VISITOR_SESSION_DAYS)
    try:
        conn = get_db_connection()
        try:
            conn.execute(
                "INSERT INTO visitor_sessions (token, visitor_id, expiry)"
                " VALUES (?, ?, ?)",
                (token, visitor_id, _stamp(expiry)))
            conn.commit()
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001 — a signup must not fail on this
        logger.error("[visitor] mint failed: %s: %s", type(e).__name__, e)
        return ""
    return token


def resolve(token: str):
    """The session behind `token`, or None. Slides the expiry on the way.

    Returns a dict with `visitor_id`, the five profile fields, and `profile`,
    a VisitorProfile the chat pipeline can use exactly where it used to use the
    body-supplied one.

    None means anonymous, and it means that for every reason: no token, an
    unknown token, an expired one, a malformed one, or a database that is
    down. A caller never has to tell those apart, and must not: they all grant
    the same (zero) privilege.

    An expired row is DELETED on read, the same lazy cleanup admin_sessions
    uses. `purge_expired()` exists for the rows nobody ever comes back for.

    TWO CLOCKS, AND A SESSION HAS TO PASS BOTH.

    `expiry` slides on every hit, so VISITOR_SESSION_DAYS is inactivity. That
    number alone is unreachable on a booth kiosk: one browser is shared by
    strangers and it is the NEXT person's traffic that renews the session, so
    the row never gets the chance to go idle and the second visitor keeps
    being answered as the first one.

    `created_at` never moves, so VISITOR_SESSION_MAX_HOURS is the bound a
    kiosk can actually reach. Past it the session dies no matter how busy the
    machine was.

    One UPDATE by primary key, and only for a request that actually carried a
    session. An anonymous request does no database work at all.
    """
    token = (token or "").strip()
    if not token:
        return None

    from app.db.connection import get_db_connection
    from app.db.timeutil import as_datetime, compare_now, to_naive_utc

    try:
        # ONE query, joined: the profile is wanted on every hit, and a second
        # round trip per request is a second pooled connection per request.
        # LEFT JOIN because a visitor row deleted out from under a live
        # session must read as anonymous, not raise.
        conn = get_db_connection()
        row = conn.execute(
            "SELECT s.visitor_id AS visitor_id, s.expiry AS expiry,"
            " s.created_at AS created_at,"
            " v.first_name AS first_name, v.last_name AS last_name,"
            " v.job AS job, v.position AS position, v.interests AS interests"
            " FROM visitor_sessions s"
            " LEFT JOIN visitors v ON v.id = s.visitor_id"
            " WHERE s.token = ?", (token,)).fetchone()
        conn.close()

        if not row or not row["visitor_id"]:
            return None

        # PostgreSQL returns an aware datetime here; SQLite returns TEXT.
        # compare_now() hands back a "now" with matching awareness, because
        # Python refuses to compare the two shapes. Never `utcnow() > expiry`.
        expiry = as_datetime(row["expiry"])
        if expiry is None or compare_now(expiry) > expiry:
            revoke(token)
            return None

        # The hard cap. `created_at` is the one timestamp on this row that
        # nothing ever moves, so this is the age of the SESSION and not the
        # gap since the last request.
        #
        # to_naive_utc on BOTH sides, not compare_now(). PostgreSQL returns an
        # aware datetime for this TIMESTAMPTZ column; SQLite's DEFAULT
        # CURRENT_TIMESTAMP returns a naive string that is already UTC.
        # compare_now() would answer that naive value with LOCAL now, so on a
        # host that is not on UTC the cap would fire hours early or hours
        # late. That is the same timezone bug _stamp() above exists to avoid.
        #
        # A created_at we cannot read revokes too. It should be impossible
        # (NOT NULL DEFAULT on both backends), and "we do not know how old
        # this session is" must not mean "keep it forever".
        created = to_naive_utc(row["created_at"])
        age_cap = datetime.timedelta(hours=VISITOR_SESSION_MAX_HOURS)
        if created is None or to_naive_utc(_now()) - created > age_cap:
            revoke(token)
            return None

        new_expiry = _now() + datetime.timedelta(days=VISITOR_SESSION_DAYS)
        stamp = _stamp(_now())
        conn = get_db_connection()
        try:
            conn.execute(
                "UPDATE visitor_sessions SET expiry = ?, last_seen = ?"
                " WHERE token = ?",
                (_stamp(new_expiry), stamp, token))
            conn.commit()
        finally:
            conn.close()

        data = {
            "visitor_id": row["visitor_id"],
            "first_name": row["first_name"] or "",
            "last_name": row["last_name"] or "",
            "job": row["job"] or "",
            "position": row["position"] or "",
            "interests": row["interests"] or "",
        }
        data["profile"] = _profile(data)
        return data
    except Exception as e:  # noqa: BLE001 — a blip must not 500 every page
        logger.error("[visitor] resolve failed: %s: %s", type(e).__name__, e)
        return None


def _profile(data: dict):
    """The three work fields as a VisitorProfile, or None.

    The same shape POST /chat used to accept in its body, so the pipeline that
    reads it does not care where it came from — only that it is now the
    server's answer and not the caller's claim. Imported here rather than at
    module scope to keep app.models out of the auth import chain.
    """
    try:
        from app.models import VisitorProfile
        return VisitorProfile(job=data["job"], position=data["position"],
                              interests=data["interests"])
    except Exception as e:  # noqa: BLE001
        logger.error("[visitor] profile build failed: %s", type(e).__name__)
        return None


def revoke(token: str) -> None:
    """End one session. Idempotent, and silent about a token that never was.

    DELETE and not a flag: see migrations/0012_visitor_sessions.sql. Sign-out
    has to be true the same second it is asked for, so there is nothing left to
    resolve afterwards.
    """
    token = (token or "").strip()
    if not token:
        return
    from app.db.connection import get_db_connection
    try:
        conn = get_db_connection()
        try:
            conn.execute("DELETE FROM visitor_sessions WHERE token = ?",
                         (token,))
            conn.commit()
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001
        logger.error("[visitor] revoke failed: %s", type(e).__name__)


def revoke_all(visitor_id: str) -> int:
    """Sign one person out of every browser. Returns how many died.

    This is the reason the sessions are rows. A lost phone in an exhibition
    hall is the case: the visitor tells the booth, an operator kills every
    session, and the token in that phone stops working immediately.
    """
    visitor_id = (visitor_id or "").strip()
    if not visitor_id:
        return 0
    from app.db.connection import get_db_connection
    try:
        conn = get_db_connection()
        try:
            removed = conn.execute(
                "DELETE FROM visitor_sessions WHERE visitor_id = ?",
                (visitor_id,)).rowcount or 0
            conn.commit()
        finally:
            conn.close()
        return max(0, removed)
    except Exception as e:  # noqa: BLE001
        logger.error("[visitor] revoke_all failed: %s", type(e).__name__)
        return 0


def purge_expired() -> int:
    """Delete sessions nobody came back for. Returns how many.

    `resolve()` already deletes an expired row the moment its owner returns,
    which is the same lazy rule admin_sessions lives by. This is for the rest:
    one row per registered visitor per browser, kept 30 days, on an install
    that runs for years. Safe to call from the retention loop.

    `datetime('now')` is INLINE, not a bound parameter: app/db/pg.py rewrites
    it into the PostgreSQL `now()` only when it can see the literal. Same
    idiom as app/services/conversations.py.
    """
    from app.db.connection import get_db_connection
    try:
        conn = get_db_connection()
        try:
            removed = conn.execute(
                "DELETE FROM visitor_sessions"
                " WHERE expiry < datetime('now')").rowcount or 0
            conn.commit()
        finally:
            conn.close()
        return max(0, removed)
    except Exception as e:  # noqa: BLE001
        logger.error("[visitor] purge failed: %s", type(e).__name__)
        return 0


# ── The cookie ───────────────────────────────────────────────────────────

def set_cookie(response, token: str) -> None:
    """Attach the session cookie. ONE place owns the attribute set.

    HttpOnly so no script can read the credential — which is the whole reason
    this replaced a challenge id kept in localStorage, where any XSS could
    lift it. `secure` follows COOKIE_SECURE, the project's production marker.

    SameSite=Lax and not Strict: a visitor who taps the link in their SMS
    arrives by top-level navigation and must still be signed in. Lax allows
    that and still blocks the cross-site POST, and every endpoint that acts on
    this cookie also runs validate_request_origin, which is what makes Lax
    enough (see app/auth/csrf.py for the same reasoning on the admin side).

    `path` is left to Starlette's default of "/", matching every other cookie
    in this codebase. clear_cookie() must keep the same attributes or the
    browser will not match the cookie it is being asked to delete.
    """
    if not token:
        return
    response.set_cookie(
        key=VISITOR_COOKIE_NAME, value=token,
        httponly=True, secure=COOKIE_SECURE, samesite="lax",
        max_age=VISITOR_SESSION_DAYS * 24 * 3600,
    )


def clear_cookie(response) -> None:
    """Remove the session cookie. Same attributes as set_cookie, on purpose.

    A delete_cookie() with different attributes does not match, so the browser
    keeps the old one and the visitor stays signed in on their own screen.
    (app/routers/admin.py has the attribute-less version; do not copy it.)
    """
    response.delete_cookie(
        key=VISITOR_COOKIE_NAME,
        httponly=True, secure=COOKIE_SECURE, samesite="lax",
    )


# ── Enforcement ──────────────────────────────────────────────────────────

def require_visitor(request: Request) -> str:
    """FastAPI dependency: the visitor id, or 401. Returns a non-empty string.

    Reads only what the middleware already resolved, so it does no I/O and
    cannot be talked into looking somewhere else. This is the only function in
    the file that raises, because raising is its entire job.

    The detail is an OBJECT and not a sentence. The frontend has to tell "you
    are not signed in, open the signup modal" apart from every other 401, and
    matching on Persian prose is not a contract. `code` is what it branches on;
    `message` is what a human reads if nothing catches it.
    """
    visitor_id = getattr(request.state, "visitor_id", "") or ""
    if not visitor_id:
        raise HTTPException(status_code=401, detail={
            "code": REGISTRATION_REQUIRED,
            "message": "برای ادامه لطفاً ثبت‌نام کنید.",
        })
    return visitor_id
