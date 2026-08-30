import io
import asyncio
import csv
import re
import secrets
import datetime

from fastapi import APIRouter, HTTPException, Request, Depends, Response, UploadFile, File
from fastapi.responses import JSONResponse, StreamingResponse, FileResponse

from app.models import (
    SmsSettingsRequest, SmsTestRequest,
    LoginRequest, ToggleRequest, ChangePasswordRequest,
    ChangeSecurityQuestionRequest, BackupScheduleRequest,
    AssistantContentRequest, AIConnectionRequest, WhitelabelBrandingRequest,
    MenuSettingsRequest,
    IdleVideosRequest,
)
from app.services import embeddings as embeddings_service
from app.services import applog
from app.services import secure_store
from app.config import (
    logger, MAX_LOGIN_ATTEMPTS, BLOCK_TIME_MINUTES,
    SESSION_TIMEOUT_HOURS, ADMIN_COOKIE_NAME, COOKIE_SECURE,
    is_module_enabled,
)
from app.auth.security import (
    verify_admin, login_block_active, record_failed_login, clear_login_attempts,
    hash_password, verify_password, is_legacy_hash,
    hash_security_answer, verify_security_answer, is_legacy_answer_hash,
    timing_equalize,
    client_ip as resolve_client_ip,
)
from app.db.connection import get_db_connection
from app.db.queries import get_setting, set_setting
from app import services as svc


router = APIRouter()


def _retire_bootstrap_credentials(username: str, client_ip: str) -> None:
    """Delete ADMIN_CREDENTIALS.txt once someone has actually logged in.

    The file exists only so the operator can get in the first time; the file
    itself says "log in, change these, then delete this file". In practice
    nobody deletes it, so a plaintext admin password sits on disk for the life
    of the installation. A successful login is the moment it stops being
    needed.

    Best-effort by design: a failure here must never turn a good login into a
    failed one, so every error is swallowed after being logged.
    """
    import os
    from app.config import DB_PATH
    try:
        folder = os.path.dirname(os.path.abspath(DB_PATH)) or "."
        path = os.path.join(folder, "ADMIN_CREDENTIALS.txt")
        if not os.path.exists(path):
            return
        os.remove(path)
        applog.audit("auth.bootstrap_credentials.removed",
                     "فایل رمز اولیه پس از ورود موفق حذف شد",
                     actor=username, actor_type="admin", ip=client_ip,
                     target=path, outcome="ok")
    except Exception as exc:                      # noqa: BLE001
        logger.warning("Could not remove bootstrap credentials file: %s", exc)


@router.post("/admin/login")
async def admin_login(creds: LoginRequest, request: Request):
    client_ip = resolve_client_ip(request)

    # 1. Brute-force check. The counter is a database row, so it survives a
    #    restart and is shared by every worker (app/auth/security.py).
    if login_block_active(client_ip):
        applog.security("auth.login.while_blocked",
                        f"تلاش ورود از {client_ip} در دورهٔ مسدودی",
                        level="warning", actor=creds.username, actor_type="admin",
                        ip=client_ip, target="admin-login", outcome="denied",
                        user_agent=request.headers.get("user-agent", ""))
        raise HTTPException(status_code=429, detail="Too many failed attempts. Try again later.")

    conn = get_db_connection()
    user = conn.execute('SELECT * FROM admins WHERE username = ?', (creds.username,)).fetchone()
    conn.close()

    auth_success = False
    password_ok = False
    if user:
        salt = user['salt'] if 'salt' in user.keys() and user['salt'] else ""
        # bcrypt runs ~0.5s of CPU — off the event loop, or one login stalls
        # every concurrent visitor routed to this worker. The security answer
        # verify and every upgrade hash below are bcrypt too (legacy rows are
        # plain SHA-256, but the cost is decided by the STORED hash).
        password_ok = await asyncio.to_thread(
            verify_password, creds.password, user['password_hash'], salt)
        answer_ok = await asyncio.to_thread(
            verify_security_answer, creds.sec_answer, user['security_answer_hash'])
        if password_ok and answer_ok:
            auth_success = True
            # Transparent upgrade: re-hash legacy SHA-256 passwords with bcrypt.
            if is_legacy_hash(user['password_hash']):
                new_hash = await asyncio.to_thread(hash_password, creds.password)
                conn = get_db_connection()
                conn.execute(
                    'UPDATE admins SET password_hash = ?, salt = ? WHERE username = ?',
                    (new_hash, '', creds.username)
                )
                conn.commit()
                conn.close()
                logger.info(f"Upgraded password hash to bcrypt for: {creds.username}")
            # Same upgrade for a legacy unsalted-SHA-256 security answer.
            if is_legacy_answer_hash(user['security_answer_hash']):
                new_answer_hash = await asyncio.to_thread(
                    hash_security_answer, creds.sec_answer)
                conn = get_db_connection()
                conn.execute(
                    'UPDATE admins SET security_answer_hash = ? WHERE username = ?',
                    (new_answer_hash, creds.username)
                )
                conn.commit()
                conn.close()
                logger.info(f"Upgraded security-answer hash to bcrypt for: {creds.username}")
    else:
        # A real username costs TWO bcrypt verifies, one for the password and
        # one for the security answer. An invented one has to cost the same,
        # or the response time itself tells an attacker which usernames exist.
        await asyncio.to_thread(
            timing_equalize, creds.password, creds.sec_answer)

    if not auth_success:
        # The REASON is recorded, never the submitted password or answer.
        # "unknown_user" vs "bad_password" is operationally vital (a sweep of
        # invented usernames looks nothing like one admin mistyping), and it
        # leaks nothing an attacker does not already know about their own input.
        if not user:
            reason = "unknown_user"
        elif not password_ok:
            reason = "bad_password"
        else:
            reason = "bad_security_answer"
        # Count the failure first: the recorded total is what the audit row
        # should carry, and it is the number the block decision is made on.
        attempts = record_failed_login(client_ip)
        applog.security("auth.login.failed",
                        f"ورود ناموفق مدیر ({reason})",
                        level="warning", actor=creds.username, actor_type="admin",
                        ip=client_ip, target="admin-login", outcome="failed",
                        error_code=reason,
                        user_agent=request.headers.get("user-agent", ""),
                        metadata={"reason": reason, "attempts_so_far": attempts})

        if attempts >= MAX_LOGIN_ATTEMPTS:
            applog.security("auth.bruteforce.blocked",
                            f"ورود از {client_ip} به دلیل تلاش‌های مکرر مسدود شد",
                            level="critical", actor=creds.username, actor_type="admin",
                            ip=client_ip, target="admin-login", outcome="blocked",
                            user_agent=request.headers.get("user-agent", ""),
                            metadata={"attempts": attempts,
                                      "block_minutes": BLOCK_TIME_MINUTES})
            raise HTTPException(status_code=429, detail="Too many failed attempts. You are blocked.")

        raise HTTPException(status_code=401, detail="Bad credentials")

    # Success - Reset attempts
    clear_login_attempts(client_ip)

    # Generate Token. Expiry is AWARE UTC: the column is TIMESTAMPTZ, and a
    # naive local "+1 hour" is stored as if it were UTC — on a host behind
    # UTC the fresh session is born already expired. Same class of bug as the
    # session slide in app/auth/security.py.
    token = secrets.token_hex(32)
    expiry = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=SESSION_TIMEOUT_HOURS)

    conn = get_db_connection()
    conn.execute('INSERT INTO admin_sessions (token, username, expiry) VALUES (?, ?, ?)', (token, creds.username, expiry.isoformat()))
    conn.commit()
    conn.close()

    applog.audit("auth.login.success", "ورود موفق مدیر",
                 actor=creds.username, target="admin-panel", outcome="ok",
                 ip=client_ip, actor_type="admin",
                 user_agent=request.headers.get("user-agent", ""))

    _retire_bootstrap_credentials(creds.username, client_ip)

    response = JSONResponse({"status": "success"})
    response.set_cookie(
        key=ADMIN_COOKIE_NAME,
        value=token,
        httponly=True,
        secure=COOKIE_SECURE,  # env-driven: true in production (HTTPS)
        samesite="lax",
        max_age=SESSION_TIMEOUT_HOURS * 3600
    )

    return response


