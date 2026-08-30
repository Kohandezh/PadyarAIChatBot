"""White-label branding — the real `whitelabel_*` keys, end to end.

Covers the whole contract from plans/whitelabel-minimal.md:
  * the admin API roundtrip (defaults → custom save → read-back, rows exist)
  * server-side validation with Persian errors and nothing written on reject
  * branding actually rendered into the cached chat shell (title, header,
    welcome, --wl-* palette, window.PADYAR_BRAND)
  * the page-cache key flipping on save (no stale shell) while the
    per-visitor chat token stays a fresh splice per request
  * defaults rendering the INOTEX look pixel-identical (no logo <img>)
  * escaping: html.escape for text positions, json+`</`-guard for the
    script payload — never html.escape inside <script>
  * the admin page (sidebar name + pre-filled form) and API auth
  * the set_setting cache-pop dependency the cache-key design relies on
  * the active default theme (inotex) actually carrying the branding

Each test runs against a throwaway SQLite DB and logs in by inserting a
real admin session row + CSRF header (same pattern as test_sms_settings).
"""
import datetime
import json
import re
import secrets

import pytest
from fastapi.testclient import TestClient

DEFAULT_NAME = "دستیار پادیار"
DEFAULT_GREETING = "سلام! من دستیار پادیار هستم. درباره نمایشگاه اینوتکس هر سوالی دارید بپرسید."

_TOKEN_RE = re.compile(r'<meta name="chat-token" content="([^"]+)"')


