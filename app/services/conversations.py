"""Who visited, what they said, and what the bot answered — kept in the DB.

WHAT THIS REPLACES
------------------
The transcript lived in `localStorage['inotex_chat_history']` and the
registered person lived in `localStorage['inotex-visitor']`. Clearing a kiosk
browser threw both away. The profile the visitor typed at registration was
written to `otp_challenges`, a table keyed by a challenge and built to expire.
This is the durable home for all of it. See
migrations/0010_conversations.sql for why the tables look the way they do,
including why `chat_logs` stays and is still written.

THE SEAM
--------
Two other pieces build on this file: the chat router calls the write side
(get_or_create_conversation, append_visitor_message, append_assistant_message,
register_visitor) and the admin panel calls the read side (list_conversations,
list_visitors, conversation_messages, weak_answers). Nothing else should touch
these three tables directly.

WRITES SWALLOW, READS DO NOT
----------------------------
The write functions are on the visitor's hot path, so they follow the rule the
rest of this codebase already follows (see `log_chat` and `applog`): a storage
fault must never cost a visitor their answer. They log and return a safe
default. The admin reads raise instead — an operator staring at an empty list
must not be told that nothing happened when the truth is that the query broke.

BOTH BACKENDS
-------------
Every statement here is the SQLite dialect that `app/db/pg.py` translates:
`?` placeholders, `INSERT OR IGNORE`, and `datetime('now', ...)` written as a
literal (a bound interval is NOT translated — same constraint the readers in
app/db/queries.py document).
"""
import json
import secrets

from app.config import logger
from app.db.connection import get_db_connection

ROLE_VISITOR = "visitor"
ROLE_ASSISTANT = "assistant"

# Same caps the registration form already applies (app/services/otp.py), so a
# profile cannot get longer by travelling through this file.
_LIMITS = {"first_name": 60, "last_name": 60, "job": 80,
           "position": 80, "interests": 200, "phone": 32}


# ── Small shared helpers ─────────────────────────────────────────────────

def _clip(value, field: str) -> str:
    return (str(value or "")).strip()[:_LIMITS[field]]


def _phone_hash(phone: str) -> tuple:
    """(canonical phone, keyed HMAC) for a raw phone string.

    Deliberately NOT a new convention. migrations/0005_leads.sql already made
    a keyed HMAC of the normalised number the dedupe key for
    `company_leads.phone_hash`, and `app/services/leads._digest()` is the
    function that produces it (same key as the OTP codes). A second hashing
    scheme here would mean the same person has two different identities in the
    same database. An unparseable number gives ('', ''): it is stored as typed
    but it dedupes against nothing, which is correct — we cannot say two
    unparseable strings are the same person.
    """
    from app.services import otp as otp_service
    from app.services.leads import _digest

    canonical = otp_service.normalize_destination(phone or "")
    if canonical is None:
        return (str(phone or "").strip()[:_LIMITS["phone"]], "")
    return (canonical, _digest(canonical))


def _like(term: str) -> str:
    """A LIKE pattern with the caller's wildcards escaped.

    A visitor or an operator typing % must match a literal %, not everything.
    Same escaping as `app/services/leads.search_companies`.
    """
    escaped = str(term)[:200].replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _as_float(value):
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _answers(value) -> dict:
    """The `answers` bag as a dict, whichever backend produced it.

    PostgreSQL hands back a parsed dict (JSONB); SQLite hands back the JSON
    text. Anything unparseable becomes {} rather than raising — a corrupt bag
    must not take out the visitor list.
    """
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _message_row(row) -> dict:
    out = dict(row)
    out["confidence"] = _as_float(out.get("confidence"))
    out["cost"] = _as_float(out.get("cost")) or 0.0
    return out


def _visitor_row(row) -> dict:
    out = dict(row)
    out["answers"] = _answers(out.get("answers"))
    return out


# ── Conversations ────────────────────────────────────────────────────────