@router.post("/admin/logout")
async def admin_logout(request: Request, response: Response):
    token = request.cookies.get(ADMIN_COOKIE_NAME)
    if token:
        conn = get_db_connection()
        conn.execute('DELETE FROM admin_sessions WHERE token = ?', (token,))
        conn.commit()
        conn.close()
    response.delete_cookie(ADMIN_COOKIE_NAME)
    applog.audit("auth.logout", "خروج مدیر", target="admin-panel", outcome="ok",
                 actor_type="admin",
                 ip=resolve_client_ip(request))
    return {"status": "logged_out"}


@router.get("/admin/csrf")
async def csrf_token(request: Request, username: str = Depends(verify_admin)):
    """The CSRF token for THIS session. Requires authentication, so it cannot
    be harvested cross-origin by an unauthenticated page."""
    from app.auth.csrf import token_for_request
    return {"csrf_token": token_for_request(request)}


@router.get("/admin/check_auth")
async def check_auth(username: str = Depends(verify_admin)):
    return {"authenticated": True}


@router.get("/admin/api/stats", dependencies=[Depends(verify_admin)])
async def get_stats():
    conn = get_db_connection()
    total_tokens = conn.execute('SELECT SUM(tokens) FROM chat_logs').fetchone()[0] or 0
    total_cost = conn.execute('SELECT SUM(cost) FROM chat_logs').fetchone()[0] or 0.0
    total_messages = conn.execute('SELECT COUNT(*) FROM chat_logs').fetchone()[0] or 0

    daily_query = '''
        SELECT
            date(created_at) as date_label,
            COUNT(*) as msg_count,
            SUM(cost) as total_cost
        FROM chat_logs
        WHERE created_at >= date('now', '-7 days')
        GROUP BY date_label
        ORDER BY date_label ASC
    '''
    daily_rows = conn.execute(daily_query).fetchall()

    # Which tier answered, over the last day. Until this existed the endpoint
    # reported only totals, so "read the ai_options rows on day one" meant
    # opening a psql shell during an exhibition — and today's tier
    # distribution was invisible too. A bot that asks "which one?" about
    # questions it could have answered now shows up as a number an operator
    # can watch during the first hour of an opening.
    source_rows = conn.execute('''
        SELECT source, COUNT(*) as answer_count
        FROM chat_logs
        WHERE created_at >= datetime('now', '-24 hours')
        GROUP BY source
        ORDER BY answer_count DESC
    ''').fetchall()
    conn.close()

    daily_stats = []
    for row in daily_rows:
        daily_stats.append({
            "date": row['date_label'],
            "count": row['msg_count'],
            "cost": row['total_cost'] or 0.0
        })

    by_source = [{"source": row['source'] or "", "count": row['answer_count']}
                 for row in source_rows]

    return {
        "total_tokens": total_tokens,
        "total_cost": total_cost,
        "total_messages": total_messages,
        "daily_stats": daily_stats,
        "by_source": by_source
    }


@router.get("/admin/api/settings", dependencies=[Depends(verify_admin)])
async def get_settings_api():
    enabled = get_setting('openai_enabled', 'true') == 'true'
    voice_enabled = get_setting('voice_enabled', 'true') == 'true'
    tts_enabled = get_setting('tts_enabled', 'true') == 'true'
    return {"openai_enabled": enabled, "voice_enabled": voice_enabled, "tts_enabled": tts_enabled}


