import time
import hashlib
import secrets
import datetime
from typing import Dict, List

import os

import bcrypt
from fastapi import HTTPException, Request, Depends

from app import config
from app.config import (
    ALLOWED_ORIGINS, SECRET_KEY, logger,
    CHAT_TOKEN_TTL, CHAT_RATE_LIMIT, CHAT_RATE_WINDOW,
    CHAT_IP_RATE_LIMIT, OTP_RATE_LIMIT, OTP_IP_RATE_LIMIT, PAGE_RATE_LIMIT,
    ADMIN_COOKIE_NAME, SESSION_TIMEOUT_HOURS,
    MAX_LOGIN_ATTEMPTS, BLOCK_TIME_MINUTES,
)
# The limits above are import-bound on purpose, exactly like CHAT_RATE_LIMIT:
# this module ENFORCES them, so tests and operators tune one place
# (security.CHAT_IP_RATE_LIMIT etc.) and every router that reads them through
# `security.` at call time follows — re-reading app.config could disagree.


# --- Password Hashing (bcrypt) ---

# bcrypt's cost factor. 12 is the production default and deliberately slow —
# that slowness is the point for a password hash. It is configurable ONLY so
# the test suite can drop it: one hash costs ~580 ms here, init_db() performs
# one, and ~130 fixtures call init_db(), which is most of the suite's runtime.
# Never lower this in production; it directly weakens password security.
BCRYPT_ROUNDS = int(os.getenv("BCRYPT_ROUNDS", "12"))


def hash_password(password: str) -> str:
    """Hash a password with bcrypt (salt is embedded in the result)."""
    return bcrypt.hashpw(password.encode("utf-8"),
                         bcrypt.gensalt(rounds=BCRYPT_ROUNDS)).decode("utf-8")


def verify_password(password: str, stored_hash: str, salt: str = "") -> bool:
    """Verify a password against either a bcrypt hash or a legacy salted SHA-256
    hash. Returns True on match. Lets old accounts keep working until they are
    upgraded on next login (see admin_login)."""
    if not stored_hash:
        return False
    if stored_hash.startswith("$2"):  # bcrypt
        try:
            return bcrypt.checkpw(password.encode("utf-8"), stored_hash.encode("utf-8"))
        except ValueError:
            return False
    # Legacy SHA-256 (+ optional salt)
    legacy = hashlib.sha256((salt + password).encode()).hexdigest() if salt \
        else hashlib.sha256(password.encode()).hexdigest()
    return secrets.compare_digest(legacy, stored_hash)


def is_legacy_hash(stored_hash: str) -> bool:
    """True if the stored hash is the old SHA-256 scheme (needs upgrading)."""
    return bool(stored_hash) and not stored_hash.startswith("$2")


# --- Security-Answer Hashing ---
# The security answer is a mandatory login factor, so it gets the same
# treatment as the password: bcrypt at rest, with a legacy unsalted-SHA-256
# verify path so existing installs keep working and are upgraded on the next
# successful login. A domain separator keeps an answer from ever being useful
# as (or colliding with) a password hash.

_ANSWER_DOMAIN = "padyar-security-answer:"


def hash_security_answer(answer: str) -> str:
    return hash_password(_ANSWER_DOMAIN + answer)


def verify_security_answer(answer: str, stored_hash: str) -> bool:
    if not stored_hash:
        return False
    if stored_hash.startswith("$2"):  # bcrypt
        try:
            return bcrypt.checkpw((_ANSWER_DOMAIN + answer).encode("utf-8"),
                                  stored_hash.encode("utf-8"))
        except ValueError:
            return False
    legacy = hashlib.sha256(answer.encode()).hexdigest()
    return secrets.compare_digest(legacy, stored_hash)


def is_legacy_answer_hash(stored_hash: str) -> bool:
    return bool(stored_hash) and not stored_hash.startswith("$2")


# A login for a username that does not exist returns in microseconds; one for
# a real username spends ~300-500 ms in bcrypt. Equalizing the two is the
# standard fix for timing-based username enumeration: run a dummy verify on
# the no-user path so both branches cost the same. Lazily built so the test
# suite's BCRYPT_ROUNDS override also applies here.
_dummy_bcrypt_hash = ""


