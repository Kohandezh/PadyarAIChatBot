import asyncio
import os
import time

from app.config import HISTORY_WINDOW_MINUTES, logger
from app.db.connection import get_db_connection


# --- Settings cache -------------------------------------------------------
# get_setting() sits on the hottest path in the app: the chat pipeline alone
# reads 6-10 settings per request (kill switch, content policy, log levels —
# twice per applog row), and each read was a pool checkout + SELECT. Under
# exhibition load that is hundreds of thousands of identical queries per day
# that all return the same few dozen rows.
#
# A short process-local TTL cache removes them. Semantics:
# * set_setting() drops the key immediately — same-worker reads are always
#   fresh after a write, which is the sequence an admin panel edit follows.
# * Other workers converge within the TTL (default 15s, SETTINGS_CACHE_TTL=0
#   disables the cache entirely).
# * Keys whose consumers demand per-request freshness (maintenance.guard)
#   are listed in _UNCACHED_SETTINGS and never cached.
_SETTINGS_CACHE_TTL = max(0.0, float(os.getenv("SETTINGS_CACHE_TTL", "15")))
_UNCACHED_SETTINGS = {"maintenance_state"}
_settings_cache: dict = {}


def clear_settings_cache() -> None:
    """Drop every cached setting. Called after writes, and by tests."""
    _settings_cache.clear()


def _read_setting_uncached(key: str):
    try:
        conn = get_db_connection()
        row = conn.execute('SELECT value FROM settings WHERE key = ?', (key,)).fetchone()
        conn.close()
        if row is None:
            return None
        from app.services.secure_store import reveal
        return reveal(row['value'])
    except Exception:
        return None


def log_chat(query, response, r_type, source, confidence, tokens=0, cost=0.0,
             *, conversation_id="", entry_id="", offer_state=""):
    """Record one answered turn.

    The three memory fields are KEYWORD-ONLY on purpose, not style: an existing
    test spy (tests/test_ai_legacy_import.py) wraps this function with a fixed
    seven-positional-argument signature, and a positional eighth parameter
    would break it silently.

    The wide INSERT is tried first and the original seven-column INSERT is the
    fallback, so an install whose migration 0009 has not run yet keeps logging
    exactly as it does today. Every failure is still swallowed: a logging fault
    has never been allowed to cost a visitor their answer.
    """
    try:
        conn = get_db_connection()
        try:
            conn.execute(
                'INSERT INTO chat_logs (query, response, response_type, source,'
                ' confidence, tokens, cost, conversation_id, entry_id, offer_state) '
                'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                (query, response, r_type, source, confidence, tokens, cost,
                 conversation_id or "", entry_id or "", offer_state or "")
            )
        except Exception:  # noqa: BLE001 — unmigrated table: log the turn anyway
            conn.rollback()
            conn.execute(
                'INSERT INTO chat_logs (query, response, response_type, source, confidence, tokens, cost) '
                'VALUES (?, ?, ?, ?, ?, ?, ?)',
                (query, response, r_type, source, confidence, tokens, cost)
            )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Failed to log chat: {e}")


def recent_turns(conversation_id: str, limit: int = 5) -> list:
    """The last few answered turns of ONE conversation, newest first.

    Returns [] on ANY problem, including a chat_logs table that predates
    migration 0009 and has no conversation_id column. With no history the
    selection tier still works — it just sees no prior turns — so an
    unmigrated install degrades to today's chatbot instead of failing.

    ORDER BY id, not created_at: SQLite's CURRENT_TIMESTAMP has one-second
    resolution, so two turns in the same second tie and the order becomes
    whatever the planner felt like. `id` is monotonic on both backends.

    `source <> 'system'` drops the two sentinel strings `no_confident_match`
    and `ai_unavailable_no_strong_match`, which are stored in the RESPONSE
    column. Replaying them to the model as prior assistant answers teaches it
    to emit them.

    The cutoff is HISTORY_WINDOW_MINUTES, a conversation's length — NOT the
    lifetime of the padyar_conv cookie. A booth kiosk is one browser shared by
    strangers and the cookie slides on every answer, so keying history to the
    cookie made each visitor's first question carry the previous visitors' raw
    words to the AI provider. See the constant's comment in app/config.py.

    The interval is int-clamped and INLINED into the SQL string for the same
    reason as last_offer_state() below: app/db/pg.py rewrites
    `datetime('now', '-N minutes')` into the PostgreSQL form only when it can
    see the literal.
    """
    if not conversation_id:
        return []
    minutes = max(1, int(HISTORY_WINDOW_MINUTES))
    try:
        conn = get_db_connection()
        try:
            rows = conn.execute(
                "SELECT query, response, source, entry_id, created_at"
                " FROM chat_logs"
                " WHERE conversation_id = ? AND source <> 'system'"
                f"   AND created_at >= datetime('now','-{minutes} minutes')"
                " ORDER BY id DESC LIMIT ?",
                (conversation_id, max(1, int(limit)))).fetchall()
        finally:
            conn.close()
        return [dict(r) for r in rows]
    except Exception as e:  # noqa: BLE001 — memory is optional, answers are not
        logger.info(f"[memory] recent_turns unavailable: {type(e).__name__}: {e}")
        return []


