# SPEC: PWA API (ماژول `pwa_api`)

| Field | Value |
|-------|-------|
| Created | 2026-08-30 |
| Updated | 2026-08-30 |
| Status | Draft |
| Domain | pwa_api |
| Author | تیم پادیار |
| Sources | InotexPWA repo — `docs/ARCHITECTURE.md` §۲، `docs/API.md` (منبع اصلی نیازمندی‌ها)، تصمیم‌های مالک محصول و عامل برنامه‌ریزی، ۲۰۲۶-۰۸-۳۰ |

این سند صرفاً تصمیم‌های از قبل گرفته‌شده را به شکل یک اسپک قابل‌اجرا در همین
مخزن (PadyarAIChatbot) می‌نویسد. طراحی مجدد نمی‌کند. هر کجا که رفتار فعلی کد
با فرض این سند فرق داشت، همینجا با ارجاع دقیق `file:line` نوشته شده — نسخهٔ
فعلی کد حاکم است، نه فرض اولیه.

---

## ۱. صورت مسئله

یک اپ موبایل جدید و کاملاً جدا («InotexPWA»، مخزن React/Vite/Capacitor) در حال
ساخت است برای نمایشگاه اینوتکس. این اپ backend خودش را نمی‌سازد؛ همین
PadyarAIChatbot (FastAPI + PostgreSQL) را به‌عنوان backend-as-a-service
مصرف می‌کند: نشست بازدیدکنندهٔ ثبت‌نامی (OTP) را دوباره استفاده می‌کند، فهرست
غرفه‌دارها را می‌خواند، و چت‌بات موجود را صدا می‌زند.

سه مانع مستقیم جلوی این مصرف هست:

۱. **هویت فقط با کوکی کار می‌کند.** `resolve_visitor` در `app/main.py:231-292`
   عمداً فقط `request.cookies` را می‌خواند — «Not a header, not a body field,
   not a query parameter, not a path segment» (`app/main.py:235-239`). یک
   کلاینت cross-origin/native که کوکی مرورگر ندارد (اپ Capacitor روی iOS/
   Android، یا یک PWA که از دامنهٔ دیگری فچ می‌زند) اصلاً نمی‌تواند وارد شود.
۲. **هیچ API عمومی برای فهرست شرکت‌ها وجود ندارد.** `/api/dataset` و
   `/api/questions` عمداً حذف شده‌اند چون کل محتوای تجاری مشتری را بدون احراز
   هویت پخش می‌کردند — کامنت `app/routers/public.py:190-209` این را با عدد
   ثبت کرده: «۱۶۸ از ۲۲۲ سطر `dataset` رکورد شرکت غرفه‌دار بودند... هرکس که
   URL را تایپ می‌کرد کل آن را دانلود می‌کرد». هیچ جایگزین عمومی و
   allowlist‌شده‌ای جای آن نیامد.
۳. **مینت کردن توکن چت به رندر HTML گره خورده.** `generate_chat_token()`
   (`app/auth/security.py:462-474`) امروز فقط از دو جا صدا زده می‌شود: رندر
   صفحهٔ اصلی (`app/routers/public.py:161`) و رفرش یک توکن **موجود**
   (`POST /api/chat-token`، `app/routers/chat.py:986-1017`، که خودش نیازمند
   یک توکن معتبر قبلی است). یک کلاینت native که هیچ‌وقت HTML سرور را رندر
   نمی‌کند راهی برای گرفتن اولین توکن ندارد.

این سند یک ماژول اختیاری تازه — `pwa_api` — تعریف می‌کند که این سه مانع را
برمی‌دارد، بدون اینکه رفتار کوکی‌محور امروز (چت وب، ادمین) را دست بزند.

---

## ۲. اهداف

- یک کلاینت cross-origin/native بتواند با همان جریان OTP امروز وارد شود و
  همان نشست (همان سطر `visitor_sessions`) را با یک بردار Bearer حمل کند —
  بدون جدول توکن تازه، بدون secret تازه.
- یک API عمومی، فقط‌خواندنی و allowlist‌شده برای فهرست/جزئیات شرکت‌های
  غرفه‌دار، دقیقاً روی همان allowlist که برای بازدیدکنندهٔ چت از قبل تعریف
  شده (`PUBLIC_PROFILE_FIELDS`)، بدون هیچ فیلد شخصی.
- یک نقطهٔ مستقل برای مینت کردن اولین توکن چت، بدون نیاز به رندر HTML.
- یک محل ذخیرهٔ ترجیحات per-visitor (تقویم، مخاطبین، زبان) روی همان سطر
  `visitors` که امروز هم دارد.
- یک QR شخصی کوتاه‌عمر برای اتصال دو بازدیدکننده به هم، با همان الگوی HMAC
  که توکن چت استفاده می‌کند.

## ۳. خارج از دامنه

- فیلد طبقه‌بندی `user_type` — مالک محصول هنوز فهرست مقادیرش را نهایی نکرده.
  پیگیری در بخش «پرسش‌های باز».
