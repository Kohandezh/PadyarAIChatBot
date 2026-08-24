"""Who a company contact is, and which company they are allowed to speak for.

WHY THIS IS NOT IN leads.py
---------------------------
`leads.py` is one flow: a visitor walks up to a booth and a contact ends up
with a one-time link. Everything in it dies within a day, on purpose. What is
here outlives that: an account, the sessions it opens, and a grant of ownership
over a row of the knowledge base. The two meet at exactly one point, the moment
a code is verified at a booth, and `leads.py` calls this module there. Keeping
them apart is what stops "who may edit this company" from being answered by
reading a lead's status.

THREE RULES, AND EVERY LINE HERE SERVES ONE OF THEM
---------------------------------------------------
1. The identity is `users.id`, never the phone number. A number proves one
   thing at one moment: whoever held that handset read six digits out loud. A
   recycled SIM, or an employee who leaves and keeps their number, would
   otherwise walk away owning a company they have nothing to do with. The
   number is a factor that binds to an account; ownership is a separate grant.

2. Saving an edit needs BOTH a live session AND a live ownership. Neither is
   sufficient. A session says which person is asking; a grant says which
   company they may touch. Missing either is a 403, and a revoked or expired
   grant is indistinguishable from one that never existed.

3. `dataset_id` is never read from a request. It is derived from the ownership
   record, re-read on every single request, and never snapshotted onto a
   session or a cookie. The deleted `edit_sessions` table made exactly that
   mistake.

CAPTURE NEVER ESCALATES AN EXISTING ACCOUNT
-------------------------------------------
A verified capture at a booth creates an account if that number has none, and
that account is active immediately: creating an account grants nothing that
existed before. If the number ALREADY has an account, the grant lands
`pending` and only the holder, from a session they started themselves, makes it
`active`. Run the other way it is an attack: sign up with a number, have a
colleague capture that number at the target's booth, and own the target.

NOTHING HERE READS THEN WRITES
------------------------------
Two visitors registering the same contact at the same second must produce one
account, and neither may fail after the SMS has been sent and billed. So the
account is created with `INSERT ... ON CONFLICT (phone_hash) DO UPDATE
... RETURNING id`, and the grant with an `INSERT ... WHERE NOT EXISTS ...
ON CONFLICT DO NOTHING`. The condition lives inside the statement, where the
database can hold it.
"""
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta
from typing import Optional

from app.config import logger
from app.db.connection import get_db_connection
from app.db.timeutil import to_naive_utc

USER_COOKIE = "padyar_user"

# Two hours, fixed, and it does NOT slide. A visitor's session slides because
# they work a whole exhibition day on one phone; a contact signs in, fixes one
# paragraph and leaves. Coming back is one phone number and one code, which is
# cheaper than any of the ways a long-lived session goes wrong.
SESSION_TTL_SECONDS = 2 * 3600

# The end of the exhibition, near enough: the setup days plus the show. A grant
# is not renewed by time passing. Extending one is an admin granting it again,
# which is what SEC-012 means by "confirmed again".
GRANT_TTL_SECONDS = 30 * 24 * 3600

# Which key computed the `phone_hash` values this build writes. Rotating the
# HMAC key means rehashing every row, and a table that cannot say which key a
# value came from cannot be rehashed in halves. See DEPLOYMENT_RUNBOOK.md.
PHONE_HASH_KEY_VERSION = 1

# A grant that opens something, written once and used by every query that
# decides access. All three conditions, every time: the status a pending grant
# has not reached, the revocation that behaves exactly like absence, and the
# expiry. Aliased `o`, and the `?` is the current time, bound by every caller.
_LIVE_GRANT = ("o.revoked_at IS NULL AND o.status = 'active'"
               " AND (o.expires_at IS NULL OR o.expires_at > ?)")


def _now() -> datetime:
    return datetime.utcnow()


def phone_digest(phone: str) -> str:
    """Keyed HMAC of a phone number.

    Deliberately the same construction as `leads._digest` and the OTP module's
    code hash, so one number has one digest everywhere in this install. Keyed
    rather than bare: an unkeyed hash of an Iranian mobile number is reversed
    by enumerating the ~10^9 possibilities, which is minutes of work.
    """
    from app.auth.security import _get_hmac_key
    return hmac.new(_get_hmac_key().encode(), (phone or "").encode(),
                    hashlib.sha256).hexdigest()


# ── Schema ───────────────────────────────────────────────────────────────
# The module owns its tables, the way app/services/leads.py and the OTP module
# do, so an install without the leads module never grows them. This is the
# dialect the db adapter translates (`?` placeholders, TEXT timestamps); the
# PostgreSQL-native version with real types is migrations/0008_identity.sql.

