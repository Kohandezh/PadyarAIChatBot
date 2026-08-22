"""The speech engine itself: deploy/tts/server.py.

This service is NOT part of the app. It runs as its own process on the GPU
host, out of /opt/padyar-tts, and it was never covered by this suite. It is
covered here because the things that broke in a load test are things a test can
hold still:

  * a cache hit must not wait behind a queue of generations (the handlers are
    async, and a waiting request costs a coroutine, not one of anyio's 40
    threads);
  * ten visitors asking the same new question must produce ONE generation;
  * `force` must actually re-roll the dice, because generation is sampled and
    a bad take would otherwise be cached forever;
  * a prune with an empty keep list must refuse rather than empty the cache;
  * a truncated clip must never reach the cache.

torch and chatterbox are not installed here and never will be: they are a
2.4 GB CUDA wheel for one specific 2016 card. torch is stubbed if absent, and
the model is replaced by one that returns a sine wave, so everything below the
model boundary (ffmpeg, the cache, the coalescing, the HTTP contract) is the
real code.
"""
import asyncio
import importlib.util
import os
import pathlib
import shutil
import sys
import tempfile
import time
import types

import numpy as np
import pytest
from fastapi.testclient import TestClient

REPO = pathlib.Path(__file__).resolve().parents[1]
SERVER_PY = REPO / "deploy" / "tts" / "server.py"

_CACHE = tempfile.mkdtemp(prefix="tts-test-cache-")
_VOICES = tempfile.mkdtemp(prefix="tts-test-voices-")


def _stub_torch() -> types.ModuleType:
    """Just enough torch for import time and /health. Never used for math."""
    cuda = types.SimpleNamespace(
        is_available=lambda: False,
        device_count=lambda: 0,
        get_device_name=lambda i=0: None,
        get_device_capability=lambda i=0: (0, 0),
        set_device=lambda i: None,
        synchronize=lambda i=None: None,
    )
    stub = types.ModuleType("torch")
    stub.__version__ = "stub"
    stub.cuda = cuda
    stub.set_num_threads = lambda n: None
    stub.get_num_threads = lambda: 1
    return stub


def _load_server():
    injected = False
    try:
        import torch                                    # noqa: F401
    except ImportError:
        sys.modules["torch"] = _stub_torch()
        injected = True
    # TTS_DEVICE=cpu keeps the CUDA branch of load_model() out of the way; the
    # model is replaced anyway, so no device is ever really touched.
    os.environ.update(TTS_CACHE_DIR=_CACHE, TTS_VOICES_DIR=_VOICES, TTS_DEVICE="cpu")
    # Loaded by path under its own name: "server" is far too common a module
    # name to claim on sys.path for the whole test session.
    spec = importlib.util.spec_from_file_location("padyar_tts_server", SERVER_PY)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        if injected:
            # Take the stub back OUT of sys.modules. server.py has bound its
            # own reference by now, and anything in the app that touches
            # scikit-learn makes scipy probe sys.modules["torch"].Tensor, which
            # a stub turns into an AttributeError in tests that have nothing to
            # do with speech.
            sys.modules.pop("torch", None)
    return module


server = _load_server()

HAS_FFMPEG = shutil.which("ffmpeg") is not None
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg is not installed")


# ── A model that behaves like Chatterbox without being it ───────────────

class FakeWav:
    """What model.generate() returns: something .squeeze().detach().cpu().numpy()s."""

    def __init__(self, samples):
        self._samples = samples

    def squeeze(self, _dim):
        return self

    def detach(self):
        return self

    def cpu(self):
        return self

    def numpy(self):
        return self._samples


class FakeModel:
    sr = 24000

    def __init__(self, seconds_per_char=0.077):
        # 0.077 s/char is the measured Persian rate the truncation guard uses,
        # so the default model produces clips the guard accepts.
        self.seconds_per_char = seconds_per_char
        self.calls = 0

    def generate(self, text, language_id=None, **kwargs):
        self.calls += 1
        count = max(1, int(self.sr * self.seconds_per_char * len(text)))
        t = np.linspace(0.0, count / self.sr, count, dtype=np.float32)
        return FakeWav(np.sin(2 * np.pi * 220.0 * t).astype(np.float32))


@pytest.fixture
def model():
    return FakeModel()


@pytest.fixture
def client(monkeypatch, model):
    """A live app whose one worker holds the fake model."""
    for entry in os.listdir(_CACHE):
        shutil.rmtree(os.path.join(_CACHE, entry), ignore_errors=True)
    monkeypatch.setattr(server, "load_model", lambda device=None: model)
    monkeypatch.setattr(server, "_workers", [], raising=False)
    with TestClient(server.app) as api:
        yield api