- هیچ تغییری در endpointهای CRUD ادمین برای شرکت‌ها (`app/routers/leads.py`).
- هیچ تغییری در رفتار کوکی‌محور صفحهٔ چت وب امروز.
- افشای `contact_name`، `contact_position`، `contact_mobile`، `email`،
  `notes` تحت هیچ شرایطی از این ماژول — این‌ها دقیقاً همان پنج فیلدی هستند که
  `app/services/company_profiles.py:62-69` صریح می‌گوید هرگز نباید این ماژول
  را ترک کنند.
- تغییر تنظیم استقرار (`ALLOWED_ORIGINS`) نیست — یک قدم Rollout است، نه کد.

---

## ۴. دلیل انتخاب این رویکرد

**چرا bearer token جدول جدا و secret جدا نمی‌خواهد.** نشست بازدیدکنندهٔ ثبت‌نامی
از قبل یک مقدار مبهم است که سرور مینت می‌کند و در `visitor_sessions.token`
جستجو می‌شود (`app/auth/visitor.py:83-115` برای `mint()`،
`app/auth/visitor.py:118-227` برای `resolve()`). این مقدار همان چیزی است که
امروز در کوکی `padyar_vs` می‌نشیند (`VISITOR_COOKIE_NAME`,
`app/config.py:144`). ساختن یک جدول توکن دوم یعنی دو مفهوم نشست موازی که
باید همیشه sync بمانند — انقضا، ابطال، حذف کاربر، همه باید دوبار پیاده شوند.
راه ساده‌تر: همان توکن خام را هم در کوکی بگذار، هم در بدنهٔ JSON برگردان.
کوکی و Bearer یک ردیف پایگاه‌داده‌اند، نه دو نشست.

**چرا `resolve_visitor` باید Bearer را هم بخواند، نه فقط pwa_api.** middleware
سراسری است (`app/main.py:231`, رجیستر شده بلافاصله بعد از CORS) و برای همهٔ
مسیرها اجرا می‌شود، نه فقط مسیرهای این ماژول. تنها گزینهٔ واقعی این بود که یا
middleware تغییر کند، یا هر endpoint این ماژول کد resolve را دوباره بنویسد. دومی
یعنی دو پیاده‌سازی احراز هویت که باید همیشه هم‌رفتار بمانند — دقیقاً همان الگوی
خطایی که کامنت خود همین middleware در بالای فایل هشدار می‌دهد. پس تغییر در یک
جا، با یک قید صریح: کوکی همیشه اول چک می‌شود؛ Bearer فقط وقتی کوکی نبود
(fallback، نه جایگزین) — چرا مهم است در REQ-001 آمده.

**چرا توکن خام در بدنهٔ JSON یک ریسک پذیرفته‌شده است، نه یک باگ.** کوکی
httpOnly دقیقاً برای همین طراحی شده بود: اسکریپت صفحه نمی‌تواند آن را بخواند
(`app/auth/visitor.py:329-336`). برگرداندن همان توکن در JSON یعنی کلاینت
مجبور است آن را جایی نگه دارد که یک اسکریپت می‌تواند بخواند —
`localStorage` روی وب، `Preferences`/`SecureStorage` روی Capacitor. این
مبادله واقعی است، نه بی‌دقتی: هیچ کلاینت cross-origin/native دیگری بدون آن
اصلاً نمی‌تواند وارد شود. جزئیات کاهش ریسک در SEC-001 و جدول ریسک‌ها.

**چرا `/api/companies` علی‌رغم آن کامنت هشدار در `public.py` ساخته می‌شود.**
`app/routers/public.py:190-209` می‌گوید صریح چرا `/api/dataset` حذف شد: همهٔ
محتوای تجاری مشتری، بدون auth، در یک پاسخ. این اسپک همان اشتباه را تکرار
نمی‌کند چون خروجی این endpoint یک **allowlist** است — همان
`PUBLIC_PROFILE_FIELDS` که از قبل برای بازدیدکنندهٔ چت طراحی شده
(`app/services/company_profiles.py:56-60`) — به‌اضافهٔ `id`، `title`،
`title_en`، `video_url` و یک برش کوتاه از `text`، نه ستون‌های شخصی. تفاوتش با
endpoint حذف‌شده در همین است: آن یکی allowlist نداشت، این یکی از روز اول
دارد. با این حال این هنوز یک دایرکتوری عمومی و صفحه‌بندی‌شدهٔ شرکت‌هاست، و
`tests/test_visit_plan.py:175` (`test_note_always_states_the_exhibitor_
directory_is_not_published`) نشان می‌دهد تا امروز عدم انتشار این دایرکتوری
یک تصمیم عمدی بوده. این اسپک آن تصمیم را **فقط برای همین مجموعهٔ فیلد**
برمی‌گرداند، نه برای کل محتوای شرکت. جزئیات در جدول ریسک‌ها.

