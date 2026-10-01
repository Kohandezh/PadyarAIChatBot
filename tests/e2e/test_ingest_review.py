"""The ingest admin page in a real browser (docs/features/knowledge-ingestion/SPEC.md,
section F, section 10, SEC-020, SC-019..SC-021).

WHAT THIS FILE HOLDS DOWN
-------------------------
1. Text from a document, a web page or a model is shown as text. A `<script>`,
   an `<img onerror>` or any HTML in a proposal or a file name shows up as
   characters, creates no element and runs nothing (SEC-020, SC-019).
2. "Seen" means seen: a card is reported only after it stood at least half in
   view for one second. Fetching a page of cards, a short glance, or a sliver
   at the bottom edge reports nothing (REQ-074, SC-020). "Approve all seen"
   counts only those and is disabled at zero (REQ-075).
3. The admin always knows where the file is: the progress sentence, the
   polling that stops once the work is over, a cancel that asks first
   (REQ-072), and after an approval the wait until the answer is live, or the
   honest sentence after 150 seconds (REQ-082).
4. Editing happens in the card, a refusal lands beside the field it names, and
   only changed fields are sent (REQ-076). Every change carries the CSRF
   header (REQ-079).
5. Every UX state of section 10 exists, a phone (375 px) never scrolls
   sideways, and a wide screen shows the source beside the proposal (SC-021).
   No technical word and no error code ever reaches the screen (REQ-080).
6. The whole thing works against the REAL app: a real upload of a committed
   DOCX through the real routes into a real SQLite file, the real extraction
   child, the real model stage with only `padyar_ai.generate` replaced, cards
   scrolled into view, approve-all-seen, and rows that are really in the
   knowledge base afterwards.

Points 1 to 5 run the REAL page (rendered once by TestClient with an admin
session) against a small recorded fake of the S3 API served by `page.route`,
so a 150-second wait or a 3-second poll is driven by `page.clock`, not slept.
Point 6 starts the real app on a local uvicorn server instead.

Playwright's ASYNC api with this file's own `browser` fixture, never the
fixtures pytest-playwright ships: their sync driver poisons the event loop of
every later test (tests/test_suite_isolation.py is the guard).
"""
import asyncio
import datetime
import json
import mimetypes
import re
import secrets
import socket
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from app.services.ingest import FIELD_MESSAGES

ROOT = Path(__file__).resolve().parents[2]
ORIGIN = "http://padyar.test"
PAGE = "/secure-panel-admin/ingest"
API = "/admin/api/ingest"
DOCX = ROOT / "tests" / "fixtures" / "ingest" / "fa-sample.docx"
T0 = datetime.datetime(2026, 10, 1, 9, 0, 0, tzinfo=datetime.timezone.utc)

SCRIPT = "<script>window.__pwned = true</script>"
IMG = '<img src="x" onerror="window.__pwned = true">'
BOLD = "<b>درشت</b>"

READING = "در حال خواندن فایل…"
SENDING = "در حال فرستادن…"
CANCEL_QUESTION = "کار این فایل متوقف شود؟ پیشنهادهای بررسی‌نشده حذف می‌شوند."
UPDATING = "تأیید شد؛ در حال به‌روزرسانی پاسخ‌ها…"
LIVE = "از همین حالا در پاسخ‌ها است"
SLOW = "پاسخ ذخیره شد و به‌زودی در چت دیده می‌شود."
ALL_REVIEWED = "همهٔ پیشنهادهای این فایل بررسی شده‌اند."
NO_FILES = "هنوز فایلی نفرستاده‌اید."
SEEN_HINT = "اول پیشنهادها را ببینید"
WAIT_NOTE = "در حال آماده شدن"
RETRY = "فایل دیگری بفرستید"
BAD_TYPE = "این نوع فایل پشتیبانی نمی‌شود. فایل Word، PDF، Excel، CSV یا متن بفرستید."
ALREADY_REVIEWED = "این پیشنهاد قبلاً بررسی شده است."
QUESTIONS_REFUSED = FIELD_MESSAGES["questions"]
TECH_WORDS = re.compile(r"\b(job|jobs|proposal|proposals|chunk|chunks|AI|embedding|embeddings)\b",
                        re.IGNORECASE)
CODES = ("bad_type", "invalid_field", "already_reviewed", "interrupted", "too_large", "duplicate")
REVIEWABLE = ("done", "failed", "local")
_PAGE_HTML = {}


def _fa(n) -> str:
    return str(n).translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹"))


def _job(**over):
    job = {"id": "job-1", "source_kind": "file", "source_name": "راهنمای نمایشگاه.docx", "format": "docx",
           "status": "ready", "error_code": "", "error_message": "", "encoding_note": "",
           "chunk_count": 3, "ai_stopped_at": None, "created_by": "admin",
           "created_at": "2026-10-01T08:00:00+00:00", "finished_at": None}
    job.update(over)
    return job


def _proposal(i, **over):
    proposal = {"id": f"p{i}", "job_id": "job-1", "seq": i,
                "source_text": f"نمایشگاه هر روز باز است و بازدیدکننده‌ها می‌توانند بلیت را بخرند. بخش {_fa(i)}.",
                "heading": f"بخش {_fa(i)}", "title": f"ساعت‌های بازدید {_fa(i)}", "title_source": "heading",
                "text": f"نمایشگاه هر روز باز است و بازدیدکننده‌ها می‌توانند بلیت را بخرند. بخش {_fa(i)}.",
                "questions": ["نمایشگاه کی باز است؟"], "synonyms": [{"word": "بلیت", "suggestion": "بلیط"}],
                "ai_state": "done", "similar_kind": "", "similar_to": "", "similar_title": "",
                "same_title_as": "", "status": "pending", "edited": False, "seen": False,
                "reviewed_by": "", "reviewed_at": None, "dataset_id": ""}
    proposal.update(over)
    return proposal


