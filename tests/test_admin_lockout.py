"""The admin brute-force lockout, now that it lives in the database.

It used to be a module-level dict in app/auth/security.py. Two holes came with
that, and both were live in production:

  * a restart or deploy cleared it, so waiting for the next deploy was enough;
  * uvicorn runs with --workers N, and each worker held its own dict, so an
    attacker got roughly N * MAX_LOGIN_ATTEMPTS guesses before anything blocked.

migrations/0001_initial.sql created `login_attempts` to fix exactly this and
nothing ever read the table. These tests hold the wiring.
"""
import datetime
import hashlib
import os
import subprocess
import sys
import threading

import pytest
from fastapi.testclient import TestClient

from app.config import MAX_LOGIN_ATTEMPTS, BLOCK_TIME_MINUTES

USERNAME = "lockadmin"
PASSWORD = "correct-horse"
ANSWER = "blue"
# Every TestClient request reports this host, so it is the lockout key.
TEST_IP = "testclient"


@pytest.fixture
def client(tmp_path, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "lockout.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    from app.main import app
    from app.auth.security import hash_password
    with TestClient(app) as c:
        from app.db.connection import get_db_connection
        conn = get_db_connection()
        conn.execute(
            "INSERT OR REPLACE INTO admins (username, password_hash, salt,"
            " security_question, security_answer_hash) VALUES (?,?,?,?,?)",
            (USERNAME, hash_password(PASSWORD), "", "color",
             hashlib.sha256(ANSWER.encode()).hexdigest()))
        conn.execute("DELETE FROM login_attempts")
        conn.commit()
        conn.close()
        yield c


def _bad_login(client):
    return client.post("/admin/login", json={"username": USERNAME,
                                             "password": "wrong",
                                             "sec_answer": ANSWER})


def _good_login(client):
    return client.post("/admin/login", json={"username": USERNAME,
                                             "password": PASSWORD,
                                             "sec_answer": ANSWER})


def _row():
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    try:
        return conn.execute("SELECT * FROM login_attempts WHERE ip = ?",
                            (TEST_IP,)).fetchone()
    finally:
        conn.close()


# --- The behaviour an operator believes they have -----------------------

def test_the_table_exists_on_this_backend(client):
    """It was in the migration but not in init_db(), so on SQLite the lockout
    had nowhere to write at all."""
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    try:
        conn.execute("SELECT ip, attempts, block_until, last_attempt"
                     " FROM login_attempts").fetchall()
    finally:
        conn.close()


def test_five_failures_block_the_sixth_attempt(client):
    codes = [_bad_login(client).status_code for _ in range(MAX_LOGIN_ATTEMPTS)]
    assert codes[:-1] == [401] * (MAX_LOGIN_ATTEMPTS - 1)
    assert codes[-1] == 429

    # The proof that the block is real: the RIGHT password is refused too.
    assert _good_login(client).status_code == 429


def test_a_successful_login_clears_the_counter(client):
    for _ in range(MAX_LOGIN_ATTEMPTS - 1):
        assert _bad_login(client).status_code == 401

    assert _good_login(client).status_code == 200
    assert _row() is None

    # A cleared counter means the full allowance is available again.
    for _ in range(MAX_LOGIN_ATTEMPTS - 1):
        assert _bad_login(client).status_code == 401


def test_the_block_expires_after_the_window(client):
    for _ in range(MAX_LOGIN_ATTEMPTS):
        _bad_login(client)
    assert _good_login(client).status_code == 429

    # Move the block into the past instead of waiting BLOCK_TIME_MINUTES.
    from app.db.connection import get_db_connection
    expired = (datetime.datetime.now()
               - datetime.timedelta(minutes=BLOCK_TIME_MINUTES + 1)).isoformat()
    conn = get_db_connection()
    conn.execute("UPDATE login_attempts SET block_until = ? WHERE ip = ?",
                 (expired, TEST_IP))
    conn.commit()
    conn.close()

    assert _good_login(client).status_code == 200
    # And the count started over rather than resuming at the limit.
    assert _row() is None


# --- The two holes the dict could never close ---------------------------

def test_the_block_survives_a_restart(client):
    """The regression that matters. Build the block, throw away every
    in-process object that held it, and it must still apply."""
    for _ in range(MAX_LOGIN_ATTEMPTS):
        _bad_login(client)

    # A real second interpreter, not a re-import. A restart is a process that
    # shares nothing but the database file, and a fresh `python -c` is exactly
    # that. Re-importing the module inside this process would leave two module
    # objects under one name, which is contamination the next test file pays
    # for (it did: test_sms_secure_storage patches
    # app.auth.security.get_app_secret and would patch the wrong copy).
    import app.config as config
    child = subprocess.run(
        [sys.executable, "-c",
         "from app.auth.security import login_block_active;"
         f" print(login_block_active({TEST_IP!r}))"],
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        env={**os.environ, "DB_PATH": config.DB_PATH, "DB_BACKEND": "sqlite",
             "OPENAI_API_KEY": "test-dummy-key"},
        capture_output=True, text=True, timeout=120)
    assert child.stdout.strip().endswith("True"), \
        f"a restarted process did not see the block\n{child.stdout}\n{child.stderr}"

    # Same thing through the API, with a second TestClient standing in for the
    # process that came up after the deploy.
    from app.main import app
    with TestClient(app) as after_restart:
        assert _good_login(after_restart).status_code == 429


def test_two_workers_share_one_counter(client):
    """Separate processes, separate connections, one row. With the dict this
    was N counters and roughly N * MAX_LOGIN_ATTEMPTS free guesses."""
    from app.auth.security import record_failed_login, login_block_active
    from app.db.connection import get_db_connection

    ip = "203.0.113.9"
    # Each call opens its own connection, which is what a second worker has.
    worker_a = [record_failed_login(ip) for _ in range(MAX_LOGIN_ATTEMPTS - 2)]
    worker_b = [record_failed_login(ip) for _ in range(2)]

    assert worker_a + worker_b == list(range(1, MAX_LOGIN_ATTEMPTS + 1))
    assert login_block_active(ip) is True

    conn = get_db_connection()
    try:
        rows = conn.execute("SELECT ip, attempts FROM login_attempts"
                            " WHERE ip = ?", (ip,)).fetchall()
    finally:
        conn.close()
    assert len(rows) == 1, "each worker kept its own row"
    assert rows[0]["attempts"] == MAX_LOGIN_ATTEMPTS


def test_concurrent_failures_are_all_counted(client):
    """Read-then-write would lose some of these, and every lost increment is a
    free guess for the attacker."""
    from app.auth.security import record_failed_login

    ip = "203.0.113.10"
    threads = [threading.Thread(target=record_failed_login, args=(ip,))
               for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    from app.db.connection import get_db_connection
    conn = get_db_connection()
    try:
        row = conn.execute("SELECT attempts FROM login_attempts WHERE ip = ?",
                           (ip,)).fetchone()
    finally:
        conn.close()
    assert row["attempts"] == 8


# --- Failing open when the store is unreachable -------------------------

def test_an_unreachable_store_does_not_lock_the_admin_out(client, monkeypatch):
    """Deliberate direction of failure: a broken lockout table must not become
    a lockout of the real admin. See the comment in app/auth/security.py."""
    import app.db.connection as connection

    def boom():
        raise RuntimeError("relation \"login_attempts\" does not exist")

    monkeypatch.setattr(connection, "get_db_connection", boom)
    from app.auth.security import login_block_active, record_failed_login
    assert login_block_active(TEST_IP) is False
    assert record_failed_login(TEST_IP) == 0


def test_a_failed_login_is_still_recorded_as_a_security_event(client):
    from app.services import applog
    applog.truncate()
    _bad_login(client)
    rows, _ = applog.query(category="security", q="login", limit=10)
    failed = [r for r in rows if r["event_name"] == "auth.login.failed"]
    assert failed, "the audit trail must survive the move to the table"
