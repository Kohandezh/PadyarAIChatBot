# Breakdown — Exhibition Lead Capture

منبع: SPEC §13.2. هر گروه نیازمندی یک issue است: زیر ۵۰۰ خط، حداکثر ۴ معیار
پذیرش، مستقلاً merge‌شدنی، بدون مخفی کردن schema و منطق.

این فایل وضعیت **امروز (۲۰۲۶-۰۸-۲۴)** را هم کنار هر issue می‌نویسد. وضعیت از
سه چیز می‌آید: خود SPEC (حاشیه‌های «بسته شد/ساخته شد»)، فایل‌های موجود در
ریپو (مهاجرت‌ها و تست‌ها)، و git log. آنچه اینجا «باقی» است، کارِ باقی است؛
issue «بسته» شده فقط اگر اثرش در کد و تست دیده شود.

راهنمای وضعیت: ✅ ساخته شد · 🔶 بخشی ساخته شد · ⬜ نمانده

## فاز ۱ — وضعیت سه‌حالته، کار ثبت، و تست کاری که ساخته شده

| # | Issue | Requirements | وضعیت | یادداشت |
|---|-------|--------------|:-----:|---------|
| 01 | `0006_lead_status.sql` + آینهٔ SQLite | REQ-071، REQ-072 | ✅ | مهاجرت موجود؛ برگشت فقط با بکاپ (REL-006) |
| 02 | وضعیت سه‌حالته در سرویس، قیف و فهرست ادمین | REQ-009، REQ-011، REQ-029، REQ-030، REQ-073 | ✅ | `released` و `_live_owner` در `app/services/leads.py` |
| 03 | مرگ دعوت‌نامه روی ارسال موفق، مهلت ۲۴ ساعت، حذف `edit_sessions` | REQ-014 تا REQ-017، SEC-005، SEC-010 | ✅ | `edit_sessions` در 0006 DROP شد (ADR-013) |
| 04 | نیازمندی‌های منفی صفحهٔ ویرایش و API | REQ-068 تا REQ-070 | ✅ | `tests/test_leads_invite.py` (فیلد اضافه، متن خالی، سقف) |
| 05 | متن رضایت روی صفحهٔ ویرایش و نسخه‌گذاری | REQ-061 تا REQ-063 | ✅ | `empty_consent` در سرویس؛ نسخهٔ v1 پیش از رویداد در تنظیمات |
| 06 | حذف شرکت مالک‌دار از جستجو و مسیر ثبت | REQ-006، REQ-007 | ✅ | `NOT EXISTS ... _live_owner` روی جستجو |
| 07 | هشدار شمارهٔ تکراری، override و حسابرسی آن | REQ-012، REQ-058 تا REQ-060 | ✅ | ستون‌های `duplicate_override_*` |
| 08 | فهرست گیرافتاده روی `verified` و آزادسازی | REQ-064، REQ-065 | ✅ | `admin_stuck` در `app/routers/leads.py` |
| 09 | کانال تحویل دعوت‌نامه (`leads_invite_channel`) | REQ-054 تا REQ-057 | ✅ | پیش‌فرض `qr`؛ fallback QR روی خطای ۱۰۱۴ |
| 10 | اعلان پیامکی رد ویرایش + دلیل رد | REQ-025، REQ-066، REQ-067 | ✅ | `0009_review_note.sql`، `review_note` روی `/my`، `admin_remind`، متن پیامک ادمین‌اِدیت‌پذیر (ADR-014) |
| 11 | `tests/test_leads_visitor.py` | REQ-001 تا REQ-013، REQ-031 تا REQ-034 | ✅ | موجود |
| 12 | `tests/test_leads_invite.py` | REQ-018 تا REQ-023، REQ-068 تا REQ-070 | ✅ | موجود |
| 13 | `tests/test_leads_review.py` | REQ-024 تا REQ-028 | ✅ | موجود؛ به‌علاوهٔ `test_leads_reject_reason.py` و `test_leads_content_rules.py` |
| 14 | `tests/test_leads_capture.py` | REQ-054 تا REQ-067، REQ-071 تا REQ-073 | ⬜ | تنها تست گمشدهٔ فاز ۱. پوشش پراکنده‌اش الان بین `test_leads_fraud_signals.py` و بقیه است؛ یک فایل جمع‌کننده با معیارهای گروه‌های N تا Q |