class FakeApi:
    """The S3 API as the page sees it, and the record of what the page sent.

    The test changes `job`, `version` or `proposals` between clock steps, the
    way the real job moves on its own. `answers[(method, path)]` replaces the
    default answer with (status, body) items, served in order, the last one
    repeated. `gates[(method, path)]` holds a request until the test sets it.
    """

    def __init__(self, html, job=None, jobs=None, proposals=()):
        self.html = html
        self.job = job
        self.jobs = list(jobs if jobs is not None else ([job] if job else []))
        self.proposals = [dict(p) for p in proposals]
        self.version = 5
        self.requests = []
        self.answers = {}
        self.gates = {}

    def sent(self, method, path=None):
        return [r for r in self.requests if r["method"] == method and (path is None or r["path"] == path)]

    def mutations(self):
        return [r for r in self.requests if r["method"] in ("POST", "PUT", "PATCH", "DELETE")]

    def _counts(self):
        pending = [p for p in self.proposals if p["status"] == "pending"]
        return {"chunk_count": self.job["chunk_count"],
                "ai_done": self.job.get("_ai_done",
                                        sum(p["ai_state"] in REVIEWABLE for p in self.proposals)),
                "pending": len(pending),
                "approved": sum(p["status"] == "approved" for p in self.proposals),
                "rejected": sum(p["status"] == "rejected" for p in self.proposals),
                "seen_pending": sum(p["seen"] for p in pending),
                "approvable_seen": sum(p["seen"] and not p["similar_kind"] and p["ai_state"] in REVIEWABLE
                                       for p in pending)}

    def _public_job(self):
        return {k: v for k, v in self.job.items() if not k.startswith("_")}

    def _find(self, proposal_id):
        return next(p for p in self.proposals if p["id"] == proposal_id)

    def _default(self, method, path, query, body):
        if path == "/admin/csrf":
            return 200, {"csrf_token": "t"}
        if method == "GET" and path == f"{API}/jobs":
            offset, limit = int(query.get("offset", 0)), int(query.get("limit", 20))
            return 200, {"items": self.jobs[offset:offset + limit], "total": len(self.jobs)}
        if method == "GET" and path == f"{API}/index-status":
            return 200, {"version": self.version}
        if method == "POST" and path in (f"{API}/jobs/upload", f"{API}/jobs/url"):
            return 202, {"job": self._public_job()}
        if method == "POST" and path == f"{API}/proposals/seen":
            marked = 0
            for p in self.proposals:
                if p["id"] in body["ids"] and p["status"] == "pending" and not p["seen"]:
                    p["seen"], marked = True, marked + 1
            return 200, {"marked": marked}
        match = re.fullmatch(rf"{API}/jobs/([^/]+)(/[a-z-]+)?", path)
        if match:
            tail = match.group(2) or ""
            if method == "GET" and not tail:
                return 200, {"job": self._public_job(), "counts": self._counts()}
            if method == "GET" and tail == "/proposals":
                wanted = query.get("status", "pending")
                items = [p for p in self.proposals if wanted == "all" or p["status"] == wanted]
                offset, limit = int(query.get("offset", 0)), int(query.get("limit", 20))
                return 200, {"items": items[offset:offset + limit], "total": len(items)}
            if method == "POST" and tail == "/cancel":
                self.job["status"] = "cancelling" if self.job["status"] == "proposing" else "cancelled"
                for p in self.proposals:
                    if p["status"] == "pending":
                        p["status"] = "rejected"
                return 200, {"job": self._public_job()}
            if method == "POST" and tail == "/approve-seen":
                ready = [p for p in self.proposals if p["status"] == "pending" and p["seen"]
                         and not p["similar_kind"] and p["ai_state"] in REVIEWABLE]
                for p in ready:
                    p["status"] = "approved"
                return 200, {"approved": len(ready), "skipped": [], "remaining": 0,
                             "index_version_before": self.version}
        match = re.fullmatch(rf"{API}/proposals/([^/]+)(/[a-z]+)?", path)
        if match:
            proposal, tail = self._find(match.group(1)), match.group(2) or ""
            if method == "PUT" and not tail:
                proposal.update(body)
                proposal["edited"] = proposal["seen"] = True
                return 200, {"proposal": proposal}
            if method == "POST" and tail == "/approve":
                proposal["status"], proposal["seen"] = "approved", True
                return 200, {"proposal": proposal, "dataset_id": "ing-0000000001",
                             "index_version_before": self.version}
            if method == "POST" and tail == "/reject":
                proposal["status"] = "rejected"
                return 200, {"proposal": proposal}
        return 404, {"detail": "not here"}

    async def handle(self, route, request):
        url = urlsplit(request.url)
        path, query = url.path, {k: v[0] for k, v in parse_qs(url.query).items()}
        raw = request.post_data_buffer if request.method in ("POST", "PUT") else None
        record = {"method": request.method, "path": path, "query": query, "headers": {},
                  "raw": raw or b"", "body": None}
        # Recorded before the first await, so the log keeps the order the
        # page sent in, which `_barrier` relies on.
        self.requests.append(record)
        try:
            record["body"] = json.loads(raw) if raw else None
        except ValueError:
            pass
        record["headers"] = await request.all_headers()
        if path == PAGE:
            return await route.fulfill(status=200, content_type="text/html", body=self.html)
        if path.startswith("/static/"):
            disk = _disk_path(path)
            if disk is not None:
                ctype = mimetypes.guess_type(disk.name)[0] or "application/octet-stream"
                return await route.fulfill(status=200, content_type=ctype, body=disk.read_bytes())
            return await route.fulfill(status=404, content_type="text/plain", body="")
        gate = self.gates.get((request.method, path))
        if gate is not None:
            await gate.wait()
        queued = self.answers.get((request.method, path))
        if queued:
            status, body = queued.pop(0) if len(queued) > 1 else queued[0]
        else:
            status, body = self._default(request.method, path, query, record["body"])
        return await route.fulfill(status=status, content_type="application/json",
                                   body=json.dumps(body, ensure_ascii=False))


