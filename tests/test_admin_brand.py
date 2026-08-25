"""The admin panel's own name comes from the install, not the repository.

This repository deploys to more than one install (inotex, elecomp) from one
branch. `templates/admin/layout.html` used to hard-code «دستیار اینوتکس» —
so the elecomp install's panel would carry the INOTEX event's name. The title
now reads the white-label key `whitelabel_app_name` (the same key
`app/routers/leads.py` already uses for the visitor page), defaulting to the
exact previous wording so an install that never set the key sees no change.

The chat themes are NOT touched: a theme IS an install's identity by design;
the admin panel is shared code, which is where hard-coding was wrong.
"""
import datetime
import secrets

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "brand.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    from app.main import app
    with TestClient(app) as c:
        from app.config import ADMIN_COOKIE_NAME
        from app.db.connection import get_db_connection
        token = secrets.token_hex(16)
        conn = get_db_connection()
        conn.execute(
            "INSERT INTO admin_sessions (token, username, expiry) VALUES (?, ?, ?)",
            (token, "tester",
             (datetime.datetime.now() + datetime.timedelta(hours=1)).isoformat()))
        conn.commit()
        conn.close()
        c.cookies.set(ADMIN_COOKIE_NAME, token)
        yield c


def _panel_title(client):
    """Any admin page carries the layout; the login page needs no session."""
    html = client.get("/secure-panel-inotex/login").text
    import re
    m = re.search(r'navbar-brand[^>]*>\s*<i[^>]*></i>\s*<span>([^<]+)</span>', html)
    assert m, "the brand <span> was not found in the layout"
    return m.group(1).strip()


def test_unset_brand_shows_yesterdays_exact_title(client):
    """The default is the previous hard-coded wording, verbatim — an install
    that never configured branding wakes up after this change to the same
    panel it went to sleep with."""
    assert _panel_title(client) == "دستیار اینوتکس"


def test_a_set_brand_titles_the_panel(client):
    """The whole point: one repository, two installs, two names. The key is
    the existing white-label app name, not a new one to remember."""
    from app.db.queries import set_setting
    set_setting("whitelabel_app_name", "چت‌بات الکامپ")
    assert _panel_title(client) == "چت‌بات الکامپ"


def test_the_hardcoded_name_is_gone_from_the_template():
    """If someone "just fixes the text" back into layout.html, this fires
    before both installs ship each other's event name again."""
    from pathlib import Path
    html = Path("templates/admin/layout.html").read_text(encoding="utf-8")
    assert "دستیار اینوتکس" not in html
    assert "{{ brand_title }}" in html
