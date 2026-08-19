# راهنمای استقرار (Runbook)

## استقرار تازه

```bash
pip install -r requirements.txt
cp .env.example .env        # سپس مقادیر واقعی: OPENAI_API_KEY، SECRET_KEY، ADMIN_*، ALLOWED_ORIGINS، COOKIE_SECURE=true
python main.py              # dev — یا در تولید:
gunicorn -k uvicorn.workers.UvicornWorker -w 4 -b 127.0.0.1:8000 app.main:app
```

Docker: `docker compose up -d` (Dockerfile و docker-compose.yml موجود است).

## بررسی سلامت
- Liveness: `GET /api/health` (ارزان، بدون فراخوانی خارجی)
- Readiness: `GET /api/ready` — تا آماده‌شدن ایندکس بازیابی 503 می‌دهد؛
  `?deep=true` سرویس خارجی AI را هم می‌سنجد (فقط دستی/مانیتورینگ کم‌بسامد).
- لاگ ساخت‌یافته: `LOG_FORMAT=json` در env.

## به‌روزرسانی دانش
```bash
python3 scripts/refresh-inotex-context.py      # exit 2 یعنی صفحهٔ رسمی تغییر کرده
# بازبینی انسانی content/review-queue.md → به‌روزرسانی app/default_content.py
# ارتقای knowledge_version در content/sources.json و settings
python3 scripts/reset-content-to-defaults.py   # پشتیبان خودکار + seed جدید
# ری‌استارت سرویس تا ایندکس بازسازی شود
```

## پشتیبان و بازیابی
- خودکار: زمان‌بندی پنل ادمین (app/services/backup.py) → پوشهٔ backups/
- دستی: `python backup_db.py`
- بازیابی: توقف سرویس → جایگزینی chat_history.db از پشتیبان → شروع سرویس
  → بررسی `/api/ready` و شمارش dataset در `/api/health`.

## بازگشت (Rollback) دانش
هر اجرای reset یک `chat_history.backup.<timestamp>.db` می‌سازد؛ بازگشت =
همان مسیر بازیابی با فایل پشتیبان قبلی.

## عیب‌یابی سریع
| علامت | اقدام |
|---|---|
| /api/ready → 503 | لاگ «Embedding backend init failed» را ببین؛ fallback خودکار TF-IDF فعال است؛ model2vec و data/models را بررسی کن |
| 503 از /chat | سرویس خارجی AI در دسترس نیست و تطبیق محلی قوی وجود ندارد — کلید/base را در پنل ادمین بررسی کن |
| پاسخ‌های قدیمی پس از تغییر تم/CSS | cache-buster خودکار است؛ hard-refresh مرورگر |
| قفل «database is locked» | WAL فعال است؛ اگر تکرار شد پروسه‌های موازی نویسنده را بررسی کن |

## Speech-to-text credentials (since the AI Control Plane landed)

Transcription resolves its key through the AI Control Plane, not the legacy
`ai_api_key` setting. Resolution order (`app/services/ai/stt.py`):

1. An explicit binding, `ai_stt_provider_instance_id`, if set.
2. Otherwise the single enabled, secret-bearing, STT-capable provider instance.
   This is why rotating the key in **Admin → AI → Providers** now fixes voice
   as well as chat, with no extra configuration.
3. Only if neither exists, the legacy `ai_api_base` / `ai_api_key` settings —
   a compatibility path for an install that never migrated.

Only OpenAI-shaped providers serve `/audio/transcriptions`, so binding is
restricted to `openai` and `openai_compatible` (`STT_CAPABLE_TYPES`). Anthropic
and Gemini are not eligible and must not be listed as if they were.

The transcription model is `ai_model_stt` (default `whisper-1`), still editable
under Settings → AI.

## Model selection is under AI → Routing

Settings → AI no longer offers "chat model" / "classification model" inputs.
Those wrote `ai_model_chat` / `ai_model_classify`, which the runtime stopped
reading when routing moved to the Control Plane — the form reported success and
changed nothing. Per-task model and provider order now live in
**AI → Routing**. The endpoint still accepts the old fields so a cached admin
page does not break, but no longer persists them.

## Duplicate dataset id

`POST /admin/api/dataset` with an existing id returns **409 Conflict** (it
returned 400 before, and 500 on PostgreSQL). The existing row is never
overwritten. Backend-neutral detection lives in `app/db/dberrors.py`.

## Running the PostgreSQL integration tests

The default suite runs on SQLite for speed. Production-critical PostgreSQL
tests are opt-in:

```bash
RUN_POSTGRES_TESTS=1 .venv/bin/python -m pytest tests/postgres -q
```
