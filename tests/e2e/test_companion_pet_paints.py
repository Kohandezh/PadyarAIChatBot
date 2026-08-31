"""The pet paints even when the atlas loses the boot race, in a real browser.

THE DEFECT THIS FILE HOLDS DOWN
-------------------------------
companion-ui.js restores the visitor's preference on load, which calls
PetCompanion.resume(). resume() schedules a render frame unconditionally —
including BEFORE the atlas image has fired onload. The old frame() began:

    function frame(now) {
        if (!atlas) return;          // loop dies, no reschedule

so a frame scheduled before the atlas arrived killed the render loop for
good: start() later saw the stale nonzero raf id and never scheduled again.
The pet's box was on the page, its state machine ran (set('greet') settled
to 'idle'), but the canvas stayed empty forever. On a slow first load —
which is every first load through a CDN — this was not a race the pet could
win; on a warm cache it usually won, which is why it looked intermittent.

The fix is in companion.js (frame() re-arms while the atlas is pending).
This test forces the losing order — the atlas is held back until the page's
own scripts have long finished booting — and asserts the character actually
paints, from outside, the way a visitor would notice it.

Pattern shared with tests/e2e/test_visitor_session_e2e.py: the REAL rendered
chat page, every asset off disk, backend stubbed, Playwright's ASYNC api
(pytest.ini sets asyncio_mode=auto and the sync fixture refuses to start
inside the loop).
"""
import asyncio
import json
import mimetypes
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ORIGIN = "http://padyar.test"

# How long the atlas is held back: comfortably past the moment
# companion-ui.js restores preferences and calls resume(), so the test
# pins the losing order of the race rather than hoping for it.
ATLAS_DELAY_S = 1.5

# The theme CSS hides the companion below a 640px viewport.
DESKTOP = {"viewport": {"width": 1440, "height": 900}}


def _disk_path(url_path: str):
    """Map a URL path onto a repository file (same rule as the other e2e
    files: /static/** and /themes/** map straight through)."""
    url_path = url_path.split("?")[0].lstrip("/")
    candidate = ROOT / url_path
    try:
        candidate = candidate.resolve()
        candidate.relative_to(ROOT)
    except (ValueError, OSError):
        return None
    return candidate if candidate.is_file() else None


def _rendered_page(db_path: str, monkeypatch) -> str:
    import app.config as config

    monkeypatch.setattr(config, "DB_PATH", db_path)
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as c:
        r = c.get("/")
    assert r.status_code == 200, r.status_code
    assert 'id="pet-canvas"' in r.text, (
        "the active theme does not render the pet canvas, so this file "
        "would be testing an empty page")
    return r.text


async def _json(route, payload):
    return await route.fulfill(status=200, content_type="application/json",
                               body=json.dumps(payload))


@pytest.fixture
async def browser():
    async_playwright = pytest.importorskip(
        "playwright.async_api").async_playwright
    async with async_playwright() as p:
        try:
            b = await p.chromium.launch()
        except Exception as e:  # noqa: BLE001 (no browser installed is a skip)
            pytest.skip(f"chromium unavailable: {e}")
        yield b
        await b.close()


async def _painted(page) -> bool:
    """True when the pet canvas holds at least one painted pixel."""
    return await page.evaluate(
        """() => {
            const c = document.getElementById('pet-canvas');
            if (!c || !c.getContext) return false;
            const d = c.getContext('2d')
                .getImageData(0, 0, c.width, c.height).data;
            for (let i = 3; i < d.length; i += 4) if (d[i] !== 0) return true;
            return false;
        }""")


async def test_pet_paints_when_atlas_loads_after_boot(browser, tmp_path,
                                                      monkeypatch):
    html = _rendered_page(str(tmp_path / "pet.db"), monkeypatch)

    async def handle(route, request):
        path = request.url[len(ORIGIN):].split("?")[0] or "/"

        if path == "/":
            return await route.fulfill(status=200,
                                       content_type="text/html", body=html)
        if path == "/api/suggestions":
            return await _json(route, [])
        if path == "/api/voice-status":
            return await _json(route, {"voice_enabled": False,
                                       "tts_enabled": False})
        if path == "/api/auth/registration-status":
            return await _json(route, {"enabled": False})
        if path == "/api/auth/session":
            return await _json(route, {"signed_in": False, "profile": {}})

        # The losing side of the race: hold the character sheet back until
        # the page's own scripts (companion-ui.js's preference restore and
        # its resume()) have certainly run. With the old frame() this is
        # exactly where the render loop died and never came back.
        if path.startswith("/static/otp/pet/"):
            await asyncio.sleep(ATLAS_DELAY_S)

        disk = _disk_path(path)
        if disk is not None:
            ctype = (mimetypes.guess_type(disk.name)[0]
                     or "application/octet-stream")
            return await route.fulfill(status=200, content_type=ctype,
                                       body=disk.read_bytes())
        return await route.fulfill(status=404, content_type="text/plain",
                                   body="")

    context = await browser.new_context(**DESKTOP)
    try:
        page = await context.new_page()
        await page.route(f"{ORIGIN}/**", handle)
        await page.goto(f"{ORIGIN}/")

        # is-ready lands with img.onload; painting must follow. On the old
        # code the canvas stays empty forever, so this times out and fails.
        await page.wait_for_selector("#pet-canvas.is-ready", timeout=10000)
        await page.wait_for_function(
            "() => (() => { const c = document.getElementById('pet-canvas');"
            " if (!c) return false;"
            " const d = c.getContext('2d')"
            "     .getImageData(0, 0, c.width, c.height).data;"
            " for (let i = 3; i < d.length; i += 4) if (d[i] !== 0)"
            "     return true;"
            " return false; })()", timeout=10000)

        assert await _painted(page), (
            "the pet canvas never painted: the render loop died on the "
            "atlas-is-still-loading frame and start() trusted the stale "
            "raf id instead of scheduling again")
    finally:
        await context.close()
