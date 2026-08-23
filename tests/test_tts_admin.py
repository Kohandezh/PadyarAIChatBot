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
        ("get", "/admin/api/tts/cache", {}),
        ("post", "/admin/api/tts/cache/warm", {}),
        ("post", "/admin/api/tts/cache/cleanup", {}),
        ("post", "/admin/api/tts/cache/clear", {}),
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
    # With a NON-EMPTY cache-buster: `?v=` on its own is one cacheable URL for
    # every release, which is the bug the buster exists to prevent.
    import re
    assert re.search(r"/static/admin/js/tts\.js\?v=\d+", res.text)
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

def test_preview_forwards_every_parameter_and_returns_the_audio(client, engine):
    engine.responses[("POST", "/tts")] = FakeResponse(
        content=b"ID3\x03audio",
        headers={"x-tts-cache": "miss", "x-tts-key": "abc123",
                 "content-type": "audio/mpeg"})

    res = client.post("/admin/api/tts/preview", json={
        "text": "سلام دنیا", "voice": "masoud",
        "exaggeration": 0.9, "cfg_weight": 0.65, "temperature": 1.2,
    })
    assert res.status_code == 200
    # The engine's content type, passed through rather than guessed. It serves
    # mp3 now, and a hardcoded audio/wav here left the browser to work the
    # format out from the bytes.
    assert res.headers["content-type"] == "audio/mpeg"
    assert res.content == b"ID3\x03audio"

    sent = engine.calls[-1]["json"]
    assert sent == {"text": "سلام دنیا", "voice": "masoud",
                    "exaggeration": 0.9, "cfg_weight": 0.65, "temperature": 1.2}


def test_preview_falls_back_to_mp3_when_the_engine_names_no_type(client, engine):
    """An engine too old to send a content type still plays, not downloads."""
    engine.responses[("POST", "/tts")] = FakeResponse(content=b"ID3\x03audio")
    res = client.post("/admin/api/tts/preview", json={"text": "سلام"})
    assert res.headers["content-type"] == "audio/mpeg"


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


# ── Saved defaults ────────────────────────────────────────────────────────
#
# Without these the three Chatterbox sliders are per-request only: an operator
# tunes a voice until it sounds right, reloads the page, and the tuning is gone.

def test_setting_bounds_match_what_chatterbox_accepts():
    from app.routers.tts import TTS_SETTING_BOUNDS
    assert TTS_SETTING_BOUNDS["exaggeration"][:2] == (0.25, 2.0)
    assert TTS_SETTING_BOUNDS["cfg_weight"][:2] == (0.2, 1.0)
    assert TTS_SETTING_BOUNDS["temperature"][:2] == (0.05, 5.0)


def test_setting_defaults_sit_inside_their_own_bounds():
    from app.routers.tts import TTS_SETTING_BOUNDS
    for key, (lo, hi, default) in TTS_SETTING_BOUNDS.items():
        assert lo <= default <= hi, f"{key} default {default} outside {lo}..{hi}"


def test_saved_settings_round_trip(client):
    res = client.post("/admin/api/tts/settings",
                      json={"exaggeration": 0.7, "cfg_weight": 0.65,
                            "temperature": 0.9, "voice": "sina"})
    assert res.status_code == 200, res.text

    got = client.get("/admin/api/tts/settings").json()
    assert abs(got["exaggeration"] - 0.7) < 1e-6
    assert abs(got["cfg_weight"] - 0.65) < 1e-6
    assert abs(got["temperature"] - 0.9) < 1e-6
    assert got["voice"] == "sina"


def test_unset_settings_fall_back_to_defaults(client):
    from app.routers.tts import TTS_SETTING_BOUNDS
    got = client.get("/admin/api/tts/settings").json()
    for key, (_lo, _hi, default) in TTS_SETTING_BOUNDS.items():
        assert got[key] == default
    assert got["voice"] == ""


@pytest.mark.parametrize("payload", [
    {"exaggeration": 5.0},        # above the model's usable range
    {"cfg_weight": 0.0},          # below it
    {"temperature": 99},          # far above
    {"exaggeration": "loud"},     # not a number at all
])
def test_out_of_range_settings_are_refused(client, payload):
    # The range inputs are a convenience, not a control — anything can POST here.
    assert client.post("/admin/api/tts/settings", json=payload).status_code == 400


def test_saved_voice_name_cannot_escape_the_voices_directory(client):
    res = client.post("/admin/api/tts/settings", json={"voice": "../../etc/passwd"})
    assert res.status_code == 400


