# معماری سامانه — Padyar Core + نمونهٔ INOTEX

تاریخ: ۱۴۰۵/۰۵/۲۳ (2026-08-14)

## دو لایهٔ منطقی

```
Padyar Core (سکوی بازمصرف‌پذیر)
  ├── بازیابی هیبریدی (همپوشانی واژگانی + امبدینگ محلی + TF-IDF + طبقه‌بند intent)
  ├── نرمال‌سازی فارسی و بسط مترادف (app/utils/normalizer.py)
  ├── لایهٔ providerها (app/services/providers.py)
  ├── سیستم ماژول (app/modules/registry.py)
  ├── سیستم تم چندلایه (themes/ — الگوی وردپرسی partialها)
  ├── امنیت (توکن HMAC چت، rate limit، نشست ادمین)
  └── مشاهده‌پذیری (/api/health، /api/ready، LOG_FORMAT=json)

INOTEX Experience (نمونهٔ محصول)
  ├── دانش رسمی seed شده (app/default_content.py — منبع: inotex.com)
  ├── منیفست منابع + snapshot + تشخیص تغییر (content/)
  ├── تم inotex (پالت رسمی + طراحی آجری ماژولار + لودر)
  ├── مجموعهٔ ارزیابی طلایی (data/eval/golden-inotex.json)
  └── پرامپت‌ها و برچسب‌های INOTEX (app/services/openai.py)
```

جداسازی منطقی است، نه پوشه‌ای — ساختار ریپازیتوری موجود (اسکلت) حفظ شده است.

## خط لولهٔ پاسخ‌گویی (app/routers/chat.py)

```
پرسش کاربر
  → نرمال‌سازی + حذف سلام ابتدای پیام
  → Tier 0: تطبیق تقریباً دقیق (Jaccard-only) با پرسش‌های منتخب  [≥0.9]
  → Tier 1: تطبیق موثق توضیحات (امبدینگ محلی/TF-IDF)            [≥0.70]
  → Tier 1.5: طبقه‌بند intent محلی (LR روی امبدینگ‌های خود نصب)
              با گیت پویا بر اساس دقت holdout
  → Tier 2: طبقه‌بند LLM خارجی (فقط اگر فعال و پیکربندی‌شده)
  → Tier 3: پاسخ مولد LLM (فقط برای out_of_domain تأییدشده)
  → fallback امن: پاسخ محکم محلی یا 503
```

هیچ لایه‌ای از پایین‌دست به vendor خاصی وابسته نیست؛ endpoint خارجی هر
سرویس سازگار با OpenAI API است (پروکسی تجاری، سکوی ملی، یا self-hosted).

## داده

SQLite (WAL) با ۸ جدول؛ دانش نسخه‌دار (`settings.knowledge_version`) و
هم‌گام با `content/sources.json`. پشتیبان‌گیری زمان‌بندی‌شده
(app/services/backup.py) + اسکریپت بازنشانی عملیاتی با پشتیبان اجباری
(scripts/reset-content-to-defaults.py).

## چرخهٔ حیات دانش

```
کشف (sources.json)
  → واکشی (scripts/refresh-inotex-context.py)
  → هش + snapshot (content/snapshots/)
  → تشخیص تغییر (freshness-report.json, exit code 2)
  → صف بازبینی انسانی (content/review-queue.md)   ← دروازهٔ حاکمیتی عمدی
  → انتشار seed جدید + ارتقای knowledge_version
  → بازنشانی عملیاتی با پشتیبان
```

انتشار خودکارِ بدون تأیید انسانی عمداً وجود ندارد.
