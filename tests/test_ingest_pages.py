"""The admin page of the ingest module, as the server renders it
(docs/features/knowledge-ingestion/SPEC.md, REQ-069, REQ-070, REQ-071,
REQ-078, REQ-080, SEC-003, SEC-020, SEC-024, and the kiosk rule of section 9).

What this file protects:

- the page is admin-only (303 to the login page without a session) and exists
  only when the install bought the module: with `ingest` off there is no menu
  link and the path is a 404, so no visible link ever leads to a dead page;
- the page shell carries no document text at all. Everything that came from a
  file, a web page or a model reaches the page later through the API and is
  written as text by static/admin/js/ingest.js (SEC-020), so the script must
  never build markup from strings;
- the fixed sentences a non-technical admin reads are there, and no technical
  word is (REQ-080);
- the admin panel keeps no ingest state in the browser: a shared booth
  browser must not hand the next person the last person's file (section 9).

What the page DOES in a browser is tests/e2e/test_ingest_review.py.
"""
import datetime
import importlib
import re
import secrets
from html.parser import HTMLParser
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
PAGE = "/secure-panel-admin/ingest"
LINK_TEXT = "افزودن دانش از فایل"
PDF_NOTE = "خواندن PDF روی این سرور فعال نیست."
PRIVACY_NOTE = "متن فایل برای پیشنهاد پرسش به سرویس هوش مصنوعی همین نصب فرستاده می‌شود."
TECH_WORDS = re.compile(r"\b(job|jobs|proposal|proposals|chunk|chunks|AI|embedding|embeddings)\b",
                        re.IGNORECASE)


def _session(username="pageadmin"):
    from app.db.connection import get_db_connection
    token = secrets.token_hex(16)
    conn = get_db_connection()
    conn.execute("INSERT OR IGNORE INTO admins (username, password_hash, salt, security_question,"
                 " security_answer_hash) VALUES (?, 'x', 'y', 'q', 'z')", (username,))
    conn.execute("INSERT INTO admin_sessions (token, username, expiry) VALUES (?, ?, ?)",
                 (token, username, (datetime.datetime.now(datetime.timezone.utc)
                                    + datetime.timedelta(hours=2)).isoformat()))
    conn.commit()
    conn.close()
    return token