## فاز ۲ — رفع امنیتی به ترتیب PRD ۱۲

| # | Issue | Requirements | وضعیت | یادداشت |
|---|-------|--------------|:-----:|---------|
| 15 | `0008_identity.sql` + آینهٔ SQLite | بخش ۹.۳ SPEC | ✅ | `users`، `user_sessions`، `dataset_owners` هر سه همین‌جا آمدند (نه `0009` که SPEC انتظار داشت — آن شماره به `review_note` خورد) |
| 16 | F1: حذف توکن خام از دست ویزیتور | SEC-008، SEC-009 | ✅ | `issued_by_session` روی دعوت‌نامه |
| 17 | F2 سرور: قاعدهٔ فقط متن ساده | SEC-024 | ✅ | `MARKUP_MESSAGE` و `clean_review_note` |
| 18 | F2 کلاینت: DOMPurify و دو نقطهٔ رندر | SEC-023 | ✅ | vendor شده؛ `tests/e2e/test_chat_output_sanitisation.py` روی هر چهار تم |
| 19 | F6: `reviewed_by` برابر نام کاربری | SEC-029 | ✅ | در بازنویسی `review_edit` |
| 20 | F5: رفع `used = 1` روی PostgreSQL | SEC-015 | ✅ | مقایسه با `TRUE`؛ گارد BOOLEAN در تست‌ها |
| 21 | F3: سوختن اتمیک روی مسیر ارسال | SEC-010 | ✅ | شرط روی خودِ `UPDATE` |
| 22 | F7: IP از پراکسی مورد اعتماد + کلید سقف روی ویزیتور | SEC-032، PER-001، PER-002 | ✅ | PER-002 با پارامتر `limit` روی `check_rate_limit` |
| 23 | F7: بودجهٔ روزانهٔ پیامک | PER-004 | ✅ | `sms_daily_budget` در `app/services/sms.py`، شمارش روی همهٔ مسیرها |
| 24 | F4: حذف `challenge_id` به‌عنوان اعتبارنامه | SEC-014 تا SEC-016، REQ-050، REQ-051، REL-008 | ⬜ | **تنها F بازِ بلاک‌کنندهٔ فاز ۲.** `challenge_id` هنوز در `app/routers/otp.py` و `app/routers/leads.py:392` اعتبارنامه است. یک برش، یک انتشار، بدون دورهٔ مهلت |
| 25 | F8: نشست ویزیتور با expiry سمت سرور | SEC-017، SEC-018، SEC-022 | ✅ | `0007_visitor_sessions.sql`، `tests/test_leads_visitor_session.py` |
| 26 | F9: `code_hash` + عمل چرخاندن لینک | SEC-019، SEC-020، REQ-032، REQ-034 | ✅ | همان مهاجرت |
| 27 | تست‌های گروه امنیت | `tests/test_leads_security.py`، XSS | 🔶 | XSS e2e موجود؛ `test_leads_security.py` به‌عنوان یک فایل نیست — با issue 24 یکی شود |
| 28 | اگر وقت بود: F13، F15، F16، F17 | REL-003، SEC-026، SEC-027، SEC-031، SEC-033 | 🔶 | audit خروجی داده ساخته شد (`data.export`)؛ برگرداندن ویرایش تأییدشده و سقف نرخ `/edit` بماند |

## فاز ۳ — لایهٔ هویت

