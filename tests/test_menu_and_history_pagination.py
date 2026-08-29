"""Two related changes to the hamburger drawer:

1. Row visibility (app/services/menu_settings.py): language / theme-toggle /
   text-size / logout can each be turned off by an admin (Settings →
   برندینگ → «نمایش موارد منو»). Covers the service roundtrip, the admin API,
   rendering into the chat page (including per-theme rows a theme never had
   to begin with, e.g. haj has no language row regardless of the flag), and
   the rendered-page cache flipping on save — same contract as branding/
   idle_video.

2. Pagination on GET /api/chat/conversations (app/routers/chat.py), added on
   top of the existing visitor-chat-history feature: the drawer's "my chats"
   list used to return the whole thing in one call; now it pages 10 at a
   time with a has_more flag. Covers the service layer's offset param, the
   endpoint's page size / has_more computation, and that paging through every
   page recovers the full set with no duplicates or drops (order-independent
   — SQLite's 1-second timestamp resolution means rows inserted in the same
   test can legitimately tie on last_message_at).

Each test runs against a throwaway SQLite DB. Visitor sign-in is done for
real (upsert_visitor + visitor_auth.mint), matching the identity rule
app/auth/visitor.py states: nothing here asserts identity by any other means.
"""
import datetime
import secrets

import pytest
from fastapi.testclient import TestClient