# ── Cache key ───────────────────────────────────────────────────────────

def test_cache_key_is_stable_for_identical_input():
    a = server.cache_key("سلام", "", 0.5, 0.5, "fa", 0.8)
    b = server.cache_key("سلام", "", 0.5, 0.5, "fa", 0.8)
    assert a == b and len(a) == 64


@pytest.mark.parametrize("changed", [
    {"text": "خداحافظ"},
    {"exaggeration": 0.6},
    {"cfg_weight": 0.6},
    {"language": "en"},
    {"temperature": 0.9},
])
def test_every_input_that_changes_the_waveform_changes_the_key(changed):
    base = dict(text="سلام", voice="", exaggeration=0.5, cfg_weight=0.5,
                language="fa", temperature=0.8)
    other = dict(base, **changed)
    assert server.cache_key(**base) != server.cache_key(**other)


def test_cache_path_is_mp3_and_fans_out():
    key = "ab" + "c" * 62
    path = server.cache_path(key)
    assert path.endswith(f"{key}.mp3")
    assert os.path.basename(os.path.dirname(path)) == "ab"


# ── The request path ────────────────────────────────────────────────────

@needs_ffmpeg
def test_first_request_generates_mp3_and_second_is_a_cache_hit(client, model):
    body = {"text": "سلام، به نمایشگاه اینوتکس خوش آمدید. امیدوارم روز خوبی داشته باشید."}

    first = client.post("/tts", json=body)
    assert first.status_code == 200
    assert first.headers["x-tts-cache"] == "miss"
    assert first.headers["content-type"] == "audio/mpeg"
    # ID3 tag or a raw MPEG frame sync. Either way it is not a RIFF header.
    assert first.content[:3] == b"ID3" or first.content[0] == 0xFF
    assert first.content[:4] != b"RIFF"
    assert os.path.exists(server.cache_path(first.headers["x-tts-key"]))
    generated = model.calls

    second = client.post("/tts", json=body)
    assert second.status_code == 200
    assert second.headers["x-tts-cache"] == "hit"
    assert second.headers["content-type"] == "audio/mpeg"
    assert second.content == first.content
    assert model.calls == generated, "a cache hit must not reach the model"


@needs_ffmpeg
def test_force_regenerates_and_overwrites_the_cached_file(client, model):
    body = {"text": "این پاسخ باید دوباره ساخته شود چون بار اول خوب از آب درنیامد."}
    first = client.post("/tts", json=body)
    assert first.headers["x-tts-cache"] == "miss"
    calls_after_first = model.calls

    forced = client.post("/tts", json=dict(body, force=True))
    assert forced.status_code == 200
    assert forced.headers["x-tts-cache"] == "miss"
    assert forced.headers["x-tts-key"] == first.headers["x-tts-key"], \
        "force must not change the key, or the cache would grow a second entry"
    assert model.calls > calls_after_first, "force must actually reach the model"

    # And the file on disk is the NEW take, not the one that came out badly.
    with open(server.cache_path(forced.headers["x-tts-key"]), "rb") as fh:
        assert fh.read() == forced.content


def test_empty_text_is_refused(client):
    assert client.post("/tts", json={"text": "   "}).status_code == 400


def test_overlong_text_is_refused(client):
    long_text = "ا" * (server.MAX_TEXT_CHARS + 1)
    assert client.post("/tts", json={"text": long_text}).status_code == 413


def test_unknown_voice_is_refused_before_any_generation(client, model):
    response = client.post("/tts", json={"text": "سلام دوباره", "voice": "nobody"})
    assert response.status_code == 400
    assert model.calls == 0


@needs_ffmpeg
def test_a_truncated_clip_is_never_cached(client, monkeypatch):
    # A model that returns a fraction of the audio the text calls for. This is
    # the failure a load test caught: HTTP 200, counted as a success, cached,
    # and then served to every later visitor asking that question.
    broken = FakeModel(seconds_per_char=0.005)
    monkeypatch.setattr(server, "load_model", lambda device=None: broken)
    monkeypatch.setattr(server, "_workers", [], raising=False)

    text = "این جمله به اندازه کافی بلند است تا نگهبان کوتاه‌شدگی آن را بسنجد و رد کند."
    response = client.post("/tts", json={"text": text})
    assert response.status_code == 502
    assert broken.calls >= 2, "the guard must retry once before refusing"
    key = server.cache_key(server.normalize(text), "", 0.5, 0.5, server.LANGUAGE, 0.8)
    assert not os.path.exists(server.cache_path(key))