def _ensure_conversation(conn, conversation_id: str, lang: str, ip: str,
                         user_agent: str) -> None:
    """Create the conversation row if this padyar_conv id has no row yet.

    `INSERT OR IGNORE` and not "SELECT then INSERT": four gunicorn workers can
    handle two messages of the same conversation at the same time, and the
    adapter turns this into `ON CONFLICT DO NOTHING`. The started_at, ip,
    user_agent and lang of the FIRST message are the ones kept — a later
    message never overwrites them, so a session keeps the identity it began
    with.
    """
    conn.execute(
        "INSERT OR IGNORE INTO conversations (id, lang, ip, user_agent)"
        " VALUES (?, ?, ?, ?)",
        (conversation_id, (lang or "fa")[:8], (ip or "")[:64],
         (user_agent or "")[:200]))


def get_or_create_conversation(conversation_id: str, *, lang: str = "fa",
                               ip: str = "", user_agent: str = "") -> dict:
    """The conversation row for this padyar_conv id, creating it if needed.

    Returns {} for an empty id or on any storage fault, so a caller on the
    chat path can carry on without a conversation record.
    """
    if not conversation_id:
        return {}
    try:
        conn = get_db_connection()
        try:
            _ensure_conversation(conn, conversation_id, lang, ip, user_agent)
            conn.commit()
            row = conn.execute(
                "SELECT * FROM conversations WHERE id = ?",
                (conversation_id,)).fetchone()
        finally:
            conn.close()
        return dict(row) if row else {}
    except Exception as e:  # noqa: BLE001 — a visitor's answer outranks a record
        logger.error("[conversations] get_or_create failed: %s: %s",
                     type(e).__name__, e)
        return {}


def get_conversation(conversation_id: str) -> dict:
    """One conversation with the visitor's name joined on. {} when unknown."""
    if not conversation_id:
        return {}
    conn = get_db_connection()
    try:
        row = conn.execute(
            "SELECT c.*, v.first_name, v.last_name, v.phone"
            " FROM conversations c"
            " LEFT JOIN visitors v ON v.id = c.visitor_id"
            " WHERE c.id = ?", (conversation_id,)).fetchone()
    finally:
        conn.close()
    if not row:
        return {}
    return _joined_conversation(row)


def _joined_conversation(row) -> dict:
    """A conversation row with the visitor columns joined on.

    An anonymous conversation joins to no visitor, so those three come back
    NULL. The panel renders them straight, so hand it '' instead of None.
    """
    item = dict(row)
    for key in ("first_name", "last_name", "phone"):
        item[key] = item.get(key) or ""
    return item


# ── Messages ─────────────────────────────────────────────────────────────

def _append(conversation_id: str, role: str, text: str, *, source: str = "",
            confidence=None, entry_id: str = "", video_url: str = "",
            tokens: int = 0, cost: float = 0.0, lang: str = "fa",
            ip: str = "", user_agent: str = "") -> int:
    """Write one message and move the conversation's counters. Returns its id.

    The conversation is ensured first, on the SAME connection, so a caller can
    never write a message into a session that does not exist. On PostgreSQL
    that is also what keeps the foreign key satisfiable; on SQLite foreign
    keys are not enforced at all, and code that is only correct on one backend
    is code that breaks in production.

    Returns 0 on any fault, and the caller is expected to ignore it.
    """
    if not conversation_id:
        return 0
    role = ROLE_ASSISTANT if role == ROLE_ASSISTANT else ROLE_VISITOR
    try:
        conn = get_db_connection()
        try:
            _ensure_conversation(conn, conversation_id, lang, ip, user_agent)
            cur = conn.execute(
                "INSERT INTO messages (conversation_id, role, text, source,"
                " confidence, entry_id, video_url, tokens, cost)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (conversation_id, role, text or "", (source or "")[:60],
                 _as_float(confidence), (entry_id or "")[:120],
                 (video_url or "")[:400], int(tokens or 0),
                 float(cost or 0.0)))
            # datetime('now') is INLINE, not a bound parameter: app/db/pg.py
            # rewrites it into the PostgreSQL form only when it can see the
            # literal. Same idiom as app/db/queries.py.
            conn.execute(
                "UPDATE conversations SET last_message_at = datetime('now'),"
                " message_count = message_count + 1 WHERE id = ?",
                (conversation_id,))
            conn.commit()
            return int(cur.lastrowid or 0)
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001 — see the module docstring
        logger.error("[conversations] append %s failed: %s: %s",
                     role, type(e).__name__, e)
        return 0


