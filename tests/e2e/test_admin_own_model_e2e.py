"""The own-model card on Admin -> AI -> Models, in a real browser.

WHAT THIS FILE HOLDS DOWN
-------------------------
1. Each of the three states reads as one plain sentence a non-technical
   staff member understands: this install has its own trained model / it has
   none yet (and what it needs) / the saved record could not be read and the
   chatbot still answers.
2. An accuracy that was never measured reads as words. Never 0, never NaN:
   both would be false statements to an evaluator reading the card.
3. Every string from the JSON is rendered as text: a `<script>` or an
   `<img onerror>` must show up as characters and run nothing.
4. The technical details are behind a collapsed toggle.
5. The history never hides the model: a history file that cannot be read
   hides only its table, with one plain line; skipped rows get a line too.

The page is the REAL admin page, rendered through TestClient with an admin
session, and the card's JSON is the REAL endpoint's answer over a real
recorded and served model in `tmp_path` for each state. Both are then served
into Chromium by a route handler, so nothing reaches a network.

The one exception is the markup case. The endpoint confirms every shown value
against the serving model, so it can no longer be made to return a
`<script>`; the escaping is a property of the page, so that JSON is the real
trained answer with its strings replaced by hand.

Playwright's ASYNC api, never the `page` fixture pytest-playwright ships. That
fixture is sync, pytest.ini sets `asyncio_mode = auto`, and the sync api
refuses to start inside a running event loop. It also leaves the loop running
on the way out, so every later test that calls `asyncio.run()` fails too. See
tests/test_suite_isolation.py, the guard against it coming back.
"""
import datetime
import json
import mimetypes
import secrets
from pathlib import Path

import pytest

from app.services import intent, search
from tests.test_admin_own_model import MODEL_NAME, _corpus, _record, _write_sidecar

ROOT = Path(__file__).resolve().parents[2]
ORIGIN = "http://padyar.test"
PAGE = "/secure-panel-admin/ai/models"
ENDPOINT = "/admin/api/ai/own-model"

SCRIPT = "<script>window.__pwned = true</script>"
IMG = '<img src="x" onerror="window.__pwned = true">'

STATE_LINE = {
    "trained": "این نصب مدل آموزش‌دیدهٔ خودش را دارد.",
    "none": "این نصب فعلاً مدل آموزش‌دیده ندارد.",
    "unreadable": ("پروندهٔ ذخیره‌شدهٔ مدل خوانده نشد یا با مدلی که الان کار می‌کند"
                   " یکی نیست. چت‌بات همچنان پاسخ می‌دهد."),
    "error": "اطلاعات مدل بارگذاری نشد.",
}


def _admin_client(c):
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    token = secrets.token_hex(16)
    conn.execute("INSERT OR IGNORE INTO admins (username, password_hash,"
                 " salt, security_question, security_answer_hash)"
                 " VALUES ('panel','x','y','q','z')")
    conn.execute("INSERT INTO admin_sessions (token, username, expiry) VALUES (?,?,?)",
                 (token, "panel",
                  (datetime.datetime.now() + datetime.timedelta(hours=1)).isoformat()))
    conn.commit()
    conn.close()
    c.cookies.set("admin_session", token)


def build_page_and_payloads(tmp_path, monkeypatch):
    """(rendered admin page, {case: endpoint JSON}) from the real app.

    Each case records into its own directory, inside the TestClient block:
    the lifespan reindex of the empty test database would otherwise remove
    a record written before it.
    """
    import app.config as config
    monkeypatch.setattr(search, "intent_classifier", None)
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "ui.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    from fastapi.testclient import TestClient
    from app.main import app

    def case(name, prepare):
        monkeypatch.setattr(config, "INTENT_MODEL_DIR", str(tmp_path / name))
        search.intent_classifier = None
        prepare()
        res = c.get(ENDPOINT)
        assert res.status_code == 200, res.status_code
        return res.json()

    def three_versions():
        for i in range(3):
            _record(tag=f"round{i}")

    def unreadable():
        three_versions()
        _write_sidecar("{not json")

    def history_partial():
        three_versions()
        with open(intent.history_path(), "a", encoding="utf-8") as fh:
            fh.write("{half a line\n")

    def not_saved():
        monkeypatch.setattr(config, "INTENT_MODEL_DIR", "")
        search.intent_classifier = intent.fit(*_corpus(), MODEL_NAME)

    def history_unreadable():
        three_versions()
        with open(intent.history_path(), "ab") as fh:
            fh.write(b"\xff\xfe\n")

    with TestClient(app) as c:
        _admin_client(c)
        page = c.get(PAGE)
        assert page.status_code == 200, page.status_code
        assert 'id="own-model-card"' in page.text
        payloads = {
            "trained": case("trained", three_versions),
            "unmeasured": case("unmeasured", lambda: _record(alpha=20, beta=1)),
            "none": case("none", lambda: None),
            "unreadable": case("unreadable", unreadable),
            "history_partial": case("history_partial", history_partial),
            "history_unreadable": case("history_unreadable", history_unreadable),
            "not_saved": case("not_saved", not_saved),
        }
    assert payloads["not_saved"]["state"] == "not_saved"
    assert payloads["trained"]["state"] == "trained"
    assert payloads["unmeasured"]["model"]["holdout_accuracy"] is None
    assert payloads["none"]["state"] == "none"
    assert payloads["unreadable"]["state"] == "unreadable"
    assert payloads["unreadable"]["history"], "the history must survive a bad sidecar"
    assert payloads["history_partial"]["history_status"] == "partial"
    assert payloads["history_unreadable"]["history_status"] == "unreadable"
    assert payloads["history_unreadable"]["state"] == "trained"

    markup = json.loads(json.dumps(payloads["trained"]))
    for field in ("embedding_model_name", "scikit_learn_version", "numpy_version"):
        markup["model"][field] = SCRIPT
    for field in ("embedding_model_revision", "training_fingerprint", "trained_at"):
        markup["model"][field] = IMG
    markup["history"][0]["trained_at"] = SCRIPT
    payloads["markup"] = markup
    return page.text, payloads