def timing_equalize(password: str) -> None:
    """Spend the same bcrypt cost an unknown username would have spent."""
    global _dummy_bcrypt_hash
    if not _dummy_bcrypt_hash:
        _dummy_bcrypt_hash = hash_password(secrets.token_hex(16))
    try:
        bcrypt.checkpw(password.encode("utf-8"), _dummy_bcrypt_hash.encode("utf-8"))
    except ValueError:
        pass


# --- Client IP ---

def client_ip(request: Request) -> str:
    """The one place this app decides who a request came from.

    Every rate limit bucket and every audit row's ip field goes through here,
    so there is a single answer to "which address do we believe".

    The rule is that a forwarding header counts only when a proxy we operate is
    known to write it. Reading X-Forwarded-For left to right, as this code used
    to, reads the one entry the CLIENT controls: a caller who varies the header
    per request gets an unlimited rate-limit bucket, which is no limit at all.
    Rightmost entries are appended by our own infrastructure, so we count from
    that end instead.

    With nothing configured the headers are ignored outright. An install that
    is not behind a proxy must never be talked out of the socket address.
    """
    direct = request.client.host if request.client else ""

    # Cloudflare rewrites this on every request that reaches the tunnel, so it
    # cannot be forged from outside. Only believed when the operator says the
    # install actually sits behind Cloudflare.
    if config.TRUST_CLOUDFLARE:
        cf = (request.headers.get("cf-connecting-ip") or "").strip()
        if cf:
            return cf

    hops = config.TRUSTED_PROXY_HOPS
    if hops > 0:
        chain = [p.strip() for p in
                 (request.headers.get("x-forwarded-for") or "").split(",") if p.strip()]
        # Too few entries means the header did not pass through the proxies we
        # expect. Prepended junk only pushes entries further left, so it never
        # moves the one we pick.
        if len(chain) >= hops:
            return chain[-hops]

    return direct


# --- Chat Rate Limiting State ---
# In-memory fallback ONLY. The primary limiter is the `rate_limit_hits`
# table, for exactly the reason the login lockout moved to the database (see
# the brute-force section below): uvicorn --workers N gives this dict N
# independent copies, so the real limit was N × CHAT_RATE_LIMIT and every
# restart handed out a fresh one. The dict survives as the fail-open path for
# when the database itself is unreachable — an unreachable store must degrade
# the limit, not take the chat endpoint down with it.
_chat_rate_limits: Dict[str, List[float]] = {}
_last_bucket_sweep = 0.0
_SWEEP_INTERVAL = 30.0


def _db_rate_limits(buckets, window: int | None = None) -> bool | None:
    """Shared SLIDING-window limiter over SEVERAL buckets in one connection.

    True/False = allowed/blocked, None = store down. `buckets` is a list of
    (key, limit) pairs sharing one window.

    One row per ADMITTED request (bucket, unix-ts). A request is allowed when
    fewer than its bucket's limit hits fall inside the last `window` seconds —
    a true sliding window, matching the in-memory limiter it replaced. A
    fixed-window counter was tried first (single atomic upsert) and rejected:
    it resets to zero at every window boundary, admitting a 2× burst around
    it, and that same reset is what made the boundary-crossing CI test flake.

    Two phases, deliberately:
      1. count every bucket READ-ONLY; if ANY bucket is over, the request is
         blocked and NOTHING is written — not even the rows of the buckets
         that were still under. Blocked attempts are deliberately NOT
         recorded: recording them would keep a tripped bucket full for as
         long as the caller keeps trying, which turns a shared-NAT booth (the
         exact case these limits are sized for) into a self-inflicted
         lockout. Multi-bucket checking must not break that rule sideways —
         a blocked identity must not drain the shared IP backstop either —
         and all-or-nothing removes any outcome where one tier spends a hit
         while the other refuses.
      2. only when every bucket allows: insert one row per bucket and prune
         each bucket's own expired rows.

    Growth is bounded: the allowed path prunes every bucket's expired rows,
    and a blocked caller leaves at most `limit` rows behind per bucket.
    """
    from app.db.connection import get_db_connection

    window_seconds = CHAT_RATE_WINDOW if window is None else window
    now = time.time()
    cutoff = now - window_seconds
    try:
        conn = get_db_connection()
        try:
            for bucket, limit in buckets:
                row = conn.execute(
                    "SELECT COUNT(*) AS n FROM rate_limit_hits WHERE key = ? AND ts > ?",
                    (bucket, cutoff),
                ).fetchone()
                if int(row["n"]) >= limit:
                    return False  # read-only path: a spamming key writes nothing
            for bucket, _limit in buckets:
                conn.execute("INSERT INTO rate_limit_hits (key, ts) VALUES (?, ?)",
                             (bucket, now))
                conn.execute("DELETE FROM rate_limit_hits WHERE key = ? AND ts <= ?",
                             (bucket, cutoff))
            conn.commit()
            return True
        finally:
            conn.close()
    except Exception as exc:                      # noqa: BLE001
        logger.error("Rate-limit store unreadable, degrading to in-memory: %s", exc)
        return None