def _disk_path(url_path: str):
    candidate = ROOT / url_path.split("?")[0].lstrip("/")
    try:
        candidate = candidate.resolve()
        candidate.relative_to(ROOT)
    except (ValueError, OSError):
        return None
    return candidate if candidate.is_file() else None


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


def _render_page(tmp_path, monkeypatch) -> str:
    """The real admin page, rendered once: the shell carries no data, so every
    test can share it."""
    if "html" not in _PAGE_HTML:
        import app.config as config
        monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "ingest-ui.db"))
        monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
        from fastapi.testclient import TestClient
        from app.main import app
        with TestClient(app) as c:
            c.cookies.set("admin_session", _session())
            response = c.get(PAGE)
        assert response.status_code == 200, response.status_code
        _PAGE_HTML["html"] = response.text
    return _PAGE_HTML["html"]


async def _frame(page):
    """Wait for the browser's next intersection update. The page's observer
    was created first, so its callback has run when this one resolves."""
    await page.evaluate("() => new Promise(done => new IntersectionObserver((_, o) => {"
                        " o.disconnect(); done(); }).observe(document.body))")


async def _barrier(page):
    """One request of our own, answered. Everything the page sent before it is
    in the log by then, so a count taken after this is complete."""
    await page.evaluate("() => fetch('/__barrier__').then(r => r.status)")


async def _advance_until(page, condition, step_ms=2000, steps=5):
    """Move the paused clock in steps until `condition` holds. A poll that
    waits for its answer before it sets the next timer cannot be driven by one
    big jump."""
    for _ in range(steps):
        await page.clock.run_for(step_ms)
        await _barrier(page)
        if await page.evaluate(condition):
            return
    raise AssertionError(f"never became true: {condition}")


# Bootstrap turns on smooth scrolling, which never moves while page.clock is
# paused: every scroll here is instant.
async def _center(page, selector):
    await page.evaluate("s => document.querySelector(s).scrollIntoView({block: 'center', behavior: 'instant'})",
                        selector)
    await _frame(page)


async def _to_top(page):
    await page.evaluate("() => window.scrollTo({top: 0, behavior: 'instant'})")
    await _frame(page)


async def _in_view(page, proposal_id) -> float:
    """The share of the card inside the viewport, so a step that should not
    count as seen is known to have shown the card at all."""
    return await page.evaluate("""id => {
        const r = document.querySelector(`.ingest-card[data-id="${id}"]`).getBoundingClientRect();
        return Math.max(0, Math.min(r.bottom, innerHeight) - Math.max(r.top, 0)) / r.height;
    }""", proposal_id)


async def _text(page, selector="#ingest-root"):
    return " ".join((await page.locator(selector).inner_text()).split())


def _card(page, proposal_id):
    return page.locator(f'.ingest-card[data-id="{proposal_id}"]')


def _seen_ids(api):
    return [i for r in api.sent("POST", f"{API}/proposals/seen") for i in r["body"]["ids"]]


def _assert_plain(text):
    found = TECH_WORDS.search(text)
    assert found is None, f"a technical word reached the screen: {found.group(0)}"
    for code in CODES:
        assert code not in text, code
    for leak in ("undefined", "null", "NaN", "[object"):
        assert leak not in text, leak


def _assert_csrf(api):
    mutations = api.mutations()
    assert mutations, "the test made no change, so this check would pass on nothing"
    for request in mutations:
        assert request["headers"].get("x-csrf-token") == "t", (request["method"], request["path"])


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
def html(tmp_path, monkeypatch):
    return _render_page(tmp_path, monkeypatch)


@pytest.fixture
async def open_page(browser):
    contexts = []

    async def _open(api, query="", viewport=None, clock=False):
        context = await browser.new_context(viewport=viewport or {"width": 1280, "height": 900})
        contexts.append(context)
        page = await context.new_page()
        await page.route(f"{ORIGIN}/**", api.handle)
        if clock:
            await page.clock.install(time=T0)
            await page.clock.pause_at(T0 + datetime.timedelta(seconds=1))
        await page.goto(f"{ORIGIN}{PAGE}{query}")
        await page.wait_for_function("document.getElementById('ingest-root').dataset.ready === '1'",
                                     polling=50)
        return page

    yield _open
    for context in contexts:
        await context.close()


# ── SEC-020, SC-019: text stays text ─────────────────────────────────────

async def test_markup_from_a_document_shows_as_text_and_runs_nothing(html, open_page):
    hostile = _proposal(1, source_text=f"{IMG} متن اصلی {BOLD}", title=SCRIPT, text=f"{BOLD} {IMG}",
                        questions=[IMG, f"{SCRIPT} چیست؟"],
                        synonyms=[{"word": IMG, "suggestion": BOLD}],
                        similar_kind="dataset", similar_to="d1", similar_title=IMG)
    company = _proposal(2, similar_kind="company", similar_to="c1", similar_title=SCRIPT,
                        title_source="model", same_title_as="d9", title=BOLD)
    normal = _proposal(3, title="برای شرکت‌ها می‌توانید فرم را پر کنید")
    job = _job(source_name=SCRIPT)
    api = FakeApi(html, job=job, jobs=[job, _job(id="job-2", source_name=IMG, status="done")],
                  proposals=[hostile, company, normal])
    page = await open_page(api, "?job=job-1")

    root = await _text(page)
    assert SCRIPT in await _text(page, "#ingest-progress"), "the file name is shown as text"
    assert IMG in await _text(page, "#ingest-jobs")
    card = await _text(page, '.ingest-card[data-id="p1"]')
    for shown in (IMG, BOLD, SCRIPT, f"{SCRIPT} چیست؟", f"شبیه یک پاسخ موجود است: {IMG}"):
        assert shown in card, shown
    assert f"به نظر شناسنامهٔ شرکت {SCRIPT} است؛ جایش صفحهٔ شرکت‌ها است." in root
    assert "برای شرکت‌ها می‌توانید فرم را پر کنید" in root, "normal Persian, half-spaces included, is untouched"
    assert await page.locator("#ingest-root img, #ingest-root script, #ingest-root b").count() == 0
    assert await page.evaluate("window.__pwned === undefined")
    approve = _card(page, "p1").locator(".ingest-approve")
    assert await approve.get_attribute("aria-label") == f"تأیید پیشنهاد: {SCRIPT}"


