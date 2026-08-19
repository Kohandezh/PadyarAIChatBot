import asyncio

from app.config import logger
from app.db.connection import get_db_connection


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


def get_setting(key, default=None):
    """Read a setting, decrypting it if it was stored encrypted.

    Secrets (the SMS gateway password and API key) are written with an `enc:`
    prefix by app/services/secure_store.py, so what is in the table is not
    human-readable. Callers keep seeing the real value and never have to know
    which settings are secret; plaintext rows written before that existed are
    returned untouched.
    """
    try:
        conn = get_db_connection()
        row = conn.execute('SELECT value FROM settings WHERE key = ?', (key,)).fetchone()
        conn.close()
        if row is None:
            return default
        from app.services.secure_store import reveal
        return reveal(row['value'])
    except Exception:
        return default


def set_setting(key, value):
    conn = get_db_connection()
    conn.execute('INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)', (key, str(value)))
    conn.commit()
    conn.close()


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
    from app.services.search import load_dataset_internal
    try:
        loop = asyncio.get_running_loop()
        loop.run_in_executor(None, load_dataset_internal)
    except RuntimeError:
        load_dataset_internal()


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
    from app.services.search import load_dataset_internal
    try:
        loop = asyncio.get_running_loop()
        loop.run_in_executor(None, load_dataset_internal)
    except RuntimeError:
        load_dataset_internal()