def _db_rate_limit(bucket: str, limit: int | None = None,
                   window: int | None = None) -> bool | None:
    """Single-bucket form of _db_rate_limits (semantics documented there).

    limit/window default to the chat constants so every caller that predates
    per-bucket thresholds keeps today's behaviour exactly.
    """
    return _db_rate_limits(
        [(bucket, CHAT_RATE_LIMIT if limit is None else limit)], window)


def check_rate_limit(http_request: Request, key: str = "",
                     limit: int | None = None, window: int | None = None):
    """Rate limit a request against ONE bucket. Keyed on the client IP unless a
    caller passes its own key, which lets a route limit per authenticated
    identity instead of per address. limit/window default to the chat
    constants so existing callers keep today's thresholds."""
    # "unknown" rather than "": an empty key would put every clientless request
    # (there is no socket in some ASGI test transports) into a bucket that also
    # collects anything else that fails to resolve, silently and unlabelled.
    bucket = key or client_ip(http_request) or "unknown"

    allowed = _db_rate_limit(f"rl:{bucket}", limit, window)
    if allowed is not None:
        if not allowed:
            raise HTTPException(
                status_code=429,
                detail="Too many requests. Please wait a moment."
            )
        return

    # In-memory fallback (store unavailable): the original sliding window.
    now = time.time()
    # Purge stale buckets to prevent unbounded memory growth. Amortised: the
    # old per-request sweep walked EVERY known IP on EVERY chat call — and if
    # the store is down under load, this fallback IS the hot path. A bucket
    # is unreachable for the rest of its window once swept, and per-bucket
    # filtering below still drops aged timestamps exactly as before.
    global _last_bucket_sweep
    if now - _last_bucket_sweep >= _SWEEP_INTERVAL:
        _last_bucket_sweep = now
        stale = [k for k, v in _chat_rate_limits.items()
                 if not v or now - v[-1] > CHAT_RATE_WINDOW]
        for k in stale:
            del _chat_rate_limits[k]
    effective_limit = CHAT_RATE_LIMIT if limit is None else limit
    window_seconds = CHAT_RATE_WINDOW if window is None else window
    timestamps = _chat_rate_limits.get(bucket, [])
    timestamps = [t for t in timestamps if now - t < window_seconds]
    if len(timestamps) >= effective_limit:
        raise HTTPException(
            status_code=429,
            detail="Too many requests. Please wait a moment."
        )
    timestamps.append(now)
    _chat_rate_limits[bucket] = timestamps


def check_rate_limits(http_request: Request, buckets):
    """Rate limit one request against SEVERAL buckets in ONE all-or-nothing pass.

    `buckets` is a list of (key, limit) pairs sharing the chat window. The
    pattern this exists for is two-tier: a tight per-identity bucket (the
    signed chat token's nonce — one abuser behind a shared NAT exhausts only
    their own budget) plus a loose per-IP backstop (mint-flooding fresh
    identities still runs into it). Checking every bucket in one connection
    with an all-or-nothing write keeps the blocked-not-recorded rule true for
    BOTH tiers at once — a blocked identity must not drain the shared IP
    backstop either (see _db_rate_limits) — and never lets one tier spend a
    hit the other refuses. The `rl:` prefix is added here, once.
    """
    rl_buckets = [(f"rl:{key}", limit) for key, limit in buckets]

    allowed = _db_rate_limits(rl_buckets)
    if allowed is not None:
        if not allowed:
            raise HTTPException(
                status_code=429,
                detail="Too many requests. Please wait a moment."
            )
        return

    # In-memory fallback (store unavailable): the single-bucket fallback's
    # sliding window, applied per bucket with the same all-or-nothing shape —
    # check every bucket first, then append to all of them or none.
    now = time.time()
    global _last_bucket_sweep
    if now - _last_bucket_sweep >= _SWEEP_INTERVAL:
        _last_bucket_sweep = now
        stale = [k for k, v in _chat_rate_limits.items()
                 if not v or now - v[-1] > CHAT_RATE_WINDOW]
        for k in stale:
            del _chat_rate_limits[k]
    kept = []
    for bucket, limit in rl_buckets:
        timestamps = [t for t in _chat_rate_limits.get(bucket, [])
                      if now - t < CHAT_RATE_WINDOW]
        if len(timestamps) >= limit:
            raise HTTPException(
                status_code=429,
                detail="Too many requests. Please wait a moment."
            )
        kept.append((bucket, timestamps))
    for bucket, timestamps in kept:
        timestamps.append(now)
        _chat_rate_limits[bucket] = timestamps