def append_visitor_message(conversation_id: str, text: str, *, lang: str = "fa",
                           ip: str = "", user_agent: str = "") -> int:
    """Record what the visitor typed. `lang`/`ip`/`user_agent` only ever set
    the conversation's own fields, and only on its first message."""
    return _append(conversation_id, ROLE_VISITOR, text,
                   lang=lang, ip=ip, user_agent=user_agent)


def append_assistant_message(conversation_id: str, text: str, *, source: str = "",
                             confidence=None, entry_id: str = "",
                             video_url: str = "", tokens: int = 0,
                             cost: float = 0.0) -> int:
    """Record what the bot answered, with the diagnostics that explain it.

    `entry_id` is WHICH dataset record produced the answer and `confidence` is
    how sure the tier was. Those two are what make "find the turns where the
    bot answered wrongly, then fix that record" a query instead of a reading
    exercise.
    """
    return _append(conversation_id, ROLE_ASSISTANT, text, source=source,
                   confidence=confidence, entry_id=entry_id,
                   video_url=video_url, tokens=tokens, cost=cost)


def conversation_messages(conversation_id: str, limit: int = 1000) -> list:
    """Every message of one conversation, oldest first.

    ORDER BY id, not created_at. SQLite's CURRENT_TIMESTAMP has one-second
    resolution, so a question and its answer written in the same second tie
    and come back in whatever order the planner felt like — which for a
    transcript means the bot appearing to answer before it was asked. `id` is
    monotonic on both backends. Migration 0009 made the same call.
    """
    if not conversation_id:
        return []
    conn = get_db_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM messages WHERE conversation_id = ?"
            " ORDER BY id ASC LIMIT ?",
            (conversation_id, max(1, int(limit)))).fetchall()
    finally:
        conn.close()
    return [_message_row(r) for r in rows]


# ── Visitors ─────────────────────────────────────────────────────────────

def upsert_visitor(*, first_name: str = "", last_name: str = "", phone: str = "",
                   job: str = "", position: str = "", interests: str = "",
                   answers: dict = None) -> str:
    """Create or update ONE visitor row for this phone. Returns the visitor id.

    The same person registering twice must not become two people, so the
    keyed HMAC of the phone is the identity. The flow is INSERT OR IGNORE and
    then UPDATE rather than SELECT and then branch: two workers handling two
    registrations of the same number at the same moment would both see "no
    row" and both insert. The partial unique index on phone_hash is what makes
    the second insert a no-op instead of a duplicate.

    A registration with no usable phone number always creates a new row. There
    is nothing to match it against, and merging two strangers because neither
    gave a number would be worse than two rows.
    """
    canonical, digest = _phone_hash(phone)
    fields = {
        "first_name": _clip(first_name, "first_name"),
        "last_name": _clip(last_name, "last_name"),
        "job": _clip(job, "job"),
        "position": _clip(position, "position"),
        "interests": _clip(interests, "interests"),
    }
    new_id = secrets.token_urlsafe(12)

    conn = get_db_connection()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO visitors (id, first_name, last_name, phone,"
            " phone_hash, job, position, interests)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (new_id, fields["first_name"], fields["last_name"], canonical,
             digest, fields["job"], fields["position"], fields["interests"]))
        conn.commit()

        visitor_id = new_id
        if digest:
            row = conn.execute(
                "SELECT id, answers FROM visitors WHERE phone_hash = ?",
                (digest,)).fetchone()
            if row:
                visitor_id = row["id"]

        # A second registration is an UPDATE, and an empty field does not
        # erase what the first one recorded: someone who re-verifies to fix a
        # typo in their job must not lose their name.
        sets, params = ["last_seen_at = datetime('now')"], []
        for column, value in fields.items():
            if value:
                sets.append(f"{column} = ?")
                params.append(value)
        if answers:
            merged = _answers(_stored_answers(conn, visitor_id))
            merged.update({str(k): v for k, v in answers.items()})
            sets.append("answers = ?")
            params.append(json.dumps(merged, ensure_ascii=False, default=str))
        params.append(visitor_id)
        conn.execute(f"UPDATE visitors SET {', '.join(sets)} WHERE id = ?",
                     tuple(params))
        conn.commit()
        return visitor_id
    finally:
        conn.close()


