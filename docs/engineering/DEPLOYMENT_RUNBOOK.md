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
