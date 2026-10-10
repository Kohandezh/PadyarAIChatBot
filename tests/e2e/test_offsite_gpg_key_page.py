"""The encryption public key part of the off-site card, in a real browser (SPEC-H3).

What this file holds down:

  * with no key the page says so and shows no fingerprint;
  * with a panel key it shows the fingerprint, the key's user id, the date and
    where the key comes from, and offers "remove";
  * a pasted key goes as JSON, a chosen file goes as multipart, and both carry
    the CSRF header;
  * after a save the textarea and the file input are empty (the key text does
    not stay on the page) and the new fingerprint shows;
  * a refusal shows the server's sentence as plain text (no HTML is built from
    it) and empties the paste box and the file input, because a refused key may
    be a private key pasted by mistake on a shared screen;
  * "remove" sends {"clear": true};
  * a phone never scrolls sideways.

The real page is rendered once by TestClient with an admin session. The API is
a small fake behind `page.route`, so each state is set up exactly. Playwright's
ASYNC api with this file's own `browser` fixture, never the pytest-playwright
sync fixtures (tests/test_suite_isolation.py is the guard).
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
GPG = "/admin/api/infra/backups/offsite-gpg"
SFTP = "/admin/api/infra/backups/offsite-settings"
FPR = "AAAA BBBB CCCC DDDD EEEE FFFF 0000 1111 2222 3333"
FPR2 = "9999 8888 7777 6666 5555 4444 3333 2222 1111 0000"
NONE = {"fingerprint": None, "uid": None, "created": None, "source": "none", "ready": False}
PANEL = {"fingerprint": FPR, "uid": "padyar-backup <ops@example.com>",
         "created": "2026-09-30", "source": "panel", "ready": True}
ENV = {**PANEL, "source": "env"}
SFTP_EMPTY = {"host": "", "port": 22, "user": "", "path": "", "auth": "password",
              "fingerprint": "", "password_saved": False, "private_key_saved": False,
              "source": "none"}
ARMORED = ("-----BEGIN PGP PUBLIC KEY BLOCK-----\n\nmQENBAAAAA\n"
           "-----END PGP PUBLIC KEY BLOCK-----")
_PAGE_HTML = {}


class FakeApi:
    def __init__(self, html, gpg):
        self.html, self.gpg, self.requests = html, dict(gpg), []
        self.save_answer = None

    def posts(self, path):
        return [r for r in self.requests if r["method"] == "POST" and r["path"] == path]

    async def handle(self, route, request):
        path = urlsplit(request.url).path
        raw = request.post_data_buffer if request.method == "POST" else None
        self.requests.append({"method": request.method, "path": path, "raw": raw,
                              "headers": await request.all_headers()})
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
                                 "offsite": {"configured": False, "state": "none",
                                             "attempted_at": None, "backup_id": None}}
        elif path == SFTP:
            status, body = 200, SFTP_EMPTY
        elif path == GPG and request.method == "GET":
            status, body = 200, self.gpg
        elif path == GPG and request.method == "POST":
            if self.save_answer:
                status, body = self.save_answer
            elif b'"clear"' in (raw or b""):
                self.gpg = dict(NONE)
                status, body = 200, self.gpg
            else:
                self.gpg = {**PANEL, "fingerprint": FPR2}
                status, body = 200, self.gpg
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
        monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "offsite-gpg-ui.db"))
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
            "document.getElementById('gpg-none').textContent.length > 0"
            " || document.getElementById('gpg-fpr').textContent.length > 0", polling=50)
        return page

    yield _open
    for context in contexts:
        await context.close()


async def _text(page, selector):
    return (await page.locator(selector).text_content() or "").strip()


async def _wait_msg(page, starts_with):
    await page.wait_for_function(
        f"document.getElementById('gpg-msg').textContent.startsWith({json.dumps(starts_with)})",
        polling=50)


async def test_without_a_key_the_page_says_so_and_shows_no_fingerprint(html, open_page):
    page = await open_page(FakeApi(html, NONE))

    assert await _text(page, "#gpg-none") == (
        "هنوز کلید عمومی تنظیم نشده است؛ "
        "تا وقتی نباشد هیچ نسخه‌ای بیرون از سرور فرستاده نمی‌شود.")
    assert not await page.is_visible("#gpg-details")
    assert await _text(page, "#gpg-fpr") == ""
    assert not await page.is_visible("#gpg-clear-btn")


async def test_a_panel_key_shows_its_fingerprint_user_id_date_and_source(html, open_page):
    page = await open_page(FakeApi(html, PANEL))

    assert await _text(page, "#gpg-fpr") == FPR
    assert await page.get_attribute("#gpg-fpr", "dir") == "ltr"
    assert await _text(page, "#gpg-uid") == "padyar-backup <ops@example.com>"
    assert await _text(page, "#gpg-created") == "2026-09-30"
    assert await _text(page, "#gpg-source") == "از پنل"
    assert await _text(page, "#gpg-ready") == "رمزگذاری با این کلید آماده است."
    assert not await page.is_visible("#gpg-none")
    assert await page.is_visible("#gpg-clear-btn")


async def test_a_key_from_the_server_settings_cannot_be_removed_here(html, open_page):
    page = await open_page(FakeApi(html, ENV))

    assert await _text(page, "#gpg-source") == "از تنظیمات سرور"
    assert not await page.is_visible("#gpg-clear-btn")


async def test_a_key_that_cannot_be_used_is_not_called_ready(html, open_page):
    page = await open_page(FakeApi(html, {**PANEL, "ready": False}))

    assert "قابل استفاده نیست" in await _text(page, "#gpg-ready")
    assert "text-danger" in (await page.get_attribute("#gpg-ready", "class"))


async def test_a_pasted_key_is_sent_as_json_with_csrf_and_leaves_no_text_behind(html, open_page):
    api = FakeApi(html, NONE)
    page = await open_page(api)

    await page.fill("#gpg-text", ARMORED)
    await page.click("#gpg-save-btn")
    await _wait_msg(page, "کلید ذخیره شد")

    sent = api.posts(GPG)[-1]
    assert sent["headers"].get("x-csrf-token") == "t"
    assert sent["headers"].get("content-type", "").startswith("application/json")
    assert json.loads(sent["raw"]) == {"public_key": ARMORED}
    assert await page.input_value("#gpg-text") == ""
    assert await _text(page, "#gpg-fpr") == FPR2
    assert await page.is_visible("#gpg-clear-btn")
    assert ARMORED not in await page.content()


async def test_a_chosen_file_is_sent_as_multipart_with_csrf(html, open_page):
    api = FakeApi(html, NONE)
    page = await open_page(api)

    # A buffer, not a path: Playwright does not capture the bytes of a
    # path-backed file in a routed request, so the body would look empty.
    await page.set_input_files("#gpg-file", files=[{
        "name": "backup-public.asc", "mimeType": "application/octet-stream",
        "buffer": ARMORED.encode()}])
    await page.click("#gpg-save-btn")
    await _wait_msg(page, "کلید ذخیره شد")

    sent = api.posts(GPG)[-1]
    assert sent["headers"].get("x-csrf-token") == "t"
    content_type = sent["headers"].get("content-type", "")
    assert content_type.startswith("multipart/form-data; boundary="), content_type
    body = sent["raw"]
    assert b'name="file"' in body and b'filename="backup-public.asc"' in body
    assert ARMORED.encode() in body
    assert await page.eval_on_selector("#gpg-file", "e => e.files.length") == 0
    assert await _text(page, "#gpg-fpr") == FPR2


async def test_saving_with_nothing_chosen_asks_for_a_key_and_sends_nothing(html, open_page):
    api = FakeApi(html, NONE)
    page = await open_page(api)

    await page.click("#gpg-save-btn")
    await _wait_msg(page, "اول متن کلید عمومی")

    assert api.posts(GPG) == []
    assert "text-danger" in (await page.get_attribute("#gpg-msg", "class"))


async def test_a_refusal_shows_the_sentence_as_text_and_builds_no_html(html, open_page):
    api = FakeApi(html, NONE)
    api.save_answer = (400, {"detail": "این کلید رد شد <b>x</b>"})
    page = await open_page(api)

    await page.fill("#gpg-text", ARMORED)
    await page.click("#gpg-save-btn")
    await _wait_msg(page, "این کلید رد شد")

    assert await _text(page, "#gpg-msg") == "این کلید رد شد <b>x</b>"
    assert await page.locator("#gpg-msg b").count() == 0
    assert "text-danger" in (await page.get_attribute("#gpg-msg", "class"))
    # A refused key does not stay on screen: it may be a PRIVATE key pasted by
    # mistake, and this page can be open on a shared screen.
    assert await page.input_value("#gpg-text") == ""
    assert ARMORED not in await page.content()
    assert not await page.is_visible("#gpg-details")


async def test_a_refused_upload_empties_the_file_input(html, open_page):
    api = FakeApi(html, NONE)
    api.save_answer = (400, {"detail": "این یک کلید خصوصی است."})
    page = await open_page(api)

    await page.set_input_files("#gpg-file", files=[{
        "name": "secret.asc", "mimeType": "application/octet-stream",
        "buffer": ARMORED.encode()}])
    await page.click("#gpg-save-btn")
    await _wait_msg(page, "این یک کلید خصوصی است")

    assert await page.eval_on_selector("#gpg-file", "e => e.files.length") == 0
    assert await page.input_value("#gpg-text") == ""


async def test_remove_sends_clear_and_goes_back_to_not_set(html, open_page):
    api = FakeApi(html, PANEL)
    page = await open_page(api)

    await page.click("#gpg-clear-btn")
    await _wait_msg(page, "کلید پاک شد")

    sent = api.posts(GPG)[-1]
    assert sent["headers"].get("x-csrf-token") == "t"
    assert json.loads(sent["raw"]) == {"clear": True}
    assert await page.is_visible("#gpg-none")
    assert not await page.is_visible("#gpg-details")
    assert not await page.is_visible("#gpg-clear-btn")


async def test_the_key_part_works_from_the_keyboard_with_labels(html, open_page):
    page = await open_page(FakeApi(html, NONE))

    assert await _text(page, "label[for=gpg-text]") == "متن کلید عمومی"
    assert await page.get_attribute("#gpg-file", "accept") == ".asc,.gpg"
    assert await page.get_attribute("#gpg-text", "dir") == "ltr"
    await page.focus("#gpg-text")
    await page.keyboard.press("Tab")
    assert await page.evaluate("document.activeElement.id") == "gpg-file"
    await page.keyboard.press("Tab")
    assert await page.evaluate("document.activeElement.id") == "gpg-save-btn"


@pytest.mark.parametrize("gpg", [NONE, PANEL])
async def test_a_phone_never_scrolls_sideways(html, open_page, gpg):
    page = await open_page(FakeApi(html, gpg), viewport={"width": 390, "height": 800})
    await page.locator("#gpg-section").scroll_into_view_if_needed()

    width = await page.evaluate("document.scrollingElement.scrollWidth")
    assert width <= 390, width