@router.post("/admin/api/toggle_openai", dependencies=[Depends(verify_admin)])
async def toggle_openai(req: ToggleRequest):
    set_setting('openai_enabled', 'true' if req.enabled else 'false')
    return {"status": "updated"}


@router.post("/admin/api/toggle_voice", dependencies=[Depends(verify_admin)])
async def toggle_voice(req: ToggleRequest):
    set_setting('voice_enabled', 'true' if req.enabled else 'false')
    return {"status": "updated"}


@router.post("/admin/api/toggle_tts", dependencies=[Depends(verify_admin)])
async def toggle_tts(req: ToggleRequest):
    set_setting('tts_enabled', 'true' if req.enabled else 'false')
    return {"status": "updated"}


# --- AI provider connection (per-install: the owner's own endpoint + key) ---

@router.get("/admin/api/sms", dependencies=[Depends(verify_admin)])
async def get_sms_settings():
    """Never returns the password or API key — only whether each is stored."""
    from app.services import sms as sms_service
    provider = get_setting("sms_provider", "") or "dev"
    return {
        "enabled": get_setting("registration_enabled", "false") == "true",
        "provider": provider,
        "username": get_setting("sms_asanak_username", ""),
        "source": get_setting("sms_asanak_source", ""),
        "template_id": get_setting("sms_asanak_template_id", ""),
        # sms_service.setting() (not get_setting()) on purpose: it resolves to
        # the built-in default text when nothing is stored yet, so the admin
        # panel shows a working example out of the box instead of an empty box.
        "invite_text": sms_service.setting("sms_asanak_invite_text"),
        "reject_text": sms_service.setting("sms_asanak_reject_text"),
        "daily_budget": get_setting("sms_daily_budget", "0"),
        # Today's spend, so the cap is a number the operator can see filling up
        # instead of a wall they hit during an event.
        "sent_today": sms_service.sent_today(),
        "url": get_setting("sms_asanak_url", ""),
        "url_default": sms_service.ASANAK_DEFAULT_URL,
        "status_url": get_setting("sms_asanak_status_url", ""),
        "status_url_default": sms_service.ASANAK_STATUS_URL,
        "credit_url": get_setting("sms_asanak_credit_url", ""),
        "credit_url_default": sms_service.ASANAK_CREDIT_URL,
        "template_url": get_setting("sms_asanak_template_url", ""),
        "template_url_default": sms_service.ASANAK_TEMPLATE_URL,
        "trim": get_setting("sms_asanak_trim", "true") != "false",
        "send_to_blacklist": get_setting("sms_asanak_send_to_blacklist", "1") != "0",
        "sms_host": get_setting("otp_sms_host", ""),
        # Booleans only. The values are encrypted at rest and never leave the
        # server — see app/services/secure_store.py.
        "has_password": bool((get_setting("sms_asanak_password", "") or "").strip()),
        "has_api_key": bool((get_setting("sms_asanak_api_key", "") or "").strip()),
        "configured": sms_service.is_configured("asanak"),
    }


@router.post("/admin/api/sms", dependencies=[Depends(verify_admin)])
async def save_sms_settings(req: SmsSettingsRequest):
    """Store the gateway settings in the settings table AND in `.env`.

    The two secrets are encrypted before either store sees them, and an empty
    secret field means "keep the stored one" — so an operator can edit the
    sender number without re-entering the password.
    """
    from app.services import sms as sms_service
    # A budget that cannot be read is treated as 0 (no cap) by the sender, so a
    # typo here would silently remove the cap. Refuse it while the operator is
    # still looking at the form. int() also accepts Persian digits and returns
    # them as a plain number, so ۱۰۰ typed on a Persian keyboard is stored 100.
    try:
        budget = str(max(0, int((req.daily_budget or "0").strip() or "0")))
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="سقف روزانه پیامک باید یک عدد باشد. برای برداشتن سقف، ۰ بگذارید.")

    set_setting("registration_enabled", "true" if req.enabled else "false")
    try:
        env_written = sms_service.save_settings({
            "sms_provider": req.provider.strip() or "dev",
            "sms_asanak_username": req.username,
            "sms_asanak_password": req.password,
            "sms_asanak_api_key": req.api_key,
            "sms_asanak_source": req.source,
            "sms_asanak_template_id": req.template_id,
            "sms_asanak_invite_text": req.invite_text,
            "sms_asanak_reject_text": req.reject_text,
            "sms_daily_budget": budget,
            "sms_asanak_url": req.url,
            "sms_asanak_status_url": req.status_url,
            "sms_asanak_credit_url": req.credit_url,
            "sms_asanak_template_url": req.template_url,
            "sms_asanak_trim": "true" if req.trim else "false",
            "sms_asanak_send_to_blacklist": "1" if req.send_to_blacklist else "0",
            "otp_sms_host": req.sms_host,
        })
    except sms_service.SmsError as e:
        # The one refusal save_settings can raise today is the production
        # dev-provider block; its detail is the sentence the operator reads.
        raise HTTPException(status_code=400, detail=e.detail)
    return {"status": "updated", "env_file": env_written}


@router.get("/admin/api/sms/credit", dependencies=[Depends(verify_admin)])
async def sms_credit():
    """Remaining gateway credit — proves the credentials without sending an SMS."""
    from app.services import sms as sms_service
    provider = (get_setting("sms_provider", "") or "dev").strip()
    if provider == "dev":
        # Say WHERE. The old wording named a setting without saying how to reach
        # it, and the control is a TAB STRIP rather than a labelled field — an
        # operator with every credential filled in read this as a panel bug.
        raise HTTPException(
            status_code=400,
            detail="سرویس‌دهندهٔ فعال «dev» است و پیامک واقعی فرستاده نمی‌شود. "
                   "بالای همین صفحه تب «آسانک» را انتخاب کنید، سپس «ذخیره تنظیمات» "
                   "را بزنید و دوباره امتحان کنید.")
    if not sms_service.is_configured(provider):
        raise HTTPException(status_code=400, detail="ابتدا نام کاربری، رمز عبور و شماره فرستنده را ذخیره کنید.")
    try:
        return {"credit": sms_service.credit(provider)}
    except sms_service.SmsError as e:
        raise HTTPException(status_code=502, detail=e.detail)