# ── Coalescing ──────────────────────────────────────────────────────────

async def test_identical_concurrent_requests_generate_once(monkeypatch):
    """Ten visitors, one new question, one generation."""
    calls = []

    async def slow_synthesize(text, *args, **kwargs):
        calls.append(text)
        await asyncio.sleep(0.05)
        return b"audio-for-" + text.encode()

    monkeypatch.setattr(server, "synthesize", slow_synthesize)
    key = "coalesce" + "0" * 56
    req = server.SpeakRequest(text="one question")

    results = await asyncio.gather(
        *(server.generate_once(key, "one question", req) for _ in range(10))
    )

    assert len(calls) == 1
    assert all(r == b"audio-for-one question" for r in results)
    assert server._inflight == {}, "the in-flight entry must always be removed"
    os.remove(server.cache_path(key))


async def test_a_failed_generation_does_not_poison_the_key(monkeypatch):
    attempts = []

    async def flaky(text, *args, **kwargs):
        attempts.append(text)
        if len(attempts) == 1:
            raise RuntimeError("the card fell over")
        return b"second time lucky"

    monkeypatch.setattr(server, "synthesize", flaky)
    key = "poison" + "0" * 58
    req = server.SpeakRequest(text="q")

    with pytest.raises(RuntimeError):
        await server.generate_once(key, "q", req)
    assert server._inflight == {}, "a failure must not leave the key claimed"

    assert await server.generate_once(key, "q", req) == b"second time lucky"
    assert len(attempts) == 2
    os.remove(server.cache_path(key))


async def test_a_cancelled_caller_does_not_abandon_the_others(monkeypatch):
    """One visitor closes the tab. The other nine still get their audio."""
    async def slow(text, *args, **kwargs):
        await asyncio.sleep(0.05)
        return b"still here"

    monkeypatch.setattr(server, "synthesize", slow)
    key = "cancel" + "0" * 58
    req = server.SpeakRequest(text="q")

    quitter = asyncio.create_task(server.generate_once(key, "q", req))
    stayer = asyncio.create_task(server.generate_once(key, "q", req))
    await asyncio.sleep(0)          # let both join the same in-flight task
    quitter.cancel()

    assert await stayer == b"still here"
    os.remove(server.cache_path(key))


# ── Cache management ────────────────────────────────────────────────────

def _seed(key: str, payload: bytes = b"x" * 100, suffix: str = ".mp3") -> str:
    path = os.path.join(_CACHE, key[:2], f"{key}{suffix}")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(payload)
    return path


def test_cache_stats_counts_files_and_bytes(client):
    _seed("aa" + "1" * 62, b"a" * 10)
    _seed("bb" + "2" * 62, b"b" * 30)
    stats = client.get("/cache/stats").json()
    assert stats["files"] == 2
    assert stats["bytes"] == 40
    # ISO-8601 with an offset, so the admin panel can render a local time.
    assert stats["oldest"].startswith("20") and "+" in stats["oldest"]
    assert stats["newest"] >= stats["oldest"]


def test_cache_stats_on_an_empty_cache(client):
    stats = client.get("/cache/stats").json()
    assert stats == {"files": 0, "bytes": 0, "oldest": None, "newest": None}


def test_prune_with_an_empty_keep_list_refuses(client):
    kept = _seed("cc" + "3" * 62)
    response = client.post("/cache/prune", json={"keep": []})
    assert response.status_code == 400
    assert "delete_all" in response.json()["detail"]
    assert os.path.exists(kept), "a refused prune must delete nothing"


def test_prune_with_no_body_at_all_refuses(client):
    kept = _seed("cc" + "4" * 62)
    assert client.post("/cache/prune", json={}).status_code == 400
    assert os.path.exists(kept)


def test_prune_keeps_the_named_keys_and_deletes_the_rest(client):
    keep_key = "dd" + "5" * 62
    drop_key = "ee" + "6" * 62
    kept = _seed(keep_key, b"k" * 10)
    dropped = _seed(drop_key, b"d" * 25)

    result = client.post("/cache/prune", json={"keep": [keep_key]}).json()

    assert result == {"deleted": 1, "freed_bytes": 25}
    assert os.path.exists(kept)
    assert not os.path.exists(dropped)


def test_prune_sweeps_pre_mp3_wav_entries_even_for_a_kept_key(client):
    key = "ff" + "7" * 62
    stale = _seed(key, b"w" * 12, suffix=".wav")
    current = _seed(key, b"m" * 8)

    result = client.post("/cache/prune", json={"keep": [key]}).json()

    assert result["deleted"] == 1
    assert not os.path.exists(stale)
    assert os.path.exists(current)