_TABLES = (
    """
    CREATE TABLE IF NOT EXISTS users (
        id                     TEXT PRIMARY KEY,
        phone                  TEXT NOT NULL DEFAULT '',
        phone_hash             TEXT NOT NULL UNIQUE,
        phone_hash_key_version INTEGER NOT NULL DEFAULT 1,
        first_name             TEXT NOT NULL DEFAULT '',
        last_name              TEXT NOT NULL DEFAULT '',
        position               TEXT NOT NULL DEFAULT '',
        job                    TEXT NOT NULL DEFAULT '',
        interests              TEXT NOT NULL DEFAULT '',
        status                 TEXT NOT NULL DEFAULT 'active',
        source                 TEXT NOT NULL DEFAULT '',
        created_at             TEXT NOT NULL,
        last_login_at          TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS user_sessions (
        token      TEXT PRIMARY KEY,
        user_id    TEXT NOT NULL,
        expiry     TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS dataset_owners (
        id         TEXT PRIMARY KEY,
        dataset_id TEXT NOT NULL,
        user_id    TEXT NOT NULL,
        granted_by TEXT NOT NULL DEFAULT '',
        granted_at TEXT NOT NULL,
        expires_at TEXT,
        status     TEXT NOT NULL DEFAULT 'pending',
        revoked_at TEXT,
        UNIQUE (dataset_id, user_id)
    )
    """,
)

_INDEXES = (
    "CREATE INDEX IF NOT EXISTS ix_user_sessions_user   ON user_sessions(user_id)",
    "CREATE INDEX IF NOT EXISTS ix_user_sessions_expiry ON user_sessions(expiry)",
    "CREATE INDEX IF NOT EXISTS ix_dataset_owners_dataset ON dataset_owners(dataset_id)",
    "CREATE INDEX IF NOT EXISTS ix_dataset_owners_user    ON dataset_owners(user_id)",
)


def ensure_tables() -> None:
    conn = get_db_connection()
    try:
        for ddl in _TABLES:
            conn.execute(ddl)
        for ddl in _INDEXES:
            conn.execute(ddl)
        conn.commit()
    finally:
        conn.close()


def _audit(event: str, detail: str = "", **fields):
    logger.info("[identity] event=%s %s", event, detail)
    from app.services import applog
    warn = any(word in event for word in ("refused", "blocked", "revoked", "failed"))
    applog.record("identity", f"identity.{event}",
                  level="warning" if warn else "info",
                  message=detail, metadata=fields)


# ── The account ──────────────────────────────────────────────────────────

def find_or_create_user(phone: str, source: str = "login", first_name: str = "",
                        last_name: str = "", position: str = "") -> dict:
    """The account behind a phone number, creating it if there is none.

    One statement, so two callers arriving in the same second produce one row
    and neither of them fails. `created` is read from the id that comes back:
    the candidate id was generated here, so getting it back means this call is
    the one that inserted. `xmax = 0` would say the same thing on PostgreSQL
    and nothing at all on SQLite.

    An existing account keeps its own name and role. The booth typed them for
    a lead; they are not a correction of what the person told us themselves.
    """
    ensure_tables()
    # Hashed before the connection opens: the HMAC key is read from `settings`
    # and on a fresh install that WRITES it, which is a second writer inside an
    # open transaction (see leads.create_invite for the same note).
    digest = phone_digest(phone)
    candidate = secrets.token_urlsafe(12)
    now = _now().isoformat()
    conn = get_db_connection()
    try:
        row = conn.execute(
            "INSERT INTO users (id, phone, phone_hash, phone_hash_key_version,"
            " first_name, last_name, position, status, source, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)"
            # DO UPDATE, not DO NOTHING: a skipped insert returns no row at all,
            # and this call has to come back with the id either way. Rewriting
            # `phone` with the value that produced the same digest changes
            # nothing except that there is a row to return.
            " ON CONFLICT (phone_hash) DO UPDATE SET phone = excluded.phone"
            " RETURNING id, status",
            (candidate, phone or "", digest, PHONE_HASH_KEY_VERSION,
             (first_name or "").strip()[:60], (last_name or "").strip()[:60],
             (position or "").strip()[:80], source, now),
        ).fetchone()
        conn.commit()
    finally:
        conn.close()
    created = row["id"] == candidate
    if created:
        _audit("user_created", source, user_id=row["id"])
    return {"id": row["id"], "status": row["status"], "created": created}


