"""Admin → AI → Text to speech.

The speech engine listens on 127.0.0.1:8003 with no authentication of its own.
Everything in app/routers/tts.py exists to make that safe and usable, so these
tests answer the questions that follow from it:

  * can anyone but a logged-in admin drive the engine?     (no)
  * what does the page do when the engine is off?          (says so, in Persian)
  * do the three Chatterbox parameters reach the engine
    unchanged, and is an out-of-range one refused here
    rather than by a component the admin never sees?       (yes, and yes)
  * is the cache header forwarded?                         (yes — it is the
    only explanation the operator gets for why one preview is instant)

The engine itself is never started: `httpx.AsyncClient` is replaced inside the
router, so every test runs offline against a recorded contract.
"""
import datetime
import secrets

import httpx
import pytest
from fastapi.testclient import TestClient


# ── A stand-in for the speech service ───────────────────────────────────

class FakeResponse:
    def __init__(self, status_code=200, json_body=None, content=b"", headers=None):
        self.status_code = status_code
        self._json = json_body
        self.content = content
        self.headers = headers or {}

    def json(self):
        if self._json is None:
            raise ValueError("no json body")
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("upstream error", request=None, response=None)


class FakeClient:
    """Records what the proxy sent, and replays what the engine would answer.

    `calls` is a module-level list rather than instance state because the
    router builds a fresh client per request — which is the behaviour under
    test, so it must not be changed to make testing easier.
    """
    calls = []
    responses = {}          # (method, path) → FakeResponse | Exception

    def __init__(self, *_, **kwargs):
        self.timeout = kwargs.get("timeout")

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    def _answer(self, method, url, **kwargs):
        path = url.split("8003", 1)[-1]
        FakeClient.calls.append({"method": method, "path": path,
                                 "timeout": self.timeout, **kwargs})
        answer = FakeClient.responses.get((method, path))
        if answer is None:
            # An un-stubbed call is a bug in the test, not a network failure.
            raise AssertionError(f"unexpected upstream call: {method} {path}")
        if isinstance(answer, Exception):
            raise answer
        return answer

    async def get(self, url, **kwargs):
        return self._answer("GET", url, **kwargs)

    async def post(self, url, **kwargs):
        return self._answer("POST", url, **kwargs)

    async def delete(self, url, **kwargs):
        return self._answer("DELETE", url, **kwargs)


@pytest.fixture()
def engine(monkeypatch):
    """The router talking to a fake engine instead of a real one."""
    from app.routers import tts as tts_router
    FakeClient.calls = []
    FakeClient.responses = {}
    monkeypatch.setattr(tts_router.httpx, "AsyncClient", FakeClient)
    return FakeClient


@pytest.fixture()
def anon(tmp_path, monkeypatch):
    """The app on a throwaway database, with nobody logged in."""
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "test_chat.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)

    from app.main import app
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def client(anon):
    """The same app with a real admin session cookie and a CSRF token."""
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
    anon.cookies.set(ADMIN_COOKIE_NAME, token)
    from app.auth.csrf import token_for_session
    anon.headers.update({'X-CSRF-Token': token_for_session(token)})
    return anon


HEALTHY = {"status": "ok", "model_loaded": True, "error": None, "device": "cuda",
           "gpu": "Tesla P40", "sample_rate": 24000, "language": "fa",
           "cpu_threads": None}


# ── The module ──────────────────────────────────────────────────────────

def test_tts_is_an_optional_module_a_customer_can_be_sold():
    """Every new feature is optional until every customer needs it."""
    from app.modules.registry import MODULES
    assert MODULES["tts"].is_core is False
    assert MODULES["tts"].router_module == "app.routers.tts"


# ── Nobody unauthenticated drives the engine ────────────────────────────

def test_every_endpoint_refuses_an_anonymous_caller(anon, engine):
    calls = [
        ("get", "/admin/api/tts/health", {}),
        ("get", "/admin/api/tts/voices", {}),
        ("post", "/admin/api/tts/preview", {"json": {"text": "سلام"}}),
        ("delete", "/admin/api/tts/voices/someone", {}),
    ]
    for method, url, kwargs in calls:
        res = getattr(anon, method)(url, **kwargs)
        assert res.status_code in (401, 403), f"{method} {url} was not refused"
    # And nothing was forwarded to the engine on the way to being refused.
    assert engine.calls == []