**چرا `visitor_settings` یک ستون JSONB است، نه سه جدول جدید.** تقویم، مخاطبین
و زبان هر سه per-visitor و کم‌حجم‌اند، و ساختار داخلی‌شان هنوز در حال شکل
گرفتن است (مثلاً `user_type` هنوز نهایی نشده — بخش ۳). سه جدول رابطه‌ای
یعنی سه migration، سه اندیس، سه مسیر نوشتن، برای دیتایی که فقط یک بازدیدکننده
آن را می‌خواند و می‌نویسد. یک ستون JSONB روی همان سطر `visitors`
(`app/db/connection.py:387-400`) که از قبل صاحب داده‌های شخصی همین
بازدیدکننده است، ساده‌ترین چیزی است که کار می‌کند — دقیقاً همان قاعدهٔ
سادگی در `CLAUDE.md` («No unnecessary abstraction»).

---

## ۵. دامنه

ماژول اختیاری تازهٔ `pwa_api` (`app/modules/registry.py`)، با این نقاط تماس:

| Area | Impact |
|------|--------|
| `app/routers/pwa_api.py` (جدید) | تمام endpointهای این اسپک |
| `app/modules/registry.py` | ثبت `ModuleDef` تازه، الگو از `leads` (خط ۱۱۹-۱۲۵) |
| `app/main.py` (`resolve_visitor`) | افزودن مسیر Bearer به‌عنوان fallback بعد از کوکی |
| `app/auth/visitor.py` | یک تابع resolve مشترک برای کوکی و Bearer (بدون تغییر رفتار `mint`/`resolve` فعلی) |
| `app/services/company_profiles.py` | خواندن `PUBLIC_PROFILE_FIELDS` برای B، بدون تغییر در خود فایل |
| `app/auth/security.py` | استفادهٔ مجدد از `validate_request_origin` و `generate_chat_token`، الگوی HMAC مشابه برای E |
| `app/db/connection.py` | ستون `visitor_settings` روی `visitors` برای بک‌اند تست SQLite |
| `migrations/0017_visitor_settings.sql` (جدید) | schema فقط برای D |
| `tests/test_pwa_api.py` (جدید) | تست این ماژول |

هیچ جدول یا ستونی روی `companies`، `company_leads` یا `dataset_owners` تغییر
نمی‌کند.

---

## ۶. داستان‌های کاربر

- US-1: به‌عنوان اپ InotexPWA (native/Capacitor)، می‌خواهم بعد از تأیید OTP یک
  اعتبارنامهٔ قابل ذخیره بگیرم، تا بین باز و بسته کردن اپ دوباره لاگین نخواهم.
- US-2: به‌عنوان بازدیدکننده در InotexPWA، می‌خواهم فهرست غرفه‌دارها را جستجو
  و مرور کنم، بدون این‌که مجبور باشم اول ثبت‌نام کنم.
- US-3: به‌عنوان InotexPWA، می‌خواهم بدون رندر یک صفحهٔ HTML سروری بتوانم
  اولین توکن چت را بگیرم، تا همان endpoint چت موجود را صدا بزنم.
- US-4: به‌عنوان بازدیدکننده، می‌خواهم رویدادهای برنامهٔ من در تقویم شخصی‌ام و
  زبان انتخابی‌ام بین جلسات حفظ شود.
- US-5: به‌عنوان بازدیدکننده، می‌خواهم QR شخصی خودم را نشان بدهم تا یک
  بازدیدکنندهٔ دیگر با اسکن آن به من وصل شود، بدون این‌که آن QR برای همیشه
  معتبر بماند.

---

## ۷. نیازمندی‌های کارکردی

### گروه A: احراز هویت با Bearer token

- REQ-001: `resolve_visitor` (`app/main.py:231-292`) بعد از این تغییر، وقتی
  کوکی `padyar_vs` وجود ندارد یا نامعتبر است، هدر
  `Authorization: Bearer <token>` را با همان مسیر جستجوی
  `visitor_auth.resolve()` بررسی می‌کند. کوکی همیشه اولویت دارد و اگر معتبر
  بود، Bearer اصلاً خوانده نمی‌شود. این تغییر global است (روی همهٔ مسیرها اثر
  می‌کند)، ولی چون فقط زمانی فعال می‌شود که کوکی نباشد، هیچ ترافیک کوکی‌محور
  امروز (چت وب، پنل ادمین که مسیر جدای خودش `ADMIN_COOKIE_NAME` را دارد)
  رفتارش عوض نمی‌شود.
- REQ-002: `POST /api/auth/otp/verify` وقتی هدر درخواست `X-Client: pwa` دارد،
  فیلد تازهٔ `access_token` را به پاسخ موفق اضافه می‌کند — همان مقدار خامی که
  `visitor_auth.mint()` (`app/auth/visitor.py:83`) تولید و در کوکی گذاشته
  می‌شود (`app/routers/otp.py:367-369`). بدون این هدر، پاسخ دقیقاً همان شکل
  امروز می‌ماند (`{"verified": true, "message": ..., "profile": ...}`،
  `app/routers/otp.py:379`) — هیچ کلاینت کوکی‌محور موجود چیزی اضافه نمی‌بیند.
