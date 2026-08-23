"""Admin panel → AI → Text to speech.

A THIN PROXY, ON PURPOSE. The Chatterbox service (deploy/tts/server.py) listens
on 127.0.0.1:8003 with no authentication of its own — that is safe only while
nothing outside the host can reach it. So the browser never talks to 8003; it
talks to these endpoints, which require an admin session, and they make the
loopback hop. Every rule about who may synthesise audio is enforced here.

Nothing in this module knows how speech is produced. It validates the three
Chatterbox parameters, forwards, and translates failures into Persian an
operator can act on — "the speech service is not running" rather than a
ConnectError traceback. That is the whole contract.
"""
import re

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, Response

from app.auth.security import verify_admin
from app.config import (TTS_URL, TTS_TIMEOUT, TTS_STATUS_TIMEOUT,
                        TTS_PRERENDER_TIMEOUT, logger)
from app.db.queries import get_setting, set_setting
from app.models import TTSPreviewRequest
from app.services import applog, tts_lexicon

router = APIRouter()

# One sentence, in Persian, for the two ways the hop itself fails. The page
# shows these verbatim, so they must read as instructions to a person and not
# as a diagnosis of a network stack.
UNREACHABLE_FA = ("سرویس تبدیل متن به صدا در دسترس نیست. "
                  "احتمالاً خاموش است — با پشتیبانی فنی تماس بگیرید.")
TIMEOUT_FA = ("سرویس تبدیل متن به صدا پاسخ نداد. "
              "متن را کوتاه‌تر کنید و دوباره تلاش کنید.")


def _upstream(path: str) -> str:
    return f"{TTS_URL}{path}"


def _fail(exc: Exception) -> HTTPException:
    """One place that turns a transport failure into an answer for the page.

    503 for "not running" and 504 for "did not answer in time" are kept
    distinct because they need different actions from the operator: start the
    service, versus wait or send less text.
    """
    if isinstance(exc, httpx.TimeoutException):
        return HTTPException(status_code=504, detail=TIMEOUT_FA)
    return HTTPException(status_code=503, detail=UNREACHABLE_FA)


def _detail_of(response: httpx.Response) -> str:
    """The upstream's own message, if it sent one worth showing."""
    try:
        body = response.json()
    except Exception:  # noqa: BLE001 — a non-JSON error body is not a message
        return ""
    detail = body.get("detail") if isinstance(body, dict) else ""
    if isinstance(detail, list):  # pydantic validation errors
        detail = "; ".join(str(d.get("msg", d)) for d in detail)
    return str(detail or "")


# ── Engine status ───────────────────────────────────────────────────────

@router.get("/admin/api/tts/health", dependencies=[Depends(verify_admin)])
async def tts_health():
    """Is the engine up, and is it on the GPU?

    Deliberately answers 200 with `reachable: false` when the service is down,
    instead of an error status. The page needs to RENDER that state — a red
    badge saying the engine is off — and an error status would send the JS down
    its "something went wrong" path for a situation that is perfectly well
    understood.
    """
    try:
        async with httpx.AsyncClient(timeout=TTS_STATUS_TIMEOUT) as client:
            response = await client.get(_upstream("/health"))
            response.raise_for_status()
            data = response.json()
    except Exception as exc:  # noqa: BLE001
        logger.warning("TTS health check failed: %s: %s", type(exc).__name__, exc)
        return {"reachable": False, "message_fa": UNREACHABLE_FA,
                "status": "down", "model_loaded": False}
    data["reachable"] = True
    return data


# ── Preview ─────────────────────────────────────────────────────────────

@router.post("/admin/api/tts/preview", dependencies=[Depends(verify_admin)])
async def tts_preview(req: TTSPreviewRequest):
    """Synthesise one piece of text and hand the wav straight back.

    The audio is streamed through rather than saved: a preview is something an
    admin listens to once while tuning a slider, and writing every attempt to
    disk would fill the media directory with takes nobody wants. The engine's
    own cache already keeps the ones that matter.
    """
    payload = req.model_dump()
    # How this installation wants its words read, applied on the way out. The
    # engine is told the spoken form and nothing else: it has no idea a lexicon
    # exists, and the operator never sees one in the box they typed into.
    payload["text"] = tts_lexicon.apply(payload["text"])
    try:
        async with httpx.AsyncClient(timeout=TTS_TIMEOUT) as client:
            response = await client.post(_upstream("/tts"), json=payload)
    except Exception as exc:  # noqa: BLE001
        raise _fail(exc) from exc

    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code,
                            detail=_detail_of(response) or "تولید صدا ممکن نشد")

    # X-TTS-Cache is why one request is instant and the next takes seconds.
    # Forwarding it is the difference between the page explaining that and the
    # operator concluding the service is unreliable.
    headers = {
        "X-TTS-Cache": response.headers.get("x-tts-cache", ""),
        "X-TTS-Key": response.headers.get("x-tts-key", ""),
        # Same-origin fetch cannot read a custom header unless it is exposed.
        "Access-Control-Expose-Headers": "X-TTS-Cache, X-TTS-Key",
        "Cache-Control": "no-store",
    }
    # Whatever the engine encoded, not a guess. It serves mp3 now, and
    # hardcoding audio/wav here left the browser to work the format out from
    # the bytes.
    return Response(response.content,
                    media_type=response.headers.get("content-type", "audio/mpeg"),
                    headers=headers)