def test_a_corrupted_setting_does_not_break_the_page(client):
    """A hand-edited row must degrade to the default, not 500 the panel."""
    from app.db.queries import set_setting
    set_setting("tts_exaggeration", "not-a-number")

    got = client.get("/admin/api/tts/settings").json()
    assert got["exaggeration"] == 0.5


# ── Ready-made audio ────────────────────────────────────────────────────
#
# The panel's promise is that a visitor never waits for an answer that was
# already written down. These cover the part of that promise this router owns:
# the operator names ANSWERS, and keys are never computed here.

def _seed_answers(*texts):
    """Put answers in the knowledge base the endpoints read from."""
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    for i, text in enumerate(texts, 1):
        conn.execute("INSERT INTO dataset (id, title, text, video_url)"
                     " VALUES (?, ?, ?, ?)", (f"a{i}", f"عنوان {i}", text, ""))
    conn.commit()
    conn.close()


CACHE_STATS = {"files": 12, "bytes": 3_500_000,
               "oldest": "2026-08-20T10:00:00+00:00",
               "newest": "2026-08-22T09:00:00+00:00"}


def test_cache_stats_pairs_the_engines_files_with_this_installs_answers(client, engine):
    _seed_answers("پاسخ یکم", "پاسخ دوم")
    engine.responses[("GET", "/cache/stats")] = FakeResponse(json_body=dict(CACHE_STATS))

    body = client.get("/admin/api/tts/cache").json()

    assert body["reachable"] is True
    assert body["files"] == 12
    # The number the engine cannot know: it has no concept of a dataset.
    assert body["answers"] == 2


def test_cache_stats_still_counts_answers_when_the_engine_is_down(client, engine):
    _seed_answers("پاسخ یکم", "پاسخ دوم", "پاسخ سوم")
    engine.responses[("GET", "/cache/stats")] = httpx.ConnectError("refused")

    body = client.get("/admin/api/tts/cache").json()

    assert body["reachable"] is False
    assert body["answers"] == 3          # this install's own truth, still true
    assert body["files"] == 0
    assert body["message_fa"]


# ── Waiting out a connection that died ──────────────────────────────────
#
# A real answer takes minutes on a P40 and no connection in the path survives
# that: Cloudflare cuts at 100 seconds, the app's client at 180. The work is
# shielded and still reaches the cache, so the page keeps asking whether it
# arrived instead of reporting a failure that did not happen.

@pytest.mark.parametrize("engine_state", ["ready", "working", "none"])
def test_status_passes_the_engine_verdict_straight_through(client, engine, engine_state):
    engine.responses[("POST", "/tts/status")] = FakeResponse(
        json_body={"key": "abc", "state": engine_state, "bytes": 7})

    res = client.post("/admin/api/tts/status", json={"text": "سلام"})

    assert res.status_code == 200
    assert res.json()["state"] == engine_state


def test_status_asks_about_the_text_that_was_actually_synthesised(client, engine):
    """Otherwise a word this install respells would poll forever: the key is
    derived from the spoken form, and the page would be asking about a key
    nothing will ever write."""
    client.post("/admin/api/tts/lexicon",
                json={"entries": [{"written": "دور", "spoken": "دوور"}]})
    engine.responses[("POST", "/tts/status")] = FakeResponse(
        json_body={"key": "abc", "state": "working", "bytes": 0})

    client.post("/admin/api/tts/status", json={"text": "اشیاء دور و نزدیک"})

    sent = [c for c in engine.calls if c["path"] == "/tts/status"][-1]["json"]
    assert sent["text"] == "اشیاء دوور و نزدیک"


def test_status_uses_the_short_timeout_not_the_generation_one(client, engine):
    """It is asked every 30 seconds while every card is busy. A poll that can
    hang for three minutes is not a poll."""
    from app.config import TTS_STATUS_TIMEOUT
    engine.responses[("POST", "/tts/status")] = FakeResponse(
        json_body={"key": "abc", "state": "working", "bytes": 0})

    client.post("/admin/api/tts/status", json={"text": "سلام"})

    assert engine.calls[-1]["timeout"] == TTS_STATUS_TIMEOUT


def test_a_stopped_engine_makes_the_poll_data_not_an_error(client, engine):
    """Same convention as /health. A failed poll is a state the page renders,
    not a trip down its 'something went wrong' path."""
    engine.responses[("POST", "/tts/status")] = httpx.ConnectError("refused")

    res = client.post("/admin/api/tts/status", json={"text": "سلام"})

    assert res.status_code == 200
    assert res.json()["reachable"] is False
    assert res.json()["state"] == "unknown"


def test_status_refuses_an_anonymous_caller(anon):
    assert anon.post("/admin/api/tts/status",
                     json={"text": "سلام"}).status_code in (401, 403)


