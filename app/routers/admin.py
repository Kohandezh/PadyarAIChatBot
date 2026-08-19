import io
import csv
import hashlib
import secrets
import datetime

from fastapi import APIRouter, HTTPException, Request, Depends, Response, UploadFile, File
from fastapi.responses import JSONResponse, StreamingResponse, FileResponse

from app.models import (
    SmsSettingsRequest, SmsTestRequest,
    LoginRequest, ToggleRequest, ChangePasswordRequest,
    ChangeSecurityQuestionRequest, BackupScheduleRequest,
    AssistantContentRequest, AIConnectionRequest,
)
from app.services import embeddings as embeddings_service
from app.services import applog
from app.config import (
    logger, MAX_LOGIN_ATTEMPTS, BLOCK_TIME_MINUTES,
    SESSION_TIMEOUT_HOURS, ADMIN_COOKIE_NAME, COOKIE_SECURE,
)
from app.auth.security import (
    verify_admin, login_attempts,
    hash_password, verify_password, is_legacy_hash,
)
from app.db.connection import get_db_connection
from app.db.queries import get_setting, set_setting
from app.services.search import load_dataset_internal
from app import services as svc


router = APIRouter()


@router.post("/admin/login")
async def admin_login(creds: LoginRequest, request: Request):
    client_ip = request.client.host

    # 1. Rate Limiting Check
    if client_ip in login_attempts:
        tracker = login_attempts[client_ip]
        if tracker['block_until'] and datetime.datetime.now() < tracker['block_until']:
            applog.security("auth.login.while_blocked",
                            f"تلاش ورود از {client_ip} در دورهٔ مسدودی",
                            level="warning", actor=creds.username, actor_type="admin",
                            ip=client_ip, target="admin-login", outcome="denied",
                            user_agent=request.headers.get("user-agent", ""))
            raise HTTPException(status_code=429, detail="Too many failed attempts. Try again later.")
        if tracker['block_until'] and datetime.datetime.now() >= tracker['block_until']:
            tracker['attempts'] = 0
            tracker['block_until'] = None

    conn = get_db_connection()
    user = conn.execute('SELECT * FROM admins WHERE username = ?', (creds.username,)).fetchone()
    conn.close()

    auth_success = False
    password_ok = False
    if user:
        salt = user['salt'] if 'salt' in user.keys() and user['salt'] else ""
        password_ok = verify_password(creds.password, user['password_hash'], salt)
        ans_hash = hashlib.sha256(creds.sec_answer.encode()).hexdigest()
        if password_ok and ans_hash == user['security_answer_hash']:
            auth_success = True
            # Transparent upgrade: re-hash legacy SHA-256 passwords with bcrypt.
            if is_legacy_hash(user['password_hash']):
                new_hash = hash_password(creds.password)
                conn = get_db_connection()
                conn.execute(
                    'UPDATE admins SET password_hash = ?, salt = ? WHERE username = ?',
                    (new_hash, '', creds.username)
                )
                conn.commit()
                conn.close()
                logger.info(f"Upgraded password hash to bcrypt for: {creds.username}")

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
        applog.security("auth.login.failed",
                        f"ورود ناموفق مدیر ({reason})",
                        level="warning", actor=creds.username, actor_type="admin",
                        ip=client_ip, target="admin-login", outcome="failed",
                        error_code=reason,
                        user_agent=request.headers.get("user-agent", ""),
                        metadata={"reason": reason,
                                  "attempts_so_far": login_attempts.get(client_ip, {}).get("attempts", 0) + 1})
        if client_ip not in login_attempts:
            login_attempts[client_ip] = {'attempts': 0, 'block_until': None}

        login_attempts[client_ip]['attempts'] += 1

        if login_attempts[client_ip]['attempts'] >= MAX_LOGIN_ATTEMPTS:
            login_attempts[client_ip]['block_until'] = datetime.datetime.now() + datetime.timedelta(minutes=BLOCK_TIME_MINUTES)
            applog.security("auth.bruteforce.blocked",
                            f"ورود از {client_ip} به دلیل تلاش‌های مکرر مسدود شد",
                            level="critical", actor=creds.username, actor_type="admin",
                            ip=client_ip, target="admin-login", outcome="blocked",
                            user_agent=request.headers.get("user-agent", ""),
                            metadata={"attempts": login_attempts[client_ip]['attempts'],
                                      "block_minutes": BLOCK_TIME_MINUTES})
            raise HTTPException(status_code=429, detail="Too many failed attempts. You are blocked.")

        raise HTTPException(status_code=401, detail="Bad credentials")

    # Success - Reset attempts
    if client_ip in login_attempts:
        del login_attempts[client_ip]

    # Generate Token
    token = secrets.token_hex(32)
    expiry = datetime.datetime.now() + datetime.timedelta(hours=SESSION_TIMEOUT_HOURS)

    conn = get_db_connection()
    conn.execute('INSERT INTO admin_sessions (token, username, expiry) VALUES (?, ?, ?)', (token, creds.username, expiry.isoformat()))
    conn.commit()
    conn.close()

    applog.audit("auth.login.success", "ورود موفق مدیر",
                 actor=creds.username, target="admin-panel", outcome="ok",
                 ip=client_ip, actor_type="admin",
                 user_agent=request.headers.get("user-agent", ""))

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
                 ip=request.client.host if request.client else "")
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
    conn.close()

    daily_stats = []
    for row in daily_rows:
        daily_stats.append({
            "date": row['date_label'],
            "count": row['msg_count'],
            "cost": row['total_cost'] or 0.0
        })

    return {
        "total_tokens": total_tokens,
        "total_cost": total_cost,
        "total_messages": total_messages,
        "daily_stats": daily_stats
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
    set_setting("registration_enabled", "true" if req.enabled else "false")
    env_written = sms_service.save_settings({
        "sms_provider": req.provider.strip() or "dev",
        "sms_asanak_username": req.username,
        "sms_asanak_password": req.password,
        "sms_asanak_api_key": req.api_key,
        "sms_asanak_source": req.source,
        "sms_asanak_template_id": req.template_id,
        "sms_asanak_url": req.url,
        "sms_asanak_status_url": req.status_url,
        "sms_asanak_credit_url": req.credit_url,
        "sms_asanak_template_url": req.template_url,
        "sms_asanak_trim": "true" if req.trim else "false",
        "sms_asanak_send_to_blacklist": "1" if req.send_to_blacklist else "0",
        "otp_sms_host": req.sms_host,
    })
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
    return {
        "api_base": get_setting("ai_api_base", ""),
        "api_base_default": OPENAI_API_BASE,
        # The key itself never leaves the server — only whether one exists.
        "has_key": bool((get_setting("ai_api_key", "") or "").strip()),
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
        "search_backend": get_setting("search_backend", "tfidf"),
        "embedding_available": embeddings_service.available(),
        "default_lang": get_setting("default_chat_lang", "fa"),
    }


@router.post("/admin/api/ai-connection", dependencies=[Depends(verify_admin)])
async def save_ai_connection(req: AIConnectionRequest):
    set_setting("ai_api_base", req.api_base.strip())
    if req.api_key.strip():
        set_setting("ai_api_key", req.api_key.strip())
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
    backend = req.search_backend if req.search_backend in ("tfidf", "embedding") else "tfidf"
    backend_changed = get_setting("search_backend", "tfidf") != backend
    set_setting("search_backend", backend)
    if backend_changed:
        # Rebuild the retrieval index in the background so the switch takes
        # effect without a restart (first enable also downloads the model).
        import threading
        from app.services.search import load_dataset_internal
        threading.Thread(target=load_dataset_internal, daemon=True).start()
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
    new_medical = req.medical_safety.strip()
    current_medical = get_setting("assistant_medical_safety", DEFAULT_MEDICAL_SAFETY)
    if new_medical != current_medical:
        if not req.password:
            raise HTTPException(status_code=403, detail="برای تغییر «خط‌قرمزها و محدودیت‌ها» باید رمز عبور مدیر را وارد کنید.")
        conn = get_db_connection()
        user = conn.execute('SELECT password_hash, salt FROM admins WHERE username = ?', (username,)).fetchone()
        conn.close()
        salt = user['salt'] if user and 'salt' in user.keys() and user['salt'] else ""
        if not user or not verify_password(req.password, user['password_hash'], salt):
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
            log['id'], log['created_at'], log['query'], log['response'],
            log['response_type'], log['source'], log['confidence'],
            log['tokens'], log['cost']
        ])

    output.seek(0)
    return StreamingResponse(
        io.BytesIO(output.getvalue().encode('utf-8-sig')),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=chat_history.csv"}
    )


