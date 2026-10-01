# آشنایی یک‌ساعته با معماری کد

این سند برای یک مهندس تازه است. در شصت دقیقه، قدم به قدم، کد واقعی را باز
می‌کنید و مسیر یک پیام چت را از ورود تا پاسخ دنبال می‌کنید. توضیح کامل و عمیق
هر بخش در `docs/engineering/ARCHITECTURE.md` است. این سند آن را تکرار نمی‌کند،
فقط می‌گوید کجا را باز کنید.

شماره‌خط‌ها با commit پایهٔ `3a4a415` (2026-09-30) نوشته شده‌اند. اگر کد
جابه‌جا شده، نام تابع را جست‌وجو کنید.

## دقیقهٔ ۰ تا ۵: تصویر کلی

- `CLAUDE.md` بخش «What Is This Project?»: اپ یک CMS برای چت‌بات است که برای
  هر مشتری جدا نصب می‌شود. هر قابلیت یک ماژول است.
- `docs/engineering/ARCHITECTURE.md`: فقط عنوان‌ها را نگاه کنید تا بدانید برای
  عمق کجا برگردید.
- `docs/engineering/CODE_OWNERSHIP.md`: فهرست همهٔ زیرسیستم‌ها، فایل‌ها و
  تست‌هایشان. این نقشهٔ بقیهٔ این یک ساعت است.

## دقیقهٔ ۵ تا ۱۲: اپ چطور بالا می‌آید

- `app/main.py:76` تابع `lifespan`: کارهای شروع و پایان اپ (دیتابیس، نمایهٔ
  جست‌وجو، زمان‌بندها).
- `app/main.py:257` میان‌افزار `resolve_visitor`: بازدیدکننده را فقط از کوکی
  نشست `padyar_vs` می‌شناسد، نه از بدنه یا هدر درخواست.
- `app/main.py:375` میان‌افزار `csrf_protection`: همهٔ درخواست‌های تغییردهنده
  زیر پیشوندهای ادمین را بررسی می‌کند.
- `app/main.py:611`: routerهای ماژول‌ها بار می‌شوند.

## دقیقهٔ ۱۲ تا ۱۸: رجیستری ماژول‌ها

- `app/modules/registry.py:25` دیکشنری `MODULES`: ۶ ماژول هسته (همیشه روشن) و
  ۹ ماژول اختیاری.
- `app/modules/registry.py:132` تابع `resolve_enabled_modules`: متغیر
  `ENABLED_MODULES` را می‌خواند. خالی یعنی همهٔ ماژول‌ها.
- `app/modules/registry.py:161` تابع `load_module_routers`: router هر ماژول
  روشن را import می‌کند.
- `app/config.py:315` تابع `is_module_enabled`: کد برای پرسیدن «این ماژول
  روشن است؟» از این استفاده می‌کند، نه از خطای import.

تمرین: یک ماژول اختیاری مثل `leads` را در `MODULES` پیدا کنید و router آن را
در `app/routers/leads.py` باز کنید.

## دقیقهٔ ۱۸ تا ۳۵: مسیر یک پیام چت

همه در `app/routers/chat.py`. تابع `chat_endpoint` در
`app/routers/chat.py:256` شروع می‌شود. به ترتیب:

1. **نگهبان‌ها** (`app/routers/chat.py:256` تا حدود خط ۲۷۵): حالت نگهداری،
   بررسی Origin (`app/auth/security.py:446`)، توکن HMAC چت
   (`app/auth/security.py:400`)، و سقف نرخ دوسطحی
   (`app/auth/security.py:310`).
2. **در ثبت‌نام:** اگر ماژول `registration` روشن باشد، بازدیدکنندهٔ بدون نشست
   پاسخ 401 می‌گیرد (`app/auth/visitor.py:366`).
3. **شناسهٔ گفتگو:** کوکی `padyar_conv` فقط وقتی ادامه پیدا می‌کند که مال همین
   بازدیدکننده باشد.
4. **تیر انتخاب (pick):** `app/routers/chat.py:400`. اگر پیام «۳» یا «دومی»
   باشد، همان رکوردی سرو می‌شود که در پیام قبلی پیشنهاد شده بود، با تابع
   `resolve_pick` در `app/services/answer.py:273`. بدون هیچ تماس AI.
5. **تیر ۰:** `app/routers/chat.py:607`. یک تطابق تقریباً دقیق در فهرست
   پرسش‌های دست‌نوشته.
6. **تیر ۱:** `app/routers/chat.py:971`. بازیابی محلی (BM25 + امبدینگ محلی)،
   فقط اگر امتیاز از `TRUSTED_MATCH_THRESHOLD` در `app/config.py:41` بالاتر
   باشد.