# ── Cache ───────────────────────────────────────────────────────────────
#
# Generating an answer costs seconds of GPU. Every answer in the dataset is
# FIXED text, so it only ever has to be generated once — and if it is generated
# before a visitor asks, they never wait at all. That is what this section is
# for: see how much audio is stored, build the missing pieces, and throw away
# what no longer belongs to any answer.
#
# The operator never sees a cache key. They see answers.


def _dataset_texts() -> list:
    """Every answer currently in the knowledge base, in dataset order."""
    from app.db.connection import get_db_connection

    conn = get_db_connection()
    try:
        rows = conn.execute(
            "SELECT text FROM dataset WHERE text IS NOT NULL AND text <> ''"
            " ORDER BY id").fetchall()
    finally:
        conn.close()
    # The SPOKEN form, not the stored one. Warming and cleanup both call this,
    # and they must agree to the byte: a key is derived from the text that was
    # synthesised, so if cleanup asked about the stored wording it would decide
    # every warmed clip belonged to no answer and delete the lot.
    return [tts_lexicon.apply(str(r[0]).strip())
            for r in rows if str(r[0]).strip()]


def _generation_settings() -> dict:
    """The saved sliders, so warming produces the audio the panel would.

    Keys are derived from these values. Warm with different numbers than the
    service will later be asked for and every entry is a miss — the cache would
    fill up and still never be hit.
    """
    saved = _saved_tts_settings()
    return {"voice": saved["voice"], "exaggeration": saved["exaggeration"],
            "cfg_weight": saved["cfg_weight"], "temperature": saved["temperature"]}


@router.get("/admin/api/tts/cache", dependencies=[Depends(verify_admin)])
async def tts_cache_stats():
    """How much audio is stored, and how many answers could have some.

    `answers` comes from this install's own database, not the engine — the
    engine has no idea what a dataset is. Together they let the page say
    "27 files for 16 answers", which is the shape of the only question an
    operator actually has.
    """
    answers = len(_dataset_texts())
    try:
        async with httpx.AsyncClient(timeout=TTS_STATUS_TIMEOUT) as client:
            response = await client.get(_upstream("/cache/stats"))
            response.raise_for_status()
            data = response.json()
    except Exception as exc:  # noqa: BLE001
        logger.warning("TTS cache stats failed: %s: %s", type(exc).__name__, exc)
        return {"reachable": False, "message_fa": UNREACHABLE_FA, "answers": answers,
                "files": 0, "bytes": 0, "oldest": None, "newest": None}
    data["reachable"] = True
    data["answers"] = answers
    return data


@router.post("/admin/api/tts/cache/warm")
async def tts_cache_warm(username: str = Depends(verify_admin)):
    """Render every dataset answer that has no audio yet.

    Answers already in the cache are skipped by the engine, so pressing this
    twice is cheap and pressing it after adding one answer generates one clip.
    """
    texts = _dataset_texts()
    if not texts:
        raise HTTPException(status_code=400,
                            detail="هیچ پاسخی در پایگاه دانش نیست که صدا بسازیم")

    payload = {"texts": texts, **_generation_settings()}
    try:
        async with httpx.AsyncClient(timeout=TTS_PRERENDER_TIMEOUT) as client:
            response = await client.post(_upstream("/prerender"), json=payload)
    except Exception as exc:  # noqa: BLE001
        raise _fail(exc) from exc

    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code,
                            detail=_detail_of(response) or "ساخت صداها ممکن نشد")

    body = response.json()
    applog.audit("admin.tts.cache.warmed",
                 f"صدای {body.get('rendered', 0)} پاسخ ساخته شد",
                 actor=username,
                 metadata={k: body.get(k) for k in ("total", "rendered", "skipped")})
    return body


