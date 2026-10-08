"""The 20 second poll after a manual drill must not clear a bulk-delete choice.

THE BUG
-------
After the operator presses «تمرین بازیابی», the page reloads the list every
20 seconds, for up to 45 minutes, to notice when the drill is done. A reload
rebuilds the rows, and that clears the set of ticked backups. An operator who
had ticked backups and opened the bulk-delete dialog lost the choice while
typing the confirmation phrase, and the confirm sent no ids.

WHAT THIS PINS
--------------
While any Bootstrap dialog is open (or opening), the poll skips its reload and
tries again on the next tick. When the dialog is closed, the poll works again.
A normal load() is not changed.

HOW
---
The REAL admin page is rendered through TestClient. The two API answers the
page needs are served by a route handler, so nothing reaches a network. The
page script's 20 s interval is shortened to 150 ms by an init script, so the
test does not wait 20 s. Real Chromium, real Bootstrap, real page script.

Playwright's ASYNC api only, with the browser fixture below. The sync `page`
fixture of pytest-playwright is banned (tests/test_suite_isolation.py).
"""
import asyncio
import datetime
import json
import mimetypes
import secrets
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ORIGIN = "http://padyar.test"
PAGE = "/secure-panel-admin/infrastructure/backups"
API = "/admin/api/infra/backups"
IDS = ["pg_20260830_120000_ab12cd", "pg_20260829_120000_ab12cd"]

# Page script waits 20000 ms between polls. Make that 150 ms in the browser.
FAST_POLL = """
(() => {
  const real = window.setInterval.bind(window);
  window.setInterval = (fn, ms, ...rest) => real(fn, ms === 20000 ? 150 : ms, ...rest);
})();
"""


def _row(backup_id):
    return {
        "backup_id": backup_id, "created_at": "2026-08-30T12:00:00+00:00",
        "kind": "scheduled", "total_bytes": 42, "files": [],
        "verification": {"state": "verified", "checked_at": None, "problems": []},
        "drill": None,
    }


def _listing():
    return {"engine": "postgresql", "backups": [_row(i) for i in IDS],
            "schedule": {}, "latest_drill": None}


@pytest.fixture
def page_html(tmp_path, monkeypatch):
    """The real admin Backups page, as an admin sees it."""
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "ui.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    from app.db.connection import init_db
    init_db()
    from fastapi.testclient import TestClient
    from app.db.connection import get_db_connection
    from app.main import app
    from app.routers import backups as backups_router
    if not any(str(getattr(r, "path", "")).startswith(API) for r in app.routes):
        app.include_router(backups_router.router)
    with TestClient(app) as c:
        token = secrets.token_hex(16)
        conn = get_db_connection()
        conn.execute("INSERT INTO admin_sessions (token, username, expiry)"
                     " VALUES (?, ?, ?)",
                     (token, "tester", (datetime.datetime.now()
                                        + datetime.timedelta(hours=1)).isoformat()))
        conn.commit()
        conn.close()
        c.cookies.set(config.ADMIN_COOKIE_NAME, token)
        res = c.get(PAGE)
        assert res.status_code == 200, res.status_code
        return res.text


@pytest.fixture
async def browser():
    async_playwright = pytest.importorskip("playwright.async_api").async_playwright
    async with async_playwright() as p:
        try:
            b = await p.chromium.launch()
        except Exception as e:  # noqa: BLE001  (no browser installed is a skip)
            pytest.skip(f"chromium unavailable, the poll test did not run: {e}")
        yield b
        await b.close()


def _disk(path: str):
    candidate = (ROOT / path.split("?")[0].lstrip("/")).resolve()
    try:
        candidate.relative_to(ROOT)
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