# --- Chat Token ---
# Dedicated signing key. If SECRET_KEY is set in the env we use it; otherwise we
# get-or-create a stable random key in the settings table. INSERT OR IGNORE makes
# this race-safe across gunicorn workers — only the first write wins, then every
# worker reads the same value.
_hmac_key_cache = ""


def _get_hmac_key() -> str:
    global _hmac_key_cache
    if _hmac_key_cache:
        return _hmac_key_cache
    if SECRET_KEY:
        _hmac_key_cache = SECRET_KEY
        return _hmac_key_cache
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES ('app_secret_key', ?)",
            (secrets.token_hex(32),),
        )
        conn.commit()
        row = conn.execute("SELECT value FROM settings WHERE key = 'app_secret_key'").fetchone()
    finally:
        conn.close()
    _hmac_key_cache = row["value"]
    return _hmac_key_cache


def get_app_secret() -> str:
    """This install's long-lived secret: SECRET_KEY, else the generated one.

    Public name for the same key that signs chat tokens. Other modules derive
    their own key from it (see app/services/secure_store.py) instead of adding
    a second secret an operator would have to manage.
    """
    return _get_hmac_key()


def validate_chat_token(http_request: Request, grace_seconds: float = 0.0) -> str:
    """Check the signed chat token; return the visitor identity it carries.

    v2 tokens are "{ts}.{nonce}.{sig}": the nonce is a random per-token
    identity INSIDE the HMAC-signed payload, so /chat and /api/transcribe can
    rate-limit per visitor even when a whole exhibition hall shares one
    NAT'd address. The identity cannot be forged or chosen by the client — it
    is only believed after the signature over "{ts}.{nonce}" verifies.

    v1 tokens ("{ts}.{sig}", minted before nonces existed) stay valid for
    their remaining TTL and return "" — the caller then falls back to an
    IP-keyed tight bucket, exactly the pre-nonce behaviour, so a deploy never
    invalidates tokens visitors already hold.

    `grace_seconds` extends the TTL (the token-refresh flow lands on this
    same signature; 0.0 is today's strict expiry). Raises 403 exactly as
    before on anything missing, tampered, malformed or expired.
    """
    token = http_request.headers.get("X-Chat-Token")
    if not token:
        raise HTTPException(status_code=403, detail="Invalid or missing chat token.")
    try:
        parts = token.split(".")
        if len(parts) == 3:
            ts_raw, nonce, signature = parts
            if not nonce:
                raise ValueError()
        elif len(parts) == 2:
            ts_raw, signature = parts
            nonce = ""
        else:
            raise ValueError()
        # The signed payload is everything but the signature: v2 signs
        # "{ts}.{nonce}", v1 signs the bare "{ts}".
        payload = f"{ts_raw}.{nonce}" if nonce else ts_raw
        expected_sig = hashlib.sha256(f"{payload}.{_get_hmac_key()}".encode()).hexdigest()[:32]
        if not secrets.compare_digest(signature, expected_sig):
            raise HTTPException(status_code=403, detail="Invalid or missing chat token.")
        ts = float(ts_raw)
        if time.time() - ts > CHAT_TOKEN_TTL + grace_seconds:
            raise HTTPException(status_code=403, detail="Chat token expired. Please refresh the page.")
    except (ValueError, IndexError):
        raise HTTPException(status_code=403, detail="Invalid or missing chat token.")
    return nonce