def user_by_phone(phone: str) -> Optional[dict]:
    ensure_tables()
    conn = get_db_connection()
    try:
        row = conn.execute(
            "SELECT id, status, first_name, last_name FROM users WHERE phone_hash = ?",
            (phone_digest(phone),),
        ).fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


def block_user(user_id: str, blocked: bool = True) -> bool:
    """Block or unblock an account. Blocking signs it out everywhere, now.

    The sessions are DELETED rather than left to expire. A status flag alone
    only works where somebody remembered to read it, and the whole reason to
    block an account is that its next request must fail. Unblocking does not
    bring the sessions back: the person signs in again, which is a phone number
    and a code.
    """
    ensure_tables()
    conn = get_db_connection()
    try:
        cur = conn.execute("UPDATE users SET status = ? WHERE id = ?",
                           ("blocked" if blocked else "active", user_id))
        if blocked:
            conn.execute("DELETE FROM user_sessions WHERE user_id = ?", (user_id,))
        conn.commit()
        changed = (cur.rowcount or 0) > 0
    finally:
        conn.close()
    if changed:
        _audit("user_blocked" if blocked else "user_unblocked", "", user_id=user_id)
    return changed


def list_users() -> list:
    """The admin roster: who has an account, and how much they own.

    The number is `owns`, counting live grants only, because a revoked grant in
    that column would read as access this person still has.
    """
    ensure_tables()
    now = _now().isoformat()
    conn = get_db_connection()
    try:
        rows = conn.execute(
            "SELECT u.id, u.first_name, u.last_name, u.phone, u.status, u.source,"
            " u.created_at, u.last_login_at,"
            " (SELECT COUNT(*) FROM dataset_owners o WHERE o.user_id = u.id"
            f"    AND {_LIVE_GRANT}) AS owns"
            " FROM users u ORDER BY u.created_at DESC", (now,),
        ).fetchall()
    finally:
        conn.close()
    from app.services import otp as otp_service
    out = []
    for r in rows:
        d = dict(r)
        # The full number is the admin's to see, but a roster on a laptop in a
        # hall is not the screen to put it on.
        d["phone"] = otp_service.mask_destination(d.get("phone") or "")
        out.append(d)
    return out


# ── The session ──────────────────────────────────────────────────────────

def start_session(user_id: str) -> dict:
    ensure_tables()
    token = secrets.token_urlsafe(32)
    now = _now()
    expiry = now + timedelta(seconds=SESSION_TTL_SECONDS)
    conn = get_db_connection()
    try:
        conn.execute(
            "INSERT INTO user_sessions (token, user_id, expiry, created_at)"
            " VALUES (?, ?, ?, ?)",
            (token, user_id, expiry.isoformat(), now.isoformat()),
        )
        conn.execute("UPDATE users SET last_login_at = ? WHERE id = ?",
                     (now.isoformat(), user_id))
        conn.commit()
    finally:
        conn.close()
    _audit("session_started", "", user_id=user_id)
    return {"token": token, "expires_at": expiry.isoformat(),
            "max_age": SESSION_TTL_SECONDS}


def user_by_session(token: str) -> Optional[dict]:
    """The account behind a cookie, or None. Expiry is decided HERE.

    Three things are re-read on every request: the session exists, it has not
    expired, and the account is still active. None of those answers comes from
    the cookie, so a client that keeps one forever gains nothing.
    """
    if not token:
        return None
    ensure_tables()
    now = _now()
    conn = get_db_connection()
    try:
        row = conn.execute(
            "SELECT s.expiry, u.id, u.status, u.first_name, u.last_name, u.phone"
            " FROM user_sessions s JOIN users u ON u.id = s.user_id"
            " WHERE s.token = ?", (token,),
        ).fetchone()
        if row is None:
            return None
        if to_naive_utc(row["expiry"]) < now or row["status"] != "active":
            # Deleted, not left to rot: a dead session is not evidence of
            # anything and should stop costing a join on every retry.
            conn.execute("DELETE FROM user_sessions WHERE token = ?", (token,))
            conn.commit()
            return None
    finally:
        conn.close()
    return {"id": row["id"], "first_name": row["first_name"],
            "last_name": row["last_name"], "phone": row["phone"]}


def end_session(token: str) -> None:
    if not token:
        return
    ensure_tables()
    conn = get_db_connection()
    try:
        conn.execute("DELETE FROM user_sessions WHERE token = ?", (token,))
        conn.commit()
    finally:
        conn.close()