@router.post("/admin/api/sms/test", dependencies=[Depends(verify_admin)])
async def test_sms(req: SmsTestRequest):
    """Send one real test message with the STORED credentials.

    This is how an operator verifies the gateway without anyone having to
    paste a password anywhere but this panel.
    """
    from app.services import sms as sms_service
    from app.services.otp import normalize_destination, mask_destination
    destination = normalize_destination(req.destination)
    if destination is None:
        raise HTTPException(status_code=400, detail="شماره واردشده معتبر نیست.")
    provider = (get_setting("sms_provider", "") or "dev").strip()
    if provider == "dev":
        # Say WHERE. The old wording named a setting without saying how to reach
        # it, and the control is a TAB STRIP rather than a labelled field — an
        # operator with every credential filled in read this as a panel bug.
        raise HTTPException(
            status_code=400,
            detail="سرویس‌دهندهٔ فعال «dev» است و پیامک واقعی فرستاده نمی‌شود. "
                   "بالای همین صفحه تب «آسانک» را انتخاب کنید، سپس «ذخیره تنظیمات» "
                   "را بزنید و دوباره امتحان کنید.")
    # A throwaway code, not a real one. On a service line the only content that
    # reaches a handset is the approved template, so a test that sent plain
    # text would "succeed" and still never arrive — exactly the false green
    # that sent an operator hunting for a bug in this panel.
    probe_code = f"{secrets.randbelow(100000):05d}"
    try:
        msgid = sms_service.send(provider, destination,
                                 "پیام آزمایشی سامانهٔ اینوتکس", code=probe_code)
    except sms_service.SmsError as e:
        # The operator sees the gateway's own reason (expired web-service
        # password, no credit, sender barred from links...). A visitor never
        # does — the OTP path shows str(e), which stays generic.
        raise HTTPException(status_code=502, detail=e.detail)
    # "queued", not "sent": the gateway accepting a message is not the handset
    # receiving it. The id is what an operator quotes to Asanak support when a
    # message with a perfectly successful response never arrives.
    return {"status": "queued", "destination": mask_destination(destination), "msgid": msgid}


def _stt_status() -> dict:
    """Transcription binding summary for the settings page.

    Never returns a secret — only which provider serves transcription, which
    model, and whether it resolves at all. Failure to introspect must not take
    the whole settings page down, so it degrades to "unknown".
    """
    try:
        from app.services.ai import stt
        return stt.status()
    except Exception:  # noqa: BLE001
        return {"configured": False, "source": "", "model": "",
                "instance_id": "", "provider_display_name": "",
                "detail_fa": "وضعیت رونویسی قابل تشخیص نیست."}


@router.get("/admin/api/ai-connection", dependencies=[Depends(verify_admin)])
async def get_ai_connection():
    from app.config import OPENAI_API_BASE
    from app.services.health import eligible_target_counts
    return {
        "api_base": get_setting("ai_api_base", ""),
        "api_base_default": OPENAI_API_BASE,
        # The key itself never leaves the server — only whether one exists.
        # (get_setting transparently decrypts the at-rest form.)
        "has_key": bool((get_setting("ai_api_key", "") or "").strip()),
        # Routing reality, not just key presence: `has_key` alone used to
        # read as "AI works" while zero eligible route targets meant every
        # chat/classify call failed. Same counts the health probe reports,
        # so the settings page and /admin/health can never disagree.
        "routes": eligible_target_counts(),
        # `model_chat` / `model_classify` are DEPRECATED and no longer read by
        # the runtime — the AI Control Plane's routes decide those. They are
        # still returned so an older cached admin page does not break on a
        # missing key, but they are reported as inert rather than as settings.
        "model_chat": get_setting("ai_model_chat", ""),
        "model_classify": get_setting("ai_model_classify", ""),
        "model_chat_deprecated": True,
        "model_classify_deprecated": True,
        "routing_url": "/secure-panel-inotex/ai/routing",
        "model_stt": get_setting("ai_model_stt", ""),
        # Where transcription actually gets its credential. Never the secret.
        "stt": _stt_status(),
        "feature_tts": get_setting("tts_enabled", "true") == "true",
        "feature_stt": get_setting("voice_enabled", "true") == "true",
        "embedding_available": embeddings_service.available(),
        "default_lang": get_setting("default_chat_lang", "fa"),
    }