def last_entity_id(conversation_id: str, within_minutes: int) -> str:
    """The newest still-fresh served entry for this conversation, or ''.

    The follow-up tier's memory: «کجاس؟» right after a company answer means
    THAT company's booth, exactly the way the offer state makes a bare «3»
    mean the third row of the last list. Same staleness rule, same kiosk
    reasoning, same inlined-minutes idiom as last_offer_state (pg.py
    rewrites the SQLite datetime literal only when it can see it).
    """
    if not conversation_id:
        return ""
    minutes = max(1, int(within_minutes))
    try:
        conn = get_db_connection()
        try:
            row = conn.execute(
                "SELECT entry_id FROM chat_logs"
                " WHERE conversation_id = ? AND entry_id <> ''"
                f"   AND created_at >= datetime('now','-{minutes} minutes')"
                " ORDER BY id DESC LIMIT 1",
                (conversation_id,)).fetchone()
        finally:
            conn.close()
        return (row["entry_id"] if row else "") or ""
    except Exception:  # noqa: BLE001 — memory is an optimization, never a gate
        return ""


def last_offer_state(conversation_id: str, within_minutes: int) -> str:
    """The newest still-fresh offer JSON for this conversation, or ''.

    A booth kiosk is ONE browser and ONE cookie shared by many people, so an
    offer goes stale on purpose: a bare "3" typed twenty minutes after
    somebody else's list must not resolve against that stranger's list.

    The interval is int-clamped and INLINED into the SQL string, not bound as
    a parameter — app/db/pg.py rewrites `datetime('now', '-N minutes')` into
    the PostgreSQL form only when it can see the literal, and a bound
    parameter would stay untranslated and fail there. Same idiom as
    app/services/ai/health.py.
    """
    if not conversation_id:
        return ""
    minutes = max(1, int(within_minutes))
    try:
        conn = get_db_connection()
        try:
            row = conn.execute(
                "SELECT offer_state FROM chat_logs"
                " WHERE conversation_id = ? AND offer_state <> ''"
                f"   AND created_at >= datetime('now','-{minutes} minutes')"
                " ORDER BY id DESC LIMIT 1",
                (conversation_id,)).fetchone()
        finally:
            conn.close()
        return (row["offer_state"] or "") if row else ""
    except Exception as e:  # noqa: BLE001 — no offer is a valid answer
        logger.info(f"[memory] last_offer_state unavailable: {type(e).__name__}: {e}")
        return ""