def verified_destination(challenge_id: str) -> Optional[str]:
    """The number a challenge that has JUST been verified belongs to.

    Read straight from `otp_challenges` because the OTP module returns only a
    masked number, and login needs the real one to find the account. It is safe
    to call only immediately after `otp.verify()` returned True: that call
    consumes the challenge, so a replay of the same id is refused there and
    never reaches this function.
    """
    conn = get_db_connection()
    try:
        row = conn.execute(
            "SELECT destination FROM otp_challenges WHERE id = ? AND used = TRUE",
            (challenge_id,),
        ).fetchone()
    finally:
        conn.close()
    return row["destination"] if row else None


# ── Ownership ────────────────────────────────────────────────────────────

def grant_ownership(dataset_id: str, user_id: str, granted_by: str = "",
                    status: str = "active",
                    expires_at: Optional[str] = None) -> dict:
    """Give one person one company, unless somebody else already has it.

    Returns `{"ok": bool, "reason": str, "id": str}`. `reason` is `"taken"`
    when another account holds a live grant on that company and `"exists"` when
    this account already has a grant on it, live or not: re-granting is not a
    way to reset an expiry, because SEC-012 says a renewal is a fresh admin
    decision and an admin renews by revoking and granting again.

    The one-live-owner rule sits INSIDE the INSERT. Checking it first and
    inserting second is a check made in the past.
    """
    ensure_tables()
    grant_id = secrets.token_urlsafe(12)
    now = _now()
    expiry = expires_at or (now + timedelta(seconds=GRANT_TTL_SECONDS)).isoformat()
    conn = get_db_connection()
    try:
        cur = conn.execute(
            "INSERT INTO dataset_owners (id, dataset_id, user_id, granted_by,"
            " granted_at, expires_at, status)"
            " SELECT ?, ?, ?, ?, ?, ?, ?"
            " WHERE NOT EXISTS (SELECT 1 FROM dataset_owners o"
            "   WHERE o.dataset_id = ? AND o.user_id <> ?"
            f"    AND {_LIVE_GRANT})"
            " ON CONFLICT (dataset_id, user_id) DO NOTHING",
            (grant_id, dataset_id, user_id, (granted_by or "")[:60],
             now.isoformat(), expiry, status,
             dataset_id, user_id, now.isoformat()),
        )
        granted = (cur.rowcount or 0) == 1
        conn.commit()
        if granted:
            existing = None
        else:
            existing = conn.execute(
                "SELECT id FROM dataset_owners WHERE dataset_id = ? AND user_id = ?",
                (dataset_id, user_id),
            ).fetchone()
    finally:
        conn.close()
    if granted:
        _audit("ownership_granted", status, dataset_id=dataset_id, user_id=user_id,
               granted_by=granted_by)
        return {"ok": True, "reason": "", "id": grant_id}
    reason = "exists" if existing is not None else "taken"
    _audit("ownership_refused", reason, dataset_id=dataset_id, user_id=user_id)
    return {"ok": False, "reason": reason,
            "id": existing["id"] if existing is not None else ""}


def accept_grant(user_id: str, ownership_id: str) -> bool:
    """Turn a pending grant into a live one, from the holder's own session.

    This is the other half of the rule that a booth never escalates an existing
    account. The condition is in the UPDATE, including the one-live-owner
    check, so a company claimed in the meantime cannot be accepted twice.
    """
    ensure_tables()
    now = _now().isoformat()
    conn = get_db_connection()
    try:
        cur = conn.execute(
            "UPDATE dataset_owners SET status = 'active'"
            " WHERE id = ? AND user_id = ? AND status = 'pending'"
            "   AND revoked_at IS NULL AND (expires_at IS NULL OR expires_at > ?)"
            "   AND NOT EXISTS (SELECT 1 FROM dataset_owners o"
            "     WHERE o.dataset_id = dataset_owners.dataset_id AND o.id <> dataset_owners.id"
            "       AND o.revoked_at IS NULL AND o.status = 'active'"
            "       AND (o.expires_at IS NULL OR o.expires_at > ?))",
            (ownership_id, user_id, now, now),
        )
        accepted = (cur.rowcount or 0) == 1
        conn.commit()
    finally:
        conn.close()
    _audit("ownership_accepted" if accepted else "ownership_accept_refused", "",
           user_id=user_id, ownership_id=ownership_id)
    return accepted