@router.post("/admin/api/ai-connection", dependencies=[Depends(verify_admin)])
async def save_ai_connection(req: AIConnectionRequest):
    set_setting("ai_api_base", req.api_base.strip())
    if req.api_key.strip():
        # Encrypted at rest, like every other secret the panel stores — a
        # plaintext row would ship in every downloadable database backup.
        set_setting("ai_api_key", secure_store.protect(req.api_key.strip()))
    # `model_chat` / `model_classify` are accepted for backward compatibility
    # with an older client, but deliberately NOT persisted: the runtime reads
    # route targets from the AI Control Plane, so storing them would recreate
    # exactly the bug this removes — a form that reports success and changes
    # nothing. `model_stt` IS still meaningful; it names the transcription
    # model (see app/services/ai/stt.py).
    set_setting("ai_model_stt", req.model_stt.strip())
    set_setting("tts_enabled", "true" if req.feature_tts else "false")
    set_setting("voice_enabled", "true" if req.feature_stt else "false")
    set_setting("default_chat_lang", req.default_lang if req.default_lang in ("fa", "en") else "fa")
    # Bridge this save into the AI control plane (the chat/classify engine
    # routes exclusively off control-plane tables — a save that only wrote
    # the legacy rows above left Tier 2 dead while this endpoint answered
    # 200). ensure_panel_provider is idempotent, fills only missing pieces,
    # never touches hand-built routes or the enabled flags, and rotates the
    # default instance's secret — see app/services/ai/legacy_import.py.
    from app.services.ai import legacy_import
    from app.services.ai.errors import AIError
    from app.services.openai import provider_config
    # Empty submit = keep the stored key (same semantics as the write above).
    # With no submitted AND no stored key, ensure is skipped entirely: there
    # is nothing routable to build, and create_instance rejects secret-less
    # providers — a keyless save must not 500 on "provider requires an API
    # key". With a stored key, ensure still runs so base-url changes and
    # missing-route repairs work without re-entering the secret.
    key_for_ensure = req.api_key.strip() or (get_setting("ai_api_key", "") or "").strip()
    if key_for_ensure:
        # Base pinned to the submitted value, else the legacy ai_api_base
        # row / OPENAI_API_BASE — an empty string must never reach
        # create/update_instance (adapter validation would reject it or
        # store a broken base_url on an otherwise working instance).
        base_for_ensure = req.api_base.strip() or provider_config()[0]
        try:
            legacy_import.ensure_panel_provider(base_for_ensure, key_for_ensure,
                                                actor="admin")
        except AIError as e:
            # Owner ruling: HTTP 500 with detail — the save did not do what
            # the screen promised, and a silent partial success is exactly
            # the bug class this fixes. No 200-with-warning soft mode.
            # (Unexpected non-AI errors propagate as a plain 500 either way.)
            raise HTTPException(status_code=500, detail=e.message_fa)
    return {"status": "updated"}


@router.get("/admin/api/assistant", dependencies=[Depends(verify_admin)])
async def get_assistant_content():
    """Current editable assistant content + the available presets. Each value
    falls back to the built-in default, so the form shows the active prompt."""
    from app.services.openai import (
        DEFAULT_ASSISTANT_NAME, DEFAULT_ASSISTANT_ORG, DEFAULT_ASSISTANT_PHONE,
        DEFAULT_ASSISTANT_WEBSITE, DEFAULT_ASSISTANT_KNOWLEDGE,
        DEFAULT_PERSONALITY, DEFAULT_MEDICAL_SAFETY, DEFAULT_TONE,
        TONE_PRESETS, MEDICAL_PRESETS,
    )
    from app.services import scope
    return {
        "name": get_setting("assistant_name", DEFAULT_ASSISTANT_NAME),
        "org": get_setting("assistant_org", DEFAULT_ASSISTANT_ORG),
        "phone": get_setting("assistant_phone", DEFAULT_ASSISTANT_PHONE),
        "website": get_setting("assistant_website", DEFAULT_ASSISTANT_WEBSITE),
        "knowledge": get_setting("assistant_knowledge", DEFAULT_ASSISTANT_KNOWLEDGE),
        "personality": get_setting("assistant_personality", DEFAULT_PERSONALITY),
        "medical_safety": get_setting("assistant_medical_safety", DEFAULT_MEDICAL_SAFETY),
        "tone": get_setting("assistant_tone", DEFAULT_TONE),
        "tone_presets": [{"key": k, "label": v["label"]} for k, v in TONE_PRESETS.items()],
        "medical_presets": MEDICAL_PRESETS,
        # The keys a new customer in a different category changes instead of
        # editing Python. Defaults come from app/services/scope.py, so an
        # install that never touched them keeps today's exact wording.
        "domain": scope.domain("fa"),
        "domain_en": scope.domain("en"),
        "refusal_fa": scope.refusal_text("fa"),
        "refusal_en": scope.refusal_text("en"),
        "collection_noun_fa": get_setting("collection_noun_fa", "شرکت"),
        "collection_noun_en": get_setting("collection_noun_en", "companies"),
        "options_shown": int(get_setting("options_shown", "5") or 5),
        "chat_log_retention_days": int(get_setting("chat_log_retention_days", "0") or 0),
    }