def _stored_answers(conn, visitor_id: str):
    row = conn.execute("SELECT answers FROM visitors WHERE id = ?",
                       (visitor_id,)).fetchone()
    return row["answers"] if row else "{}"


def record_answers(visitor_id: str, answers: dict) -> bool:
    """Merge more answers into a visitor's bag. This is the whole reason the
    bag exists: the bot asks something new tomorrow and stores it without a
    migration. Existing keys are overwritten, the rest are kept."""
    if not visitor_id or not answers:
        return False
    conn = get_db_connection()
    try:
        merged = _answers(_stored_answers(conn, visitor_id))
        merged.update({str(k): v for k, v in answers.items()})
        cur = conn.execute(
            "UPDATE visitors SET answers = ?, last_seen_at = datetime('now')"
            " WHERE id = ?",
            (json.dumps(merged, ensure_ascii=False, default=str), visitor_id))
        conn.commit()
        return bool(cur.rowcount)
    finally:
        conn.close()


def get_visitor(visitor_id: str) -> dict:
    if not visitor_id:
        return {}
    conn = get_db_connection()
    try:
        row = conn.execute("SELECT * FROM visitors WHERE id = ?",
                           (visitor_id,)).fetchone()
    finally:
        conn.close()
    return _visitor_row(row) if row else {}


def find_visitor_by_phone(phone: str) -> dict:
    """Look a visitor up by phone without ever comparing the plaintext."""
    _canonical, digest = _phone_hash(phone)
    if not digest:
        return {}
    conn = get_db_connection()
    try:
        row = conn.execute("SELECT * FROM visitors WHERE phone_hash = ?",
                           (digest,)).fetchone()
    finally:
        conn.close()
    return _visitor_row(row) if row else {}


def attach_visitor(conversation_id: str, visitor_id: str) -> bool:
    """Point an already-started conversation at the person who just registered.

    This is the whole reason `conversations.visitor_id` is fillable rather
    than fixed at creation: somebody walks up, asks four questions, and only
    then registers. Those four questions are theirs and must stay on the
    conversation that now carries their name — nothing about the messages
    changes here.
    """
    if not conversation_id or not visitor_id:
        return False
    try:
        conn = get_db_connection()
        try:
            cur = conn.execute(
                "UPDATE conversations SET visitor_id = ? WHERE id = ?",
                (visitor_id, conversation_id))
            conn.execute(
                "UPDATE visitors SET last_seen_at = datetime('now') WHERE id = ?",
                (visitor_id,))
            conn.commit()
            return bool(cur.rowcount)
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001 — registration must still succeed
        logger.error("[conversations] attach_visitor failed: %s: %s",
                     type(e).__name__, e)
        return False