- REQ-003: `GET /api/auth/session`، `POST /api/auth/profile` و
  `POST /api/auth/logout` هیچ تغییر کد جداگانه‌ای لازم ندارند. هر سه از
  `request.state.visitor_id` (`require_visitor`،
  `app/auth/visitor.py:366-384`) می‌خوانند که خودش محصول همان
  `resolve_visitor` است؛ با REQ-001 این سه «به‌خودی‌خود» مسیر Bearer را هم
  می‌پذیرند.
- REQ-004: طول عمر توکن bearer دقیقاً همان طول عمر نشست کوکی است —
  `VISITOR_SESSION_DAYS` (پیش‌فرض ۳۰) برای idle، `VISITOR_SESSION_MAX_HOURS`
  (پیش‌فرض ۱۲) برای سقف مطلق از لحظهٔ mint (`app/config.py`، مستند در
  `app/auth/visitor.py:133-146`). هیچ منطق انقضای جدیدی نوشته نمی‌شود.

### گروه B: API عمومی شرکت‌ها

- REQ-005: `GET /api/companies` روی `app.companies` (نه `dataset` —
  `migrations/0013_companies.sql`) جستجو می‌کند. پارامترها: `q` (جستجوی متن
  آزاد روی `title`/`title_en`)، `industry` (نگاشت به ستون `activity_field`)،
  `type` (نگاشت به ستون `company_type`)، `page`، `page_size`. خروجی هر سطر:
  اجتماع `PUBLIC_PROFILE_FIELDS`
  (`app/services/company_profiles.py:56-60`: `website`, `company_phone`,
  `fax`, `address`, `address_en`, `province`, `booth_number`, `hall`,
  `company_type`, `org_stage`, `activity_field`, `participation`) به‌اضافهٔ
  `id`, `title`, `title_en`, `video_url`, و `short_text` (برش `text`، طول
  ثابت — به‌اندازهٔ همان کاراکتر شمار summary دیگر endpointهای عمومی، مثلاً
  الگوی `LEAD_MAX_CHARS` در `app/config.py`، عدد دقیق در پیاده‌سازی تعیین
  می‌شود).
- REQ-006: `GET /api/companies/{id}` همان اجتماع فیلد را برای یک `id` برمی‌
  گرداند؛ `id` ناموجود `404` می‌دهد.
- REQ-007: هیچ‌کدام از این دو endpoint فیلدهای `contact_name`,
  `contact_position`, `contact_mobile`, `email`, `notes` را برنمی‌گردانند —
  این پنج فیلد اصلاً در SELECT نمی‌آیند (همان الگوی
  `public_profile()`، `app/services/company_profiles.py:102-125`، که SELECT
  را صراحتاً روی نام ستون‌های allowlist می‌سازد تا یک ستون withheld حتی در
  حافظه هم لود نشود).
- REQ-008: هر دو endpoint بدون احراز هویت باز هستند، ولی همان سقف نرخ عمومی
  مسیرهای بدون‌کوکی امروز را دارند — الگوی
  `security.check_rate_limit(request, key=f"page:{client_ip(request)}",
  limit=security.PAGE_RATE_LIMIT)` که `read_root()`
  (`app/routers/public.py:150-154`) استفاده می‌کند، با یک کلید per-IP جدا
  برای این دو مسیر.

### گروه C: مینت توکن چت

- REQ-009: `POST /api/chat-token/mint` نیازمند نشست بازدیدکننده است — کوکی یا
  Bearer، از همان مسیر REQ-001 (`require_visitor`). بدون نشست، `401` با
  `REGISTRATION_REQUIRED` (`app/auth/visitor.py:380-383`).
- REQ-010: قبل از هر چیز `validate_request_origin` را دقیقاً مثل `/chat`
  (`app/routers/chat.py:219`) و `/api/chat-token` موجود
  (`app/routers/chat.py:1013`) اجرا می‌کند.
- REQ-011: پاسخ موفق `{"chat_token": <token>, "expires_at": <iso8601>}`
  است، با `<token>` خروجی همان `security.generate_chat_token()`
  (`app/auth/security.py:462-474`) که رندر HTML امروز صدا می‌زند
  (`app/routers/public.py:161`)، و `expires_at` برابر لحظهٔ mint به‌اضافهٔ
  `CHAT_TOKEN_TTL` (پیش‌فرض ۳۶۰۰ ثانیه، `app/config.py:118`).
- REQ-012: این endpoint با `POST /api/chat-token` موجود اشتباه گرفته نمی‌شود:
  آن یکی رفرش یک توکن **موجود و معتبر** است و خودش با `validate_chat_token`
  کار می‌کند (`app/routers/chat.py:987-1017`)؛ این یکی مینت **اولین** توکن
  از روی نشست بازدیدکننده است و هیچ توکن قبلی نمی‌خواهد. هیچ‌کدام جایگزین
  دیگری نمی‌شود.

### گروه D: تنظیمات شخصی بازدیدکننده

- REQ-013: مهاجرت `migrations/0017_visitor_settings.sql`:
  `ALTER TABLE app.visitors ADD COLUMN IF NOT EXISTS visitor_settings JSONB
  NOT NULL DEFAULT '{}'::jsonb;`. شکل شمای مقدار:
  ```json
  {
    "calendar": [{"event_id": "string", "added_at": "iso8601"}],
    "contacts": [{"visitor_id": 0, "connected_at": "iso8601"}],
    "language": "string"
  }
  ```