def test_the_page_sends_anonymous_visitors_to_the_login_screen(anon):
    res = anon.get("/secure-panel-inotex/ai/tts", follow_redirects=False)
    assert res.status_code in (302, 303, 307)
    assert "login" in res.headers["location"]


def test_the_page_renders_for_an_admin(client):
    res = client.get("/secure-panel-inotex/ai/tts")
    assert res.status_code == 200
    assert "تبدیل متن به صدا" in res.text
    assert "/static/admin/js/tts.js" in res.text
    # The three real Chatterbox knobs, and no invented fourth one.
    for slider in ("p-exaggeration", "p-cfg", "p-temperature"):
        assert slider in res.text


# ── A dead engine is a rendered state, not an exception ─────────────────

def test_health_reports_a_stopped_engine_in_plain_persian(client, engine):
    engine.responses[("GET", "/health")] = httpx.ConnectError("connection refused")
    body = client.get("/admin/api/tts/health").json()
    assert body["reachable"] is False
    assert body["model_loaded"] is False
    assert "در دسترس نیست" in body["message_fa"]


def test_health_passes_the_engine_report_through(client, engine):
    engine.responses[("GET", "/health")] = FakeResponse(json_body=dict(HEALTHY))
    body = client.get("/admin/api/tts/health").json()
    assert body["reachable"] is True
    assert body["device"] == "cuda"
    assert body["gpu"] == "Tesla P40"
    assert body["model_loaded"] is True


def test_the_voice_list_survives_a_stopped_engine(client, engine):
    engine.responses[("GET", "/voices")] = httpx.ConnectError("connection refused")
    body = client.get("/admin/api/tts/voices").json()
    assert body["voices"] == []
    assert body["reachable"] is False
    assert body["message_fa"]


def test_the_voice_list_is_forwarded_when_the_engine_is_up(client, engine):
    engine.responses[("GET", "/voices")] = FakeResponse(
        json_body={"voices": ["masoud", "sara"], "default": ""})
    body = client.get("/admin/api/tts/voices").json()
    assert body["voices"] == ["masoud", "sara"]
    assert body["reachable"] is True


# ── Preview ─────────────────────────────────────────────────────────────

def test_preview_forwards_every_parameter_and_returns_the_wav(client, engine):
    engine.responses[("POST", "/tts")] = FakeResponse(
        content=b"RIFF....WAVE",
        headers={"x-tts-cache": "miss", "x-tts-key": "abc123"})

    res = client.post("/admin/api/tts/preview", json={
        "text": "سلام دنیا", "voice": "masoud",
        "exaggeration": 0.9, "cfg_weight": 0.65, "temperature": 1.2,
    })
    assert res.status_code == 200
    assert res.headers["content-type"] == "audio/wav"
    assert res.content == b"RIFF....WAVE"

    sent = engine.calls[-1]["json"]
    assert sent == {"text": "سلام دنیا", "voice": "masoud",
                    "exaggeration": 0.9, "cfg_weight": 0.65, "temperature": 1.2}


def test_preview_forwards_the_cache_header(client, engine):
    """The only explanation the operator ever gets for a five-second wait."""
    engine.responses[("POST", "/tts")] = FakeResponse(
        content=b"wav", headers={"x-tts-cache": "hit", "x-tts-key": "k"})
    res = client.post("/admin/api/tts/preview", json={"text": "سلام"})
    assert res.headers["x-tts-cache"] == "hit"
    assert res.headers["x-tts-key"] == "k"


def test_preview_uses_the_defaults_when_the_sliders_are_untouched(client, engine):
    engine.responses[("POST", "/tts")] = FakeResponse(content=b"wav")
    client.post("/admin/api/tts/preview", json={"text": "سلام"})
    sent = engine.calls[-1]["json"]
    assert (sent["exaggeration"], sent["cfg_weight"], sent["temperature"]) == (0.5, 0.5, 0.8)


@pytest.mark.parametrize("field,value", [
    ("exaggeration", 2.5), ("exaggeration", 0.1),
    ("cfg_weight", 1.5), ("cfg_weight", 0.0),
    ("temperature", 6.0), ("temperature", 0.0),
])
def test_a_parameter_outside_chatterboxs_range_never_reaches_the_engine(
        client, engine, field, value):
    res = client.post("/admin/api/tts/preview", json={"text": "سلام", field: value})
    assert res.status_code == 422
    assert engine.calls == []


def test_empty_text_is_refused_before_the_engine_is_bothered(client, engine):
    res = client.post("/admin/api/tts/preview", json={"text": ""})
    assert res.status_code == 422
    assert engine.calls == []


