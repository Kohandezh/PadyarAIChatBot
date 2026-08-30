import sqlite3
import secrets
import hashlib

from app.config import logger


def get_db_connection():
    """The application connection.

    Routes to PostgreSQL when DB_BACKEND=postgres (production). The SQLite
    branch below is kept for the test suite and for rollback during the
    transition — it is NOT the production source of truth any more.
    """
    from app.config import DB_BACKEND
    if DB_BACKEND == "postgres":
        from app.db import pg
        return pg.connect()

    from app.config import DB_PATH
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    # WAL = many readers + one writer at the same time (instead of locking the
    # whole file). busy_timeout makes a blocked write wait up to 5s for the lock
    # instead of instantly raising "database is locked". Both matter now that
    # the DB is the only home for content and gunicorn runs 4 workers.
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA busy_timeout=5000')
    conn.execute('PRAGMA synchronous=NORMAL')
    return conn


def ensure_dataset_columns(cursor) -> None:
    """Add the bilingual and ordering columns to an older `dataset` table.

    Installs created before the bilingual knowledge base have only
    (id, title, text, video_url). Empty English columns are a valid state —
    the chat falls back to Persian — so this is safe to run on every boot and
    from the operator reset script.

    `position` is the explicit display order for the public knowledge base.
    It replaces `ORDER BY rowid`, which is a SQLite-only pseudo-column and
    therefore 500'd on PostgreSQL. The backfill below reads `rowid` while it
    is still available, which is exactly why this lives on the SQLite path —
    the PostgreSQL side gets the same order from migration 0004, where it had
    to be written out by hand because the source of truth was already gone.

    SQLite-only helper: both callers pass a `sqlite3` cursor.
    """
    for column, ddl in (("title_en", "TEXT DEFAULT ''"),
                        ("text_en", "TEXT DEFAULT ''"),
                        ("position", "INTEGER")):
        try:
            cursor.execute(f"ALTER TABLE dataset ADD COLUMN {column} {ddl}")
        except sqlite3.OperationalError:
            pass  # column already present
    # Spaced by 10 so an entry can later be slotted between two others without
    # renumbering. Only fills gaps, so it never reorders an existing catalog.
    try:
        cursor.execute("UPDATE dataset SET position = rowid * 10"
                       " WHERE position IS NULL")
    except sqlite3.OperationalError:
        pass


def _rebuild_synonyms_pk(cursor) -> None:
    """Move a database created before the split off `source TEXT PRIMARY KEY`.

    SQLite cannot alter a primary key in place and CREATE TABLE IF NOT EXISTS
    leaves an existing table alone, so without this an older file keeps the
    single-column key and keeps the behaviour this change exists to remove: the
    second synonym of a word silently replaces the first. That is exactly the
    divergence, still present, on the one backend a developer runs locally and
    the suite runs against. The table holds a few dozen rows and nothing
    references it, so the copy is free.

    SQLite-only helper: on PostgreSQL the migration already has the pair key.
    """
    columns = cursor.execute('PRAGMA table_info(synonyms)').fetchall()
    if sum(1 for c in columns if c['pk']) != 1:
        return  # already keyed on the pair
    cursor.execute('ALTER TABLE synonyms RENAME TO synonyms_old')
    cursor.execute('CREATE TABLE synonyms ('
                   ' source TEXT NOT NULL,'
                   ' target TEXT NOT NULL,'
                   ' PRIMARY KEY (source, target))')
    # The old table allowed a NULL target; the new one does not.
    cursor.execute("INSERT OR IGNORE INTO synonyms (source, target)"
                   " SELECT source, COALESCE(target, '') FROM synonyms_old")
    cursor.execute('DROP TABLE synonyms_old')
    logger.info("[db] synonyms rebuilt with PRIMARY KEY (source, target)")