@router.post("/admin/api/reload_dataset", dependencies=[Depends(verify_admin)])
async def reload_dataset_api():
    load_dataset_internal()
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
async def change_password(req: ChangePasswordRequest, username: str = Depends(verify_admin)):
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
        if not verify_password(req.current_password, user['password_hash'], salt):
            raise HTTPException(status_code=401, detail="رمز عبور فعلی اشتباه است")

        # Store the new password as bcrypt (salt embedded, so the salt column is cleared)
        new_hash = hash_password(req.new_password)
        conn.execute('UPDATE admins SET password_hash = ?, salt = ? WHERE username = ?', (new_hash, '', username))
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
    """Replace the in-memory search index with the restored data."""
    from app.services.search import load_dataset_internal
    load_dataset_internal()


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

        # Verify current answer
        current_hash = hashlib.sha256(req.current_answer.encode()).hexdigest()
        if current_hash != user['security_answer_hash']:
            raise HTTPException(status_code=401, detail="پاسخ سوال امنیتی فعلی اشتباه است")

        if not req.new_question.strip() or not req.new_answer.strip():
            raise HTTPException(status_code=400, detail="سوال و پاسخ جدید نمی‌توانند خالی باشند")

        # Update
        new_answer_hash = hashlib.sha256(req.new_answer.encode()).hexdigest()
        conn.execute(
            'UPDATE admins SET security_question = ?, security_answer_hash = ? WHERE username = ?',
            (req.new_question.strip(), new_answer_hash, username)
        )
        conn.commit()
    finally:
        conn.close()

    return {"status": "success", "message": "سوال امنیتی با موفقیت تغییر کرد"}