@pytest.fixture
def app_db(tmp_path, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "ingest-pages.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    yield


@pytest.fixture
def anon(app_db):
    from app.main import app
    with TestClient(app) as c:
        yield c


@pytest.fixture
def client(app_db):
    from app.main import app
    with TestClient(app) as c:
        c.cookies.set("admin_session", _session())
        yield c


class _TextOf(HTMLParser):
    """The text a reader sees inside one element: no script, no style, no tag."""

    def __init__(self, element_id):
        super().__init__()
        self.element_id = element_id
        self.depth = 0
        self.skip = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if self.depth:
            if tag not in ("br", "input", "img", "hr", "meta", "link"):
                self.depth += 1
            if tag in ("script", "style"):
                self.skip += 1
        elif dict(attrs).get("id") == self.element_id:
            self.depth = 1

    def handle_endtag(self, tag):
        if self.depth:
            if tag in ("script", "style"):
                self.skip -= 1
            self.depth -= 1

    def handle_data(self, data):
        if self.depth and not self.skip:
            self.parts.append(data)


def _visible_text(html, element_id="ingest-root") -> str:
    parser = _TextOf(element_id)
    parser.feed(html)
    return " ".join("".join(parser.parts).split())


# ── REQ-069, SEC-001: who reaches the page ───────────────────────────────

def test_without_a_session_the_page_sends_the_visitor_to_the_login(anon):
    response = anon.get(PAGE, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/secure-panel-admin/login"


def test_an_admin_gets_the_page_with_its_script(client):
    response = client.get(PAGE)
    assert response.status_code == 200
    assert 'id="ingest-root"' in response.text
    assert "/static/admin/js/ingest.js" in response.text
    for hook in ("ingest-file", "ingest-url", "ingest-read", "ingest-progress", "ingest-cards",
                 "ingest-approve-seen", "ingest-jobs"):
        assert f'id="{hook}"' in response.text, hook


# ── REQ-070, SEC-003: the menu link follows the module ───────────────────

def test_the_menu_links_to_the_page_with_its_words_and_icon(client):
    sidebar = client.get("/secure-panel-admin").text
    link = re.search(r'<a class="nav-link" href="/secure-panel-admin/ingest">(.*?)</a>', sidebar, re.S)
    assert link, "the ingest page has no menu link: reachable only by typing its address"
    assert "fa-file-import" in link.group(1)
    assert f'nav-link-title">{LINK_TEXT}<' in link.group(1)


def test_the_menu_link_is_marked_active_on_the_page(client):
    page = client.get(PAGE).text
    item = re.search(r'<li class="nav-item ([^"]*)">\s*<a class="nav-link" href="/secure-panel-admin/ingest">',
                     page)
    assert item and "active" in item.group(1)


def test_with_the_module_off_there_is_no_link_and_the_page_is_a_404(app_db, monkeypatch):
    import app.config as config
    import app.main
    from app.modules.registry import CORE_MODULE_NAMES
    previous = config.ENABLED_MODULES
    monkeypatch.setattr(config, "ENABLED_MODULES", CORE_MODULE_NAMES + ["leads"])
    try:
        rebuilt = importlib.reload(app.main).app
        with TestClient(rebuilt) as c:
            c.cookies.set("admin_session", _session())
            sidebar = c.get("/secure-panel-admin").text
            assert "/secure-panel-admin/leads" in sidebar, "the control: an enabled module keeps its link"
            assert "/secure-panel-admin/ingest" not in sidebar
            assert LINK_TEXT not in sidebar
            assert c.get(PAGE, follow_redirects=False).status_code == 404
    finally:
        monkeypatch.setattr(config, "ENABLED_MODULES", previous)
        importlib.reload(app.main)


# ── REQ-071, SEC-024: the fixed sentences ────────────────────────────────

def test_the_input_card_says_what_to_do_and_where_the_text_goes(client):
    text = _visible_text(client.get(PAGE).text)
    for sentence in ("فایل را اینجا بکشید یا انتخاب کنید", "Word، PDF، Excel، CSV، متن",
                     "یا نشانی یک صفحهٔ وب", "بخوان",
                     "برای شناسنامهٔ شرکت‌ها از صفحهٔ شرکت‌ها استفاده کنید.", PRIVACY_NOTE):
        assert sentence in text, sentence


def test_the_pdf_note_shows_only_when_this_server_cannot_read_pdf(client, monkeypatch):
    from app.services import ingest_extract
    monkeypatch.setattr(ingest_extract, "pdf_available", lambda: False)
    assert PDF_NOTE in _visible_text(client.get(PAGE).text)
    monkeypatch.setattr(ingest_extract, "pdf_available", lambda: True)
    assert PDF_NOTE not in client.get(PAGE).text


def test_the_page_shows_no_technical_word(client):
    text = _visible_text(client.get(PAGE).text)
    assert text, "the page root was not found, so this check would pass on nothing"
    assert not TECH_WORDS.search(text), TECH_WORDS.search(text).group(0)


# ── SEC-020 and the kiosk rule: what the script may do ───────────────────

def test_the_script_never_builds_markup_from_strings():
    script = (ROOT / "static" / "admin" / "js" / "ingest.js").read_text(encoding="utf-8")
    for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval("):
        assert sink not in script, f"{sink} would let document text become markup (SEC-020)"


def test_the_script_keeps_nothing_in_the_browser_storage():
    script = (ROOT / "static" / "admin" / "js" / "ingest.js").read_text(encoding="utf-8")
    assert "localStorage" not in script and "sessionStorage" not in script
    assert "fetchAuth" in script, "every request goes through fetchAuth (REQ-079)"
    assert re.search(r"(?<![\w.])fetch\(", script) is None, "a bare fetch() would skip the CSRF header"