7. **تیر ۱.۵:** `app/routers/chat.py:1011`. طبقه‌بند intent آموزش‌دیده روی
   دادهٔ خود همین نصب.
8. **تیر انتخاب با مدل:** `app/routers/chat.py:1081` تابع `select_records`
   (`app/services/answer.py:907`) را صدا می‌زند. مدل فقط **id رکورد** انتخاب
   می‌کند و متن پاسخ از دیتابیس نوشته می‌شود (ADR-018).
9. **بدون AI:** `app/routers/chat.py:1266`. اگر AI خاموش یا خراب باشد، فقط
   تطابق محلی قوی (`LOCAL_FALLBACK_THRESHOLD`، `app/config.py:46`) سرو می‌شود.
10. **ثبت:** هر شاخه از `_log_turn` در `app/routers/chat.py:123` عبور می‌کند.

توجه: بین این تیرها چند تیر محلی دیگر هم هست (شرکت، غرفه، راهنما، رد، …). هر
کدام بعد از یک پاسخ غلط واقعی اضافه شده. ترتیب دقیق را خود router می‌گوید، نه
نمودار `CLAUDE.md`.

## دقیقهٔ ۳۵ تا ۴۲: درهای ورود (auth)

- **ادمین:** `app/auth/security.py:616` تابع `verify_admin`. هر endpoint ادمین
  آن را با `Depends` صدا می‌زند.
- **CSRF:** `app/auth/csrf.py:55` ثابت `PROTECTED_PREFIXES`.
  `tests/test_csrf.py` شکست می‌خورد اگر یک endpoint ادمین بیرون این پیشوندها
  باشد.
- **بازدیدکننده:** `app/auth/visitor.py:366` تابع `require_visitor`.
- **ماژول leads، سه در جدا:** `app/routers/leads.py:123` (`/v/{code}` برای
  بازدیدکنندهٔ میدانی)، `app/routers/leads.py:281` (`/edit/{token}` برای تماس
  شرکت با لینک یک‌بارمصرف)، و `app/routers/leads.py:418` (صف ادمین با
  `verify_admin`).
- مدل تهدید کامل: `docs/engineering/SECURITY_MODEL.md`.

## دقیقهٔ ۴۲ تا ۵۰: دیتابیس و migrationها

- `app/db/connection.py:8` تابع `get_db_connection`: PostgreSQL در production،
  SQLite فقط برای تست.
- `app/db/connection.py:615` تابع `init_db`: جدول‌های SQLite تست. هر تغییر
  schema باید اینجا هم آینه شود.
- `migrations/`: فایل‌های شماره‌دار SQL برای PostgreSQL.
- `scripts/apply_migrations.py:89` تابع `checksum` و
  `scripts/apply_migrations.py:210`: اگر یک migration اعمال‌شده عوض شده باشد،
  اسکریپت متوقف می‌شود و deploy را قطع می‌کند. پس migration اعمال‌شده هرگز
  ویرایش نمی‌شود.
- قانون‌ها: `docs/engineering/DATABASE.md`.

## دقیقهٔ ۵۰ تا ۶۰: CI و استقرار

- `.github/workflows/ci.yml`: پنج job مسدودکننده، `test`
  (`.github/workflows/ci.yml:16`)، `postgres-tests`
  (`.github/workflows/ci.yml:72`)، `dependency-audit`
  (`.github/workflows/ci.yml:129`)، `secret-scan`
  (`.github/workflows/ci.yml:159`) و `identity-guard`
  (`.github/workflows/ci.yml:255`).
- `.github/workflows/ci.yml:210` job `deploy`: فقط بعد از سبز شدن آن پنج job و
  فقط روی `main`، روی runner سرور اجرا می‌شود.
- `deploy/padyar-deploy.sh:22`: شش مرحلهٔ deploy به ترتیب (پشتیبان، checkout،
  وابستگی‌ها، migration، راه‌اندازی دوباره، بررسی سلامت) و اینکه هر شکست کجا
  برمی‌گردد.
- `.github/workflows/release.yml`: با tag `v*` نسخه منتشر می‌شود.
- `.github/workflows/pr-governance.yml`: بررسی مشورتی متن PR (ADR-026).
- فرایند بازبینی: `docs/engineering/REVIEW_PROCESS.md`.

## بعد از این یک ساعت

- یک زیرسیستم از `docs/engineering/REVIEW_PLAN.md` انتخاب کنید و تست‌هایش را
  بخوانید.
- قانون‌های الزام‌آور: `docs/engineering/ENGINEERING_CONSTITUTION.md`.
- تصمیم‌های معماری و دلیلشان: `docs/engineering/DECISIONS.md`.