def ensure_chat_log_columns(cursor) -> None:
    """Add the conversation-memory columns to an older `chat_logs` table.

    The SQLite mirror of migrations/0009_conversation_memory.sql. Installs
    created before conversation memory have only the eight original columns,
    and `CREATE TABLE IF NOT EXISTS` does nothing on an existing table — so
    without this the readers would find no columns, degrade to their empty
    default, and the whole pick tier would be quietly off on every install
    that has been running for more than a day.

    Empty is a valid state for all three: a row logged before this ran simply
    belongs to no conversation. Safe to run on every boot.

    SQLite-only helper: the caller passes a `sqlite3` cursor.
    """
    for column in ("conversation_id", "entry_id", "offer_state"):
        try:
            cursor.execute(
                f"ALTER TABLE chat_logs ADD COLUMN {column} TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass  # column already present
    # Same two indexes as the migration: the readers filter on
    # conversation_id and take the newest row by id.
    for name, ddl in (
        ("ix_chat_logs_conversation",
         "CREATE INDEX IF NOT EXISTS ix_chat_logs_conversation"
         " ON chat_logs(conversation_id, id DESC)"),
        ("ix_chat_logs_offer",
         "CREATE INDEX IF NOT EXISTS ix_chat_logs_offer"
         " ON chat_logs(conversation_id, id DESC) WHERE offer_state <> ''"),
    ):
        try:
            cursor.execute(ddl)
        except sqlite3.OperationalError:
            logger.debug(f"[db] index {name} not created")


def _create_sqlite_schema(cursor):
    """Create and migrate the SQLite schema.

    PostgreSQL never runs this. There, `migrations/*.sql` owns the schema and
    the application deliberately does not create production tables at runtime
    (see docs/engineering/DEPLOYMENT_RUNBOOK.md).
    """
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS chat_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        query TEXT,
        response TEXT,
        response_type TEXT,
        source TEXT,
        confidence REAL,
        tokens INTEGER DEFAULT 0,
        cost REAL DEFAULT 0.0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        conversation_id TEXT DEFAULT '',
        entry_id TEXT DEFAULT '',
        offer_state TEXT DEFAULT ''
    )
    ''')

    # CREATE TABLE IF NOT EXISTS never reaches an EXISTING chat_history.db, so
    # the three columns above land only on a brand-new database. Without the
    # ALTER pass below, conversation memory would be silently dead on every dev
    # box and every SQLite install that already had a chat_logs table, while
    # the tests (which always start from a fresh file) passed.
    ensure_chat_log_columns(cursor)

    cursor.execute('''
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT
    )
    ''')

    # A word has SEVERAL synonyms, so the key is the pair. This used to be
    # `source TEXT PRIMARY KEY` while migrations/0001_initial.sql (read off the
    # live table) already keyed on (source, target). The same admin action then
    # did two different things: `INSERT OR REPLACE` replaced the row here and
    # added a second one on PostgreSQL, and a delete by source removed one
    # mapping here and every mapping there.
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS synonyms (
        source TEXT NOT NULL,
        target TEXT NOT NULL,
        PRIMARY KEY (source, target)
    )
    ''')
    _rebuild_synonyms_pk(cursor)
    cursor.execute('CREATE INDEX IF NOT EXISTS ix_synonyms_source ON synonyms(source)')

    cursor.execute('''
    CREATE TABLE IF NOT EXISTS admins (
        username TEXT PRIMARY KEY,
        password_hash TEXT,
        salt TEXT,
        security_question TEXT,
        security_answer_hash TEXT
    )
    ''')

    cursor.execute('''
    CREATE TABLE IF NOT EXISTS admin_sessions (
        token TEXT PRIMARY KEY,
        username TEXT,
        expiry TIMESTAMP
    )
    ''')

    # Mirrors app.login_attempts in migrations/0001_initial.sql. It was added
    # there and never here, so on this backend the brute-force lockout had no
    # table to write to at all. TIMESTAMP for TIMESTAMPTZ is the same mapping
    # admin_sessions.expiry already uses.
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS login_attempts (
        ip TEXT PRIMARY KEY,
        attempts INTEGER NOT NULL DEFAULT 0,
        block_until TIMESTAMP,
        last_attempt TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    ''')

    # Cross-worker rate limiting (chat + public form endpoints). Same story as
    # login_attempts above: an in-process dict is one dict per uvicorn worker.
    # One row per admitted request; `ts` is a unix epoch float so the window
    # comparison is plain numeric on both backends. Mirrors
    # app.rate_limit_hits in migrations/0007_security_hardening.sql.
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS rate_limit_hits (
        key TEXT NOT NULL,
        ts REAL NOT NULL
    )
    ''')
    cursor.execute(
        'CREATE INDEX IF NOT EXISTS ix_rate_limit_hits_key_ts'
        ' ON rate_limit_hits(key, ts)')

    # Migration: add username column to existing admin_sessions
    try:
        cursor.execute('SELECT username FROM admin_sessions LIMIT 1')
    except sqlite3.OperationalError:
        cursor.execute('ALTER TABLE admin_sessions ADD COLUMN username TEXT')

    cursor.execute('''
    CREATE TABLE IF NOT EXISTS dataset (
        id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        text TEXT NOT NULL,
        video_url TEXT DEFAULT '',
        title_en TEXT DEFAULT '',
        text_en TEXT DEFAULT '',
        position INTEGER
    )
    ''')

    ensure_dataset_columns(cursor)

    # No FOREIGN KEY on dataset_id (there used to be one, ON DELETE CASCADE).
    # PostgreSQL (migrations/0001_initial.sql) never had this FK — dataset_id
    # is a plain indexed TEXT column there. migrations/0013_companies.sql
    # deletes every company id out of `dataset` (they move to `companies`),
    # and a curated question row for a company must survive that: 840 of them
    # do, on production, and they are what Tier 0 (find_similar_question)
    # still answers from. A CASCADE FK here would have deleted those rows the
    # moment the dataset cleanup ran, silently, so both backends now agree:
    # removing a dataset/company row never touches `questions`.
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS questions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        question TEXT NOT NULL,
        dataset_id TEXT NOT NULL,
        video_url TEXT DEFAULT ''
    )
    ''')

    _create_companies_table(cursor)
    ensure_companies_columns(cursor)
    _create_conversation_tables(cursor)
    _create_visitor_sessions_table(cursor)

    try:
        cursor.execute('SELECT salt FROM admins LIMIT 1')
    except sqlite3.OperationalError:
        cursor.execute('ALTER TABLE admins ADD COLUMN salt TEXT')


def _create_companies_table(cursor):
    """The SQLite half of migrations/0013_companies.sql.

    Read that file (and docs/features/companies-own-table/RESEARCH.md) for
    WHY: a `dataset` row used to BE a company when `company_profiles` held a
    row keyed to it, and every reader had to remember to subtract companies
    out. This table merges what a company IS (the old `dataset` columns) with
    what the organizer KNOWS about it (the old `company_profiles` columns,
    formerly created by app/services/leads.py's `_TABLES`), so a company is
    one row, one table, no join.

    Created here — a CORE table, not gated behind the leads/registration
    module the way `company_profiles` was — because a `companies` row can now
    be the answer to a Tier 0 curated question or a pick-tier offer on ANY
    install, whether or not that install ordered the leads module. See
    app/services/search.py's `companies_lookup` fallback in `get_entry()` and
    `find_similar_question()`.

    No FOREIGN KEY on `id` anywhere else needs it: `questions.dataset_id`,
    `company_leads.dataset_id`, `dataset_edits.dataset_id` and
    `edit_invites.dataset_id` are plain TEXT columns on both backends, exactly
    as they were when they pointed into `dataset` — see the note beside the
    `questions` table above for why that stays true here too.
    """
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS companies (
        id                TEXT PRIMARY KEY,
        title             TEXT NOT NULL DEFAULT '',
        title_en          TEXT NOT NULL DEFAULT '',
        text              TEXT NOT NULL DEFAULT '',
        text_en           TEXT NOT NULL DEFAULT '',
        video_url         TEXT NOT NULL DEFAULT '',
        position          INTEGER,
        contact_name      TEXT NOT NULL DEFAULT '',
        contact_position  TEXT NOT NULL DEFAULT '',
        contact_mobile    TEXT NOT NULL DEFAULT '',
        email             TEXT NOT NULL DEFAULT '',
        website           TEXT NOT NULL DEFAULT '',
        company_phone     TEXT NOT NULL DEFAULT '',
        fax               TEXT NOT NULL DEFAULT '',
        address           TEXT NOT NULL DEFAULT '',
        address_en        TEXT NOT NULL DEFAULT '',
        province          TEXT NOT NULL DEFAULT '',
        company_type      TEXT NOT NULL DEFAULT '',
        org_stage         TEXT NOT NULL DEFAULT '',
        activity_field    TEXT NOT NULL DEFAULT '',
        participation     TEXT NOT NULL DEFAULT '',
        notes             TEXT NOT NULL DEFAULT '',
        source            TEXT NOT NULL DEFAULT 'import',
        created_at        TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at        TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        priority_boost    INTEGER NOT NULL DEFAULT 0,
        booth_number      TEXT NOT NULL DEFAULT '',
        hall              TEXT NOT NULL DEFAULT ''
    )
    ''')