@pytest.fixture
async def backups_page(browser, page_html):
    """(page, counter): the page is loaded and the first list has rendered."""
    counter = {"lists": 0, "drills": 0}

    async def handle(route, request):
        path = request.url[len(ORIGIN):].split("?")[0] or "/"
        json_type = "application/json"
        if path == PAGE:
            return await route.fulfill(status=200, content_type="text/html",
                                       body=page_html)
        if path == API and request.method == "GET":
            counter["lists"] += 1
            return await route.fulfill(status=200, content_type=json_type,
                                       body=json.dumps(_listing()))
        if path == f"{API}/{IDS[0]}/drill" and request.method == "POST":
            counter["drills"] += 1
            return await route.fulfill(status=202, content_type=json_type,
                                       body=json.dumps({"message": "شروع شد"}))
        if path == "/admin/csrf":
            return await route.fulfill(status=200, content_type=json_type,
                                       body=json.dumps({"csrf_token": "t"}))
        disk = _disk(path) if path.startswith("/static/") else None
        if disk is not None:
            ctype = mimetypes.guess_type(disk.name)[0] or "application/octet-stream"
            return await route.fulfill(status=200, content_type=ctype,
                                       body=disk.read_bytes())
        return await route.fulfill(status=404, content_type="text/plain", body="")

    context = await browser.new_context(viewport={"width": 1280, "height": 900})
    page = await context.new_page()
    await context.add_init_script(FAST_POLL)
    await page.route(f"{ORIGIN}/**", handle)
    await page.goto(f"{ORIGIN}{PAGE}")
    await page.wait_for_selector("#backups-body input[type=checkbox]")
    yield page, counter
    await context.close()


async def _lists_after(counter, start, at_least, seconds=15):
    """Wait until the list was requested `at_least` more times, or time is up."""
    for _ in range(int(seconds / 0.05)):
        if counter["lists"] - start >= at_least:
            return True
        await asyncio.sleep(0.05)
    return False


# Tick the first backup and open the bulk-delete dialog in ONE script step, so
# no poll can run in between. The test is about what the poll does afterwards.
TICK_AND_OPEN = """
() => {
  document.querySelector('#backups-body input[type=checkbox]').click();
  document.getElementById('bulk-delete-btn').click();
}
"""


async def _start_the_drill_poll(page, counter):
    await page.get_by_role("button", name="تمرین بازیابی", exact=True).first.click()
    await page.wait_for_selector("#drill-running:not([hidden])")
    # The page also reloads once, 2 seconds after the click (not the poll).
    # Wait past it, so only the poll is left running.
    await asyncio.sleep(2.6)
    # The poll is running when the list is requested again and again.
    start = counter["lists"]
    assert await _lists_after(counter, start, 3), "the drill poll did not start"


async def test_an_open_bulk_delete_dialog_keeps_its_choice_across_polls(
        backups_page):
    page, counter = backups_page
    await _start_the_drill_poll(page, counter)

    await page.evaluate(TICK_AND_OPEN)
    await page.wait_for_selector("#bulkDeleteModal.show")
    start = counter["lists"]

    # More than five poll ticks pass while the dialog stays open.
    await asyncio.sleep(0.9)

    assert counter["lists"] == start, "the poll must not reload under a dialog"
    assert await page.locator(
        "#backups-body input[type=checkbox]").first.is_checked()
    assert not await page.locator("#bulk-delete-btn").is_disabled(), \
        "the choice was cleared, the confirm would send no ids"


async def test_the_poll_works_again_after_the_dialog_is_closed(backups_page):
    page, counter = backups_page
    await _start_the_drill_poll(page, counter)
    await page.evaluate(TICK_AND_OPEN)
    await page.wait_for_selector("#bulkDeleteModal.show")
    await asyncio.sleep(0.5)

    await page.evaluate(
        "() => bootstrap.Modal.getInstance(document.getElementById('bulkDeleteModal')).hide()")
    await page.wait_for_selector("#bulkDeleteModal:not(.show)")
    start = counter["lists"]

    assert await _lists_after(counter, start, 2), "the poll did not resume"


async def test_the_poll_also_waits_for_the_drill_detail_dialog(backups_page):
    page, counter = backups_page
    await _start_the_drill_poll(page, counter)
    await page.evaluate(
        "() => new bootstrap.Modal(document.getElementById('drillModal')).show()")
    await page.wait_for_selector("#drillModal.show")
    start = counter["lists"]

    await asyncio.sleep(0.9)

    assert counter["lists"] == start