# ── REQ-074, REQ-075, SC-020: seen means seen ────────────────────────────

async def test_a_card_counts_as_seen_only_after_a_second_at_least_half_in_view(html, open_page):
    long_text = " ".join(["نمایشگاه هر روز از صبح تا عصر باز است و بازدیدکننده‌ها بلیت را در ورودی می‌خرند."] * 9)
    proposals = [_proposal(i, source_text=long_text) for i in range(1, 21)]
    api = FakeApi(html, job=_job(chunk_count=20), proposals=proposals)
    page = await open_page(api, "?job=job-1", viewport={"width": 1280, "height": 520}, clock=True)
    assert await page.locator(".ingest-card").count() == 20

    await page.clock.run_for(5000)
    assert _seen_ids(api) == [], "fetching a page of cards must mark none of them"

    assert await _in_view(page, "p1") == 0
    await _center(page, '.ingest-card[data-id="p1"]')
    assert await _in_view(page, "p1") == 1
    await page.clock.run_for(500)
    await _to_top(page)
    await page.clock.run_for(5000)
    assert _seen_ids(api) == [], "half a second is a glance, not a look"

    await page.evaluate("""() => {
        const card = document.querySelector('.ingest-card[data-id="p1"]');
        window.scrollBy({top: card.getBoundingClientRect().top - window.innerHeight + 120, behavior: 'instant'});
    }""")
    await _frame(page)
    assert 0.2 < await _in_view(page, "p1") < 0.5
    await page.clock.run_for(4000)
    assert _seen_ids(api) == [], "less than half the card is not a look"

    await _center(page, '.ingest-card[data-id="p2"]')
    assert await _in_view(page, "p2") == 1
    assert await _in_view(page, "p1") < 0.5 and await _in_view(page, "p3") < 0.5
    async with page.expect_request(lambda r: r.url.endswith(f"{API}/proposals/seen")):
        await page.clock.run_for(1100)
        await page.clock.run_for(2100)
    await page.wait_for_function(
        "document.getElementById('ingest-approve-seen').textContent.includes('(۱)')", polling=50)
    assert _seen_ids(api) == ["p2"], "only the card that was really looked at is reported"
    assert await page.locator("#ingest-approve-seen").is_enabled()
    _assert_csrf(api)


async def test_approve_all_seen_counts_only_seen_unlabelled_cards(html, open_page):
    proposals = [_proposal(1, seen=True), _proposal(2, seen=True, similar_kind="company", similar_title="آلفا"),
                 _proposal(3), _proposal(4, seen=True)]
    api = FakeApi(html, job=_job(chunk_count=4), proposals=proposals)
    page = await open_page(api, "?job=job-1")
    button = page.locator("#ingest-approve-seen")
    assert "(۲)" in await button.inner_text()

    # The server approves p1 and skips p4 (a duplicate found at approve
    # time), so after the click one seen card is still waiting.
    api.answers[("POST", f"{API}/jobs/job-1/approve-seen")] = [
        (200, {"approved": 1, "skipped": [{"id": "p4", "reason": "duplicate"}], "remaining": 1,
               "index_version_before": 5})]
    api.proposals[0]["status"] = "approved"
    await button.click()
    await page.wait_for_function("document.getElementById('ingest-bulk-msg').textContent.includes('۱ پیشنهاد')",
                                 polling=50)
    message = await _text(page, "#ingest-bulk-msg")
    assert "۱ پیشنهاد تأیید شد." in message, message
    assert "۱ مورد تأیید نشد" in message, message
    await page.wait_for_function(
        "document.getElementById('ingest-approve-seen').textContent.includes('(۱)')", polling=50)
    assert await button.is_enabled(), "remaining > 0 keeps the same button ready for the rest"
    _assert_csrf(api)


async def test_with_nothing_seen_approve_all_is_disabled_and_says_why(html, open_page):
    api = FakeApi(html, job=_job(), proposals=[_proposal(1), _proposal(2, seen=True, similar_kind="duplicate")])
    page = await open_page(api, "?job=job-1", viewport={"width": 1280, "height": 400}, clock=True)
    button = page.locator("#ingest-approve-seen")
    assert "(۰)" in await button.inner_text()
    assert await button.is_disabled()
    hint = page.locator("#ingest-approve-seen-hint")
    assert await hint.is_visible() and (await hint.inner_text()).strip() == SEEN_HINT
    assert await button.get_attribute("aria-describedby") == "ingest-approve-seen-hint"


# ── REQ-072: progress, polling, cancel ───────────────────────────────────

async def test_progress_reads_as_sentences_and_polling_stops_when_the_work_is_over(html, open_page):
    api = FakeApi(html, job=_job(status="extracting", chunk_count=0))
    page = await open_page(api, "?job=job-1", clock=True)
    assert await page.locator("#ingest-progress").get_attribute("aria-live") == "polite"
    assert READING in await _text(page, "#ingest-progress")

    api.job.update(status="proposing", chunk_count=85, _ai_done=12)
    await page.clock.run_for(3000)
    await page.wait_for_function(
        "document.getElementById('ingest-stage').textContent.includes('۱۲ از ۸۵')", polling=50)
    assert "در حال آماده کردن پیشنهادها: ۱۲ از ۸۵" in await _text(page, "#ingest-stage")
    bar = page.locator("#ingest-bar")
    assert await bar.get_attribute("aria-valuenow") == "14"

    api.job.update(status="ready", ai_stopped_at=3)
    await page.clock.run_for(3000)
    await page.wait_for_function("document.getElementById('ingest-running').hidden", polling=50)
    assert "هوش مصنوعی در بخش ۳ متوقف شد؛ بقیهٔ پیشنهادها بدون پرسش آماده شدند." in await _text(page)
    await _barrier(page)
    reads = len(api.sent("GET", f"{API}/jobs/job-1"))
    await page.clock.run_for(15000)
    await _barrier(page)
    assert len(api.sent("GET", f"{API}/jobs/job-1")) == reads, "a finished file must not be read again"


