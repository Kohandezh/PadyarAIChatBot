"""The off-site destination card on Infrastructure > Backups, in a real browser
(SPEC-H2 items 1, 2, 4 and 7).

What this file holds down:

  * the secret is write-only: the page shows "saved", never a value, and the
    secret inputs are empty after every load and every save;
  * one switch picks password or key, and only the chosen secret is sent;
  * a save carries the CSRF header; a refused save shows the server's plain
    sentence and keeps what the operator typed;
  * "Test connection" tests what is SAVED, so it waits while the form has
    unsaved changes, and it waits until a panel destination exists;
  * the test result reads as one sentence, green or red;
  * a phone (375 px) never scrolls sideways.

The real page is rendered once by TestClient with an admin session; the API
is a small fake behind `page.route`, so each state is set up exactly.
Playwright's ASYNC api with this file's own `browser` fixture, never the
pytest-playwright sync fixtures (tests/test_suite_isolation.py is the guard).
"""
import datetime
import json
import mimetypes
import secrets
from pathlib import Path
from urllib.parse import urlsplit

import pytest

ROOT = Path(__file__).resolve().parents[2]
ORIGIN = "http://padyar.test"
PAGE = "/secure-panel-admin/infrastructure/backups"
API = "/admin/api/infra/backups/offsite-settings"
FPR = "SHA256:" + "A" * 43
SAVED = {"host": "backup.example.com", "port": 2222, "user": "backup",
         "path": "/upload/myevent", "auth": "password", "fingerprint": FPR,
         "password_saved": True, "private_key_saved": False, "source": "panel"}
EMPTY = {"host": "", "port": 22, "user": "", "path": "", "auth": "password",
         "fingerprint": "", "password_saved": False, "private_key_saved": False,
         "source": "none"}
TYPED_PASSWORD = "Typed-in-the-form-123"
KEY = "-----BEGIN OPENSSH PRIVATE KEY-----\nAAAA\n-----END OPENSSH PRIVATE KEY-----"
_PAGE_HTML = {}


class FakeApi:
    def __init__(self, html, settings):
        self.html, self.settings, self.requests = html, dict(settings), []
        self.save_answer = None
        self.offsite = None  # the list endpoint's `offsite` view, when a test sets it
        self.test_answer = (200, {"ok": True,
                                  "message": "اتصال برقرار شد و نوشتن فایل آزمایشی موفق بود."})

    def posts(self, path):
        return [r for r in self.requests if r["method"] == "POST" and r["path"] == path]

    async def handle(self, route, request):
        path = urlsplit(request.url).path
        raw = request.post_data_buffer if request.method == "POST" else None
        record = {"method": request.method, "path": path,
                  "body": json.loads(raw) if raw else None,
                  "headers": await request.all_headers()}
        self.requests.append(record)
        if path == PAGE:
            return await route.fulfill(status=200, content_type="text/html", body=self.html)
        if path.startswith("/static/"):
            disk = (ROOT / path.lstrip("/")).resolve()
            if disk.is_file() and ROOT in disk.parents:
                ctype = mimetypes.guess_type(disk.name)[0] or "application/octet-stream"
                return await route.fulfill(status=200, content_type=ctype,
                                           body=disk.read_bytes())
            return await route.fulfill(status=404, body="")
        status, body = 404, {"detail": "not here"}
        if path == "/admin/csrf":
            status, body = 200, {"csrf_token": "t"}
        elif path == "/admin/api/infra/backups":
            status, body = 200, {"backups": [], "schedule": {}, "labels": {},
                                 "offsite": self.offsite or {
                                     "configured": self.settings["source"] != "none",
                                     "state": "none", "attempted_at": None,
                                     "backup_id": None}}
        elif path == API and request.method == "GET":
            status, body = 200, self.settings
        elif path == API and request.method == "POST":
            if self.save_answer:
                status, body = self.save_answer
            else:
                sent = record["body"]
                self.settings = {**self.settings, **{k: sent[k] for k in
                                                     ("host", "user", "path", "auth",
                                                      "fingerprint")},
                                 "port": int(sent["port"] or 22),
                                 "password_saved": self.settings["password_saved"]
                                 or bool(sent.get("password")),
                                 "private_key_saved": self.settings["private_key_saved"]
                                 or bool(sent.get("private_key")),
                                 "source": "panel" if sent["host"] else "none"}
                status, body = 200, self.settings
        elif path == f"{API}/test":
            status, body = self.test_answer
        return await route.fulfill(status=status, content_type="application/json",
                                   body=json.dumps(body, ensure_ascii=False))