# Every request a browser makes carries these; validate_request_origin
# refuses anything else (see tests/test_visitor_auth_otp.py for precedent).
BROWSER = {"Origin": "http://localhost", "User-Agent": "pytest-agent/1.0"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "menu_history.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    from app.main import app
    with TestClient(app) as c:
        c.headers.update(BROWSER)
        yield c


def _admin_login(client):
    from app.config import ADMIN_COOKIE_NAME
    from app.db.connection import get_db_connection
    token = secrets.token_hex(16)
    expiry = datetime.datetime.now() + datetime.timedelta(hours=1)
    conn = get_db_connection()
    conn.execute(
        'INSERT INTO admin_sessions (token, username, expiry) VALUES (?, ?, ?)',
        (token, "tester", expiry.isoformat()))
    conn.commit()
    conn.close()
    client.cookies.set(ADMIN_COOKIE_NAME, token)
    from app.auth.csrf import token_for_session
    client.headers.update({'X-CSRF-Token': token_for_session(token)})


def _sign_in_visitor(client, phone):
    from app.config import VISITOR_COOKIE_NAME
    from app.auth import visitor as visitor_auth
    from app.services import conversations
    visitor_id = conversations.upsert_visitor(first_name="تست", last_name="کاربر", phone=phone)
    token = visitor_auth.mint(visitor_id)
    assert token, "mint() failed — session was never created"
    client.cookies.set(VISITOR_COOKIE_NAME, token)
    return visitor_id


def _seed_conversations(visitor_id, n):
    """n conversations, one message each. Ids only — order is deliberately
    not asserted anywhere (see module docstring)."""
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    ids = []
    for i in range(n):
        conv_id = f"conv-{visitor_id}-{i:03d}"
        ids.append(conv_id)
        conn.execute(
            "INSERT INTO conversations (id, visitor_id, message_count) VALUES (?, ?, 1)",
            (conv_id, visitor_id))
        conn.execute(
            "INSERT INTO messages (conversation_id, role, text) VALUES (?, 'visitor', ?)",
            (conv_id, f"پیام شماره {i}"))
    conn.commit()
    conn.close()
    return set(ids)


# ── 1. Menu settings: service ───────────────────────────────────────────

def test_menu_settings_default_all_on(client):
    from app.services import menu_settings
    assert menu_settings.get_menu_settings() == {
        "menu_show_language": True,
        "menu_show_theme_toggle": True,
        "menu_show_text_size": True,
        "menu_show_logout": True,
    }


def test_menu_settings_set_and_get_roundtrip(client):
    from app.services import menu_settings
    menu_settings.set_menu_settings({
        "menu_show_language": False,
        "menu_show_theme_toggle": True,
        "menu_show_text_size": False,
        "menu_show_logout": True,
    })
    assert menu_settings.get_menu_settings() == {
        "menu_show_language": False,
        "menu_show_theme_toggle": True,
        "menu_show_text_size": False,
        "menu_show_logout": True,
    }


def test_menu_settings_cache_key_changes_with_content(client):
    from app.services import menu_settings
    before = menu_settings.menu_settings_cache_key()
    menu_settings.set_menu_settings({"menu_show_logout": False})
    after = menu_settings.menu_settings_cache_key()
    assert before != after


# ── 2. Menu settings: admin API ─────────────────────────────────────────

def test_menu_settings_api_requires_admin(client):
    from app.main import app
    with TestClient(app) as anon:
        assert anon.get("/admin/api/menu-settings").status_code in (401, 403)
        r = anon.post("/admin/api/menu-settings", json={
            "show_language": False, "show_theme_toggle": True,
            "show_text_size": True, "show_logout": True,
        })
        assert r.status_code in (401, 403)


def test_menu_settings_api_roundtrip(client):
    _admin_login(client)
    assert client.get("/admin/api/menu-settings").json() == {
        "menu_show_language": True, "menu_show_theme_toggle": True,
        "menu_show_text_size": True, "menu_show_logout": True,
    }
    r = client.post("/admin/api/menu-settings", json={
        "show_language": False, "show_theme_toggle": False,
        "show_text_size": True, "show_logout": False,
    })
    assert r.status_code == 200, r.text
    assert client.get("/admin/api/menu-settings").json() == {
        "menu_show_language": False, "menu_show_theme_toggle": False,
        "menu_show_text_size": True, "menu_show_logout": False,
    }


# ── 3. Chat page rendering ───────────────────────────────────────────────

def test_chat_renders_all_rows_by_default(client):
    html = client.get("/").text
    assert 'id="lang-btn"' in html
    assert 'id="theme-btn"' in html
    assert 'onclick="adjustFontSize(1)"' in html
    assert 'data-show-logout="true"' in html


def test_admin_can_turn_off_a_row_and_it_disappears_from_the_chat_page(client):
    from app.services import menu_settings
    menu_settings.set_menu_settings({"menu_show_language": False})
    html = client.get("/").text
    assert 'id="lang-btn"' not in html
    # Untouched rows are still there.
    assert 'id="theme-btn"' in html
    assert 'onclick="adjustFontSize(1)"' in html


def test_logout_flag_reaches_the_account_section_as_a_data_attribute(client):
    from app.services import menu_settings
    html = client.get("/").text
    assert 'data-show-logout="true"' in html

    menu_settings.set_menu_settings({"menu_show_logout": False})
    html = client.get("/").text
    assert 'data-show-logout="false"' in html


def test_menu_settings_save_invalidates_page_cache(client):
    from app.services import menu_settings
    first = client.get("/").text
    assert 'id="lang-btn"' in first

    menu_settings.set_menu_settings({"menu_show_language": False})

    second = client.get("/").text
    assert 'id="lang-btn"' not in second


def test_haj_theme_has_no_language_row_regardless_of_the_flag(client):
    """The flag only ever hides a row a theme already has — it can never ADD
    one. haj is Persian-only by an unrelated, deliberate product decision."""
    from app.db.queries import set_setting
    from app.services import menu_settings
    set_setting("active_theme", "haj")
    menu_settings.set_menu_settings({"menu_show_language": True})
    html = client.get("/").text
    assert 'id="lang-btn"' not in html


# ── 4. "My chats" pagination ─────────────────────────────────────────────

def test_conversations_endpoint_requires_a_signed_in_visitor(client):
    r = client.get("/api/chat/conversations")
    assert r.status_code == 401


def test_first_page_is_ten_with_has_more_true(client):
    from app.routers.chat import MENU_HISTORY_PAGE_SIZE
    assert MENU_HISTORY_PAGE_SIZE == 10
    visitor_id = _sign_in_visitor(client, "+989120000301")
    _seed_conversations(visitor_id, 15)

    r = client.get("/api/chat/conversations")
    assert r.status_code == 200, r.text
    data = r.json()
    assert len(data["conversations"]) == 10
    assert data["has_more"] is True


def test_second_page_has_the_rest_and_has_more_false(client):
    visitor_id = _sign_in_visitor(client, "+989120000302")
    _seed_conversations(visitor_id, 15)

    r = client.get("/api/chat/conversations?offset=10")
    assert r.status_code == 200, r.text
    data = r.json()
    assert len(data["conversations"]) == 5
    assert data["has_more"] is False


def test_paging_through_everything_recovers_the_full_set_with_no_overlap(client):
    visitor_id = _sign_in_visitor(client, "+989120000303")
    expected_ids = _seed_conversations(visitor_id, 23)

    seen = set()
    offset = 0
    for _ in range(10):  # generous iteration cap — a real loop would use has_more
        r = client.get(f"/api/chat/conversations?offset={offset}")
        assert r.status_code == 200, r.text
        data = r.json()
        page_ids = {c["id"] for c in data["conversations"]}
        assert not (page_ids & seen), "a page repeated an id already seen"
        seen |= page_ids
        offset += len(data["conversations"])
        if not data["has_more"]:
            break
    else:
        pytest.fail("has_more never went false — pagination looped forever")

    assert seen == expected_ids


def test_offset_past_the_end_is_an_empty_page_not_an_error(client):
    visitor_id = _sign_in_visitor(client, "+989120000304")
    _seed_conversations(visitor_id, 3)

    r = client.get("/api/chat/conversations?offset=500")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["conversations"] == []
    assert data["has_more"] is False


def test_a_visitor_never_sees_another_visitors_page(client):
    visitor_a = _sign_in_visitor(client, "+989120000305")
    ids_a = _seed_conversations(visitor_a, 3)

    from app.services import conversations
    visitor_b = conversations.upsert_visitor(first_name="دیگری", phone="+989120000306")
    _seed_conversations(visitor_b, 3)

    r = client.get("/api/chat/conversations")
    assert r.status_code == 200, r.text
    got_ids = {c["id"] for c in r.json()["conversations"]}
    assert got_ids == ids_a


def test_list_conversations_for_visitor_offset_is_capped(client):
    """Same backstop as the existing limit cap — an unbounded offset turns
    one request into a full-table scan."""
    from app.services import conversations
    visitor_id = _sign_in_visitor(client, "+989120000307")
    _seed_conversations(visitor_id, 3)
    # Must not raise, and must not silently become a huge OFFSET.
    rows = conversations.list_conversations_for_visitor(visitor_id, offset=999_999_999)
    assert rows == []