# ── How words are read ──────────────────────────────────────────────────
#
# Persian writes no short vowels, so «دور» is both `duur` (far) and `dowr` (a
# turn) and the model has to guess. On the exhibition dataset it guessed wrong
# every time. These tests hold the one rule that makes the fix safe: a rule
# fires on a whole word and never inside one.

def test_a_rule_rewrites_the_word_and_not_the_word_it_sits_inside(client):
    from app.services import tts_lexicon
    client.post("/admin/api/tts/lexicon",
                json={"entries": [{"written": "دور", "spoken": "دوور"}]})

    # The INOTEX narration contains both, in one sentence.
    assert tts_lexicon.apply("اشیاء دور و نزدیک") == "اشیاء دوور و نزدیک"
    assert tts_lexicon.apply("دوربینِ شناخته‌شده") == "دوربینِ شناخته‌شده"


def test_a_diacritic_does_not_hide_the_end_of_a_word(client):
    """«دورِ» is «دور» with the operator's own ezafe on it, and still the word."""
    from app.services import tts_lexicon
    client.post("/admin/api/tts/lexicon",
                json={"entries": [{"written": "دور", "spoken": "دوور"}]})
    assert tts_lexicon.apply("دورِ زمین") == "دوورِ زمین"


def test_the_longer_rule_wins(client):
    from app.services import tts_lexicon
    client.post("/admin/api/tts/lexicon", json={"entries": [
        {"written": "عدسی", "spoken": "adasi"},
        {"written": "عدسی چشم", "spoken": "adasiye cheshm"},
    ]})
    assert tts_lexicon.apply("عدسی چشم") == "adasiye cheshm"


def test_a_rule_never_rewrites_what_another_rule_just_produced(client):
    """One pass. Otherwise the output depends on the order rows were typed in."""
    from app.services import tts_lexicon
    client.post("/admin/api/tts/lexicon", json={"entries": [
        {"written": "دور", "spoken": "دوور"},
        {"written": "دوور", "spoken": "چیز دیگری"},
    ]})
    assert tts_lexicon.apply("دور") == "دوور"


def test_the_saved_list_comes_back_as_it_was_typed(client):
    entries = [{"written": "دور", "spoken": "دوور"},
               {"written": "شبکیه", "spoken": "شَبَکیه"}]
    save = client.post("/admin/api/tts/lexicon", json={"entries": entries})
    assert save.status_code == 200
    assert client.get("/admin/api/tts/lexicon").json()["entries"] == entries


def test_an_empty_row_is_dropped_rather_than_refused(client):
    """The page adds a blank row when you click «کلمهٔ تازه»; saving without
    filling it in is not an error, it is a change of mind."""
    res = client.post("/admin/api/tts/lexicon", json={"entries": [
        {"written": "دور", "spoken": "دوور"},
        {"written": "", "spoken": ""},
    ]})
    assert res.status_code == 200
    assert res.json()["entries"] == [{"written": "دور", "spoken": "دوور"}]


@pytest.mark.parametrize("entries, because", [
    ([{"written": "دور", "spoken": ""}], "half a rule does nothing"),
    ([{"written": "", "spoken": "دوور"}], "half a rule does nothing"),
    ([{"written": "دور", "spoken": "الف"},
      {"written": "دور", "spoken": "ب"}], "two readings of one word"),
])
def test_a_rule_that_cannot_be_obeyed_is_refused_in_persian(client, entries, because):
    res = client.post("/admin/api/tts/lexicon", json={"entries": entries})
    assert res.status_code == 400, because
    assert res.json()["detail"]


def test_a_preview_is_generated_from_the_spoken_form(client, engine):
    client.post("/admin/api/tts/lexicon",
                json={"entries": [{"written": "دور", "spoken": "دوور"}]})
    engine.responses[("POST", "/tts")] = FakeResponse(content=b"audio")

    client.post("/admin/api/tts/preview", json={"text": "اشیاء دور و نزدیک"})

    assert engine.calls[-1]["json"]["text"] == "اشیاء دوور و نزدیک"


def test_warming_and_cleanup_agree_on_the_text_they_keyed_the_audio_by(client, engine):
    """The bug this prevents: warm renders the spoken form, cleanup asks about
    the stored form, the keys do not match, and cleanup deletes every clip it
    just made."""
    _seed_answers("اشیاء دور و نزدیک")
    client.post("/admin/api/tts/lexicon",
                json={"entries": [{"written": "دور", "spoken": "دوور"}]})
    engine.responses[("POST", "/prerender")] = FakeResponse(
        json_body={"total": 1, "rendered": 1, "skipped": 0, "failed": 0, "errors": []})
    engine.responses[("POST", "/cache/prune")] = FakeResponse(
        json_body={"deleted": 0, "kept": 1, "bytes": 0})

    client.post("/admin/api/tts/cache/warm")
    client.post("/admin/api/tts/cache/cleanup")

    warmed = [c for c in engine.calls if c["path"] == "/prerender"][-1]["json"]["texts"]
    kept = [c for c in engine.calls if c["path"] == "/cache/prune"][-1]["json"]["keep_texts"]
    assert warmed == ["اشیاء دوور و نزدیک"]
    assert kept == warmed