@router.post("/admin/api/assistant", dependencies=[Depends(verify_admin)])
async def save_assistant_content(req: AssistantContentRequest, username: str = Depends(verify_admin)):
    from app.services.openai import TONE_PRESETS, DEFAULT_TONE, DEFAULT_MEDICAL_SAFETY
    name = req.name.strip()
    org = req.org.strip()
    if not name or not org:
        raise HTTPException(status_code=400, detail="نام دستیار و نام سازمان نمی‌توانند خالی باشند.")

    # Changing the medical-safety rules is sensitive (it can weaken safety), so
    # it requires re-entering the admin password. Only gated when it changes —
    # editing the name/tone/etc. needs no extra confirmation.
    # The refusal wording rides the SAME gate: it is the out-of-scope safety
    # sentence the bot says when a question is not ours, and weakening it is
    # exactly as sensitive as weakening the red lines above it.
    from app.services import scope
    new_medical = req.medical_safety.strip()
    current_medical = get_setting("assistant_medical_safety", DEFAULT_MEDICAL_SAFETY)
    refusal_changed = (
        (req.refusal_fa is not None and req.refusal_fa.strip() != scope.refusal_text("fa"))
        or (req.refusal_en is not None and req.refusal_en.strip() != scope.refusal_text("en"))
    )
    if new_medical != current_medical or refusal_changed:
        if not req.password:
            raise HTTPException(status_code=403, detail="برای تغییر «خط‌قرمزها و محدودیت‌ها» یا «جملهٔ رد سوال خارج از موضوع» باید رمز عبور مدیر را وارد کنید.")
        conn = get_db_connection()
        user = conn.execute('SELECT password_hash, salt FROM admins WHERE username = ?', (username,)).fetchone()
        conn.close()
        salt = user['salt'] if user and 'salt' in user.keys() and user['salt'] else ""
        ok = user is not None and await asyncio.to_thread(
            verify_password, req.password, user['password_hash'], salt)
        if not ok:
            raise HTTPException(status_code=403, detail="رمز عبور نادرست است.")

    tone = req.tone if req.tone in TONE_PRESETS else DEFAULT_TONE
    set_setting("assistant_name", name)
    set_setting("assistant_org", org)
    set_setting("assistant_phone", req.phone.strip())
    set_setting("assistant_website", req.website.strip())
    set_setting("assistant_knowledge", req.knowledge.strip())
    set_setting("assistant_personality", req.personality.strip())
    set_setting("assistant_medical_safety", req.medical_safety.strip())
    set_setting("assistant_tone", tone)

    # Only written when the form actually sent them, so an older admin page
    # cannot blank a value it does not know about.
    for field, key in (("domain", "assistant_domain"),
                       ("domain_en", "assistant_domain_en"),
                       ("refusal_fa", "refusal_text_fa"),
                       ("refusal_en", "refusal_text_en"),
                       ("collection_noun_fa", "collection_noun_fa"),
                       ("collection_noun_en", "collection_noun_en")):
        value = getattr(req, field)
        if value is not None:
            set_setting(key, value.strip())
    if req.options_shown is not None:
        # Clamped, not validated-and-rejected: a typo must make the list a
        # sensible length, never break the tier for every visitor.
        set_setting("options_shown", max(1, min(15, int(req.options_shown))))
    if req.chat_log_retention_days is not None:
        set_setting("chat_log_retention_days", max(0, int(req.chat_log_retention_days)))
    return {"status": "updated"}


# ── White-label branding ────────────────────────────────────────────────
# Defaults and the escaping/cache contract live in app/services/branding.py;
# these endpoints are only the read/write surface for the admin form.

_HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
_SAFE_LOGO_RE = re.compile(r"^https?://", re.IGNORECASE)


@router.get("/admin/api/branding", dependencies=[Depends(verify_admin)])
async def get_branding_settings():
    """The 5 keys with defaults filled in, so the form always shows the
    active look — including on a fresh install that never saved anything."""
    from app.services.branding import get_branding
    return get_branding()


@router.post("/admin/api/branding", dependencies=[Depends(verify_admin)])
async def save_branding_settings(req: WhitelabelBrandingRequest,
                                 username: str = Depends(verify_admin)):
    name = req.app_name.strip()
    logo = req.logo_url.strip()
    primary = req.primary_color.strip()
    accent = req.accent_color.strip()
    welcome = req.welcome_text.strip()

    # Backstop validation — the form's native color picker can only emit
    # #rrggbb, but this API is reachable by any admin tooling, and a bad
    # value here renders into the PUBLIC chat page. Empty welcome is legal
    # and means "fall back to the default greeting" (branding.get_branding).
    if not name or len(name) > 60:
        raise HTTPException(
            status_code=400,
            detail="نام نمایشی دستیار نمی‌تواند خالی باشد و حداکثر ۶۰ نویسه است.")
    if not _HEX_COLOR_RE.match(primary) or not _HEX_COLOR_RE.match(accent):
        raise HTTPException(
            status_code=400,
            detail="رنگ‌ها باید کد شش‌رقمی hex باشند، مثل #2D5CA7.")
    if len(welcome) > 300:
        raise HTTPException(
            status_code=400,
            detail="پیام خوش‌آمدگویی حداکثر می‌تواند ۳۰۰ نویسه باشد.")
    # Scheme allowlist, not a denylist: `javascript:` and every exotic scheme
    # are rejected by construction. Site-relative (uploaded logo) or absolute
    # http(s) only. A leading `//` is protocol-relative — an EXTERNAL origin
    # in disguise — so it is excluded from the site-relative branch too.
    if logo and (logo.startswith("//")
                 or not (logo.startswith("/") or _SAFE_LOGO_RE.match(logo))):
        raise HTTPException(
            status_code=400,
            detail="نشانی لوگو باید با / شروع شود یا یک آدرس کامل http/https باشد.")

    from app.services.branding import WL_FIELD_TO_KEY
    values = {
        "app_name": name, "logo_url": logo, "primary_color": primary,
        "accent_color": accent, "welcome_text": welcome,
    }
    for field, key in WL_FIELD_TO_KEY.items():
        set_setting(key, values[field])
    # Every value is validated server-side and re-escaped at every render
    # (branding.chat_branding_context) — the audit row records only what
    # changed, never the operator's raw paste.
    applog.audit("settings.branding.updated",
                 "برندینگ نصب به‌روزرسانی شد",
                 actor=username, target="settings")
    return {"status": "updated"}


# ── Hamburger-drawer row visibility ─────────────────────────────────────

@router.get("/admin/api/menu-settings", dependencies=[Depends(verify_admin)])
async def get_menu_settings_api():
    from app.services.menu_settings import get_menu_settings
    return get_menu_settings()


@router.post("/admin/api/menu-settings", dependencies=[Depends(verify_admin)])
async def save_menu_settings_api(req: MenuSettingsRequest,
                                 username: str = Depends(verify_admin)):
    from app.services.menu_settings import set_menu_settings
    set_menu_settings({
        "menu_show_language": req.show_language,
        "menu_show_theme_toggle": req.show_theme_toggle,
        "menu_show_text_size": req.show_text_size,
        "menu_show_logout": req.show_logout,
    })
    applog.audit("settings.menu.updated",
                 "نمایش موارد منوی همبرگری به‌روزرسانی شد",
                 actor=username, target="settings")
    return {"status": "updated"}