def test_prune_everything_needs_delete_all(client):
    _seed("ab" + "8" * 62, b"z" * 7)
    result = client.post("/cache/prune", json={"keep": [], "delete_all": True}).json()
    assert result == {"deleted": 1, "freed_bytes": 7}
    assert client.get("/cache/stats").json()["files"] == 0


# ── Prerender ───────────────────────────────────────────────────────────

@needs_ffmpeg
def test_prerender_reports_made_skipped_and_failed(client):
    text = "این پاسخ از پیش ساخته می‌شود تا بازدیدکننده منتظر نماند."
    first = client.post("/prerender", json={"texts": [text, "  ", text]}).json()
    # The same text twice in one batch: rendered once, then already cached.
    assert first == {"total": 2, "rendered": 1, "cached_already": 1,
                     "failed": 0, "errors": []}

    again = client.post("/prerender", json={"texts": [text]}).json()
    assert again["rendered"] == 0 and again["cached_already"] == 1


def test_prerender_records_the_text_of_each_failure(client, monkeypatch):
    async def broken(*args, **kwargs):
        raise RuntimeError("no card")

    monkeypatch.setattr(server, "synthesize", broken)
    result = client.post("/prerender", json={"texts": ["یک", "دو"]}).json()
    assert result["failed"] == 2 and result["rendered"] == 0
    assert [e["error"] for e in result["errors"]] == ["no card", "no card"]


# ── Encoding ────────────────────────────────────────────────────────────

@needs_ffmpeg
def test_mp3_is_much_smaller_than_the_wav_it_came_from():
    samples = np.sin(np.linspace(0, 2000, 24000 * 10, dtype=np.float32))
    wav = server.to_wav_bytes(samples, 24000)
    mp3 = server.to_mp3_bytes(samples, 24000)
    assert mp3[:3] == b"ID3" or mp3[0] == 0xFF
    assert len(mp3) < len(wav) / 3


# ── Health ──────────────────────────────────────────────────────────────

def test_health_reports_the_loaded_workers(client):
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["model_loaded"] is True
    assert body["audio_format"] == "audio/mpeg"
    assert body["sample_rate"] == FakeModel.sr
    assert [w["device"] for w in body["workers"]] == ["cpu"]


def test_worker_devices_defaults_to_one_and_pins_a_card_per_worker(monkeypatch):
    monkeypatch.setattr(server, "DEVICE", "cuda")
    monkeypatch.setattr(server, "TTS_WORKERS", 1)
    assert server.worker_devices() == ["cuda"]
    monkeypatch.setattr(server, "TTS_WORKERS", 2)
    assert server.worker_devices() == ["cuda:0", "cuda:1"]
    # A CPU install never grows a second card out of nowhere.
    monkeypatch.setattr(server, "DEVICE", "cpu")
    assert server.worker_devices() == ["cpu", "cpu"]


# ── An unrecoverable CUDA context ───────────────────────────────────────
#
# The failure these cover took production down for six hours on 2026-08-22: one
# device-side assert poisoned the process's CUDA context, and because nothing
# detected it, every later /tts answered 500 while /health cheerfully reported
# model_loaded:true.

class ExplodingModel:
    """A model whose generate() raises. The failure has to come from INSIDE
    generation, on the worker thread, which is where the real one came from."""

    sr = 24000

    def __init__(self, exc):
        self.exc = exc

    def generate(self, _text, language_id=None, **_kwargs):
        raise self.exc


CUDA_ASSERT = ("CUDA error: device-side assert triggered\n"
               "CUDA kernel errors might be asynchronously reported at some "
               "other API call, so the stacktrace below might be incorrect.")


@pytest.mark.parametrize("device,exc,expected", [
    ("cuda", RuntimeError(CUDA_ASSERT), True),
    ("cuda", RuntimeError("CUDA error: an illegal memory access was encountered"), True),
    ("cuda", RuntimeError("unspecified launch failure"), True),
    # A plain bad request on the same card is NOT a reason to kill the process.
    ("cuda", RuntimeError("expected a 1-D tensor"), False),
    ("cuda", ValueError(CUDA_ASSERT), False),
    # On CPU there is no context to poison, so nothing here should ever fire.
    ("cpu", RuntimeError(CUDA_ASSERT), False),
])
def test_only_the_sticky_cuda_failures_count_as_poisoned(monkeypatch, device, exc,
                                                         expected):
    monkeypatch.setattr(server, "DEVICE", device)
    assert server.is_cuda_context_poisoned(exc) is expected