def test_the_lexicon_endpoints_refuse_an_anonymous_caller(anon):
    assert anon.get("/admin/api/tts/lexicon").status_code in (401, 403)
    assert anon.post("/admin/api/tts/lexicon",
                     json={"entries": []}).status_code in (401, 403)


def test_warming_sends_every_answer_with_the_saved_settings(client, engine):
    _seed_answers("پاسخ یکم", "پاسخ دوم")
    client.post("/admin/api/tts/settings",
                json={"exaggeration": 0.7, "cfg_weight": 0.4, "temperature": 0.9,
                      "voice": "narrator"})
    engine.responses[("POST", "/prerender")] = FakeResponse(
        json_body={"total": 2, "rendered": 2, "skipped": 0, "failed": 0, "errors": []})

    res = client.post("/admin/api/tts/cache/warm")

    assert res.status_code == 200
    sent = [c for c in engine.calls if c["path"] == "/prerender"][-1]["json"]
    assert sent["texts"] == ["پاسخ یکم", "پاسخ دوم"]
    # Warming with settings the panel would not later ask for fills the cache
    # with entries nothing ever hits, so these must be the SAVED ones.
    assert sent["exaggeration"] == 0.7
    assert sent["cfg_weight"] == 0.4
    assert sent["temperature"] == 0.9
    assert sent["voice"] == "narrator"


def test_warming_an_empty_knowledge_base_never_reaches_the_engine(client, engine):
    res = client.post("/admin/api/tts/cache/warm")
    assert res.status_code == 400
    assert engine.calls == []


def test_warming_gets_a_timeout_fit_for_a_whole_dataset(client, engine):
    _seed_answers("پاسخ یکم")
    engine.responses[("POST", "/prerender")] = FakeResponse(
        json_body={"total": 1, "rendered": 1, "skipped": 0, "failed": 0, "errors": []})

    client.post("/admin/api/tts/cache/warm")

    from app.config import TTS_TIMEOUT, TTS_PRERENDER_TIMEOUT
    sent = [c for c in engine.calls if c["path"] == "/prerender"][-1]
    assert sent["timeout"] == TTS_PRERENDER_TIMEOUT
    assert TTS_PRERENDER_TIMEOUT > TTS_TIMEOUT


def test_cleanup_sends_texts_and_never_computes_a_key(client, engine):
    _seed_answers("پاسخ یکم", "پاسخ دوم")
    engine.responses[("POST", "/cache/prune")] = FakeResponse(
        json_body={"deleted": 4, "freed_bytes": 900_000})

    res = client.post("/admin/api/tts/cache/cleanup")

    assert res.status_code == 200
    sent = [c for c in engine.calls if c["path"] == "/cache/prune"][-1]["json"]
    assert sent["keep_texts"] == ["پاسخ یکم", "پاسخ دوم"]
    # Keys are the engine's business. A key computed here could drift from the
    # one the engine looks up by, and a cleanup would delete live audio.
    assert not sent.get("keep")
    assert not sent.get("delete_all")


def test_cleanup_refuses_when_there_is_nothing_to_keep(client, engine):
    """An empty knowledge base makes cleanup indistinguishable from wiping."""
    res = client.post("/admin/api/tts/cache/cleanup")
    assert res.status_code == 400
    assert engine.calls == []


def test_clearing_everything_says_so_explicitly(client, engine):
    _seed_answers("پاسخ یکم")
    engine.responses[("POST", "/cache/prune")] = FakeResponse(
        json_body={"deleted": 9, "freed_bytes": 2_000_000})

    res = client.post("/admin/api/tts/cache/clear")

    assert res.status_code == 200
    sent = [c for c in engine.calls if c["path"] == "/cache/prune"][-1]["json"]
    assert sent["delete_all"] is True
    assert sent["keep"] == []


def test_a_stopped_engine_during_a_cleanup_is_a_persian_sentence(client, engine):
    _seed_answers("پاسخ یکم")
    engine.responses[("POST", "/cache/prune")] = httpx.ConnectError("refused")

    res = client.post("/admin/api/tts/cache/cleanup")

    assert res.status_code == 503
    assert "در دسترس نیست" in res.json()["detail"]
