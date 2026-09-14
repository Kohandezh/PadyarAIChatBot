"""The dashboard's low-confidence queue must be fixable in place.

WHAT WAS BROKEN. The dashboard's «سوالات با اطمینان پایین» table was read-only:
an operator saw the question the bot fumbled, then had to leave the screen,
open the conversations page (or the dataset editor), retype the question and
hunt for the right answer. The conversations page already ships the fix flow
(openFix → POST /admin/api/questions); the dashboard rows just lacked the
entry_id the flow pre-selects with, and the button itself.

WHAT THIS ENFORCES.
1. Behavioural: /admin/api/low_confidence returns entry_id for every row —
   the additive field the fix modal needs to pre-select the answer the bot
   did serve (wrong or not) so the operator starts from something.
2. Source scan: dashboard.js actually wires the «اصلاح» action to the SAME
   endpoint as conversations.js, through fetchAuth (CSRF), and the dashboard
   template carries the fix modal it drives. A request test cannot see what
   the browser sends — same reason, same pattern, as
   tests/test_admin_js_csrf_conformance.py.
"""
import datetime
import os
import secrets

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture
def client(tmp_path, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "fixaction.db"))
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


def _log(conn, query, entry_id="", confidence=0.05):
    conn.execute(
        "INSERT INTO chat_logs (query, response, response_type, source, confidence,"
        " tokens, cost, entry_id, created_at) VALUES (?,'r','system','local',?,1,0.0,?,?)",
        (query, confidence, entry_id, datetime.datetime.now().isoformat()))
    conn.commit()


def test_low_confidence_payload_carries_entry_id(client):
    """Rows keep the entry_id logged in chat_logs; empty stays empty.

    Without the field the dashboard fix modal cannot pre-select the answer
    the bot actually served, and the operator starts every fix from a blank
    list — the read-only screen again, one click deeper.
    """
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    _log(conn, "با ورودی", entry_id="entry-9")
    _log(conn, "بدون ورودی", entry_id="")
    conn.close()

    res = client.get("/admin/api/low_confidence")
    assert res.status_code == 200
    rows = {r["query"]: r for r in res.json()}
    assert rows["با ورودی"]["entry_id"] == "entry-9"
    assert rows["بدون ورودی"]["entry_id"] == ""


def test_dashboard_js_wires_the_fix_to_the_questions_endpoint():
    """The «اصلاح» action must post through fetchAuth to /admin/api/questions.

    That endpoint inserts the question → dataset mapping and reindexes on the
    way out, so the saved fix is live for the very next visitor. A bare
    fetch() would 403 on CSRF; posting anywhere else would bypass the
    reindex single-source.
    """
    with open(os.path.join(REPO_ROOT, "static", "admin", "js", "dashboard.js"),
              encoding="utf-8") as fh:
        src = fh.read()
    save_call = "fetchAuth('/admin/api/questions'" in src \
        or 'fetchAuth("/admin/api/questions"' in src
    assert save_call, (
        "static/admin/js/dashboard.js must save the fix via fetchAuth to "
        "/admin/api/questions (same flow as conversations.js openFix)."
    )
    assert "اصلاح" in src, (
        "static/admin/js/dashboard.js must render an «اصلاح» action per "
        "low-confidence row."
    )


def test_dashboard_template_carries_the_fix_modal():
    """The modal dashboard.js drives must exist on the dashboard page."""
    with open(os.path.join(REPO_ROOT, "templates", "admin", "dashboard.html"),
              encoding="utf-8") as fh:
        src = fh.read()
    for needle in ('id="fix-modal"', 'id="fix-question"', 'id="fix-save"'):
        assert needle in src, (
            f"templates/admin/dashboard.html is missing {needle} — the fix "
            "flow has no surface to run on."
        )