@router.post("/admin/api/tts/cache/cleanup")
async def tts_cache_cleanup(username: str = Depends(verify_admin)):
    """Delete stored audio that no current answer would ever ask for.

    The texts go up, not the keys: the engine derives keys with the same
    function it looks them up by, so a cleanup can never delete a live entry
    because two implementations disagreed. See PruneRequest.survivors().
    """
    texts = _dataset_texts()
    if not texts:
        raise HTTPException(
            status_code=400,
            detail="پایگاه دانش خالی است؛ برای پاک کردن همهٔ صداها از دکمهٔ حذف کامل استفاده کنید")

    payload = {"keep_texts": texts, **_generation_settings()}
    try:
        async with httpx.AsyncClient(timeout=TTS_TIMEOUT) as client:
            response = await client.post(_upstream("/cache/prune"), json=payload)
    except Exception as exc:  # noqa: BLE001
        raise _fail(exc) from exc

    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code,
                            detail=_detail_of(response) or "پاک‌سازی ممکن نشد")

    body = response.json()
    applog.audit("admin.tts.cache.cleaned",
                 f"{body.get('deleted', 0)} فایل صوتی بلااستفاده حذف شد",
                 actor=username, metadata=body)
    return body


@router.post("/admin/api/tts/cache/clear")
async def tts_cache_clear(username: str = Depends(verify_admin)):
    """Throw away ALL stored audio, including answers that are still live.

    Separate from cleanup because it is a different decision, not a stronger
    one: every clip here cost GPU time, and after this the next visitor to ask
    anything waits for it to be made again. delete_all is spelled out so the
    engine refuses an empty keep list that arrived by accident.
    """
    try:
        async with httpx.AsyncClient(timeout=TTS_TIMEOUT) as client:
            response = await client.post(_upstream("/cache/prune"),
                                         json={"keep": [], "delete_all": True})
    except Exception as exc:  # noqa: BLE001
        raise _fail(exc) from exc

    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code,
                            detail=_detail_of(response) or "حذف ممکن نشد")

    body = response.json()
    applog.audit("admin.tts.cache.cleared",
                 f"همهٔ صداهای ذخیره‌شده حذف شد ({body.get('deleted', 0)} فایل)",
                 actor=username, metadata=body)
    return body


# ── Voices ──────────────────────────────────────────────────────────────

@router.get("/admin/api/tts/voices", dependencies=[Depends(verify_admin)])
async def tts_voices():
    """Same convention as /health: a down engine is data, not an exception."""
    try:
        async with httpx.AsyncClient(timeout=TTS_STATUS_TIMEOUT) as client:
            response = await client.get(_upstream("/voices"))
            response.raise_for_status()
            data = response.json()
    except Exception as exc:  # noqa: BLE001
        logger.warning("TTS voice list failed: %s: %s", type(exc).__name__, exc)
        return {"voices": [], "default": "", "reachable": False,
                "message_fa": UNREACHABLE_FA}
    data["reachable"] = True
    return data


@router.post("/admin/api/tts/voices")
async def tts_add_voice(request: Request,
                        name: str = Form(...),
                        file: UploadFile = File(...),
                        username: str = Depends(verify_admin)):
    """Forward a recorded or uploaded reference clip to the engine.

    No validation of the audio happens here. The engine converts with ffmpeg
    and measures the RESULT, so it is the only party that can tell a 6-second
    clip from a 6-second container that decodes to nothing — duplicating a
    weaker version of that check here would only produce two different answers
    to the same question.
    """
    payload = await file.read()
    try:
        async with httpx.AsyncClient(timeout=TTS_TIMEOUT) as client:
            response = await client.post(
                _upstream("/voices"),
                data={"name": name},
                files={"file": (file.filename or "clip", payload,
                                file.content_type or "application/octet-stream")},
            )
    except Exception as exc:  # noqa: BLE001
        raise _fail(exc) from exc

    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code,
                            detail=_detail_of(response) or "ذخیرهٔ صدا ممکن نشد")

    body = response.json()
    # A stored voice changes what visitors hear, so it belongs in the audit
    # trail with the admin who put it there.
    applog.audit("admin.tts.voice.added",
                 f"نمونهٔ صدای «{body.get('name', '')}» ذخیره شد",
                 actor=username, target=str(body.get("name", "")),
                 metadata={"seconds": body.get("seconds"),
                           "replaced": body.get("replaced")})
    return body