def validate_request_origin(http_request: Request):
    ua = http_request.headers.get("user-agent", "")
    if not ua or len(ua) < 10:
        raise HTTPException(status_code=403, detail="Forbidden.")

    origin = http_request.headers.get("origin", "")
    referer = http_request.headers.get("referer", "")
    source = origin or referer
    if not source:
        raise HTTPException(status_code=403, detail="Forbidden.")

    hostname = source.split("://")[-1].split("/")[0].split(":")[0]
    if hostname not in ALLOWED_ORIGINS:
        raise HTTPException(status_code=403, detail="Forbidden.")


def generate_chat_token() -> str:
    """Mint a v2 chat token: "{ts}.{nonce}.{sig}".

    The nonce (16 hex chars, 64 bits) makes every mint unique — two renders
    in the same second used to produce IDENTICAL tokens, which also meant one
    shared rate-limit identity for anyone holding both. Unforgeability still
    comes from the signature, not the nonce's entropy: the nonce is the
    identity, the HMAC is the proof.
    """
    ts = str(int(time.time()))
    nonce = secrets.token_hex(8)
    sig = hashlib.sha256(f"{ts}.{nonce}.{_get_hmac_key()}".encode()).hexdigest()[:32]
    return f"{ts}.{nonce}.{sig}"


# --- Admin Brute-Force Lockout ---
#
# The state lives in the `login_attempts` table, not in a module-level dict.
# The dict was per process and per boot, and both halves of that were holes:
#
#   * every deploy or restart handed an attacker a clean counter, which is the
#     one thing a lockout must not do;
#   * production runs uvicorn with --workers N (deploy/systemd/*.service), so N
#     processes each kept their own count. An attacker got roughly
#     N * MAX_LOGIN_ATTEMPTS guesses before anything blocked, and once one
#     worker blocked them the next request could land on a worker that had
#     never heard of them.
#
# The table has existed since migrations/0001_initial.sql, which says plainly
# why it was created. Nothing ever read or wrote it until now.
#
# FAILING OPEN, DELIBERATELY
# --------------------------
# Every function here swallows storage errors and reports "not blocked". That
# is the weaker of the two choices against an attacker, and it is the right one
# here:
#
#   * the password check does NOT depend on this table. If the database is
#     fully down the `admins` lookup fails too and nobody authenticates at all,
#     so failing open costs nothing in that case;
#   * the case that actually differs is a partial failure (missing table after
#     a botched migration, a permission error on this table alone). Failing
#     closed there locks the real admin out of the panel exactly when they need
#     it to fix the outage, and the only way back in is direct database access;
#   * bcrypt at 12 rounds still costs an attacker ~0.5s per guess, so the
#     throttle degrades rather than disappearing.
#
# The trade is: a degraded store means slower guessing instead of no guessing.
# The failure is logged at error level so an operator sees the lockout is down.
#
# PRUNING
# -------
# Nothing prunes this table and nothing needs to. It holds at most one row per
# IP that has failed an admin login, a successful login deletes that IP's row,
# and an expired block deletes it too. One admin panel behind one hostname
# collects internet scanners at a rate of, at worst, thousands of rows a year,
# which is kilobytes. It would only matter against a botnet spraying millions
# of distinct addresses, and the answer then is a periodic DELETE by
# last_attempt, not per-request work on every login.


def login_block_active(ip: str) -> bool:
    """True while `ip` is inside a brute-force lockout.

    Clears the row when the block has run out, so the next failure starts a
    fresh count. That is what the in-memory version did on expiry.
    """
    from app.db.connection import get_db_connection
    from app.db.timeutil import as_datetime, compare_now

    try:
        conn = get_db_connection()
        try:
            row = conn.execute(
                "SELECT attempts, block_until FROM login_attempts WHERE ip = ?",
                (ip,)).fetchone()
            if not row or row["block_until"] is None:
                return False
            until = as_datetime(row["block_until"])
            if until and compare_now(until) < until:
                return True
            conn.execute("DELETE FROM login_attempts WHERE ip = ?", (ip,))
            conn.commit()
            return False
        finally:
            conn.close()
    except Exception as exc:                      # noqa: BLE001
        logger.error("Brute-force lockout unreadable, allowing the attempt: %s", exc)
        return False


