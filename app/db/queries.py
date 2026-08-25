import asyncio
import os
import time

from app.config import logger
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


def log_chat(query, response, r_type, source, confidence, tokens=0, cost=0.0):
    try:
        conn = get_db_connection()
        conn.execute(
            'INSERT INTO chat_logs (query, response, response_type, source, confidence, tokens, cost) '
            'VALUES (?, ?, ?, ?, ?, ?, ?)',
            (query, response, r_type, source, confidence, tokens, cost)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Failed to log chat: {e}")


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