def ensure_companies_columns(cursor) -> None:
    """Add columns introduced after `companies` first shipped (migration
    0013) to an older SQLite table. CREATE TABLE IF NOT EXISTS above does
    nothing once the table already exists, so an install that predates a
    given column needs this ALTER pass — same shape as
    ensure_dataset_columns() and ensure_chat_log_columns() above.

    `priority_boost` is the SQLite mirror of migrations/0014: 0/1 stands in
    for SQLite's absent BOOLEAN, same as `used` and `active` do elsewhere in
    this file. `booth_number` mirrors migrations/0015, `hall` mirrors
    migrations/0016. Safe to run on every boot.

    SQLite-only helper: the caller passes a `sqlite3` cursor.
    """
    for column, ddl in (("priority_boost", "INTEGER NOT NULL DEFAULT 0"),
                        ("booth_number", "TEXT NOT NULL DEFAULT ''"),
                        ("hall", "TEXT NOT NULL DEFAULT ''")):
        try:
            cursor.execute(f"ALTER TABLE companies ADD COLUMN {column} {ddl}")
        except sqlite3.OperationalError:
            pass  # column already present


def _create_conversation_tables(cursor):
    """The SQLite half of migrations/0010_conversations.sql.

    Read that file for WHY these three tables exist, why `chat_logs` stays,
    why `answers` is one JSON bag, and why `visitor_id` is '' rather than NULL.
    This is only the type mapping: TIMESTAMPTZ -> TIMESTAMP, JSONB -> TEXT
    holding the same JSON, NUMERIC/DOUBLE PRECISION -> REAL, IDENTITY ->
    AUTOINCREMENT.

    A whole new TABLE lands on its own: an install that has been running for a
    year gets these three on its next boot. New COLUMNS do not — CREATE TABLE
    IF NOT EXISTS does nothing to an existing table — which is why the two
    summary columns get the same ALTER pass `ensure_chat_log_columns()` uses.
    Only a developer box can be in that state (migration 0010 has not been
    applied to any production install), but a machine that silently never
    summarizes is a bad afternoon to debug.

    `created_at` is TIMESTAMP DEFAULT CURRENT_TIMESTAMP, not an ISO string
    written by Python, so retention can compare it with
    `datetime('now','-N days')` exactly as it already does for chat_logs.
    CURRENT_TIMESTAMP writes 'YYYY-MM-DD HH:MM:SS'; an isoformat() string
    sorts differently ('T' > ' ') and would silently break that comparison.

    SQLite-only helper: PostgreSQL never runs this, migrations/ owns it there.
    """
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS visitors (
        id TEXT PRIMARY KEY,
        first_name TEXT NOT NULL DEFAULT '',
        last_name TEXT NOT NULL DEFAULT '',
        phone TEXT NOT NULL DEFAULT '',
        phone_hash TEXT NOT NULL DEFAULT '',
        job TEXT NOT NULL DEFAULT '',
        position TEXT NOT NULL DEFAULT '',
        interests TEXT NOT NULL DEFAULT '',
        answers TEXT NOT NULL DEFAULT '{}',
        visitor_settings TEXT NOT NULL DEFAULT '{}',
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        last_seen_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    ''')
    # Partial, so the many visitors captured without a phone do not all
    # collide on ''.
    cursor.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_visitors_phone_hash"
                   " ON visitors(phone_hash) WHERE phone_hash <> ''")
    cursor.execute('CREATE INDEX IF NOT EXISTS ix_visitors_created'
                   ' ON visitors(created_at DESC)')

    cursor.execute('''
    CREATE TABLE IF NOT EXISTS conversations (
        id TEXT PRIMARY KEY,
        visitor_id TEXT NOT NULL DEFAULT '',
        started_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        last_message_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        message_count INTEGER NOT NULL DEFAULT 0,
        lang TEXT NOT NULL DEFAULT 'fa',
        ip TEXT NOT NULL DEFAULT '',
        user_agent TEXT NOT NULL DEFAULT '',
        summary TEXT NOT NULL DEFAULT '',
        summary_upto_id INTEGER NOT NULL DEFAULT 0
    )
    ''')
    # For a database created before the summary columns existed. Both are
    # empty by default, which is exactly "this conversation has no summary
    # yet" — the state every new conversation starts in anyway.
    for column, ddl in (("summary", "TEXT NOT NULL DEFAULT ''"),
                        ("summary_upto_id", "INTEGER NOT NULL DEFAULT 0")):
        try:
            cursor.execute(
                f"ALTER TABLE conversations ADD COLUMN {column} {ddl}")
        except sqlite3.OperationalError:
            pass  # column already present
    cursor.execute('CREATE INDEX IF NOT EXISTS ix_conversations_started'
                   ' ON conversations(started_at DESC)')
    cursor.execute("CREATE INDEX IF NOT EXISTS ix_conversations_visitor"
                   " ON conversations(visitor_id) WHERE visitor_id <> ''")

    cursor.execute('''
    CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        conversation_id TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'visitor',
        text TEXT NOT NULL DEFAULT '',
        source TEXT NOT NULL DEFAULT '',
        confidence REAL,
        entry_id TEXT NOT NULL DEFAULT '',
        video_url TEXT NOT NULL DEFAULT '',
        tokens INTEGER NOT NULL DEFAULT 0,
        cost REAL NOT NULL DEFAULT 0,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
    )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS ix_messages_conversation'
                   ' ON messages(conversation_id, id)')
    cursor.execute("CREATE INDEX IF NOT EXISTS ix_messages_weak"
                   " ON messages(created_at DESC, confidence)"
                   " WHERE role = 'assistant'")