def register_visitor(conversation_id: str, profile: dict) -> str:
    """Store a verified registration and bind it to the current conversation.

    One call, because it is one event: the OTP was verified, so this person is
    now known and everything they said in this session is theirs. Returns the
    visitor id, or '' if the profile could not be stored.
    """
    profile = profile or {}
    try:
        visitor_id = upsert_visitor(
            first_name=profile.get("first_name", ""),
            last_name=profile.get("last_name", ""),
            phone=profile.get("phone", "") or profile.get("destination", ""),
            job=profile.get("job", ""),
            position=profile.get("position", ""),
            interests=profile.get("interests", ""),
            answers=profile.get("answers"))
    except Exception as e:  # noqa: BLE001 — a failed record must not fail a signup
        logger.error("[conversations] register_visitor failed: %s: %s",
                     type(e).__name__, e)
        return ""
    attach_visitor(conversation_id, visitor_id)
    return visitor_id


# ── Admin reads ──────────────────────────────────────────────────────────

def list_conversations(*, since=None, until=None, has_visitor=None,
                       source: str = "", min_confidence=None,
                       max_confidence=None, q: str = "",
                       limit: int = 50, offset: int = 0) -> list:
    """Conversations newest-activity first, with the filters the panel needs.

    `since`/`until` bound `started_at` and take anything that prints as a
    timestamp ('2026-08-28' is midnight, so pass a time for an end-of-day
    bound). `has_visitor` True/False splits registered from anonymous.
    `source` and the confidence bounds match a conversation that CONTAINS such
    an assistant turn, which is how "show me where it answered badly" is
    actually asked. `max_confidence` is the one that finds bad answers;
    `min_confidence` is there so confidence is a range like the dates are.
    `q` is a free-text search over the message bodies.
    """
    where, params = [], []
    if since:
        where.append("c.started_at >= ?")
        params.append(str(since)[:40])
    if until:
        where.append("c.started_at <= ?")
        params.append(str(until)[:40])
    if has_visitor is True:
        where.append("c.visitor_id <> ''")
    elif has_visitor is False:
        where.append("c.visitor_id = ''")
    if source:
        where.append("EXISTS (SELECT 1 FROM messages m WHERE"
                     " m.conversation_id = c.id AND m.source = ?)")
        params.append(str(source)[:60])
    # confidence IS NOT NULL matters: a visitor's message has no score, and
    # NULL <= 0.3 is not true anyway, but saying so keeps the intent readable.
    if min_confidence is not None:
        where.append("EXISTS (SELECT 1 FROM messages m WHERE"
                     " m.conversation_id = c.id AND m.role = 'assistant'"
                     " AND m.confidence IS NOT NULL AND m.confidence >= ?)")
        params.append(float(min_confidence))
    if max_confidence is not None:
        where.append("EXISTS (SELECT 1 FROM messages m WHERE"
                     " m.conversation_id = c.id AND m.role = 'assistant'"
                     " AND m.confidence IS NOT NULL AND m.confidence <= ?)")
        params.append(float(max_confidence))
    if q:
        where.append("EXISTS (SELECT 1 FROM messages m WHERE"
                     " m.conversation_id = c.id AND m.text LIKE ? ESCAPE '\\')")
        params.append(_like(q))

    clause = (" WHERE " + " AND ".join(where)) if where else ""
    params.extend([max(1, min(int(limit), 500)), max(0, int(offset))])

    conn = get_db_connection()
    try:
        rows = conn.execute(
            "SELECT c.id, c.visitor_id, c.started_at, c.last_message_at,"
            " c.message_count, c.lang, c.ip, c.user_agent,"
            " v.first_name, v.last_name, v.phone"
            " FROM conversations c"
            " LEFT JOIN visitors v ON v.id = c.visitor_id"
            + clause +
            " ORDER BY c.last_message_at DESC, c.id DESC"
            " LIMIT ? OFFSET ?", tuple(params)).fetchall()
    finally:
        conn.close()
    out = []
    for row in rows:
        item = dict(row)
        # LEFT JOIN on an anonymous conversation gives NULL names. The panel
        # renders these straight, so hand it '' rather than None.
        for key in ("first_name", "last_name", "phone"):
            item[key] = item.get(key) or ""
        out.append(item)
    return out