async def test_a_failed_read_while_the_file_is_being_read_is_retried_quietly(html, open_page):
    api = FakeApi(html, job=_job(status="extracting", chunk_count=0))
    page = await open_page(api, "?job=job-1", clock=True)
    api.answers[("GET", f"{API}/jobs/job-1")] = [(502, "Bad Gateway")]
    await page.clock.run_for(3000)
    await _barrier(page)
    assert await page.locator("#ingest-alert").is_hidden(), "one lost answer is not an error to show"
    api.answers.pop(("GET", f"{API}/jobs/job-1"))
    api.job.update(status="ready", chunk_count=1)
    await _advance_until(page, "document.getElementById('ingest-running').hidden", step_ms=3000)
    assert await page.locator("#ingest-alert").is_hidden()


async def test_cancel_asks_first_and_only_a_yes_stops_the_work(html, open_page):
    api = FakeApi(html, job=_job(status="extracting", chunk_count=0))
    page = await open_page(api, "?job=job-1", clock=True)
    questions = []

    async def say_no(dialog):
        questions.append(dialog.message)
        await dialog.dismiss()
    page.once("dialog", say_no)
    await page.locator("#ingest-cancel").click()
    assert questions == [CANCEL_QUESTION]
    assert api.sent("POST", f"{API}/jobs/job-1/cancel") == []

    page.once("dialog", lambda dialog: asyncio.ensure_future(dialog.accept()))
    await page.locator("#ingest-cancel").click()
    await page.wait_for_function("document.getElementById('ingest-running').hidden", polling=50)
    assert len(api.sent("POST", f"{API}/jobs/job-1/cancel")) == 1
    assert "کار این فایل لغو شده است." in await _text(page, "#ingest-progress")
    _assert_csrf(api)


# ── REQ-082: after an approval ───────────────────────────────────────────

async def test_an_approval_says_when_it_is_live_and_is_honest_after_150_seconds(html, open_page):
    api = FakeApi(html, job=_job(chunk_count=2), proposals=[_proposal(1), _proposal(2)])
    page = await open_page(api, "?job=job-1", viewport={"width": 1280, "height": 400}, clock=True)

    await _card(page, "p1").locator(".ingest-approve").click()
    await page.wait_for_function(
        "document.querySelector('.ingest-card[data-id=\"p1\"] .ingest-card-msg').textContent.length > 0",
        polling=50)
    assert UPDATING in await _text(page, '.ingest-card[data-id="p1"]')
    await page.clock.run_for(4000)
    await _barrier(page)
    assert len(api.sent("GET", f"{API}/index-status")) >= 1, "the page asks while it waits"
    assert UPDATING in await _text(page, '.ingest-card[data-id="p1"]')
    api.version = 6
    await _advance_until(page, "document.querySelector('.ingest-card[data-id=\"p1\"]').dataset.state === 'live'")
    assert LIVE in await _text(page, '.ingest-card[data-id="p1"]')
    assert not await _card(page, "p1").locator(".ingest-body").is_visible(), "a live card folds away"

    await _card(page, "p2").locator(".ingest-approve").click()
    await page.wait_for_function(
        f"document.querySelector('.ingest-card[data-id=\"p2\"]').textContent.includes('{UPDATING}')", polling=50)
    await page.clock.run_for(148000)
    assert SLOW not in await _text(page, '.ingest-card[data-id="p2"]'), "148 seconds is not yet the end"
    await page.clock.run_for(4000)
    assert SLOW in await _text(page, '.ingest-card[data-id="p2"]')
    await _barrier(page)
    polls = len(api.sent("GET", f"{API}/index-status"))
    await page.clock.run_for(10000)
    await _barrier(page)
    assert len(api.sent("GET", f"{API}/index-status")) == polls, "nobody waits any more, so nobody asks"
    _assert_csrf(api)


async def test_a_refused_approval_says_why_on_its_card(html, open_page):
    api = FakeApi(html, job=_job(), proposals=[_proposal(1)])
    api.answers[("POST", f"{API}/proposals/p1/approve")] = [
        (409, {"detail": ALREADY_REVIEWED, "code": "already_reviewed"})]
    page = await open_page(api, "?job=job-1", clock=True)
    await _card(page, "p1").locator(".ingest-approve").click()
    await page.wait_for_function(
        "document.querySelector('.ingest-card[data-id=\"p1\"] .ingest-card-msg').textContent.length > 0",
        polling=50)
    text = await _text(page, '.ingest-card[data-id="p1"]')
    assert ALREADY_REVIEWED in text
    _assert_plain(await _text(page))


async def test_reject_removes_the_card_from_review(html, open_page):
    api = FakeApi(html, job=_job(), proposals=[_proposal(1), _proposal(2)])
    page = await open_page(api, "?job=job-1")
    await _card(page, "p2").locator(".ingest-reject").click()
    await page.wait_for_function(
        "document.querySelector('.ingest-card[data-id=\"p2\"]').textContent.includes('رد شد')", polling=50)
    assert len(api.sent("POST", f"{API}/proposals/p2/reject")) == 1
    assert not await _card(page, "p2").locator(".ingest-body").is_visible()
    _assert_csrf(api)


# ── REQ-076: editing in the card ─────────────────────────────────────────