def _session():
    from app.db.connection import get_db_connection
    token = secrets.token_hex(16)
    conn = get_db_connection()
    conn.execute("INSERT INTO admin_sessions (token, username, expiry) VALUES (?, ?, ?)",
                 (token, "pageadmin", (datetime.datetime.now(datetime.timezone.utc)
                                       + datetime.timedelta(hours=2)).isoformat()))
    conn.commit()
    conn.close()
    return token


@pytest.fixture
def html(tmp_path, monkeypatch):
    if "html" not in _PAGE_HTML:
        import app.config as config
        monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "offsite-ui.db"))
        monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
        from fastapi.testclient import TestClient

        from app.main import app
        with TestClient(app) as c:
            c.cookies.set("admin_session", _session())
            response = c.get(PAGE)
        assert response.status_code == 200, response.status_code
        _PAGE_HTML["html"] = response.text
    return _PAGE_HTML["html"]


@pytest.fixture
async def browser():
    async_playwright = pytest.importorskip("playwright.async_api").async_playwright
    async with async_playwright() as p:
        try:
            b = await p.chromium.launch()
        except Exception as e:  # noqa: BLE001 (no browser installed is a skip)
            pytest.skip(f"chromium unavailable: {e}")
        yield b
        await b.close()


@pytest.fixture
async def open_page(browser):
    contexts = []

    async def _open(api, viewport=None):
        context = await browser.new_context(viewport=viewport or {"width": 1280, "height": 900})
        contexts.append(context)
        page = await context.new_page()
        await page.route(f"{ORIGIN}/**", api.handle)
        await page.goto(f"{ORIGIN}{PAGE}")
        await page.wait_for_function(
            "document.getElementById('offsite-source').textContent.length > 0", polling=50)
        return page

    yield _open
    for context in contexts:
        await context.close()


async def _text(page, selector):
    return (await page.locator(selector).text_content() or "").strip()


async def test_a_saved_secret_shows_as_saved_and_its_field_stays_empty(html, open_page):
    page = await open_page(FakeApi(html, SAVED))

    assert await page.input_value("#offsite-host") == "backup.example.com"
    assert await page.input_value("#offsite-port") == "2222"
    assert await page.input_value("#offsite-password") == ""
    assert await _text(page, "#offsite-password-state") == "✅ ذخیره شده"
    assert await _text(page, "#offsite-source") == "مقصد در همین صفحه ذخیره شده است."
    assert await page.is_enabled("#offsite-test-btn")
    assert await page.is_visible("#offsite-password")
    assert not await page.is_visible("#offsite-key")


async def test_the_switch_shows_one_secret_field_and_only_that_secret_is_sent(html, open_page):
    api = FakeApi(html, SAVED)
    page = await open_page(api)

    await page.fill("#offsite-password", TYPED_PASSWORD)
    await page.click("label[for=offsite-auth-key]")
    assert await page.is_visible("#offsite-key")
    assert not await page.is_visible("#offsite-password")
    await page.fill("#offsite-key", KEY)
    await page.click("#offsite-save-btn")
    await page.wait_for_function(
        "document.getElementById('offsite-msg').textContent.startsWith('ذخیره شد')", polling=50)

    sent = api.posts(API)[-1]
    assert sent["headers"].get("x-csrf-token") == "t"
    assert sent["body"]["auth"] == "key" and sent["body"]["private_key"] == KEY
    assert "password" not in sent["body"]
    assert await page.input_value("#offsite-key") == ""
    assert await page.input_value("#offsite-password") == ""
    assert await _text(page, "#offsite-key-state") == "✅ ذخیره شده"


async def test_a_refused_save_shows_the_plain_reason_and_keeps_what_was_typed(html, open_page):
    api = FakeApi(html, SAVED)
    api.save_answer = (400, {"detail": "درگاه باید عددی بین ۱ تا ۶۵۵۳۵ باشد."})
    page = await open_page(api)

    await page.fill("#offsite-port", "abc")
    await page.fill("#offsite-password", TYPED_PASSWORD)
    await page.click("#offsite-save-btn")
    await page.wait_for_function(
        "document.getElementById('offsite-msg').textContent.includes('درگاه')", polling=50)

    assert "text-danger" in (await page.get_attribute("#offsite-msg", "class"))
    assert await page.input_value("#offsite-password") == TYPED_PASSWORD
    assert not await page.is_enabled("#offsite-test-btn"), "unsaved changes must block the test"


async def test_the_test_waits_for_a_save_and_says_why(html, open_page):
    api = FakeApi(html, SAVED)
    page = await open_page(api)

    await page.fill("#offsite-user", "someone-else")

    assert not await page.is_enabled("#offsite-test-btn")
    assert await _text(page, "#offsite-test-hint") == \
        "اول «ذخیره» را بزنید، بعد اتصال را آزمایش کنید."
    await page.click("#offsite-save-btn")
    await page.wait_for_function(
        "!document.getElementById('offsite-test-btn').disabled", polling=50)
    assert api.posts(f"{API}/test") == []


