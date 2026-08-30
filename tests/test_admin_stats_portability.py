"""The /admin/api/stats weekly query must run on PostgreSQL, not just SQLite.

The 2026-08-30 elecomp incident: the dashboard's daily-stats query used
SQLite's two-argument `date('now', '-7 days')`. PostgreSQL has no such
function (`function date(unknown, unknown) does not exist`) and the endpoint
returned 500 on every load in production. The test suite runs on SQLite,
where the query is legal, so nothing caught it — hence the source scan below
in addition to the behavioural test.
"""
import datetime
import secrets

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "stats.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    from app.main import app
    with TestClient(app) as c:
        from app.db.connection import get_db_connection
        conn = get_db_connection()
        token = secrets.token_hex(16)
        conn.execute("INSERT OR IGNORE INTO admins (username, password_hash, salt,"
                     " security_question, security_answer_hash)"
                     " VALUES ('ops','x','y','q','z')")
        conn.execute("INSERT INTO admin_sessions (token, username, expiry) VALUES (?,?,?)",
                     (token, "ops",
                      (datetime.datetime.now() + datetime.timedelta(hours=1)).isoformat()))
        conn.commit()
        conn.close()
        c.cookies.set("admin_session", token)
        from app.auth.csrf import token_for_session
        c.headers.update({'X-CSRF-Token': token_for_session(token)})
        yield c


def _log(conn, when, tokens=1):
    conn.execute(
        "INSERT INTO chat_logs (query, response, tokens, cost, created_at)"
        " VALUES ('q','r',?,0.0,?)", (tokens, when.isoformat()))
    conn.commit()


def test_stats_sql_has_no_sqlite_only_date_calls():
    """SQLite's modifier form `date('now', '-7 days')` is a 500 on PostgreSQL.

    The suite cannot execute this query against PG, so the guard is a source
    scan of the routers for the exact shape that broke production. Any date
    arithmetic belongs in Python, with the result passed as a bound parameter.
    """
    from pathlib import Path
    routers = Path(__file__).resolve().parents[1] / "app" / "routers"
    offenders = []
    for path in routers.glob("*.py"):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            code = line.split("#", 1)[0]  # SQL lives in string literals, not comments
            if "date('now'" in code or 'date("now"' in code:
                offenders.append(f"{path.name}:{lineno}")
    assert offenders == [], (
        "SQLite-only date() call(s) found — they 500 on PostgreSQL: %s" % offenders)


def test_stats_weekly_window_keeps_recent_drops_old(client):
    """Rows inside 7 days are counted, older rows are not — via a bound
    parameter, the form both SQLite and PostgreSQL accept."""
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    now = datetime.datetime.now()
    _log(conn, now - datetime.timedelta(days=1), tokens=3)
    _log(conn, now - datetime.timedelta(days=30), tokens=100)
    conn.close()

    res = client.get("/admin/api/stats")
    assert res.status_code == 200
    body = res.json()
    assert body["total_messages"] == 2
    assert body["total_tokens"] == 103
    assert len(body["daily_stats"]) == 1, "the 30-day-old row must fall outside the window"