async def test_editing_happens_in_the_card_and_a_refusal_lands_by_its_field(html, open_page):
    api = FakeApi(html, job=_job(), proposals=[_proposal(1)])
    page = await open_page(api, "?job=job-1", clock=True)
    card = _card(page, "p1")
    await card.locator(".ingest-edit").click()
    editor = card.locator(".ingest-editor")
    assert await editor.is_visible()
    assert await editor.locator(".ingest-f-title").input_value() == "ساعت‌های بازدید ۱"
    assert await editor.locator(".ingest-f-questions").input_value() == "نمایشگاه کی باز است؟"
    assert await editor.locator(".ingest-f-synonyms").input_value() == "بلیت = بلیط"
    assert await page.locator(".modal.show").count() == 0, "no separate window"

    api.answers[("PUT", f"{API}/proposals/p1")] = [
        (422, {"detail": QUESTIONS_REFUSED, "code": "invalid_field", "field": "questions"})]
    await editor.locator(".ingest-f-questions").fill("نمایشگاه کی باز است؟\nکی")
    await editor.locator(".ingest-save").click()
    error = editor.locator('[data-error-for="questions"]')
    await error.wait_for(state="visible")
    assert (await error.inner_text()).strip() == QUESTIONS_REFUSED
    assert await editor.locator('[data-error-for="title"]').is_hidden()
    assert api.sent("POST", f"{API}/proposals/p1/approve") == []

    api.answers.pop(("PUT", f"{API}/proposals/p1"))
    await editor.locator(".ingest-f-questions").fill("نمایشگاه کی باز است؟\nبلیت را کجا بخرم؟")
    await editor.locator(".ingest-save").click()
    await page.wait_for_function(
        "['approved', 'live', 'slow'].includes(document.querySelector('.ingest-card[data-id=\"p1\"]').dataset.state)",
        polling=50)
    puts = api.sent("PUT", f"{API}/proposals/p1")
    assert puts[-1]["body"] == {"questions": ["نمایشگاه کی باز است؟", "بلیت را کجا بخرم؟"]}, \
        "only the field the admin changed is sent"
    order = [(r["method"], r["path"]) for r in api.mutations()]
    assert order[-2:] == [("PUT", f"{API}/proposals/p1"), ("POST", f"{API}/proposals/p1/approve")]
    _assert_csrf(api)


async def test_a_synonym_line_without_an_equals_sign_is_caught_before_sending(html, open_page):
    api = FakeApi(html, job=_job(), proposals=[_proposal(1)])
    page = await open_page(api, "?job=job-1")
    card = _card(page, "p1")
    await card.locator(".ingest-edit").click()
    await card.locator(".ingest-f-synonyms").fill("بلیت بلیط")
    await card.locator(".ingest-save").click()
    await card.locator('[data-error-for="synonyms"]').wait_for(state="visible")
    assert "کلمه = مترادف" in await card.locator('[data-error-for="synonyms"]').inner_text()
    assert api.sent("PUT") == []


async def test_the_keyboard_reaches_every_card_button_and_escape_closes_the_editor(html, open_page):
    api = FakeApi(html, job=_job(), proposals=[_proposal(1)])
    page = await open_page(api, "?job=job-1", clock=True)
    card = _card(page, "p1")
    await card.locator(".ingest-approve").focus()
    await page.keyboard.press("Tab")
    assert await page.evaluate("document.activeElement.classList.contains('ingest-edit')")
    await page.keyboard.press("Tab")
    assert await page.evaluate("document.activeElement.classList.contains('ingest-reject')")
    await page.keyboard.press("Shift+Tab")
    await page.keyboard.press("Enter")
    assert await card.locator(".ingest-editor").is_visible()
    assert await page.evaluate("document.activeElement.classList.contains('ingest-f-title')")
    await page.keyboard.press("Escape")
    assert await card.locator(".ingest-editor").is_hidden()
    assert await page.evaluate("document.activeElement.classList.contains('ingest-edit')")
    assert api.mutations() == []


# ── Section 10: the other states ─────────────────────────────────────────

async def test_a_chosen_file_is_sent_at_once_and_the_page_follows_it(html, open_page):
    api = FakeApi(html, job=_job(status="queued", chunk_count=0), jobs=[])
    gate = api.gates[("POST", f"{API}/jobs/upload")] = asyncio.Event()
    page = await open_page(api)
    assert NO_FILES in await _text(page, "#ingest-jobs-card")
    assert await page.locator("#ingest-read").is_disabled(), "no address yet"

    await page.locator("#ingest-file").set_input_files(
        files=[{"name": "راهنما.docx", "mimeType": "application/octet-stream", "buffer": b"PK\x03\x04 fake"}])
    await page.wait_for_function(
        f"document.getElementById('ingest-read').textContent.includes('{SENDING}')", polling=50)
    assert await page.locator("#ingest-read").is_disabled()
    assert await page.locator("#ingest-pick").is_disabled()
    gate.set()
    await page.wait_for_function("location.search === '?job=job-1'", polling=50)
    await page.locator("#ingest-progress").wait_for(state="visible")
    upload = api.sent("POST", f"{API}/jobs/upload")[0]
    assert upload["headers"]["content-type"].startswith("multipart/form-data")
    assert "راهنما.docx".encode() in upload["raw"] and b"PK\x03\x04 fake" in upload["raw"]
    _assert_csrf(api)


async def test_a_web_address_goes_by_the_read_button(html, open_page):
    api = FakeApi(html, job=_job(status="queued", source_kind="url", source_name="https://نمونه.ایران/درباره"))
    page = await open_page(api)
    read = page.locator("#ingest-read")
    await page.locator("#ingest-url").fill("https://example.com/about")
    assert await read.is_enabled()
    await page.locator("#ingest-url").fill("")
    assert await read.is_disabled()
    await page.locator("#ingest-url").fill("https://example.com/about")
    await read.click()
    await page.wait_for_function("location.search === '?job=job-1'", polling=50)
    assert api.sent("POST", f"{API}/jobs/url")[0]["body"] == {"url": "https://example.com/about"}
    _assert_csrf(api)


async def test_a_refused_file_shows_the_sentence_in_a_red_band_and_a_way_on(html, open_page):
    api = FakeApi(html, job=_job(), jobs=[])
    api.answers[("POST", f"{API}/jobs/upload")] = [(415, {"detail": BAD_TYPE, "code": "bad_type"})]
    page = await open_page(api)
    await page.locator("#ingest-file").set_input_files(
        files=[{"name": "photo.pdf", "mimeType": "application/pdf", "buffer": b"\x89PNG"}])
    alert = page.locator("#ingest-alert")
    await alert.wait_for(state="visible")
    assert await alert.get_attribute("role") == "alert"
    assert BAD_TYPE in await _text(page, "#ingest-alert")
    _assert_plain(await _text(page))
    await page.locator("#ingest-alert-retry").click()
    assert await alert.is_hidden()
    assert await page.evaluate("document.activeElement.id") == "ingest-pick"