- REQ-014: `GET /api/me/settings` (نیازمند نشست، کوکی یا Bearer) دقیقاً
  `{"calendar": [...], "contacts": [...], "language": "..."}` برمی‌گرداند —
  اگر ستون خالی (`{}`) باشد، هر سه کلید با مقدار پیش‌فرض (لیست خالی، رشتهٔ
  خالی) پر می‌شوند.
- REQ-015: `POST /api/me/calendar` بدنه `{"event_id": "..."}`؛ درج idempotent
  (یک `event_id` تکراری چیزی اضافه نمی‌کند)؛ خروجی `calendar` به‌روزشده.
- REQ-016: `DELETE /api/me/calendar/{event_id}` آن آیتم را حذف می‌کند؛ خروجی
  `calendar` به‌روزشده. `event_id` ناموجود خطا نمی‌دهد، فقط تغییری ایجاد
  نمی‌کند (idempotent، مثل REQ-015).
- REQ-017: `POST /api/me/contacts/connect` بدنه `{"qr_payload": "..."}`.
  payload را طبق گروه E اعتبارسنجی می‌کند؛ در موفقیت، هر دو سطر `visitors`
  (بازدیدکنندهٔ فعلی و بازدیدکنندهٔ صاحب QR) را در **یک تراکنش اتمیک** به‌روز
  می‌کند تا `contacts` هر دو طرف را ببیند. `409` اگر همین جفت از قبل وصل
  شده‌اند؛ `400` اگر payload نامعتبر یا منقضی است.

### گروه E: QR شخصی

- REQ-018: `GET /api/me/qr` (نیازمند نشست) `{"payload": "...",
  "expires_at": "iso8601"}` برمی‌گرداند. `payload` یک توکن HMAC‌شده است که id
  بازدیدکننده را رمزگذاری می‌کند، با همان `app_secret` که
  `generate_chat_token()`/`_get_hmac_key()` استفاده می‌کنند
  (`app/auth/security.py`) و همان الگوی تأیید با `secrets.compare_digest`
  (به‌جای `==`، تا زمان‌سنجی مقایسه چیزی درز نکند — همان قاعده‌ای که
  `validate_chat_token` رعایت می‌کند).
- REQ-019: عمر `payload` چند ساعته است (نه دائمی) و هر بار درخواست دوباره
  mint می‌شود، نه کش می‌شود — یک گوشی گم‌شده یا افتاده‌دست‌کسی نباید یک QR
  همیشه‌معتبر روی صفحه‌اش داشته باشد.

---

## ۸. نیازمندی‌های غیرکارکردی

### ۸.۱ کارایی

- PER-001: `GET /api/companies` روی مجموعهٔ فعلی شرکت‌ها (چند صد سطر
  `app.companies`) زیر ۲۰۰ میلی‌ثانیه پاسخ می‌دهد — همان سقفی که
  `company_search`/جستجوی مشابه در ماژول `leads` برایش طراحی شده
  (`docs/features/exhibition-lead-capture/SPEC.md` PER-005).
- PER-002: بار این ماژول نباید زمان پاسخ `/chat` را در اوج ترافیک محسوس بدتر
  کند — همان قاعدهٔ PER-006 در `exhibition-lead-capture/SPEC.md`، چون هر دو
  ماژول روی همان pool اتصال دیتابیس سوارند.

### ۸.۲ امنیت

- SEC-001: بازگرداندن توکن خام نشست در بدنهٔ JSON (REQ-002) یعنی این مقدار
  دیگر httpOnly نیست و روی کلاینت در `localStorage` یا Capacitor
  `Preferences` می‌نشیند — سطح مواجهه‌اش با XSS بیشتر از کوکی httpOnly است.
  این مبادله بدون جایگزین است: هیچ کلاینت cross-origin/native واقعی بدون آن
  نمی‌تواند وارد شود. کاهش پیشنهادی: سمت InotexPWA در build نیتیو از
  `SecureStoragePlugin` (یا keychain پلتفرم) استفاده کند؛ روی وب همان سطح
  ریسکی پذیرفته می‌شود که تقریباً هر SPA مصرف‌کنندهٔ API دارد. جزئیات در
  جدول ریسک‌ها.
- SEC-002: مسیر Bearer در `resolve_visitor` **فقط fallback** است — کوکی
  همیشه اول چک می‌شود و اگر معتبر بود Bearer اصلاً خوانده نمی‌شود (REQ-001).
  این یعنی چت وب و پنل ادمین رفتار امروزشان عوض نمی‌شود؛ فقط ترافیکی که از
  ابتدا کوکی نداشته (یعنی یک کلاینت جدید) از این مسیر تازه بهره می‌برد.
- SEC-003: هیچ secret یا جدول توکن جدیدی ساخته نمی‌شود. اعتبار Bearer دقیقاً
  همان اعتبار کوکی است — همان سطر `visitor_sessions`، همان قواعد انقضا/ابطال
  (REQ-004). ابطال یک نشست (`visitor_auth.revoke()`،
  `app/auth/visitor.py:246-266`) هر دو مسیر ورود را همزمان می‌بندد.