def revoke_grant(ownership_id: str, actor: str = "") -> bool:
    """Revoked, not deleted. The row is the answer to "who could edit this".

    A revoked grant is indistinguishable from an absent one at every read, so
    nothing downstream has to remember to check a second thing.
    """
    ensure_tables()
    conn = get_db_connection()
    try:
        cur = conn.execute(
            "UPDATE dataset_owners SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL",
            (_now().isoformat(), ownership_id),
        )
        conn.commit()
        revoked = (cur.rowcount or 0) == 1
    finally:
        conn.close()
    if revoked:
        from app.services import applog
        applog.audit("identity.ownership_revoked", "مالکیت لغو شد",
                     actor=actor, target=ownership_id)
        _audit("ownership_revoked", "", ownership_id=ownership_id, actor=actor)
    return revoked


def revoke_dataset_grants(dataset_id: str, actor: str = "") -> int:
    """Take a company back from whoever holds it.

    Called when an admin releases a stuck registration: the company returns to
    every visitor's search list, so the account that got it at the booth must
    stop being able to write its answer at the same moment.
    """
    ensure_tables()
    conn = get_db_connection()
    try:
        cur = conn.execute(
            "UPDATE dataset_owners SET revoked_at = ?"
            " WHERE dataset_id = ? AND revoked_at IS NULL",
            (_now().isoformat(), dataset_id),
        )
        conn.commit()
        count = cur.rowcount or 0
    finally:
        conn.close()
    if count:
        _audit("ownership_revoked", "released", dataset_id=dataset_id, actor=actor)
    return count


def live_grants(user_id: str) -> list:
    """Every company this account may speak for, with the company's own row.

    The join is what makes a grant pointing at a deleted company disappear
    instead of opening an empty editor.
    """
    ensure_tables()
    conn = get_db_connection()
    try:
        rows = conn.execute(
            "SELECT o.id, o.dataset_id, o.expires_at, d.title, d.text"
            " FROM dataset_owners o JOIN dataset d ON d.id = o.dataset_id"
            f" WHERE o.user_id = ? AND {_LIVE_GRANT}"
            " ORDER BY d.title", (user_id, _now().isoformat()),
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def pending_grants(user_id: str) -> list:
    """Companies a booth attached to this EXISTING account, awaiting its say-so.

    They open nothing until accepted. They are shown so the person can accept
    the one they were expecting, and see the one they were not.
    """
    ensure_tables()
    conn = get_db_connection()
    try:
        rows = conn.execute(
            "SELECT o.id, o.dataset_id, o.expires_at, d.title"
            " FROM dataset_owners o JOIN dataset d ON d.id = o.dataset_id"
            " WHERE o.user_id = ? AND o.status = 'pending' AND o.revoked_at IS NULL"
            "   AND (o.expires_at IS NULL OR o.expires_at > ?)"
            " ORDER BY d.title", (user_id, _now().isoformat()),
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def grant_for(user_id: str, ownership_id: str = "") -> Optional[dict]:
    """THE authorisation check. A live grant of this user, or None.

    Every read and every write on the session path goes through here, and the
    `dataset_id` the caller then uses comes out of the row this returns. That
    is the whole of rule 3: the request names an OWNERSHIP, never a company,
    and an ownership id belonging to somebody else matches nothing because
    `user_id` is in the WHERE clause.

    With no id and exactly one live grant, that grant is the answer: one
    company is the ordinary case and asking "which one?" of somebody who has
    one is a question with no purpose.
    """
    grants = live_grants(user_id)
    if not ownership_id:
        return grants[0] if len(grants) == 1 else None
    for g in grants:
        if g["id"] == ownership_id:
            return g
    return None


# ── The booth ────────────────────────────────────────────────────────────

def capture_owner(dataset_id: str, phone: str, first_name: str = "",
                  last_name: str = "", position: str = "") -> dict:
    """A verified capture at a booth becomes an account and a grant.

    A number with no account gets one, active, holding the company: creating an
    account raises nobody's access, because a second ago it did not exist.

    A number that ALREADY has an account gets a `pending` grant instead, and
    the holder turns it on from a session they started themselves. Otherwise
    the booth is a way to attach any company to any existing account: sign up
    with a number, have a colleague capture it at the target's booth, own the
    target.
    """
    user = find_or_create_user(phone, source="booth", first_name=first_name,
                               last_name=last_name, position=position)
    status = "active" if user["created"] else "pending"
    grant = grant_ownership(dataset_id, user["id"], granted_by="booth", status=status)
    _audit("capture_owner", status, dataset_id=dataset_id, user_id=user["id"],
           new_account=user["created"], granted=grant["ok"])
    return {"user_id": user["id"], "new_account": user["created"],
            "status": status, "granted": grant["ok"], "reason": grant["reason"]}