@router.get("/admin/api/idle-videos", dependencies=[Depends(verify_admin)])
async def get_idle_videos_api():
    if not is_module_enabled("video"):
        raise HTTPException(status_code=404, detail="Video module is not enabled")
    from app.services import idle_video
    return idle_video.get_idle_videos()


@router.post("/admin/api/idle-videos", dependencies=[Depends(verify_admin)])
async def save_idle_videos_api(req: IdleVideosRequest, username: str = Depends(verify_admin)):
    if not is_module_enabled("video"):
        raise HTTPException(status_code=404, detail="Video module is not enabled")
    from app.services import idle_video

    main = req.main.strip()
    extra = [u.strip() for u in req.extra if u and u.strip()]
    if len(extra) > idle_video.IDLE_VIDEO_EXTRA_MAX:
        raise HTTPException(
            status_code=400,
            detail=f"حداکثر {idle_video.IDLE_VIDEO_EXTRA_MAX} ویدیوی اضافه مجاز است.")

    # Every value must name a file actually uploaded through
    # /admin/api/upload_video — this is emitted raw into the public chat page,
    # so it can never be an arbitrary URL.
    for url in ([main] if main else []) + extra:
        if not idle_video.is_valid_video_url(url):
            raise HTTPException(
                status_code=400,
                detail=f"ویدیوی «{url}» یافت نشد. ابتدا آن را آپلود کنید.")

    idle_video.set_idle_videos(main, extra)
    applog.audit("settings.idle_videos.updated",
                 "ویدیوهای حالت انتظار دستیار به‌روزرسانی شد",
                 actor=username, target="settings")
    return {"status": "updated"}


@router.get("/admin/api/low_confidence", dependencies=[Depends(verify_admin)])
async def get_low_confidence():
    conn = get_db_connection()
    logs = conn.execute('''
        SELECT created_at, query, confidence, response_type
        FROM chat_logs
        WHERE confidence < 0.19
        ORDER BY created_at DESC
        LIMIT 50
    ''').fetchall()
    conn.close()
    return [dict(row) for row in logs]


def _csv_safe(value) -> str:
    """Neutralize spreadsheet formula injection in exported cells.

    Visitor-typed text is exported and opened in Excel/Sheets; a cell starting
    with = + - @ (or tab/CR, which some suites still treat as a formula lead)
    would execute there. A leading apostrophe defuses it and is invisible in
    the rendered cell.
    """
    s = "" if value is None else str(value)
    if s.startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + s
    return s


@router.get("/admin/api/export_csv", dependencies=[Depends(verify_admin)])
async def export_csv():
    conn = get_db_connection()
    logs = conn.execute('SELECT * FROM chat_logs ORDER BY created_at DESC').fetchall()
    conn.close()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['ID', 'Time', 'Query', 'Response', 'Type', 'Source', 'Confidence', 'Tokens', 'Cost'])

    for log in logs:
        writer.writerow([
            _csv_safe(log['id']), _csv_safe(log['created_at']),
            _csv_safe(log['query']), _csv_safe(log['response']),
            _csv_safe(log['response_type']), _csv_safe(log['source']),
            _csv_safe(log['confidence']), _csv_safe(log['tokens']),
            _csv_safe(log['cost'])
        ])

    output.seek(0)
    return StreamingResponse(
        io.BytesIO(output.getvalue().encode('utf-8-sig')),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=chat_history.csv"}
    )


@router.post("/admin/api/reload_dataset", dependencies=[Depends(verify_admin)])
async def reload_dataset_api():
    from app.services.search import reindex_and_publish
    reindex_and_publish()
    return {"status": "reloaded", "count": len(svc.search.dataset)}


@router.post("/admin/api/clear_tokens", dependencies=[Depends(verify_admin)])
async def clear_tokens():
    conn = get_db_connection()
    conn.execute('UPDATE chat_logs SET tokens = 0')
    conn.commit()
    conn.close()
    return {"status": "cleared"}


@router.post("/admin/api/clear_cost", dependencies=[Depends(verify_admin)])
async def clear_cost():
    conn = get_db_connection()
    conn.execute('UPDATE chat_logs SET cost = 0.0')
    conn.commit()
    conn.close()
    return {"status": "cleared"}


@router.post("/admin/api/clear_history", dependencies=[Depends(verify_admin)])
async def clear_history():
    conn = get_db_connection()
    conn.execute('DELETE FROM chat_logs')
    conn.commit()
    conn.close()
    return {"status": "cleared"}


@router.get("/admin/api/profile", dependencies=[Depends(verify_admin)])
async def get_profile(username: str = Depends(verify_admin)):
    conn = get_db_connection()
    user = conn.execute('SELECT username, security_question FROM admins WHERE username = ?', (username,)).fetchone()
    conn.close()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return {"username": user['username'], "security_question": user['security_question']}


