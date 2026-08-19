# مالکیت فنی و وضعیت بازبینی

آخرین به‌روزرسانی: ۱۴۰۵/۰۵/۲۳ (2026-08-14)

صداقت: ستون «بازبینی انسانی» وضعیت واقعی است. بازبینی‌ای که انجام نشده،
انجام‌شده اعلام نمی‌شود.

| زیرسیستم | فایل‌های کلیدی | منطق طراحی | تست‌ها | بازبینی انسانی |
|---|---|---|---|---|
| خط لولهٔ چت | app/routers/chat.py | ADR-003/004/005 | test_chat*, eval harness | pending |
| بازیابی هیبریدی | app/services/search.py, embeddings.py, intent.py | ARCHITECTURE.md | test_embedding_search.py + golden set | pending |
| دانش رسمی INOTEX | app/default_content.py, content/ | sources.json + review-queue | test_default_seed.py | pending |
| چرخهٔ تازه‌سازی | scripts/refresh-inotex-context.py | ADR-006 | اجرای واقعی ثبت‌شده | pending |
| لایهٔ provider | app/services/providers.py, services/openai.py | ADR-007 | /api/ready live check | pending |
| تم INOTEX | themes/inotex/ | ADR-001/002 | test_public_ui.py + QA بصری | pending |
| امنیت | app/auth/security.py | SECURITY_MODEL.md | test_security* | pending |
| دیتابیس و پشتیبان | app/db/, backup_db.py, scripts/reset-content-to-defaults.py | ARCHITECTURE.md | test_reset_script.py | pending |
| ارزیابی | scripts/run_eval.py, data/eval/ | improvement-log.md | خوداجرا | pending |

**مالک فنی:** تا تعیین رسمی توسط تیم، مالک همهٔ زیرسیستم‌ها «مالک محصول»
است و باید در اولین بازبینی انسانی نام فرد مسئول هر ردیف ثبت شود.