async def test_a_server_failure_without_a_sentence_still_reads_as_plain_words(html, open_page):
    api = FakeApi(html, job=_job(), jobs=[])
    api.answers[("POST", f"{API}/jobs/url")] = [(500, "Internal Server Error")]
    page = await open_page(api)
    await page.locator("#ingest-url").fill("https://example.com/")
    await page.locator("#ingest-read").click()
    await page.locator("#ingest-alert").wait_for(state="visible")
    text = await _text(page, "#ingest-alert")
    assert "500" not in text and "Internal" not in text
    assert len(text) > 10


async def test_a_failed_file_says_what_happened_and_keeps_what_was_ready(html, open_page):
    sentence = "کار این فایل نیمه‌کاره ماند. پیشنهادهای آماده را می‌توانید بررسی کنید، یا فایل را دوباره بفرستید."
    api = FakeApi(html, job=_job(status="failed", error_code="interrupted", error_message=sentence),
                  proposals=[_proposal(1)])
    page = await open_page(api, "?job=job-1")
    assert sentence in await _text(page, "#ingest-alert")
    assert await page.locator("#ingest-alert-retry").is_visible()
    assert await _card(page, "p1").is_visible()
    _assert_plain(await _text(page))


async def test_recent_files_read_in_plain_words(html, open_page):
    jobs = [_job(id="a", status="ready", chunk_count=85), _job(id="b", status="done"),
            _job(id="c", status="failed", error_code="bad_zip"), _job(id="d", status="cancelled"),
            _job(id="e", status="proposing")]
    api = FakeApi(html, job=jobs[0], jobs=jobs)
    page = await open_page(api)
    text = await _text(page, "#ingest-jobs")
    for words in ("آمادهٔ بررسی: ۸۵ پیشنهاد", "تمام شد", "خوانده نشد", "لغو شد", WAIT_NOTE):
        assert words in text, words
    assert await page.locator('#ingest-jobs a[href="?job=a"]').count() == 1
    _assert_plain(await _text(page))


async def test_a_file_with_everything_reviewed_says_so(html, open_page):
    api = FakeApi(html, job=_job(status="done"), proposals=[_proposal(1, status="approved")])
    page = await open_page(api, "?job=job-1")
    assert ALL_REVIEWED in await _text(page, "#ingest-review")
    assert await page.locator(".ingest-card").count() == 0


async def test_a_ready_file_counts_its_proposals_and_a_waiting_card_cannot_be_approved(html, open_page):
    api = FakeApi(html, job=_job(status="proposing", chunk_count=2, _ai_done=1),
                  proposals=[_proposal(1), _proposal(2, ai_state="waiting")])
    page = await open_page(api, "?job=job-1", clock=True)
    waiting = _card(page, "p2")
    assert await waiting.locator(".ingest-approve").is_disabled()
    assert WAIT_NOTE in await _text(page, '.ingest-card[data-id="p2"]')
    assert await _card(page, "p1").locator(".ingest-approve").is_enabled()
    assert WAIT_NOTE in await _text(page, "#ingest-jobs")

    api.job["status"] = "ready"
    api.proposals[1]["ai_state"] = "done"
    await page.clock.run_for(3000)
    await page.wait_for_function(
        "!document.querySelector('.ingest-card[data-id=\"p2\"] .ingest-approve').disabled", polling=50)
    assert "۲ پیشنهاد آماده است. هرکدام را کنار متن اصلی ببینید و تأیید کنید." in await _text(page, "#ingest-summary")
    await page.wait_for_function(
        "document.getElementById('ingest-jobs').textContent.includes('آمادهٔ بررسی')", polling=50)
    assert await page.locator("#ingest-progress-body").is_hidden(), "a finished file leaves no empty box"


async def test_the_page_has_no_technical_words_in_any_state(html, open_page):
    api = FakeApi(html, job=_job(status="proposing", chunk_count=3, _ai_done=1),
                  proposals=[_proposal(1, similar_kind="duplicate", similar_to="p0"),
                             _proposal(2, title_source="model"), _proposal(3, ai_state="waiting")])
    page = await open_page(api, "?job=job-1")
    text = await _text(page)
    assert "این متن تکراری است." in text
    assert "عنوان پیشنهادی" in text
    _assert_plain(text)
    await _card(page, "p1").locator(".ingest-edit").click()
    _assert_plain(await _text(page))


# ── SC-021: phone and wide screen ────────────────────────────────────────

async def test_a_phone_never_scrolls_sideways_and_a_wide_screen_shows_two_columns(html, open_page):
    long_name = "https://example.com/" + "a" * 160
    long_title = "عنوان" * 40
    job = _job(source_kind="url", source_name=long_name)
    proposals = [_proposal(1, title=long_title, source_text="متن " * 400, text="پاسخ" * 300,
                           similar_kind="dataset", similar_title=long_title)]
    api = FakeApi(html, job=job, jobs=[job], proposals=proposals)

    phone = await open_page(api, "?job=job-1", viewport={"width": 375, "height": 800})
    overflow = await phone.evaluate("[document.documentElement.scrollWidth, document.documentElement.clientWidth]")
    assert overflow[0] <= overflow[1], overflow
    source = await _card(phone, "p1").locator(".ingest-source").bounding_box()
    answer = await _card(phone, "p1").locator(".ingest-answer").bounding_box()
    assert answer["y"] >= source["y"] + source["height"] - 1, "under 992 px the source sits above the answer"
    approve = await _card(phone, "p1").locator(".ingest-approve").bounding_box()
    body = await _card(phone, "p1").bounding_box()
    assert approve["width"] > body["width"] * 0.75, "buttons are full width on a phone"
    assert await phone.locator("#ingest-pick").is_visible()
    scroll = await phone.evaluate(
        "(() => { const s = document.querySelector('.ingest-source'); return [s.scrollHeight, s.clientHeight]; })()")
    assert scroll[0] > scroll[1], "a long source scrolls inside its box"

    wide = await open_page(api, "?job=job-1", viewport={"width": 1280, "height": 900})
    source = await _card(wide, "p1").locator(".ingest-source").bounding_box()
    answer = await _card(wide, "p1").locator(".ingest-answer").bounding_box()
    assert abs(source["y"] - answer["y"]) < 40 and abs(source["x"] - answer["x"]) > 200, (source, answer)
    overflow = await wide.evaluate("[document.documentElement.scrollWidth, document.documentElement.clientWidth]")
    assert overflow[0] <= overflow[1], overflow