- SEC-004: `GET /api/companies*` هرگز `contact_name`, `contact_position`,
  `contact_mobile`, `email`, `notes` را برنمی‌گرداند، حتی اگر پارامتر یا هدر
  ورودی چیز دیگری بخواهد — allowlist در سطح SQL SELECT اجرا می‌شود، نه با
  فیلتر کردن بعد از خواندن (REQ-007).
- SEC-005: `payload` گروه E مثل توکن چت با `secrets.compare_digest` تأیید
  می‌شود، نه با مقایسهٔ رشتهٔ معمولی، تا زمان‌سنجی پاسخ چیزی دربارهٔ صحت
  payload درز ندهد.

### ۸.۳ پایایی

- REL-001: `POST /api/me/contacts/connect` (REQ-017) یا هر دو سطر `visitors`
  را در یک تراکنش به‌روز می‌کند یا هیچ‌کدام را — یک قطعی وسط راه هرگز یک
  اتصال یک‌طرفه باقی نمی‌گذارد.
- REL-002: `resolve_visitor` بعد از تغییر هنوز هرگز raise نمی‌کند — یک هدر
  Bearer ناقص یا نامعتبر دقیقاً مثل یک کوکی نامعتبر «anonymous» می‌شود، نه
  خطا (همان قاعدهٔ مستندشده در `app/main.py:251-254`).

---

## ۹. طراحی فنی

### ۹.۱ معماری

```
InotexPWA (native / cross-origin web)
    │
    ├─ POST /api/auth/otp/request            (بدون تغییر)
    ├─ POST /api/auth/otp/verify  ── X-Client: pwa ──▶ + access_token در بدنه
    │        (کوکی padyar_vs هم مثل امروز ست می‌شود؛ روی وب همان کوکی هم کار می‌کند)
    │
    ├─ هر درخواست بعدی:  Authorization: Bearer <access_token>
    │        resolve_visitor:  کوکی نیست → Bearer را با visitor_auth.resolve() چک کن
    │        (کوکی هست → Bearer حتی خوانده نمی‌شود)
    │
    ├─ GET  /api/companies، /api/companies/{id}      (بدون auth، allowlist)
    ├─ POST /api/chat-token/mint                     (نیازمند نشست) → chat_token
    ├─ POST /chat  با X-Chat-Token: <chat_token>      (همان endpoint موجود)
    ├─ GET/POST/DELETE /api/me/settings, /calendar    (نیازمند نشست)
    └─ GET  /api/me/qr  →  POST /api/me/contacts/connect  (بین دو بازدیدکننده)
```

سه قاعده‌ای که کل این ماژول روی آن‌ها ایستاده:

۱. **کوکی و Bearer یک اعتبارنامه‌اند، نه دو.** هر دو به همان سطر
   `visitor_sessions` می‌رسند؛ فرق فقط در این است که سرور از کجا مقدار توکن
   را می‌خواند.
۲. **allowlist شرکت‌ها همانی است که برای چت‌بات از قبل وجود داشت.** هیچ فیلد
   عمومی تازه‌ای اختراع نمی‌شود؛ فقط همان `PUBLIC_PROFILE_FIELDS` از یک
   endpoint مستقل هم در دسترس است.
۳. **مینت توکن چت یک تابع دارد، دو راه صدا زدن.** `generate_chat_token()`
   بدون تغییر می‌ماند؛ فقط یک مسیر HTTP تازه آن را صدا می‌زند.

### ۹.۲ تغییرات API

| Method | Endpoint | Description |
|--------|----------|--------------|
| POST | `/api/auth/otp/verify` | تغییر: با هدر `X-Client: pwa`، فیلد `access_token` به پاسخ اضافه می‌شود (REQ-002) |
| GET | `/api/auth/session` | بدون تغییر کد؛ حالا مسیر Bearer را هم می‌پذیرد (REQ-003) |
| POST | `/api/auth/profile` | بدون تغییر کد؛ حالا مسیر Bearer را هم می‌پذیرد (REQ-003) |
| POST | `/api/auth/logout` | بدون تغییر کد؛ حالا مسیر Bearer را هم می‌پذیرد (REQ-003) |
| GET | `/api/companies` | جدید: فهرست عمومی allowlist‌شدهٔ شرکت‌ها (REQ-005) |
| GET | `/api/companies/{id}` | جدید: جزئیات یک شرکت، همان allowlist (REQ-006) |
| POST | `/api/chat-token/mint` | جدید: مینت اولین توکن چت از روی نشست بازدیدکننده (REQ-009 تا REQ-012) |
| GET | `/api/me/settings` | جدید: خواندن `visitor_settings` (REQ-014) |
| POST | `/api/me/calendar` | جدید: افزودن idempotent یک رویداد (REQ-015) |
| DELETE | `/api/me/calendar/{event_id}` | جدید: حذف یک رویداد (REQ-016) |
| POST | `/api/me/contacts/connect` | جدید: اتصال دو بازدیدکننده با QR (REQ-017) |
| GET | `/api/me/qr` | جدید: QR شخصی کوتاه‌عمر (REQ-018، REQ-019) |