def list_visitors(*, since=None, until=None, q: str = "",
                  limit: int = 50, offset: int = 0) -> list:
    """Registered people, newest first, with how many sessions each one had.

    `q` searches name, job, position and interests. It does NOT search the
    phone: the stored number is the raw one, and letting an operator scan for
    a partial number is a different (auditable) feature — use
    find_visitor_by_phone for a whole number.
    """
    where, params = [], []
    if since:
        where.append("v.created_at >= ?")
        params.append(str(since)[:40])
    if until:
        where.append("v.created_at <= ?")
        params.append(str(until)[:40])
    if q:
        where.append("(v.first_name LIKE ? ESCAPE '\\'"
                     " OR v.last_name LIKE ? ESCAPE '\\'"
                     " OR v.job LIKE ? ESCAPE '\\'"
                     " OR v.position LIKE ? ESCAPE '\\'"
                     " OR v.interests LIKE ? ESCAPE '\\')")
        params.extend([_like(q)] * 5)

    clause = (" WHERE " + " AND ".join(where)) if where else ""
    params.extend([max(1, min(int(limit), 500)), max(0, int(offset))])

    conn = get_db_connection()
    try:
        rows = conn.execute(
            "SELECT v.*, (SELECT COUNT(*) FROM conversations c"
            "  WHERE c.visitor_id = v.id) AS conversation_count"
            " FROM visitors v" + clause +
            " ORDER BY v.created_at DESC, v.id DESC"
            " LIMIT ? OFFSET ?", tuple(params)).fetchall()
    finally:
        conn.close()
    return [_visitor_row(r) for r in rows]


def weak_answers(threshold: float = 0.19, limit: int = 50) -> list:
    """The recent assistant turns the bot was least sure about, newest first.

    This is the read `ix_messages_weak` exists for, and the reason `messages`
    carries `entry_id`: each row names the dataset record that produced it, so
    a wrong answer points straight at the content to fix.
    """
    conn = get_db_connection()
    try:
        rows = conn.execute(
            "SELECT m.id, m.conversation_id, m.text, m.source, m.confidence,"
            " m.entry_id, m.created_at"
            " FROM messages m"
            " WHERE m.role = 'assistant' AND m.confidence IS NOT NULL"
            "   AND m.confidence < ?"
            " ORDER BY m.created_at DESC, m.id DESC LIMIT ?",
            (float(threshold), max(1, min(int(limit), 500)))).fetchall()
    finally:
        conn.close()
    return [_message_row(r) for r in rows]


# ── Retention ────────────────────────────────────────────────────────────

def purge_expired() -> dict:
    """Delete transcript older than `chat_log_retention_days`. 0 = keep all.

    Called from app/db/queries.purge_chat_logs() so there is ONE dial and one
    cycle, not two that can drift apart. `visitors` is deliberately not
    touched — see the retention note in migrations/0010_conversations.sql.

    Messages go first and conversations second. PostgreSQL would cascade, but
    SQLite does not enforce foreign keys at all, so doing it explicitly is the
    only version that behaves the same on both. Conversations are cut on
    `last_message_at`, so a session that is still being used is never half
    deleted while a visitor is talking to it.

    The interval is int-clamped and INLINED into the SQL: app/db/pg.py
    rewrites `datetime('now', '-N days')` only when it can see the literal.
    """
    from app.db.queries import get_setting
    try:
        days = int(get_setting("chat_log_retention_days", "0") or "0")
    except (TypeError, ValueError):
        days = 0
    if days <= 0:
        return {"messages": 0, "conversations": 0}

    conn = get_db_connection()
    try:
        messages = conn.execute(
            "DELETE FROM messages"
            f" WHERE created_at < datetime('now','-{days} days')").rowcount or 0
        conversations = conn.execute(
            "DELETE FROM conversations"
            f" WHERE last_message_at < datetime('now','-{days} days')").rowcount or 0
        conn.commit()
    finally:
        conn.close()
    return {"messages": max(0, messages), "conversations": max(0, conversations)}