def purge_chat_logs() -> int:
    """Delete chat turns older than `chat_log_retention_days`. Returns the count.

    `chat_logs` is the UNREDACTED store — log_chat writes the raw visitor query
    with no content policy applied, unlike applog which scrubs — and until now
    nothing pruned it. The selection tier reads it back and ships up to five
    turns to the AI provider, so an operator needs a dial.

    Default 0 = keep forever, so no existing install loses data by upgrading.

    The return value stays the number of CHAT_LOGS rows deleted. The durable
    transcript (`conversations`, `messages`) is pruned here too, on the same
    setting and in the same cycle, but its counts are logged rather than
    returned: an existing test and app/main.py's retention loop both read this
    number as "chat_logs rows removed".
    """
    try:
        days = int(get_setting("chat_log_retention_days", "0") or "0")
    except (TypeError, ValueError):
        days = 0
    if days <= 0:
        return 0
    # One dial, one cycle. Two purges on two schedules drift apart, and the
    # half that stops running is the half nobody notices. Failures here must
    # not stop chat_logs from being pruned, hence the separate try.
    try:
        from app.services import conversations
        removed = conversations.purge_expired()
        if removed["messages"] or removed["conversations"]:
            logger.info("[retention] transcript purge removed %s messages,"
                        " %s conversations",
                        removed["messages"], removed["conversations"])
    except Exception as e:  # noqa: BLE001 — retention must never break a request
        logger.error("[retention] transcript purge failed: %s: %s",
                     type(e).__name__, e)
    try:
        conn = get_db_connection()
        try:
            cur = conn.execute(
                "DELETE FROM chat_logs"
                f" WHERE created_at < datetime('now','-{days} days')")
            deleted = cur.rowcount or 0
            conn.commit()
        finally:
            conn.close()
        return max(0, deleted)
    except Exception as e:  # noqa: BLE001 — retention must never break a request
        logger.error(f"[retention] chat_logs purge failed: {type(e).__name__}: {e}")
        return 0


def get_setting(key, default=None, fresh: bool = False):
    """Read a setting, decrypting it if it was stored encrypted.

    Secrets (the SMS gateway password and API key) are written with an `enc:`
    prefix by app/services/secure_store.py, so what is in the table is not
    human-readable. Callers keep seeing the real value and never have to know
    which settings are secret; plaintext rows written before that existed are
    returned untouched.

    Reads are served from a short TTL cache (see the module comment above).
    Pass fresh=True to bypass it — used by state that must reflect the table
    on every request, and by the search-index version check.
    """
    cacheable = (
        _SETTINGS_CACHE_TTL > 0
        and key not in _UNCACHED_SETTINGS
        and not fresh
    )
    if cacheable:
        hit = _settings_cache.get(key)
        if hit is not None and hit[1] > time.monotonic():
            return hit[0] if hit[0] is not None else default
    value = _read_setting_uncached(key)
    if cacheable:
        _settings_cache[key] = (value, time.monotonic() + _SETTINGS_CACHE_TTL)
    if value is None:
        return default
    return value


def set_setting(key, value):
    conn = get_db_connection()
    conn.execute('INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)', (key, str(value)))
    conn.commit()
    conn.close()
    _settings_cache.pop(key, None)


def save_dataset(dataset: list):
    """Write dataset to SQLite — the single source of truth.

    The chat frontend reads /api/dataset (served from this table). There are no
    JSON files anymore: the DB is the only home for dataset content."""
    conn = get_db_connection()
    conn.execute('DELETE FROM dataset')
    # This replaces the whole table, so the caller's list order IS the new
    # display order — record it as `position` rather than relying on insertion
    # order, which stopped meaning anything once PostgreSQL became the backend.
    conn.executemany(
        'INSERT INTO dataset (id, title, text, video_url, position) VALUES (?, ?, ?, ?, ?)',
        [(d.get('id', ''), d.get('title', ''), d.get('text', ''), d.get('video_url', ''),
          (i + 1) * 10) for i, d in enumerate(dataset)]
    )
    conn.commit()
    conn.close()
    # Reindex in background
    from app.services.search import reindex_and_publish
    try:
        loop = asyncio.get_running_loop()
        loop.run_in_executor(None, reindex_and_publish)
    except RuntimeError:
        reindex_and_publish()


def save_questions(questions_data: list):
    """Write questions to SQLite — the single source of truth.

    The chat frontend reads /api/questions (served from this table). There are
    no JSON files anymore: the DB is the only home for questions content."""
    conn = get_db_connection()
    conn.execute('DELETE FROM questions')
    conn.executemany(
        'INSERT INTO questions (id, question, dataset_id, video_url) VALUES (?, ?, ?, ?)',
        [(q.get('id'), q.get('question', ''), q.get('dataset_id', ''), q.get('video_url', '')) for q in questions_data]
    )
    conn.commit()
    conn.close()
    # Reindex in background
    from app.services.search import reindex_and_publish
    try:
        loop = asyncio.get_running_loop()
        loop.run_in_executor(None, reindex_and_publish)
    except RuntimeError:
        reindex_and_publish()
