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
import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, Response

from app.auth.security import verify_admin
from app.config import TTS_URL, TTS_TIMEOUT, TTS_STATUS_TIMEOUT, logger
from app.models import TTSPreviewRequest
from app.services import applog

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
    return Response(response.content, media_type="audio/wav", headers=headers)


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

@router.get("/secure-panel-inotex/ai/tts", response_class=HTMLResponse)
async def admin_tts_page(request: Request):
    """Same session check and login redirect as every other admin page
    (see app/routers/public.py). Living in the module's own router means an
    install without `tts` in ENABLED_MODULES has no such page at all, rather
    than a page that loads and then cannot do anything."""
    from app.routers.public import _render, _require_admin

    redirect = await _require_admin(request)
    if redirect:
        return redirect
    return _render("admin/tts.html", request=request, active_page="ai_tts")
