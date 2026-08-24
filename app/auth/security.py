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
    ALLOWED_ORIGINS, SECRET_KEY,
    CHAT_TOKEN_TTL, CHAT_RATE_LIMIT, CHAT_RATE_WINDOW,
    ADMIN_COOKIE_NAME, SESSION_TIMEOUT_HOURS,
)


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
_chat_rate_limits: Dict[str, List[float]] = {}


def check_rate_limit(http_request: Request, key: str = "",
                     limit: int = CHAT_RATE_LIMIT):
    """Rate limit a request. Keyed on the client IP unless a caller passes its
    own key, which lets a route limit per authenticated identity instead of per
    address.

    `limit` is this bucket's ceiling. A shared one meant a route could not be
    given a limit that fits it without moving /chat's, so the lead routes were
    stuck with the chat's number: generous for a stranger with a link, mean for
    a visitor typing at a booth. The window stays shared, because a per-route
    window is a second number to reason about for no gain."""
    # "unknown" rather than "": an empty key would put every clientless request
    # (there is no socket in some ASGI test transports) into a bucket that also
    # collects anything else that fails to resolve, silently and unlabelled.
    ip = key or client_ip(http_request) or "unknown"
    now = time.time()
    # Purge stale IPs to prevent unbounded memory growth
    stale = [k for k, v in _chat_rate_limits.items() if not v or now - v[-1] > CHAT_RATE_WINDOW]
    for k in stale:
        del _chat_rate_limits[k]
    timestamps = _chat_rate_limits.get(ip, [])
    timestamps = [t for t in timestamps if now - t < CHAT_RATE_WINDOW]
    if len(timestamps) >= limit:
        raise HTTPException(
            status_code=429,
            detail="Too many requests. Please wait a moment."
        )
    timestamps.append(now)
    _chat_rate_limits[ip] = timestamps


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


def validate_chat_token(http_request: Request):
    token = http_request.headers.get("X-Chat-Token")
    if not token:
        raise HTTPException(status_code=403, detail="Invalid or missing chat token.")
    try:
        parts = token.split(".")
        if len(parts) != 2:
            raise ValueError()
        payload, signature = parts
        expected_sig = hashlib.sha256(f"{payload}.{_get_hmac_key()}".encode()).hexdigest()[:32]
        if not secrets.compare_digest(signature, expected_sig):
            raise HTTPException(status_code=403, detail="Invalid or missing chat token.")
        ts = float(payload)
        if time.time() - ts > CHAT_TOKEN_TTL:
            raise HTTPException(status_code=403, detail="Chat token expired. Please refresh the page.")
    except (ValueError, IndexError):
        raise HTTPException(status_code=403, detail="Invalid or missing chat token.")


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
    ts = str(int(time.time()))
    sig = hashlib.sha256(f"{ts}.{_get_hmac_key()}".encode()).hexdigest()[:32]
    return f"{ts}.{sig}"


# --- Admin Auth ---

# Brute Force Tracker (IP -> {attempts, block_until})
login_attempts: Dict[str, dict] = {}


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
    return row['username']