def _disk_path(url_path: str):
    url_path = url_path.split("?")[0].lstrip("/")
    candidate = ROOT / url_path
    try:
        candidate = candidate.resolve()
        candidate.relative_to(ROOT)
    except (ValueError, OSError):
        return None
    return candidate if candidate.is_file() else None


async def open_card(browser, html, answer, viewport=None):
    """A fresh context showing the page with `answer` as (status, JSON)."""
    status, payload = answer

    async def handle(route, request):
        path = request.url[len(ORIGIN):].split("?")[0] or "/"
        if path == PAGE:
            return await route.fulfill(status=200, content_type="text/html", body=html)
        if path == ENDPOINT:
            return await route.fulfill(status=status, content_type="application/json",
                                       body=json.dumps(payload))
        if path == "/admin/api/ai/routes":
            return await route.fulfill(status=200, content_type="application/json",
                                       body=json.dumps({"instances": []}))
        if path == "/admin/csrf":
            return await route.fulfill(status=200, content_type="application/json",
                                       body=json.dumps({"csrf_token": "t"}))
        disk = _disk_path(path) if path.startswith("/static/") else None
        if disk is not None:
            ctype = mimetypes.guess_type(disk.name)[0] or "application/octet-stream"
            return await route.fulfill(status=200, content_type=ctype, body=disk.read_bytes())
        return await route.fulfill(status=404, content_type="text/plain", body="")

    context = await browser.new_context(viewport=viewport or {"width": 1280, "height": 900})
    page = await context.new_page()
    await page.route(f"{ORIGIN}/**", handle)
    await page.goto(f"{ORIGIN}{PAGE}")
    await page.wait_for_function(
        "document.getElementById('own-model-card').dataset.state !== 'loading'")
    return context, page


@pytest.fixture
async def browser():
    async_playwright = pytest.importorskip("playwright.async_api").async_playwright
    async with async_playwright() as p:
        try:
            b = await p.chromium.launch()
        except Exception as e:  # noqa: BLE001 — no browser installed is a skip
            pytest.skip(f"chromium unavailable: {e}")
        yield b
        await b.close()


@pytest.fixture
def rendered(tmp_path, monkeypatch):
    return build_page_and_payloads(tmp_path, monkeypatch)


@pytest.fixture
async def show(browser, rendered):
    html, payloads = rendered
    contexts = []

    async def _show(case, status=200, viewport=None):
        context, page = await open_card(browser, html,
                                        (status, payloads.get(case, {})), viewport)
        contexts.append(context)
        return page

    yield _show
    for context in contexts:
        await context.close()


async def _card_text(page) -> str:
    return await page.locator("#own-model-card").inner_text()


async def test_a_trained_model_reads_as_plain_facts(show, rendered):
    page = await show("trained")
    model = rendered[1]["trained"]["model"]
    assert await page.locator("#own-model-card").get_attribute("data-state") == "trained"
    assert await page.locator("#own-model-state").inner_text() == STATE_LINE["trained"]
    text = await _card_text(page)
    score = round(model["holdout_accuracy"] * 100)
    persian = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")
    assert f"از هر ۱۰۰ پرسش حدود {str(score).translate(persian)} پرسش درست" in text, text
    size = str(model["holdout_size"]).translate(persian)
    assert f"برای این آزمون {size} پرسش کنار گذاشته شد" in text, text
    assert "مدلی که الان کار می‌کند بعد از آزمون روی همهٔ پرسش‌ها آموزش دیده است." in text
    assert "ندیده بود" not in text, "the served model was trained on the test questions too"
    assert "۲۸" in text and "NaN" not in text
    rows = await page.locator("#own-model-history tbody tr").count()
    assert rows == 3
    first_version = await page.locator("#own-model-history tbody tr td").first.inner_text()
    assert first_version == "۳", "the history must be newest first"


async def test_the_technical_details_start_collapsed(show):
    page = await show("trained")
    details = page.locator("#own-model-tech")
    assert await details.get_attribute("open") is None
    assert not await page.locator("#own-model-tech code").first.is_visible()
    await page.locator("#own-model-tech summary").click()
    codes = await page.locator("#own-model-tech code").all_inner_texts()
    assert any(len(c) == 64 and set(c) <= set("0123456789abcdef") for c in codes), codes


