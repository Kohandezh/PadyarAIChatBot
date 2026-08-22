"""Every admin page must be reachable from the sidebar.

This exists because of a real failure: nine working admin pages shipped with
no navigation at all. The edits that were supposed to add the links silently
did not apply, and the scripts that made them printed a success message
regardless — so the defect survived several rounds of "verification" that only
ever checked page CONTENT, never the menu.

The lesson encoded here: assert against RENDERED HTML, not against a template
file and never against a script's own claim of success.
"""
import datetime
import secrets

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "nav.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    from app.main import app
    with TestClient(app) as c:
        from app.db.connection import get_db_connection
        conn = get_db_connection()
        token = secrets.token_hex(16)
        conn.execute(
            "INSERT OR IGNORE INTO admins (username, password_hash, salt,"
            " security_question, security_answer_hash) VALUES ('nav','x','y','q','z')")
        conn.execute(
            "INSERT INTO admin_sessions (token, username, expiry) VALUES (?,?,?)",
            (token, "nav",
             (datetime.datetime.now() + datetime.timedelta(hours=1)).isoformat()))
        conn.commit()
        conn.close()
        c.cookies.set("admin_session", token)
        # Admin mutations require a CSRF token. These tests exercise the
        # endpoints, not the CSRF guard itself (see tests/test_csrf.py).
        from app.auth.csrf import token_for_session
        c.headers.update({'X-CSRF-Token': token_for_session(token)})
        yield c


def _sidebar(client) -> str:
    page = client.get("/secure-panel-inotex")
    assert page.status_code == 200
    return page.text


# Every operational page built for this product, and the link that must reach it.
REQUIRED_LINKS = [
    "/secure-panel-inotex/ops",
    "/secure-panel-inotex/ops/services",
    "/secure-panel-inotex/security/sessions",
    "/secure-panel-inotex/logs",
    "/secure-panel-inotex/logs/overview",
    "/secure-panel-inotex/logs/settings",
    "/secure-panel-inotex/infrastructure/database",
    "/secure-panel-inotex/infrastructure/storage",
    "/secure-panel-inotex/infrastructure/backups",
]


@pytest.mark.parametrize("href", REQUIRED_LINKS)
def test_sidebar_links_to_every_operational_page(client, href):
    assert f'href="{href}"' in _sidebar(client), (
        f"{href} has no sidebar link — the page would be reachable only by "
        f"typing the URL, which is how nine pages were orphaned before")


@pytest.mark.parametrize("href", REQUIRED_LINKS)
def test_every_linked_page_actually_responds(client, href):
    """A link is worthless if it 404s. Guards against the opposite failure:
    navigation that points at routes an install does not mount."""
    assert client.get(href, follow_redirects=False).status_code == 200, href


@pytest.mark.parametrize("title", ["عملیات", "لاگ‌ها", "زیرساخت"])
def test_the_three_menu_groups_render(client, title):
    assert f'nav-link-title">{title}<' in _sidebar(client)


def test_category_shortcuts_are_present(client):
    """The log explorer serves every category via ?category=; the sub-menu is
    the only discoverable route to them."""
    sidebar = _sidebar(client)
    for category in ("llm", "sms", "otp", "auth", "security", "audit", "api", "chat"):
        assert f"/secure-panel-inotex/logs?category={category}" in sidebar, category


def test_menus_hide_when_their_module_is_disabled(tmp_path, monkeypatch):
    """An install without the optional module must not advertise routes it
    does not mount — a link to a 404 is worse than no link."""
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "nav2.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    monkeypatch.setattr(config, "ENABLED_MODULES", ["theme"])
    from app.main import app
    with TestClient(app) as c:
        from app.db.connection import get_db_connection
        conn = get_db_connection()
        token = secrets.token_hex(16)
        conn.execute(
            "INSERT OR IGNORE INTO admins (username, password_hash, salt,"
            " security_question, security_answer_hash) VALUES ('nav','x','y','q','z')")
        conn.execute(
            "INSERT INTO admin_sessions (token, username, expiry) VALUES (?,?,?)",
            (token, "nav",
             (datetime.datetime.now() + datetime.timedelta(hours=1)).isoformat()))
        conn.commit()
        conn.close()
        c.cookies.set("admin_session", token)
        sidebar = c.get("/secure-panel-inotex").text
    assert 'nav-link-title">لاگ‌ها<' not in sidebar
    assert 'nav-link-title">زیرساخت<' not in sidebar