# ── The real app ─────────────────────────────────────────────────────────

class _Model:
    """padyar_ai.generate, the one fake in the real-app test: each chunk gets
    a question, and the second one a synonym too."""

    def __init__(self):
        self.calls = []

    async def __call__(self, messages, **kwargs):
        from app.services.ai.request import AIResponse
        self.calls.append(kwargs)
        n = len(self.calls)
        body = {"title": "", "questions": ["نمایشگاه چه ساعتی باز است؟" if n == 1 else "هزینهٔ غرفه چقدر است؟"],
                "synonyms": [] if n == 1 else [{"word": "غرفه", "suggestion": "استند"}]}
        return AIResponse(content=json.dumps(body, ensure_ascii=False), finish_reason="stop",
                          tokens_output=17, latency_ms=4)


@pytest.fixture
def live_app(tmp_path, monkeypatch):
    """The real app on a local uvicorn server, over a real SQLite file. Only the
    model is replaced. The local embedder is switched off, because loading it
    would download a model from the network."""
    import tempfile
    import uvicorn
    import app.config as config
    from app.services import embeddings
    from app.services.ai.wrapper import padyar_ai
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "ingest-live.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    monkeypatch.setattr(embeddings, "available", lambda: False)
    monkeypatch.setattr(padyar_ai, "external_ai_enabled", lambda: True)
    model = _Model()
    monkeypatch.setattr(padyar_ai, "generate", model)
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(uploads))
    from app.main import app
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 30
    while not server.started:
        assert thread.is_alive() and time.monotonic() < deadline, "the app did not start"
        time.sleep(0.05)
    try:
        yield f"http://127.0.0.1:{port}", _session("liveadmin"), model
    finally:
        server.should_exit = True
        thread.join(15)
        sock.close()


def _rows(query, params=()):
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    try:
        return [dict(r) for r in conn.execute(query, params).fetchall()]
    finally:
        conn.close()


async def test_the_real_app_reads_a_file_and_publishes_only_what_the_admin_saw(browser, live_app):
    base, token, model = live_app
    context = await browser.new_context(viewport={"width": 1280, "height": 600})
    try:
        await context.add_cookies([{"name": "admin_session", "value": token, "url": base}])
        page = await context.new_page()
        await page.goto(f"{base}{PAGE}")
        await page.wait_for_function("document.getElementById('ingest-root').dataset.ready === '1'")
        assert NO_FILES in await _text(page, "#ingest-jobs-card")

        await page.locator("#ingest-file").set_input_files(str(DOCX))
        await page.wait_for_function("location.search.startsWith('?job=')", timeout=15000)
        await page.wait_for_function(
            "document.querySelectorAll('.ingest-card').length > 0"
            " && [...document.querySelectorAll('.ingest-card .ingest-approve')].every(b => !b.disabled)"
            " && document.getElementById('ingest-running').hidden", timeout=60000)
        cards = page.locator(".ingest-card")
        assert await cards.count() == 2, "fa-sample.docx has two headed sections"
        assert len(model.calls) == 2 and all(c["task"] == "chat" for c in model.calls)
        assert "نمایشگاه چه ساعتی باز است؟" in await _text(page, "#ingest-cards")
        assert await page.locator("#ingest-approve-seen").is_disabled(), "nothing is seen before it is looked at"

        for n, proposal_id in enumerate(await cards.evaluate_all("cs => cs.map(c => c.dataset.id)"), start=1):
            await _center(page, f'.ingest-card[data-id="{proposal_id}"]')
            await page.wait_for_function(
                f"document.getElementById('ingest-approve-seen').textContent.includes('({_fa(n)})')",
                timeout=10000)
        await page.locator("#ingest-approve-seen").click()
        await page.wait_for_function(
            "document.getElementById('ingest-bulk-msg').textContent.includes('۲ پیشنهاد تأیید شد.')",
            timeout=10000)
        await page.wait_for_function(
            f"document.getElementById('ingest-bulk-msg').textContent.includes('{LIVE}')", timeout=30000)
        await page.wait_for_function(
            f"document.getElementById('ingest-summary').textContent.includes('{ALL_REVIEWED}')", timeout=10000)
        await page.wait_for_function("document.getElementById('ingest-jobs').textContent.includes('تمام شد')",
                                     timeout=10000)
    finally:
        await context.close()

    entries = _rows("SELECT id, title, text FROM dataset ORDER BY title")
    assert [e["title"] for e in entries] == ["ثبت‌نام غرفه‌ها", "ساعت‌های بازدید"], entries
    assert all(e["id"].startswith("ing-") for e in entries)
    assert "۲٬۵۰۰٬۰۰۰ تومان" in next(e["text"] for e in entries if e["title"] == "ثبت‌نام غرفه‌ها")
    questions = {r["question"] for r in _rows("SELECT question FROM questions")}
    assert questions == {"نمایشگاه چه ساعتی باز است؟", "هزینهٔ غرفه چقدر است؟"}
    assert _rows("SELECT source, target FROM synonyms") == [{"source": "غرفه", "target": "استند"}]
    assert {r["status"] for r in _rows("SELECT status FROM ingest_jobs")} == {"done"}