async def test_values_from_the_json_render_as_text_and_run_nothing(show):
    page = await show("markup")
    await page.locator("#own-model-tech summary").click()
    codes = await page.locator("#own-model-tech code").all_inner_texts()
    assert codes.count(SCRIPT) == 3, codes
    assert codes.count(IMG) == 2, codes
    facts = await page.locator(".datagrid").inner_text()
    assert IMG in facts, facts
    assert SCRIPT in await page.locator("#own-model-history tbody").inner_text()
    assert await page.locator("#own-model-card script, #own-model-card img").count() == 0
    assert await page.evaluate("window.__pwned === undefined")


async def test_an_unmeasured_accuracy_reads_as_words_not_zero(show):
    page = await show("unmeasured")
    assert await page.locator("#own-model-state").inner_text() == STATE_LINE["trained"]
    accuracy = await page.locator("#own-model-accuracy").inner_text()
    assert accuracy == "دقت اندازه گرفته نشد، چون پرسش کافی برای آزمون نبود."
    text = await _card_text(page)
    for wrong in ("NaN", "۰ از ۱۰۰", "0%", "٪", "حدود ۰", "undefined", "null"):
        assert wrong not in text, wrong
    assert "اندازه گرفته نشد" in await page.locator("#own-model-history tbody").inner_text()


async def test_an_install_that_keeps_no_record_shows_its_model_without_a_warning(show):
    page = await show("not_saved")
    assert await page.locator("#own-model-card").get_attribute("data-state") == "not_saved"
    assert await page.locator("#own-model-state").inner_text() == STATE_LINE["trained"]
    note = await page.locator("#own-model-not-saved").inner_text()
    assert note == "این نصب نسخه‌ای از مدل را روی دیسک نگه نمی‌دارد، پس شمارهٔ نسخه و تاریخچه ندارد."
    text = await _card_text(page)
    assert "۲۸" in text
    assert "از هر ۱۰۰ پرسش حدود" in text
    for wrong in ("خوانده نشد", "NaN", "undefined", "null", "—"):
        assert wrong not in text, wrong
    assert await page.locator("#own-model-history").count() == 0
    await page.locator("#own-model-tech summary").click()
    codes = await page.locator("#own-model-tech code").all_inner_texts()
    assert "" not in codes, codes
    assert MODEL_NAME in codes


async def test_no_model_says_so_and_what_it_needs(show):
    page = await show("none")
    assert await page.locator("#own-model-card").get_attribute("data-state") == "none"
    assert await page.locator("#own-model-state").inner_text() == STATE_LINE["none"]
    text = await _card_text(page)
    assert "دست‌کم ۲۰ پرسش" in text and "۲ موضوع" in text, text
    # The cause may be the embedder, not the data: advice to add questions
    # would then be false.
    assert "با افزودن پرسش" not in text, text
    assert await page.locator("#own-model-tech").count() == 0


async def test_an_unreadable_record_says_the_chatbot_still_answers(show):
    page = await show("unreadable")
    assert await page.locator("#own-model-card").get_attribute("data-state") == "unreadable"
    assert await page.locator("#own-model-state").inner_text() == STATE_LINE["unreadable"]
    for fact in ("#own-model-accuracy", ".datagrid", "#own-model-tech"):
        assert await page.locator(fact).count() == 0, fact
    assert await page.locator("#own-model-history tbody tr").count() == 3


async def test_skipped_history_rows_get_one_line_under_the_table(show):
    page = await show("history_partial")
    assert await page.locator("#own-model-state").inner_text() == STATE_LINE["trained"]
    assert await page.locator("#own-model-history tbody tr").count() == 3
    note = await page.locator("#own-model-history-note").inner_text()
    assert note == "بعضی خط‌های تاریخچه خوانده نشد و اینجا نیامده است."


async def test_an_unreadable_history_hides_only_the_table(show):
    page = await show("history_unreadable")
    assert await page.locator("#own-model-state").inner_text() == STATE_LINE["trained"]
    assert await page.locator("#own-model-accuracy").count() == 1
    assert await page.locator(".datagrid").count() == 1
    assert await page.locator("#own-model-history").count() == 0
    note = await page.locator("#own-model-history-note").inner_text()
    assert note == "تاریخچهٔ نسخه‌ها خوانده نشد."


async def test_a_failed_request_says_it_could_not_load(show):
    # 500, not 401: fetchAuth() sends a 401 to the login page, which is right.
    page = await show("anything", status=500)
    assert await page.locator("#own-model-state").inner_text() == STATE_LINE["error"]


async def test_the_card_fits_a_phone_screen(show):
    page = await show("trained", viewport={"width": 375, "height": 800})
    await page.locator("#own-model-tech summary").click()
    overflow = await page.evaluate(
        "(() => { const c = document.getElementById('own-model-card');"
        " return c.scrollWidth - c.clientWidth; })()")
    assert overflow <= 1, overflow