def _create_visitor_sessions_table(cursor):
    """The SQLite half of migrations/0012_visitor_sessions.sql.

    Read that file for WHY the table exists (a registered visitor's identity
    must come from a credential the server minted, not from a body field) and
    why it is a table rather than a stateless signed token (a session has to
    be revocable). This is only the type mapping: TIMESTAMPTZ -> TIMESTAMP.

    Two places the mirror differs from the PostgreSQL original, both because
    SQLite cannot express them usefully:

    * the foreign key is written out but not enforced — SQLite does not check
      foreign keys unless the connection turns them on, so it is here for the
      reader, exactly as `messages.conversation_id` is;
    * `expiry` carries no DEFAULT because every row is written by
      app/auth/visitor.py mint(), which always supplies one.

    `created_at` and `last_seen` use DEFAULT CURRENT_TIMESTAMP for the same
    reason the conversation tables do: CURRENT_TIMESTAMP writes
    'YYYY-MM-DD HH:MM:SS' and a Python isoformat() string sorts differently
    ('T' > ' '), which would silently break any later date comparison.
    app/auth/visitor.py writes `expiry` in the matching space-separated shape
    for exactly that reason.

    SQLite-only helper: PostgreSQL never runs this, migrations/ owns it there.
    """
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS visitor_sessions (
        token TEXT PRIMARY KEY,
        visitor_id TEXT NOT NULL DEFAULT '',
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        expiry TIMESTAMP,
        last_seen TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (visitor_id) REFERENCES visitors(id) ON DELETE CASCADE
    )
    ''')
    # "Log this person out everywhere" is a lookup, not a scan.
    cursor.execute('CREATE INDEX IF NOT EXISTS ix_visitor_sessions_visitor'
                   ' ON visitor_sessions(visitor_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS ix_visitor_sessions_expiry'
                   ' ON visitor_sessions(expiry)')


def _seed_defaults(cursor):
    """Seed what a brand-new install needs, on either backend.

    Safe to run on every boot: every statement is `INSERT OR IGNORE` (which the
    PostgreSQL adapter rewrites to `ON CONFLICT DO NOTHING`) or guarded by a
    count, so existing customer content is never touched.
    """
    cursor.execute('INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)', ('openai_enabled', 'true'))
    cursor.execute('INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)', ('active_theme', 'inotex'))
    # The knowledge version travels with health/ready responses and logs so
    # any answer can be traced back to the content release that produced it.
    # Bump it whenever content/sources.json publishes a new verified state.
    cursor.execute('INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)', ('knowledge_version', 'inotex-kb-2026-08-14.1'))

    from app.config import SEED_DEFAULT_CONTENT

    # CHAINED on purpose. `app/db/pg.py` gives Connection.cursor() -> self and
    # Connection.execute() -> Cursor, so fetchone()/rowcount live on what
    # execute() RETURNS, not on the object execute() was called on. Splitting
    # this into two statements works on SQLite and raises
    # "'Connection' object has no attribute 'fetchone'" on PostgreSQL.
    # app/default_content.py uses the same chained idiom for the same reason.
    if cursor.execute('SELECT COUNT(*) as count FROM synonyms').fetchone()['count'] == 0 \
            and SEED_DEFAULT_CONTENT:
        # Useful INOTEX synonym expansions for a fresh install. Existing
        # customer content is never touched: this seed only runs on an empty
        # table. The canonical list lives in app.default_content.
        from app.default_content import seed_default_synonyms
        seed_default_synonyms(cursor)

    # New installations open with useful INOTEX answers. Existing customer
    # content is never changed: the seed only runs when the table is empty.
    if SEED_DEFAULT_CONTENT:
        from app.default_content import seed_default_content
        seed_default_content(cursor)

    _seed_admin(cursor)


def init_db():
    """Prepare the database a fresh install needs, on the CONFIGURED backend.

    WHY THIS IS BACKEND-AWARE
    -------------------------
    This used to call `sqlite3.connect(DB_PATH)` unconditionally. On a
    PostgreSQL install that quietly created a stray `chat_history.db` and
    seeded THAT — leaving PostgreSQL with no admin row (so nobody could log
    into the panel at all) and an empty knowledge base. Nothing errored:
    `/api/health` reported "ok" and the seed had simply gone into a different
    database.

    The seeding SQL itself is backend-neutral — `?` placeholders and
    `INSERT OR IGNORE`, both of which `app/db/pg.py` translates — so only the
    connection and the DDL had to change. Schema creation stays SQLite-only
    because on PostgreSQL `migrations/*.sql` owns it.
    """
    from app.config import DB_BACKEND

    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        if DB_BACKEND != "postgres":
            _create_sqlite_schema(cursor)
        _seed_defaults(cursor)
        conn.commit()
    finally:
        conn.close()


def _seed_admin(cursor):
    """Create the first admin account on a brand-new install.

    No hardcoded password: credentials come from the env (ADMIN_USERNAME /
    ADMIN_PASSWORD / ADMIN_SECURITY_ANSWER). Anything left empty is randomly
    generated and written to ADMIN_CREDENTIALS.txt so the operator can log in
    and change it. If an admin already exists this is a no-op, so existing
    installs are never touched."""
    from app.config import ADMIN_USERNAME, ADMIN_PASSWORD, ADMIN_SECURITY_ANSWER
    from app.auth.security import hash_password, hash_security_answer

    password = ADMIN_PASSWORD or secrets.token_urlsafe(12)
    answer = ADMIN_SECURITY_ANSWER or secrets.token_urlsafe(6)
    pwd_hash = hash_password(password)
    ans_hash = hash_security_answer(answer)

    # INSERT OR IGNORE (username is PRIMARY KEY) — race-safe across gunicorn
    # workers; rowcount is 1 only for the worker that actually created the row.
    # rowcount is read from what execute() RETURNS — see the note in
    # _seed_defaults; on PostgreSQL the cursor passed in is a Connection and
    # carries no rowcount of its own.
    result = cursor.execute(
        'INSERT OR IGNORE INTO admins (username, password_hash, salt, security_question, security_answer_hash) '
        'VALUES (?, ?, ?, ?, ?)',
        (ADMIN_USERNAME, pwd_hash, '', 'What is your favorite color?', ans_hash)
    )
    # Only reveal the generated secrets if we created the account AND they were
    # auto-generated (not supplied via env).
    if result.rowcount == 1 and (not ADMIN_PASSWORD or not ADMIN_SECURITY_ANSWER):
        _write_admin_credentials(ADMIN_USERNAME, password, answer)


def _write_admin_credentials(username, password, answer):
    """Write the generated login beside the database it belongs to.

    Deliberately NOT BASE_DIR: the test suite points DB_PATH at a throwaway
    database, seeds an admin into it, and would otherwise overwrite the real
    installation's ADMIN_CREDENTIALS.txt with the password of a temp database
    that is deleted seconds later — locking the operator out of their own
    panel. Keying the file to the database keeps a real install's file exactly
    where it has always been (next to chat_history.db in the project root)
    while a throwaway database writes a throwaway file next to itself.
    """
    import os
    from app.config import DB_PATH
    folder = os.path.dirname(os.path.abspath(DB_PATH)) or "."
    path = os.path.join(folder, "ADMIN_CREDENTIALS.txt")
    try:
        # 0600 explicitly. The default umask made this 0644 — a generated admin
        # password readable by every account on the host, which on a shared
        # server is the whole point of generating one undone.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(
                "Padyar Chatbot — auto-generated admin login\n"
                "===========================================\n"
                f"Username:         {username}\n"
                f"Password:         {password}\n"
                f"Security answer:  {answer}\n\n"
                "IMPORTANT: log in, change these in the admin panel, then delete this file.\n"
            )
        logger.warning("Admin credentials generated → %s (change them, then delete the file)", path)
    except OSError as e:
        # Never the credentials themselves in a log — a shipped journal or log
        # aggregator would make the bootstrap password durable far beyond the
        # file's intended lifetime. Point the operator at the recovery path
        # instead (re-seed by deleting the DB, or set ADMIN_PASSWORD /
        # ADMIN_SECURITY_ANSWER in the environment before first boot).
        logger.warning(
            "Admin credentials generated but the credentials file could not be "
            "written (%s). Re-run with ADMIN_PASSWORD / ADMIN_SECURITY_ANSWER "
            "set in the environment, or delete the database to re-seed.",
            e,
        )