@router.delete("/admin/api/tts/voices/{name}")
async def tts_delete_voice(name: str, username: str = Depends(verify_admin)):
    try:
        async with httpx.AsyncClient(timeout=TTS_STATUS_TIMEOUT) as client:
            response = await client.delete(_upstream(f"/voices/{name}"))
    except Exception as exc:  # noqa: BLE001
        raise _fail(exc) from exc

    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code,
                            detail=_detail_of(response) or "حذف صدا ممکن نشد")

    applog.audit("admin.tts.voice.removed", f"نمونهٔ صدای «{name}» حذف شد",
                 actor=username, target=name)
    return response.json()


# ── The page ────────────────────────────────────────────────────────────

# Saved defaults for the three Chatterbox controls, plus the default voice.
#
# Without these the sliders are per-request only: an operator tunes a voice
# until it sounds right, reloads the page, and the tuning is gone. Persisting
# them in `settings` (the same key-value table the white-label options use)
# makes the panel open on the values this installation actually chose.
#
# Bounds are enforced HERE as well as in the browser: the range inputs are a
# convenience, not a control — anything can POST this endpoint.
TTS_SETTING_BOUNDS = {
    "exaggeration": (0.25, 2.0, 0.5),
    "cfg_weight": (0.2, 1.0, 0.5),
    "temperature": (0.05, 5.0, 0.8),
}


def _saved_tts_settings() -> dict:
    out = {}
    for key, (_lo, _hi, default) in TTS_SETTING_BOUNDS.items():
        raw = get_setting(f"tts_{key}", "")
        try:
            out[key] = float(raw) if raw not in ("", None) else default
        except (TypeError, ValueError):
            # A hand-edited or corrupted row must not break the page.
            out[key] = default
    out["voice"] = get_setting("tts_voice", "") or ""
    return out


@router.get("/admin/api/tts/settings", dependencies=[Depends(verify_admin)])
async def tts_settings_get():
    return _saved_tts_settings()


@router.post("/admin/api/tts/settings", dependencies=[Depends(verify_admin)])
async def tts_settings_save(payload: dict):
    saved = {}
    for key, (lo, hi, _default) in TTS_SETTING_BOUNDS.items():
        if key not in payload:
            continue
        try:
            value = float(payload[key])
        except (TypeError, ValueError):
            raise HTTPException(status_code=400,
                                detail=f"{key} باید عدد باشد")
        if not lo <= value <= hi:
            raise HTTPException(
                status_code=400,
                detail=f"{key} باید بین {lo} و {hi} باشد")
        set_setting(f"tts_{key}", f"{value:.3f}")
        saved[key] = value

    if "voice" in payload:
        voice = str(payload["voice"] or "").strip()
        # Same character class the TTS service sanitises to, so a value saved
        # here can never name a file the service would refuse to serve.
        if voice and not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", voice):
            raise HTTPException(status_code=400, detail="نام صدا نامعتبر است")
        set_setting("tts_voice", voice)
        saved["voice"] = voice

    logger.info("TTS defaults saved: %s", saved)
    return {"status": "ok", "saved": saved}


# ── How words are read ──────────────────────────────────────────────────

@router.get("/admin/api/tts/lexicon", dependencies=[Depends(verify_admin)])
async def tts_lexicon_get():
    return {"entries": tts_lexicon.load()}


@router.post("/admin/api/tts/lexicon")
async def tts_lexicon_save(payload: dict, username: str = Depends(verify_admin)):
    """Replace the whole list.

    Whole list, not one row at a time: the page shows every rule at once and
    the operator edits them as a block, so anything else would need the browser
    to track which row was which and would break the moment two tabs are open.
    """
    entries = payload.get("entries")
    if not isinstance(entries, list):
        raise HTTPException(status_code=400, detail="فهرست کلمه‌ها نامعتبر است")
    try:
        saved = tts_lexicon.save(entries)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Audited because it changes what every visitor hears, and because a wrong
    # rule is invisible in the dataset: the answer's text never changed.
    applog.audit("admin.tts.lexicon.saved",
                 f"تلفظ {len(saved)} کلمه ذخیره شد",
                 actor=username, metadata={"count": len(saved)})
    return {"status": "ok", "entries": saved}


@router.get("/secure-panel-inotex/ai/tts", response_class=HTMLResponse)
async def admin_tts_page(request: Request):
    """Same session check and login redirect as every other admin page
    (see app/routers/public.py). Living in the module's own router means an
    install without `tts` in ENABLED_MODULES has no such page at all, rather
    than a page that loads and then cannot do anything."""
    from app.routers.public import _render, _require_admin, admin_js_version

    redirect = await _require_admin(request)
    if redirect:
        return redirect
    return _render("admin/tts.html", request=request, active_page="ai_tts",
                   js_version=admin_js_version("tts.js"))