def test_a_stopped_engine_during_a_preview_is_a_persian_sentence(client, engine):
    engine.responses[("POST", "/tts")] = httpx.ConnectError("connection refused")
    res = client.post("/admin/api/tts/preview", json={"text": "سلام"})
    assert res.status_code == 503
    assert "در دسترس نیست" in res.json()["detail"]


def test_a_slow_engine_is_reported_differently_from_a_dead_one(client, engine):
    """504 vs 503, because the operator's next move differs: wait and shorten
    the text, versus start the service."""
    engine.responses[("POST", "/tts")] = httpx.ReadTimeout("too slow")
    res = client.post("/admin/api/tts/preview", json={"text": "سلام"})
    assert res.status_code == 504
    assert "پاسخ نداد" in res.json()["detail"]


def test_the_engines_own_rejection_reaches_the_operator(client, engine):
    engine.responses[("POST", "/tts")] = FakeResponse(
        status_code=400, json_body={"detail": "unknown voice: ghost"})
    res = client.post("/admin/api/tts/preview", json={"text": "سلام", "voice": "ghost"})
    assert res.status_code == 400
    assert res.json()["detail"] == "unknown voice: ghost"


# ── Voice management ────────────────────────────────────────────────────

def test_uploading_a_clip_forwards_it_and_is_audited(client, engine):
    engine.responses[("POST", "/voices")] = FakeResponse(
        json_body={"name": "masoud", "seconds": 8.4, "sample_rate": 24000,
                   "replaced": False})

    res = client.post("/admin/api/tts/voices",
                      data={"name": "masoud"},
                      files={"file": ("clip.webm", b"\x1a\x45\xdf\xa3fake", "audio/webm")})
    assert res.status_code == 200
    assert res.json()["seconds"] == 8.4

    forwarded = engine.calls[-1]
    assert forwarded["data"] == {"name": "masoud"}
    assert forwarded["files"]["file"][1] == b"\x1a\x45\xdf\xa3fake"

    from app.services import applog
    rows, _ = applog.query(tables=["audit_logs"], limit=50)
    assert any(r["event_name"] == "admin.tts.voice.added" and r["actor"] == "tester"
               for r in rows)


def test_a_clip_the_engine_rejects_says_why(client, engine):
    engine.responses[("POST", "/voices")] = FakeResponse(
        status_code=400,
        json_body={"detail": "the clip is 1.2s — too short to clone a voice from"})
    res = client.post("/admin/api/tts/voices",
                      data={"name": "masoud"},
                      files={"file": ("clip.webm", b"short", "audio/webm")})
    assert res.status_code == 400
    assert "too short" in res.json()["detail"]


def test_deleting_a_voice_forwards_and_is_audited(client, engine):
    engine.responses[("DELETE", "/voices/masoud")] = FakeResponse(
        json_body={"removed": "masoud"})
    res = client.delete("/admin/api/tts/voices/masoud")
    assert res.status_code == 200
    assert res.json()["removed"] == "masoud"

    from app.services import applog
    rows, _ = applog.query(tables=["audit_logs"], limit=50)
    assert any(r["event_name"] == "admin.tts.voice.removed" for r in rows)


def test_deleting_an_unknown_voice_reports_the_engines_404(client, engine):
    engine.responses[("DELETE", "/voices/ghost")] = FakeResponse(
        status_code=404, json_body={"detail": "unknown voice: ghost"})
    res = client.delete("/admin/api/tts/voices/ghost")
    assert res.status_code == 404


# ── Timeouts are not one number ─────────────────────────────────────────

def test_status_calls_give_up_long_before_a_synthesis_would(client, engine):
    """A wedged /health must not hold the page for three minutes, while a real
    synthesis legitimately takes far longer than a health check ever should."""
    from app.config import TTS_STATUS_TIMEOUT, TTS_TIMEOUT
    assert TTS_STATUS_TIMEOUT < TTS_TIMEOUT

    engine.responses[("GET", "/health")] = FakeResponse(json_body=dict(HEALTHY))
    client.get("/admin/api/tts/health")
    assert engine.calls[-1]["timeout"] == TTS_STATUS_TIMEOUT

    engine.responses[("POST", "/tts")] = FakeResponse(content=b"wav")
    client.post("/admin/api/tts/preview", json={"text": "سلام"})
    assert engine.calls[-1]["timeout"] == TTS_TIMEOUT