# ── The sidebar must survive on the page itself ──────────────────────────
#
# A link in the sidebar is only half the journey. These seven pages each
# overrode `{% block content %}`, which is the block admin/layout.html uses
# for the sidebar PLUS a nested `{% block page_content %}`. Overriding the
# outer block replaced the layout's whole body — sidebar included — so the
# pages rendered as bare full-page views with no navigation, and the only way
# back was the browser's back button. Every other admin page overrides
# `page_content`; these now do too.
#
# Each tuple is (url, the sidebar entry that must be highlighted for it).
LAYOUT_PAGES = [
    ("/secure-panel-inotex/ops", "/secure-panel-inotex/ops",
     "/static/admin/js/ops_dashboard.js"),
    ("/secure-panel-inotex/ops/services", "/secure-panel-inotex/ops/services",
     "/static/admin/js/ops_services.js"),
    ("/secure-panel-inotex/security/sessions", "/secure-panel-inotex/security/sessions",
     "/static/admin/js/security_sessions.js"),
    ("/secure-panel-inotex/logs", "/secure-panel-inotex/logs",
     "/static/admin/js/logs.js"),
    ("/secure-panel-inotex/logs/overview", "/secure-panel-inotex/logs/overview",
     "/static/admin/js/logs_overview.js"),
    ("/secure-panel-inotex/logs/settings", "/secure-panel-inotex/logs/settings",
     "/static/admin/js/logs_settings.js"),
    ("/secure-panel-inotex/infrastructure/database",
     "/secure-panel-inotex/infrastructure/database",
     "/static/admin/js/infra_database.js"),
]


@pytest.mark.parametrize("url,active_href,js", LAYOUT_PAGES)
def test_page_renders_with_the_sidebar(client, url, active_href, js):
    res = client.get(url, follow_redirects=False)
    assert res.status_code == 200, url
    html = res.text
    assert 'id="adminSidebar"' in html, (
        f"{url} rendered without the sidebar — it is overriding "
        f"{{% block content %}} instead of {{% block page_content %}}")
    # The shared sidebar actions (logout / reload / CSV) come with the layout.
    assert "/static/admin/js/auth.js" in html, url


@pytest.mark.parametrize("url,active_href,js", LAYOUT_PAGES)
def test_page_highlights_its_own_sidebar_entry(client, url, active_href, js):
    """active_page must match what layout.html tests for, or the admin sees a
    sidebar with nothing marked and no idea where they are."""
    html = client.get(url).text
    assert f'class="dropdown-item active" href="{active_href}"' in html, (
        f"{url} does not light its sidebar entry — check the active_page "
        f"passed by app/routers/public.py")
    # …and its parent dropdown is open, so the highlighted item is visible.
    assert "dropdown-menu show" in html, url


@pytest.mark.parametrize("url,active_href,js", LAYOUT_PAGES)
def test_page_keeps_its_own_javascript(client, url, active_href, js):
    """Moving the body between blocks must not drop the page's ES module —
    without it the page renders and then does nothing at all."""
    assert js in client.get(url).text, url


@pytest.mark.parametrize("url,active_href,js", LAYOUT_PAGES)
def test_page_sets_its_own_title(client, url, active_href, js):
    html = client.get(url).text
    assert "<title>" in html and "<title>پنل مدیریت |" not in html, (
        f"{url} fell back to the layout's default title")


@pytest.mark.parametrize("url,active_href,js", LAYOUT_PAGES)
def test_rendered_divs_balance(client, url, active_href, js):
    """Assert on the RENDERED html: an unbalanced <div> in a template only
    shows up once the layout has wrapped it."""
    import re
    html = client.get(url).text
    opened = len(re.findall(r"<div\b", html))
    closed = html.count("</div>")
    assert opened == closed, f"{url}: {opened} <div> vs {closed} </div>"