### ۹.۳ تغییرات دیتابیس

**یک فایل مهاجرت، فقط schema.** شمارهٔ فایل بعدی `0017` است — آخرین مهاجرت
موجود در مخزن `migrations/0016_company_hall.sql` است (تأیید شده با `ls
migrations/`، ۲۰۲۶-۰۸-۳۰). سبک کامنت مطابق `migrations/0015_company_booth_
number.sql` (یک بلوک WHY قبل از `ALTER TABLE`).

`migrations/0017_visitor_settings.sql`:

```sql
-- A JSONB scratch column for per-visitor client state the PWA owns —
-- calendar picks, contact connections, language — none of which is a fact
-- the server needs to reason about today. See docs/features/pwa-api/SPEC.md
-- REQ-013.
ALTER TABLE app.visitors
    ADD COLUMN IF NOT EXISTS visitor_settings JSONB NOT NULL DEFAULT '{}'::jsonb;
```

طبق قاعدهٔ `CLAUDE.md` («Database Changes»)، همین ستون در `app/db/
connection.py` برای بک‌اند تست SQLite هم آینه‌کاری می‌شود — در تعریف جدول
`visitors` (`app/db/connection.py:387-400`)، به‌شکل
`visitor_settings TEXT NOT NULL DEFAULT '{}'` (SQLite نوع JSONB بومی ندارد؛
همان الگوی `answers TEXT NOT NULL DEFAULT '{}'` که همین جدول از قبل دارد،
خط ۳۹۶).

هیچ جدول یا ستون دیگری لازم نیست: بخش B فقط می‌خواند
(`app.companies`، از قبل موجود)، بخش A و C هیچ schema تازه‌ای نمی‌خواهند.

---

## ۱۰. وابستگی‌ها

- توافق روی مقدار دقیق `short_text` (طول برش `text` در REQ-005) — یک عدد
  ثابت، نه یک تنظیم قابل‌تغییر (طبق قاعدهٔ سادگی `CLAUDE.md`: «No config
  options for things that can be auto-detected»).
- **پیکربندی استقرار، نه کد:** `ALLOWED_ORIGINS`
  (`app/config.py:227-229`) باید دامنهٔ واقعی InotexPWA و scheme اپ
  Capacitor (مثل `capacitor://localhost` یا معادلش روی iOS) را شامل شود، وگرنه
  `validate_request_origin` هر درخواست این ماژول را `403` می‌کند. این یک گام
  Rollout است، در بخش ۱۳.
- مخزن InotexPWA باید `X-Client: pwa` را روی همان اولین `POST /api/auth/otp/verify`
  بفرستد تا `access_token` را بگیرد؛ بدون آن هدر، پاسخ دقیقاً مثل امروز است
  و کلاینت هیچ توکنی نمی‌گیرد.

## ۱۱. ریسک‌ها

| Risk | Impact | Mitigation |
|------|--------|------------|
| توکن نشست در `localStorage`/`Preferences` به‌جای کوکی httpOnly، مواجهه با XSS بیشتر (SEC-001) | یک نشست بازدیدکننده (نه ادمین) لو می‌رود؛ سقف خسارت به همان محدودهٔ دسترسی امروز نشست بازدیدکننده است | `SecureStoragePlugin`/keychain در build نیتیو؛ روی وب همان سطح ریسک هر SPA مصرف‌کنندهٔ API؛ بدون جایگزین برای یک کلاینت واقعاً cross-origin/native |
| `GET /api/companies` یک دایرکتوری عمومی، صفحه‌بندی‌شده از شرکت‌هاست — دقیقاً همان چیزی که `app/routers/public.py:190-209` یک بار به‌عنوان نشت محتوای تجاری حذف کرد، و `tests/test_visit_plan.py:175` نشان می‌دهد عدم انتشار این فهرست تا امروز یک تصمیم عمدی بوده | scraping کامل فهرست غرفه‌دارها و اطلاعات تماس عمومی‌شان توسط شخص ثالث | این نسخه، برخلاف endpoint حذف‌شده، از روز اول allowlist دارد (فقط `PUBLIC_PROFILE_FIELDS` + یک برش کوتاه از متن)، هیچ فیلد شخصی نمی‌دهد، و صفحه‌بندی می‌شود؛ این یک تصمیم صریح مالک محصول است که در بخش ۴ ثبت شده، نه یک نظارت |
| تغییر `resolve_visitor` global است؛ یک باگ در مسیر Bearer می‌تواند روی تمام مسیرهای امروز اثر بگذارد | یک regression در middleware اصلی، هر مسیر سایت را می‌شکند | کوکی همیشه اول چک می‌شود (SEC-002)؛ تست رگرسیون صریح که چت وب و ادمین بدون هیچ هدر Bearer دقیقاً رفتار امروز را دارند (بخش ۱۲) |
| فراموش کردن به‌روزرسانی `ALLOWED_ORIGINS` در استقرار | همهٔ درخواست‌های InotexPWA به این ماژول با `403` رد می‌شوند | چک‌لیست Rollout صریح (بخش ۱۳)، قابل مشاهده قبل از رفتن به تولید |