def record_failed_login(ip: str) -> int:
    """Count one failed login for `ip`. Returns the new total, 0 if unstorable.

    The increment is a single statement. Reading the count and writing it back
    would let two workers that fail a login at the same moment both read 4 and
    both write 5, which hands an attacker free attempts. That is the exact bug
    that made the per-process dict worth replacing. `attempts + 1` is computed by
    the database inside the row's own write, so concurrent callers serialise on
    the row and every failure is counted exactly once.
    """
    from app.db.connection import get_db_connection

    now = datetime.datetime.now()
    block_until = (now + datetime.timedelta(minutes=BLOCK_TIME_MINUTES)).isoformat()
    # A brand-new row is attempt number 1, which reaches the limit only if the
    # limit is 1. Derived rather than assumed, so MAX_LOGIN_ATTEMPTS stays the
    # only source of the number.
    first_block = block_until if MAX_LOGIN_ATTEMPTS <= 1 else None
    try:
        conn = get_db_connection()
        try:
            row = conn.execute(
                """
                INSERT INTO login_attempts (ip, attempts, block_until, last_attempt)
                VALUES (?, 1, ?, ?)
                ON CONFLICT (ip) DO UPDATE SET
                    attempts     = login_attempts.attempts + 1,
                    last_attempt = excluded.last_attempt,
                    block_until  = CASE WHEN login_attempts.attempts + 1 >= ?
                                        THEN ? ELSE login_attempts.block_until END
                RETURNING attempts
                """,
                (ip, first_block, now.isoformat(),
                 MAX_LOGIN_ATTEMPTS, block_until),
            ).fetchone()
            conn.commit()
            return int(row["attempts"]) if row else 0
        finally:
            conn.close()
    except Exception as exc:                      # noqa: BLE001
        logger.error("Brute-force lockout not recorded for %s: %s", ip, exc)
        return 0


def clear_login_attempts(ip: str) -> None:
    """Forget `ip`'s failures. Called on a successful login."""
    from app.db.connection import get_db_connection

    try:
        conn = get_db_connection()
        try:
            conn.execute("DELETE FROM login_attempts WHERE ip = ?", (ip,))
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:                      # noqa: BLE001
        # Housekeeping must never turn a good login into a failed one.
        logger.error("Could not clear login attempts for %s: %s", ip, exc)


# --- Admin Auth ---


async def verify_admin(request: Request):
    from app.db.connection import get_db_connection

    token = request.cookies.get(ADMIN_COOKIE_NAME)
    if not token:
        raise HTTPException(status_code=401, detail="Unauthorized")

    conn = get_db_connection()
    row = conn.execute('SELECT expiry, username FROM admin_sessions WHERE token = ?', (token,)).fetchone()
    conn.close()

    if not row:
        raise HTTPException(status_code=401, detail="Unauthorized")

    # Old sessions without username — force re-login
    if not row['username']:
        conn = get_db_connection()
        conn.execute('DELETE FROM admin_sessions WHERE token = ?', (token,))
        conn.commit()
        conn.close()
        raise HTTPException(status_code=401, detail="Session expired")

    # PostgreSQL returns a real (aware) datetime here; SQLite returned TEXT.
    from app.db.timeutil import as_datetime, compare_now
    expiry = as_datetime(row['expiry'])
    if compare_now(expiry) > expiry:
        conn = get_db_connection()
        conn.execute('DELETE FROM admin_sessions WHERE token = ?', (token,))
        conn.commit()
        conn.close()
        raise HTTPException(status_code=401, detail="Session expired")

    # Slide expiry on activity
    new_expiry = datetime.datetime.now() + datetime.timedelta(hours=SESSION_TIMEOUT_HOURS)
    conn = get_db_connection()
    conn.execute('UPDATE admin_sessions SET expiry = ? WHERE token = ?', (new_expiry.isoformat(), token))
    conn.commit()
    conn.close()
    # The DB row just slid, but a dependency has no handle on the response, so
    # it cannot re-issue the cookie that carries this session — without help
    # the cookie still dies 1h after login no matter how active the admin is.
    # The slide_admin_cookie middleware (app/main.py) reads this flag after
    # call_next returns and re-issues the cookie, keeping the browser's copy
    # in lockstep with the row. Always-on rather than threshold-gated: admin
    # traffic is a handful of staff on no-store pages, and one Set-Cookie per
    # response avoids a cookie-issued-at column + migration for zero benefit.
    # Logout never runs verify_admin, so this flag is never set on a logout —
    # the middleware cannot resurrect the cookie logout just deleted.
    request.state.slide_admin_cookie = True
    return row['username']