def test_a_poisoned_context_answers_503_and_exits_for_a_restart(client, monkeypatch):
    """The caller gets a real message, and the process arranges its own death.

    os._exit is replaced because a passing test that genuinely exits the
    interpreter is not a passing test.
    """
    monkeypatch.setattr(server, "DEVICE", "cuda")
    exits = []
    monkeypatch.setattr(server.os, "_exit", exits.append)
    # No real second of waiting inside the suite.
    monkeypatch.setattr(server.time, "sleep", lambda _s: None)

    monkeypatch.setattr(server, "load_model",
                        lambda device=None: ExplodingModel(RuntimeError(CUDA_ASSERT)))
    monkeypatch.setattr(server, "_workers", [], raising=False)

    response = client.post("/tts", json={"text": "متن تازه برای این تست"})

    assert response.status_code == 503
    # Not the opaque 500 that started all this: the body must say what happened.
    assert "restarting" in response.json()["detail"]
    # The exit runs on its own thread; give it a moment to be scheduled.
    for _ in range(50):
        if exits:
            break
        time.sleep(0.02)
    assert exits == [70]


def test_an_ordinary_generation_error_does_not_kill_the_process(client, monkeypatch):
    """Only the sticky kind exits. Everything else stays a plain failure."""
    monkeypatch.setattr(server, "DEVICE", "cuda")
    exits = []
    monkeypatch.setattr(server.os, "_exit", exits.append)

    monkeypatch.setattr(
        server, "load_model",
        lambda device=None: ExplodingModel(RuntimeError("expected a 1-D tensor")))
    monkeypatch.setattr(server, "_workers", [], raising=False)

    with pytest.raises(RuntimeError):
        client.post("/tts", json={"text": "متن دیگری برای این تست"})
    assert exits == []


# ── Pruning by answer, not by key ───────────────────────────────────────
#
# The admin panel knows answers; only this service knows how an answer becomes
# a key. These pin that down, because a caller that guessed keys wrong would
# not get an error — it would silently delete audio that was still in use.

def test_keep_texts_derives_the_same_key_the_cache_is_written_under(client):
    text = "پاسخی که باید بماند"
    key = server.cache_key(server.normalize(text), "", 0.5, 0.5, server.LANGUAGE, 0.8)
    _seed(key)
    doomed = _seed("f" * 64)

    res = client.post("/cache/prune", json={"keep_texts": [text]})

    assert res.status_code == 200
    assert os.path.exists(server.cache_path(key))       # named by its text
    assert not os.path.exists(doomed)
    assert res.json()["deleted"] == 1


def test_keep_texts_is_normalised_the_same_way_a_request_is(client):
    """Trailing spaces must not decide whether audio survives."""
    key = server.cache_key(server.normalize("متن با فاصله"), "", 0.5, 0.5,
                           server.LANGUAGE, 0.8)
    _seed(key)

    client.post("/cache/prune", json={"keep_texts": ["   متن با فاصله   "]})

    assert os.path.exists(server.cache_path(key))


def test_keep_texts_under_different_settings_does_not_protect_the_entry(client):
    """Settings are part of the key, so they are part of what survives.

    This is the failure a caller reimplementing keys would hit: same text,
    different sliders, different audio, different file.
    """
    key = server.cache_key(server.normalize("همین متن"), "", 0.5, 0.5,
                           server.LANGUAGE, 0.8)
    _seed(key)

    client.post("/cache/prune",
                json={"keep_texts": ["همین متن"], "exaggeration": 0.9})

    assert not os.path.exists(server.cache_path(key))


def test_keep_texts_of_only_blanks_still_refuses_to_wipe_the_cache(client):
    """Blank answers reduce to no keys, which must not read as delete_all."""
    survivor = _seed("a" * 64)

    res = client.post("/cache/prune", json={"keep_texts": ["", "   "]})

    assert res.status_code == 400
    assert os.path.exists(survivor)


def test_keys_and_texts_can_be_mixed_in_one_request(client):
    by_text = server.cache_key(server.normalize("با متن"), "", 0.5, 0.5,
                               server.LANGUAGE, 0.8)
    _seed(by_text)
    by_key = _seed("b" * 64)

    res = client.post("/cache/prune",
                      json={"keep_texts": ["با متن"], "keep": ["b" * 64]})

    assert res.status_code == 200
    assert os.path.exists(server.cache_path(by_text))
    assert os.path.exists(by_key)
    assert res.json()["deleted"] == 0