@pytest.mark.parametrize("settings,text", [
    (EMPTY, "هنوز مقصدی تنظیم نشده است."),
    ({**EMPTY, "source": "env"},
     "اکنون مقصد از تنظیمات خود سرور خوانده می‌شود. اگر اینجا مقصدی ذخیره کنید، جای آن را می‌گیرد."),
])
async def test_without_a_panel_destination_there_is_nothing_to_test(html, open_page,
                                                                     settings, text):
    page = await open_page(FakeApi(html, settings))

    assert await _text(page, "#offsite-source") == text
    assert not await page.is_enabled("#offsite-test-btn")
    assert await _text(page, "#offsite-test-hint") == \
        "برای آزمایش اتصال، اول مقصد را در همین فرم ذخیره کنید."
    assert await _text(page, "#offsite-password-state") == "هنوز ذخیره نشده"


@pytest.mark.parametrize("ok,message,tone", [
    (True, "اتصال برقرار شد و نوشتن فایل آزمایشی موفق بود.", "text-success"),
    (False, "نام کاربری یا رمز عبور درست نیست.", "text-danger"),
])
async def test_the_test_result_reads_as_one_sentence(html, open_page, ok, message, tone):
    api = FakeApi(html, SAVED)
    api.test_answer = (200, {"ok": ok, "message": message})
    page = await open_page(api)

    await page.click("#offsite-test-btn")
    await page.wait_for_function(
        f"document.getElementById('offsite-msg').textContent === {json.dumps(message)}",
        polling=50)

    assert tone in (await page.get_attribute("#offsite-msg", "class"))
    assert api.posts(f"{API}/test")[-1]["headers"].get("x-csrf-token") == "t"
    assert await page.is_enabled("#offsite-test-btn"), "a second test must be possible"


async def test_a_rate_limited_test_shows_the_servers_sentence(html, open_page):
    api = FakeApi(html, SAVED)
    api.test_answer = (429, {"detail": "چند بار پشت سر هم آزمایش کردید. چند دقیقه صبر کنید و دوباره امتحان کنید."})
    page = await open_page(api)

    await page.click("#offsite-test-btn")
    await page.wait_for_function(
        "document.getElementById('offsite-msg').textContent.startsWith('چند بار')", polling=50)


async def test_a_phone_never_scrolls_sideways(html, open_page):
    page = await open_page(FakeApi(html, SAVED), viewport={"width": 375, "height": 800})
    await page.click("label[for=offsite-auth-key]")
    await page.locator("#offsite-card").scroll_into_view_if_needed()

    width = await page.evaluate("document.scrollingElement.scrollWidth")
    assert width <= 375, width


# ── Review fix 3: "ready" needs the destination AND the encryption ──────

NOT_READY = {"configured": False, "state": "not_ready", "attempted_at": None, "backup_id": None}


async def test_without_working_encryption_the_page_says_not_ready(html, open_page):
    api = FakeApi(html, SAVED)
    api.offsite = NOT_READY
    page = await open_page(api)
    await page.wait_for_function(
        "document.getElementById('offsite-status').textContent.length > 0", polling=50)

    status = await _text(page, "#offsite-status")
    assert "رمزگذاری" in status and "هیچ نسخه‌ای بیرون از سرور نیست" in status
    assert "text-danger" in (await page.get_attribute("#offsite-status", "class"))
    assert await page.is_visible("#offsite-encryption")
    assert "رمزگذاری نسخه‌ها روی خود سرور آماده نیست" in await _text(page, "#offsite-encryption")
    assert "text-success" not in (await page.get_attribute("#offsite-source", "class"))


async def test_with_encryption_ready_there_is_no_encryption_warning(html, open_page):
    api = FakeApi(html, SAVED)
    api.offsite = {"configured": True, "state": "none", "attempted_at": None,
                   "backup_id": None}
    page = await open_page(api)
    await page.wait_for_function(
        "document.getElementById('offsite-status').textContent.length > 0", polling=50)

    assert not await page.is_visible("#offsite-encryption")
    assert "رمزگذاری" not in await _text(page, "#offsite-status")


async def test_the_cards_own_line_never_reads_as_ready(html, open_page):
    """Whether copies can leave the server is the status line's job, which
    knows about encryption. The card only says the destination is saved."""
    page = await open_page(FakeApi(html, SAVED))

    assert "text-success" not in (await page.get_attribute("#offsite-source", "class"))
    assert "تنظیم شده" not in await _text(page, "#offsite-source")