@pytest.fixture
def client(tmp_path, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "branding.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    from app.main import app
    with TestClient(app) as c:
        yield c


def _login(client):
    """Create a real admin session and put its cookie + CSRF on the client."""
    from app.config import ADMIN_COOKIE_NAME
    from app.db.connection import get_db_connection
    token = secrets.token_hex(16)
    expiry = datetime.datetime.now() + datetime.timedelta(hours=1)
    conn = get_db_connection()
    conn.execute(
        'INSERT INTO admin_sessions (token, username, expiry) VALUES (?, ?, ?)',
        (token, "tester", expiry.isoformat()),
    )
    conn.commit()
    conn.close()
    client.cookies.set(ADMIN_COOKIE_NAME, token)
    from app.auth.csrf import token_for_session
    client.headers.update({'X-CSRF-Token': token_for_session(token)})
    return token


def _post_branding(client, **overrides):
    body = {
        "app_name": "دستیار سازمانی",
        "subtitle": "INOTEX",
        "logo_url": "",
        "primary_color": "#123456",
        "accent_color": "#ABCDEF",
        "yellow_light_color": "#FEBE27",
        "navy_color": "#1E2D52",
        "teal_color": "#04A584",
        "dark_teal_color": "#00644F",
        "background_color": "#000000",
        "white_color": "#FFFFFF",
        "welcome_text": "سلام! به سامانهٔ ما خوش آمدید.",
    }
    body.update(overrides)
    return client.post("/admin/api/branding", json=body)


# ── 1. Roundtrip ────────────────────────────────────────────────────────

def test_branding_roundtrip_defaults_save_readback(client):
    _login(client)
    # Fresh DB: the keys come back with their Python defaults.
    r = client.get("/admin/api/branding")
    assert r.status_code == 200
    current = r.json()
    assert current == {
        "whitelabel_app_name": DEFAULT_NAME,
        "whitelabel_subtitle": "INOTEX",
        "whitelabel_logo_url": "",
        "whitelabel_primary_color": "#2D5CA7",
        "whitelabel_accent_color": "#FCB715",
        "whitelabel_yellow_light_color": "#FEBE27",
        "whitelabel_navy_color": "#1E2D52",
        "whitelabel_teal_color": "#04A584",
        "whitelabel_dark_teal_color": "#00644F",
        "whitelabel_background_color": "#000000",
        "whitelabel_white_color": "#FFFFFF",
        "whitelabel_welcome_text": DEFAULT_GREETING,
    }

    r = _post_branding(client, logo_url="/LOGO/x.png", subtitle="نمایشگاه الکامپ",
                       navy_color="#0A0F1E", background_color="#101010")
    assert r.status_code == 200, r.text

    current = client.get("/admin/api/branding").json()
    assert current["whitelabel_app_name"] == "دستیار سازمانی"
    assert current["whitelabel_subtitle"] == "نمایشگاه الکامپ"
    assert current["whitelabel_logo_url"] == "/LOGO/x.png"
    assert current["whitelabel_primary_color"] == "#123456"
    assert current["whitelabel_accent_color"] == "#ABCDEF"
    assert current["whitelabel_navy_color"] == "#0A0F1E"
    assert current["whitelabel_background_color"] == "#101010"
    assert current["whitelabel_welcome_text"] == "سلام! به سامانهٔ ما خوش آمدید."

    # Rows really exist in the settings table — not just the API's defaults.
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    rows = {r_["key"]: r_["value"] for r_ in
            conn.execute("SELECT key, value FROM settings WHERE key LIKE 'whitelabel_%'").fetchall()}
    conn.close()
    assert rows["whitelabel_app_name"] == "دستیار سازمانی"
    assert rows["whitelabel_subtitle"] == "نمایشگاه الکامپ"
    assert len(rows) == 12


# ── 2. Validation ───────────────────────────────────────────────────────

@pytest.mark.parametrize("overrides", [
    {"primary_color": "red"},
    {"accent_color": "#12345"},                 # 5 digits, not 6
    {"navy_color": "navy"},                     # palette colors are hex-only too
    {"teal_color": ""},
    {"logo_url": "javascript:alert(1)"},
    {"logo_url": "//evil.com/x.gif"},           # protocol-relative = external
    {"app_name": "   "},                        # whitespace-only = empty
    {"welcome_text": "x" * 301},
    {"app_name": "x" * 61},
    {"subtitle": "x" * 81},
])
def test_branding_validation_rejects_bad_values(client, overrides):
    _login(client)
    r = _post_branding(client, **overrides)
    assert r.status_code == 400, overrides
    assert r.json()["detail"], "the operator gets a sentence, not a bare 400"
    # Nothing written: the read-back still shows the defaults.
    current = client.get("/admin/api/branding").json()
    assert current["whitelabel_app_name"] == DEFAULT_NAME
    assert current["whitelabel_primary_color"] == "#2D5CA7"


def test_branding_logo_accepts_relative_and_http(client):
    _login(client)
    for ok in ("/LOGO/a.png", "https://cdn.example.com/logo.svg", "http://x/y.png", ""):
        assert _post_branding(client, logo_url=ok).status_code == 200, ok


# ── 3. Chat renders branding ────────────────────────────────────────────

def test_chat_renders_branding(client):
    _login(client)
    name, welcome = "دستیار نمایشگاه", "به نمایشگاه ما خوش آمدید!"
    subtitle = "نمایشگاه الکامپ ۲۰۲۶"
    assert _post_branding(client, app_name=name, primary_color="#0B7285",
                          welcome_text=welcome, subtitle=subtitle).status_code == 200

    html = client.get("/").text
    assert f"<title>{name}</title>" in html
    assert f'data-i18n="app_title">{name}</div>' in html
    assert f'class="header-subtitle">{subtitle}</div>' in html
    assert f'id="welcome-text" dir="auto">{welcome}</div>' in html
    assert "--wl-primary:#0B7285;" in html
    assert "--wl-navy:#1E2D52;" in html  # full palette ships, not just 2 tokens
    # The JS payload mirrors json.dumps(ensure_ascii=True) + the </ guard.
    payload = json.dumps({"app_name": name, "welcome": welcome},
                         ensure_ascii=True).replace("</", "<\\/")
    assert f"window.PADYAR_BRAND={payload};" in html


# ── 4. Cache invalidation ───────────────────────────────────────────────

def test_branding_save_invalidates_page_cache_and_keeps_token_fresh(client):
    _login(client)
    first = client.get("/").text
    assert DEFAULT_NAME in first

    assert _post_branding(client, app_name="دستیار سمینار").status_code == 200

    # Same process, cached module: the very next render already shows the
    # new name — the wl_cache_key extension flipped the cache entry.
    second = client.get("/").text
    assert "دستیار سمینار" in second
    assert DEFAULT_NAME not in second

    # The per-visitor token is still spliced per request, never baked in:
    # two renders of the SAME cached shell carry different tokens.
    t1 = _TOKEN_RE.search(second).group(1)
    t2 = _TOKEN_RE.search(client.get("/").text).group(1)
    assert t1 != t2


# ── 5. Defaults render INOTEX-identical ────────────────────────────────

def test_defaults_render_inotex_identical(client):
    html = client.get("/").text
    assert f"<title>{DEFAULT_NAME}</title>" in html
    assert DEFAULT_GREETING in html
    assert "--wl-primary:#2D5CA7;" in html
    assert "--wl-accent:#FCB715;" in html
    # The default subtitle keeps the pre-key pixels: the header line every
    # theme used to hardcode.
    assert 'class="header-subtitle">INOTEX</div>' in html
    assert "--wl-teal:#04A584;" in html
    # No logo set → no <img>; the built-in SVG mark is what renders.
    assert '<img class="brand-mark"' not in html
    assert 'class="brand-mark"' in html


# ── 6. Escaping ─────────────────────────────────────────────────────────

def test_branding_values_are_escaped_in_chat_html(client):
    _login(client)
    malicious_name = "<script>alert(1)</script>"
    tricky_welcome = 'سلام "عزیز" <خوش‌آمد>'
    malicious_subtitle = '<img src=x onerror="alert(1)">'
    assert _post_branding(client, app_name=malicious_name,
                          welcome_text=tricky_welcome,
                          subtitle=malicious_subtitle).status_code == 200

    html = client.get("/").text
    # The payload can never emit a terminating </script> of its own.
    assert "alert(1)</script>" not in html
    assert r"<\/script>" in html  # the </ guard did the transformation
    # Text positions carry the html.escape'd forms.
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "سلام &quot;عزیز&quot; &lt;خوش‌آمد&gt;" in html
    assert '<img src=x onerror="alert(1)">' not in html
    assert "&lt;img src=x onerror=&quot;alert(1)&quot;&gt;" in html


# ── 7. Admin surface + auth ─────────────────────────────────────────────

def test_admin_branding_page_shows_name_and_prefilled_form(client):
    _login(client)
    assert _post_branding(client, app_name="دستیار سازمانی",
                          welcome_text="پیام جدید",
                          subtitle="سمینار سالانه").status_code == 200
    page = client.get("/secure-panel-inotex/settings/branding")
    assert page.status_code == 200
    # Sidebar carries the install's own name via {{ wl_app_name }}.
    assert "<span>دستیار سازمانی</span>" in page.text
    # The form is server-rendered pre-filled (no JS needed for first paint).
    assert 'value="دستیار سازمانی"' in page.text
    assert 'value="سمینار سالانه"' in page.text
    assert "پیام جدید</textarea>" in page.text
    assert 'href="/secure-panel-inotex/settings/branding"' in page.text


def test_branding_api_requires_admin(client):
    from app.main import app
    with TestClient(app) as anon:
        r = anon.get("/admin/api/branding")
        assert r.status_code in (401, 403)
        r = anon.post("/admin/api/branding", json={"app_name": "x",
                                                   "primary_color": "#111111",
                                                   "accent_color": "#222222"})
        assert r.status_code in (401, 403)


# ── 9. The cache-pop dependency (see plan §3) ───────────────────────────

def test_settings_cache_pop_on_save(client):
    from app.db.queries import get_setting
    # Populate the TTL cache with the pre-save state (row absent → None).
    assert get_setting("whitelabel_app_name") is None
    _login(client)
    assert _post_branding(client, app_name="دستیار سریع").status_code == 200
    # Same process, immediately: the write popped the cached key, so the new
    # value is visible at once. If set_setting ever stops popping, this fails
    # BEFORE test 4 can mask it as a cache-key bug.
    assert get_setting("whitelabel_app_name") == "دستیار سریع"


# ── 10. Active default theme renders branding ───────────────────────────

def test_active_default_theme_renders_branding(client):
    # An accidental theme-default flip (e.g. to haj, which owns its own
    # head.html and would silently drop branding) must fail HERE.
    from app.services.themes import get_active_theme
    assert get_active_theme() == "inotex"
    html = client.get("/").text
    assert "--wl-primary:#2D5CA7;" in html
    assert "window.PADYAR_BRAND=" in html
    assert f"<title>{DEFAULT_NAME}</title>" in html