## ۱۲. استراتژی تست

الگوی `tests/test_pwa_api.py` باید از این فایل‌های موجود پیروی کند:

- **احراز هویت Bearer** — الگوی `tests/test_otp.py` و
  `tests/test_visitor_session.py`: یک OTP کامل با `X-Client: pwa`، خواندن
  `access_token`، صدا زدن `GET /api/auth/session` **بدون کوکی** با فقط هدر
  `Authorization: Bearer`، و تأیید `signed_in: true`. یک تست جدا که همان
  درخواست را **با کوکی معتبر ولی بدون هدر Bearer** هم بزند و مطمئن شود چیزی
  عوض نشده (رگرسیون روی چت وب).
- **الویت کوکی روی Bearer** — یک تست که هم کوکی معتبر برای بازدیدکنندهٔ A و
  هم هدر Bearer برای بازدیدکنندهٔ B را روی یک درخواست بفرستد و تأیید کند
  پاسخ همیشه بازدیدکنندهٔ A را برمی‌گرداند (کوکی برنده است، SEC-002).
- **API شرکت‌ها** — الگوی `tests/test_company_profiles.py` و
  `tests/test_company_search.py`: تأیید این‌که پاسخ `GET /api/companies` و
  `GET /api/companies/{id}` هرگز کلیدهای `contact_name`,
  `contact_position`, `contact_mobile`, `email`, `notes` را ندارد، حتی اگر
  آن شرکت این فیلدها را در دیتابیس پر کرده باشد.
- **مینت توکن چت** — الگوی تست‌های موجود روی `POST /api/chat-token`
  (`app/routers/chat.py:986`): بدون نشست `401`؛ با نشست و origin درست، توکن
  خروجی روی `POST /chat` با `X-Chat-Token` واقعاً کار می‌کند.
- **`visitor_settings`** — الگوی `tests/test_visitor_session.py`: idempotency
  افزودن یک `event_id` تکراری (REQ-015)، حذف یک `event_id` ناموجود بدون خطا
  (REQ-016)، و اتمیک بودن `contacts/connect` زیر یک تست تراکنش شبیه‌سازی‌شده
  (REL-001).
- **QR** — تأیید امضا/تأیید HMAC مثل تست‌های `validate_chat_token` در
  `tests/test_otp.py`؛ یک payload منقضی‌شده `400` می‌گیرد.

## ۱۳. طرح استقرار (Rollout)

۱. اجرای `migrations/0017_visitor_settings.sql` با
   `scripts/apply_migrations.py` (تولید) و آینه‌کاری در
   `app/db/connection.py` (تست).
۲. افزودن `pwa_api` به `app/modules/registry.py` (`is_core=False`).
۳. افزودن `pwa_api` به `ENABLED_MODULES` نصب‌هایی که InotexPWA را مصرف
   می‌کنند.
۴. **پیکربندی، نه کد:** به‌روزرسانی `ALLOWED_ORIGINS` روی همان نصب‌ها با
   دامنهٔ واقعی InotexPWA و scheme اپ Capacitor.
۵. اطمینان از این‌که مخزن InotexPWA هدر `X-Client: pwa` را در فراخوانی
   `POST /api/auth/otp/verify` می‌فرستد.
۶. بررسی CI (`.github/workflows/ci.yml`) — همان دروازهٔ pass/fail پروژه،
   نه اجرای محلی.

## ۱۴. معیارهای موفقیت

- SC-001: یک session کامل روی InotexPWA (native) — OTP، `access_token`،
  `GET /api/companies`، `POST /api/chat-token/mint`، `POST /chat` — بدون
  هیچ کوکی مرورگر کار می‌کند.
- SC-002: چت وب موجود و پنل ادمین بعد از این تغییر، بدون هیچ تفاوت رفتاری
  قابل‌مشاهده (تست رگرسیون بخش ۱۲ سبز است).
- SC-003: هیچ تست یا بازبینی دستی یک فیلد از پنج فیلد withheld
  (`contact_name`، `contact_position`، `contact_mobile`، `email`،
  `notes`) را در پاسخ `GET /api/companies*` پیدا نمی‌کند.

## ۱۵. پرسش‌های باز

- طول دقیق برش `short_text` در `GET /api/companies` (REQ-005) — یک عدد
  ثابت باید انتخاب شود؛ این سند فقط می‌گوید «کوتاه»، نه چند کاراکتر.
- مقادیر مجاز `user_type` هنوز نهایی نشده — این فیلد عمداً از این نسخه بیرون
  گذاشته شده (بخش ۳) و باید در یک اسپک بعدی، بعد از تصمیم مالک محصول، اضافه
  شود.
- عمر دقیق `payload` گروه E («چند ساعت» در REQ-019) باید یک عدد قطعی بگیرد
  قبل از پیاده‌سازی.