| # | Issue | Requirements | وضعیت | یادداشت |
|---|-------|--------------|:-----:|---------|
| 29 | مهاجرت `dataset_owners` | جدول `dataset_owners` | ✅ | داخل `0008_identity.sql` آمد (بالا را ببینید) |
| 30 | ورود کاربر: `/login` و مسیرهای auth | REQ-035 تا REQ-039، SEC-034 | ✅ | `app/routers/leads.py`، `app/services/identity.py` |
| 31 | صفحهٔ `/my` و مجوز دو مسیره | REQ-040 تا REQ-044، SEC-001 تا SEC-007 | ✅ | `templates/leads/my.html` + `static/leads/my.js` |
| 32 | اتصال `verify_contact` به ساخت کاربر و مالکیت | REL-001، REL-002، SEC-013، SEC-036 | ✅ | `identity.capture_owner` داخل `verify_contact` |
| 33 | مالکیت تک‌نفره و منقضی‌شونده | SEC-011، SEC-012 | ✅ | `_live_owner` و انقضای grant |
| 34 | مدیریت کاربر و مالکیت در ادمین + لغو هنگام آزادسازی | REQ-045 تا REQ-048، REQ-065، SEC-006 | 🔶 | آزادسازی، مالکیت را هم لغو می‌کند؛ فهرست/مدیریت کاربران در پنل ندارد |
| 35 | یکی‌شدن `/verify` و ثبت‌نام چت روی `users` | REQ-049 | ⬜ | UI ثبت‌نام چت از تم مستقل شد (`inject_shared_assets`) ولی هنوز روی `otp_challenges` می‌نشیند، نه `users` |
| 36 | گروه M: ثبت شرکت نبود در فهرست | REQ-052، REQ-053، REQ-074 تا REQ-079 | ✅ | `tests/test_leads_new_company.py`؛ متن تایپی به‌عنوان ویرایش `pending` |
| 37 | `tests/test_identity.py` | معیارهای بخش ۹ PRD | ⬜ | پوشش پراکنده هست، فایل واحد نیست |
| 38 | فعال‌سازی نهایی و برداشتن flag | REL-009، REL-010 | ⬜ | گِیت انتشار؛ پس از 34 و 35 |
| 39 | سیگنال‌های تقلب: خوشه‌ها و دو عدد تسویه | REQ-080، REQ-081 | ✅ | `tests/test_leads_fraud_signals.py`. با پرداخت روی `verified` گِیت انتشار است |

## آنچه از این جدول حذف شد

Issue مربوط به `edit_sessions` و انتقال آن به `user_sessions`: با تصمیم مالک آن
جدول اصلاً وجود ندارد (ADR-013). کاری که از آن باقی مانده حذفش است و داخل
issue 03 نشسته.

## ترتیب کارِ باقی‌مانده

1. **Issue 24 (F4)** — تنها رفع امنیتی باز. تا بسته نشود، لایهٔ هویت روی یک
   اعتبارنامهٔ زنده سوار است.
2. **Issue 35** — یکی‌کردن ثبت‌نام چت با `users`، با rollback طبیعی (flag خاموش).
3. **Issue 34** — فهرست کاربران و مالکیت‌ها در پنل ادمین.
4. **Issue 14 و 37** — دو فایل تست جمع‌کننده. کار جدید نیستند؛ چیزی که هست را
   به نام صدا بزنند تا SC-009 قابل سنجش باشد.
5. **Issue 38** — برداشتن flag، فقط پس از ۱ تا ۳.

## گیت‌های انتشار (SPEC §13.3)

- `0006` و `0007` روی production، نه در حین رویداد. بعد از `0007` هیچ لینک
  شخصی قدیمی کار نمی‌کند؛ همان روز برای هر همکار غرفه لینک تازه بسازید.
- مقدار `leads_invite_channel` تصمیم گرفته و ثبت شده باشد.
- متن رضایت `v1` در تنظیمات نشسته و پای غرفه تمرین شده باشد.
- `leads_reward_stage = verified`؛ امضاکنندهٔ پرداخت دو ستون آخر فهرست و دو عدد
  کنار نام هر همکار را بشناسد (issue 39).
- **تست خط واقعی:** یک پیامک آزادِ لینک‌دار به شمارهٔ خودتان (ADR-15). اگر
  Status 20 گرفت، پیش از رویداد با آسانک حلش کنید — نه وسط رویداد.
- رویهٔ hash مجدد در `docs/engineering/DEPLOYMENT_RUNBOOK.md`.
- ثبت تصمیم‌ها در `docs/engineering/DECISIONS.md` — انجام شد (ADR-013 تا ADR-016).
- `docs/features/INDEX.md` به‌روز — انجام شد.