@router.post("/admin/api/change-password", dependencies=[Depends(verify_admin)])
async def change_password(req: ChangePasswordRequest, request: Request, username: str = Depends(verify_admin)):
    if req.new_password != req.confirm_password:
        raise HTTPException(status_code=400, detail="رمز عبور جدید و تکرار آن مطابقت ندارند")
    if len(req.new_password) < 6:
        raise HTTPException(status_code=400, detail="رمز عبور باید حداقل ۶ کاراکتر باشد")

    conn = get_db_connection()
    try:
        user = conn.execute('SELECT password_hash, salt FROM admins WHERE username = ?', (username,)).fetchone()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        # Verify current password (accepts bcrypt or legacy hashes)
        salt = user['salt'] if user['salt'] else ""
        if not await asyncio.to_thread(verify_password, req.current_password,
                                       user['password_hash'], salt):
            raise HTTPException(status_code=401, detail="رمز عبور فعلی اشتباه است")

        # Store the new password as bcrypt (salt embedded, so the salt column is cleared)
        new_hash = await asyncio.to_thread(hash_password, req.new_password)
        conn.execute('UPDATE admins SET password_hash = ?, salt = ? WHERE username = ?', (new_hash, '', username))
        # A password change is a rotation: every OTHER session for this admin
        # dies now, so one stolen cookie cannot outlive the rotation (the
        # current session stays — the operator changing their own password is
        # not the attacker). Sliding expiry already bounds it to an hour, this
        # closes that hour.
        conn.execute('DELETE FROM admin_sessions WHERE username = ? AND token <> ?',
                     (username, request.cookies.get(ADMIN_COOKIE_NAME, "")))
        conn.commit()
    finally:
        conn.close()

    return {"status": "success", "message": "رمز عبور با موفقیت تغییر کرد"}


# --- Database Backups ---

@router.get("/admin/api/backups", dependencies=[Depends(verify_admin)])
async def list_backups_api():
    import backup_db
    from app.services.backup import get_schedule
    return {"schedule": get_schedule(), "backups": backup_db.list_backups()}


@router.post("/admin/api/backups/create", dependencies=[Depends(verify_admin)])
async def create_backup_api():
    import backup_db
    from app.services.backup import create_backup_now
    try:
        path = create_backup_now()
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="پایگاه داده‌ای برای پشتیبان‌گیری وجود ندارد")
    import os
    return {"status": "created", "name": os.path.basename(path), "backups": backup_db.list_backups()}


@router.get("/admin/api/backups/download/{name}", dependencies=[Depends(verify_admin)])
async def download_backup_api(name: str):
    import backup_db
    path = backup_db.safe_backup_path(name)
    if not path:
        raise HTTPException(status_code=404, detail="فایل پشتیبان یافت نشد")
    return FileResponse(path, media_type="application/octet-stream", filename=name)


@router.delete("/admin/api/backups/{name}", dependencies=[Depends(verify_admin)])
async def delete_backup_api(name: str):
    import backup_db
    if not backup_db.delete_backup(name):
        raise HTTPException(status_code=404, detail="فایل پشتیبان یافت نشد")
    return {"status": "deleted", "backups": backup_db.list_backups()}


def _reindex_after_restore():
    """Replace the in-memory search index with the restored data — here and
    in every other worker."""
    from app.services.search import reindex_and_publish
    reindex_and_publish()


@router.post("/admin/api/backups/restore/{name}", dependencies=[Depends(verify_admin)])
async def restore_backup_api(name: str):
    """Restore the live database from an existing backup in the list."""
    import backup_db
    path = backup_db.safe_backup_path(name)
    if not path:
        raise HTTPException(status_code=404, detail="فایل پشتیبان یافت نشد")
    try:
        safety = backup_db.restore_backup(path)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    _reindex_after_restore()
    logger.info(f"Database restored from backup: {name} (safety backup: {safety})")
    return {"status": "restored", "safety_backup": safety}


@router.post("/admin/api/backups/restore-upload", dependencies=[Depends(verify_admin)])
async def restore_backup_upload_api(file: UploadFile = File(...)):
    """Restore the live database from an uploaded backup file."""
    import os
    import tempfile
    import backup_db

    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="فایل خالی است")

    fd, tmp_path = tempfile.mkstemp(suffix=".db")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(raw)
        try:
            safety = backup_db.restore_backup(tmp_path)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass

    _reindex_after_restore()
    logger.info(f"Database restored from uploaded file: {file.filename} (safety backup: {safety})")
    return {"status": "restored", "safety_backup": safety}


@router.post("/admin/api/backup-schedule", dependencies=[Depends(verify_admin)])
async def save_backup_schedule(req: BackupScheduleRequest):
    from app.services.backup import save_schedule
    if req.interval_hours < 1:
        raise HTTPException(status_code=400, detail="بازه زمانی نامعتبر است")

    # Validate "HH:MM" — an invalid value (e.g. "25:00") would pass split() but
    # later raise inside compute_next_run(), 500-ing here and stalling the
    # background scheduler.
    try:
        parts = req.time.split(":")
        if len(parts) != 2:
            raise ValueError
        hh, mm = int(parts[0]), int(parts[1])
        if not (0 <= hh < 24 and 0 <= mm < 60):
            raise ValueError
    except (ValueError, AttributeError):
        raise HTTPException(status_code=400, detail="فرمت ساعت نامعتبر است. باید به صورت HH:MM باشد.")

    return save_schedule(req.enabled, req.interval_hours, req.time)


@router.post("/admin/api/change-security-question", dependencies=[Depends(verify_admin)])
async def change_security_question(req: ChangeSecurityQuestionRequest, username: str = Depends(verify_admin)):
    conn = get_db_connection()
    try:
        user = conn.execute('SELECT security_answer_hash FROM admins WHERE username = ?', (username,)).fetchone()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        # Verify current answer (bcrypt, or a legacy SHA-256 row)
        if not verify_security_answer(req.current_answer, user['security_answer_hash']):
            raise HTTPException(status_code=401, detail="پاسخ سوال امنیتی فعلی اشتباه است")

        if not req.new_question.strip() or not req.new_answer.strip():
            raise HTTPException(status_code=400, detail="سوال و پاسخ جدید نمی‌توانند خالی باشند")

        # Update — bcrypt at rest, same policy as the password.
        conn.execute(
            'UPDATE admins SET security_question = ?, security_answer_hash = ? WHERE username = ?',
            (req.new_question.strip(), hash_security_answer(req.new_answer.strip()), username)
        )
        conn.commit()
    finally:
        conn.close()

    return {"status": "success", "message": "سوال امنیتی با موفقیت تغییر کرد"}
