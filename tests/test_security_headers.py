"""Response security headers — and, just as importantly, where they must NOT apply.

The risk with hardening is not that a header is missing; it is that a header is
applied too broadly and quietly breaks something. Two cases matter here:

  * the public chat is meant to be EMBEDDABLE on a customer's own site, so it
    must never carry `X-Frame-Options`;
  * HSTS must not be sent over plain HTTP in development, or a browser pins the
    dev host to HTTPS it does not serve.

Both are asserted below alongside the headers that should be present.
"""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "hdr.db"))
    from app.main import app
    with TestClient(app) as c:
        yield c


PUBLIC = "/"
ADMIN_PAGE = "/secure-panel-inotex/settings/sms"
ADMIN_API = "/admin/api/sms"


# ── Applied everywhere ───────────────────────────────────────────────────

def test_nosniff_on_public_and_admin(client):
    for path in (PUBLIC, ADMIN_PAGE, ADMIN_API):
        r = client.get(path, follow_redirects=False)
        assert r.headers.get("X-Content-Type-Options") == "nosniff", path


def test_referrer_policy_is_set(client):
    r = client.get(PUBLIC)
    assert r.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"


# ── Admin only ───────────────────────────────────────────────────────────

def test_admin_is_frame_denied(client):
    for path in (ADMIN_PAGE, ADMIN_API):
        r = client.get(path, follow_redirects=False)
        assert r.headers.get("X-Frame-Options") == "DENY", path


def test_admin_is_not_cached(client):
    for path in (ADMIN_PAGE, ADMIN_API):
        r = client.get(path, follow_redirects=False)
        assert "no-store" in (r.headers.get("Cache-Control") or ""), path


# ── The public chat must stay embeddable ─────────────────────────────────

def test_public_chat_is_still_framable(client):
    """A customer embeds this in an iframe on their own site. Denying framing
    here would break that with no security benefit — the chat is public."""
    r = client.get(PUBLIC)
    assert "X-Frame-Options" not in r.headers


def test_public_chat_is_not_marked_no_store(client):
    r = client.get(PUBLIC)
    assert "no-store" not in (r.headers.get("Cache-Control") or "")


# ── HSTS is gated on the production marker ───────────────────────────────

def test_no_hsts_when_not_production(client):
    import app.main as main
    assert main.COOKIE_SECURE is False, "this test assumes a dev config"
    r = client.get(PUBLIC)
    assert "Strict-Transport-Security" not in r.headers


def test_hsts_appears_when_cookie_secure_is_on(tmp_path, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "hsts.db"))
    import app.main as main
    monkeypatch.setattr(main, "COOKIE_SECURE", True)
    with TestClient(main.app) as c:
        r = c.get(PUBLIC)
    assert "max-age=" in r.headers.get("Strict-Transport-Security", "")


# ── The headers must not change behaviour ────────────────────────────────

def test_the_chat_still_answers_with_headers_applied(client):
    """The point of the whole exercise: hardening that breaks the product is
    not hardening."""
    page = client.get(PUBLIC).text
    import re
    token = re.search(r'name="chat-token"\s+content="([^"]+)"', page)
    assert token, "no chat token in the page"
    # `localhost` is in ALLOWED_ORIGINS, and validate_request_origin also
    # requires a plausible User-Agent — TestClient's default is too short.
    r = client.post(
        "/chat",
        json={"message": "اینوتکس چیست", "lang": "fa"},
        headers={
            "X-Chat-Token": token.group(1),
            "Origin": "http://localhost",
            "User-Agent": "Mozilla/5.0 (security-headers-test)",
        },
    )
    assert r.status_code == 200, r.text
    assert r.json().get("text", "").strip()
