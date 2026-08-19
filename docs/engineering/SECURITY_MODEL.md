# مدل امنیتی

## سطوح حمله و کنترل‌ها

| سطح | کنترل | پیاده‌سازی |
|---|---|---|
| endpoint عمومی /chat | توکن HMAC امضاشده در HTML + اعتبارسنجی Origin/Referer + rate limit per-IP | app/auth/security.py |
| پنل ادمین | هش SHA-256+salt، قفل ۵ تلاش/۵ دقیقه، نشست لغزان ۱ ساعته، مسیر مبهم /secure-panel-inotex | app/auth/security.py |
| prompt injection | بخش SECURITY ثابت در پرامپت (غیرقابل‌ویرایش مشتری) + محدودهٔ SCOPE + آزمون‌های خصمانه در مجموعهٔ طلایی (false-confident injection = 0) | app/services/openai.py، scripts/run_eval.py |
| نشت اسرار | کلیدها فقط در env/settings؛ گیت `secret_leaks=0` در ارزیابی؛ کلید هرگز به کلاینت نمی‌رود | run_eval.py گیت سخت |
| مسمومیت بازیابی | فقط منابع allowlist شدهٔ content/sources.json وارد دانش می‌شوند؛ انتشار با تأیید انسانی | ADR-006 |
| داده‌های شخصی | لاگ چت شامل متن پرسش/پاسخ است — سیاست نگه‌داری باید توسط بهره‌بردار تعیین شود (گپ شناخته‌شده) | backlog |
| CORS | allow_credentials=false با «*»؛ ادمین same-origin | app/main.py |
| کوکی | COOKIE_SECURE=true در تولید (env) | app/config.py |

## قواعد سخت
- ربات هرگز پرامپت سیستم، env، یا محتوای تنظیمات را بازگو نمی‌کند (بخش SECURITY پرامپت + آزمون).
- هیچ credential ای در کد یا تم‌ها نیست؛ `ADMIN_CREDENTIALS.txt` فقط خروجی نصب اول است و باید حذف شود.
- اسکریپت‌های عملیاتی مخرب بدون پشتیبان اجرا نمی‌شوند (reset-content-to-defaults.py پشتیبان اجباری دارد).

## گپ‌های شناخته‌شده (صادقانه)
- اسکن وابستگی‌ها (pip-audit) و اسکن کانتینر هنوز در CI نیست — planned.
- سیاست نگه‌داری/حذف لاگ چت تعریف نشده — نیازمند تصمیم بهره‌بردار.
