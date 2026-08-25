"""Maintenance-mode admin UI: the toggle card and the persistent banner.

The API behind this UI shipped and was enforced long ago
(tests/test_pg_operations.py, tests/test_csrf.py) but no admin surface was
ever wired to it — the only way to flip the switch was a hand-crafted curl
with a session cookie and a CSRF header. These tests pin the two rendered
surfaces: the card on the ops dashboard and the banner that follows the
operator through every authed admin page while maintenance is on.

Asserts against RENDERED HTML, never against the template file — the lesson
encoded in tests/test_admin_navigation.py. CSRF is already held by
tests/test_csrf.py for this exact endpoint (read needs no token, POST
requires a valid session-bound one), so it is not retested here.
"""
import datetime
import secrets

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "mtui.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    from app.main import app
    with TestClient(app) as c:
        from app.db.connection import get_db_connection
        conn = get_db_connection()
        token = secrets.token_hex(16)
        conn.execute("INSERT OR IGNORE INTO admins (username, password_hash, salt,"
                     " security_question, security_answer_hash)"
                     " VALUES ('mtui','x','y','q','z')")
        conn.execute("INSERT INTO admin_sessions (token, username, expiry) VALUES (?,?,?)",
                     (token, "mtui",
                      (datetime.datetime.now() + datetime.timedelta(hours=1)).isoformat()))
        conn.commit()
        conn.close()
        c.cookies.set("admin_session", token)
        # Admin mutations require a CSRF token. These tests exercise the
        # surfaces, not the CSRF guard itself (see tests/test_csrf.py).
        from app.auth.csrf import token_for_session
        c.headers.update({'X-CSRF-Token': token_for_session(token)})
        yield c


def _enable(client):
    r = client.post("/admin/api/ops/maintenance", json={"enabled": True, "reason": ""})
    assert r.status_code == 200


def _disable(client):
    r = client.post("/admin/api/ops/maintenance", json={"enabled": False})
    assert r.status_code == 200


def test_ops_page_renders_the_toggle(client):
    """The switch must exist on the page, labelled in plain Persian — the
    feature is useless if the operator cannot find it (AGENTS.md: findable
    in under 3 seconds, zero jargon)."""
    res = client.get("/secure-panel-inotex/ops")
    assert res.status_code == 200
    assert 'id="maintenance-toggle"' in res.text
    assert "حالت تعمیرات" in res.text


def test_toggle_hidden_when_ops_module_is_disabled(tmp_path, monkeypatch):
    """Without the ops module there is no API behind the switch, so the page
    must render no switch at all — a control that 404s on use is worse than
    no control (mirrors the sidebar-gating test in test_admin_navigation.py).

    Patched BEFORE startup like that test: with ENABLED_MODULES=["theme"]
    the ops API routers do not even mount, while the page route in
    public.py always exists.
    """
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "mtui2.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    monkeypatch.setattr(config, "ENABLED_MODULES", ["theme"])
    from app.main import app
    with TestClient(app) as c:
        from app.db.connection import get_db_connection
        conn = get_db_connection()
        token = secrets.token_hex(16)
        conn.execute("INSERT OR IGNORE INTO admins (username, password_hash, salt,"
                     " security_question, security_answer_hash)"
                     " VALUES ('mtui','x','y','q','z')")
        conn.execute("INSERT INTO admin_sessions (token, username, expiry) VALUES (?,?,?)",
                     (token, "mtui",
                      (datetime.datetime.now() + datetime.timedelta(hours=1)).isoformat()))
        conn.commit()
        conn.close()
        c.cookies.set("admin_session", token)
        res = c.get("/secure-panel-inotex/ops")
    assert res.status_code == 200
    assert 'id="maintenance-toggle"' not in res.text


def test_banner_appears_on_admin_pages_when_maintenance_on(client):
    """The banner is the operator's memory: it follows them onto EVERY authed
    admin page while maintenance is on — including pages far from the ops
    center — and disappears the moment the mode goes off."""
    _enable(client)
    for url in ("/secure-panel-inotex", "/secure-panel-inotex/ops"):
        html = client.get(url).text
        assert "حالت تعمیرات روشن است" in html, url
        # ops module enabled in this environment → the 1-click fix is linked
        assert "خاموش کردن" in html, url
    _disable(client)
    for url in ("/secure-panel-inotex", "/secure-panel-inotex/ops"):
        assert "حالت تعمیرات روشن است" not in client.get(url).text, url


def test_get_reflects_post_and_back(client):
    """The exact GET→POST→GET loop the card's JavaScript performs on every
    interaction. Complements test_pg_operations.py (which covers who/why/when
    and visitor-blocking) by pinning the round trip the UI relies on."""
    assert client.get("/admin/api/ops/maintenance").json()["enabled"] is False
    r = client.post("/admin/api/ops/maintenance", json={"enabled": True, "reason": ""})
    assert r.status_code == 200
    state = client.get("/admin/api/ops/maintenance").json()
    assert state["enabled"] is True
    assert state["enabled_by"] == "mtui"
    assert client.post("/admin/api/ops/maintenance",
                       json={"enabled": False}).status_code == 200
    assert client.get("/admin/api/ops/maintenance").json()["enabled"] is False


def test_banner_does_not_render_on_login_page(client):
    """login.html extends base.html, not layout.html — the banner is an
    authed-panel reminder and stays off the pre-auth page. Maintenance never
    blocks the panel anyway, so there is no reason to warn a logging-in
    operator there. Cookie dropped so the page actually renders (with a
    valid session the login route redirects to the dashboard).
    """
    _enable(client)
    client.cookies.clear()
    res = client.get("/secure-panel-inotex/login", follow_redirects=False)
    assert res.status_code == 200
    assert "حالت تعمیرات روشن است" not in res.text
