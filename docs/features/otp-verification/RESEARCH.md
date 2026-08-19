# OTP Verification + Pet-INOTEX — Feature Notes

تاریخ: ۱۴۰۵/۰۵/۲۳ (2026-08-14) · وضعیت: پیاده‌سازی‌شده، در انتظار بازبینی انسانی

## مرجع طراحی
`../../image/otp.PNG` (چک‌سام `ef333a55f95cd3fb`، ۱۱۲۲×۱۴۰۲) — بورد طراحی شامل
موبایل، وضعیت‌های ورودی، چیپ‌ها و کتابخانهٔ کامپوننت. **فقط مرجع بازسازی**؛
هیچ بخشی از آن به‌صورت raster وارد UI نشده. شمارهٔ نمونهٔ داخل تصویر
(+98 *** *** 4821) دادهٔ واقعی نیست.

## معماری
- ماژول اختیاری `otp` (registry) → `app/routers/otp.py` + `app/services/otp.py`
- صفحهٔ `/verify` (خارج از اسکلت چت؛ در ناوبری عمومی لینک نشده)
- API: `POST /api/auth/otp/{request,verify,resend}` + `GET /api/auth/otp/status/{id}`
- ذخیره‌سازی: جدول `otp_challenges` — فقط HMAC-SHA256 کلیددار از کد (کلید
  `_get_hmac_key` موجود)، هرگز کد خام؛ مقایسهٔ constant-time
- تحویل: provider seam؛ فقط providerِ dev (فایل outbox جیت‌ایگنور‌شده) —
  در تولید (COOKIE_SECURE=true) صریحاً refuse می‌شود؛ کد هرگز در پاسخ API نیست
- محدودیت‌ها (env-قابل‌تنظیم): TTL=120s، cooldown=45s، ۵ تلاش، ۳ ارسال مجدد،
  ۵ درخواست/ساعت به‌ازای هر شماره + rate-limit عمومی per-IP

## Pet-INOTEX
- دارایی اصیل: `static/otp/pet/inotex-pose-atlas.webp` (کپی از
  `Pet-Inotex/Avatar/poses/`، چک‌سام `35814fe06410107e`، ۱۲ پوز ۳۸۴px)
- هویت: مینی‌فیگ مرد ریش‌دار کت سرمه‌ای — تأییدشده متمایز از ماسکوت رویداد قبلی
- رندرر: `static/otp/pet.js` — جزئیات منتقل‌شده از موتور Pet-Inotex:
  crossfade ~180ms با easeOutCubic، تنفس سینوسی دورهٔ 3.4s (خواب 5.2s) با
  دامنهٔ scaleY 0.009، ورود scale 0.9→1، DPR≤3، reduced-motion → فریم ثابت
- نگاشت وضعیت: greet→welcome-wave، focus→attentive-hands، تایپ→typing،
  کامل→idle-smile، در حال بررسی→thinking، موفق→success، خطا→not-found
- جایگاه: دسکتاپ کنار کارت؛ موبایل بالای کارت در جریان سند (هرگز روی کنترل‌ها)
- سیاست سراسری بدون-ماسکوت در بقیهٔ صفحات پابرجاست (تست دارد)

## تصمیم ثبت‌شدهٔ UX
پس از کد اشتباه، ارقام واردشده **حفظ** و رقم اول انتخاب می‌شود (پاک‌کردن
شش رقم به‌خاطر یک خطای تایپی، کاربر غرفه را تنبیه می‌کند). پس از ارسال
مجدد موفق، ارقام پاک و خطاها ریست می‌شوند.

## وضعیت ارتقای تصویری (blocked)
تلاش برای HD/پوز جدید via OpenCode: `openai/chatgpt-image-latest` →
model_not_found برای پروژهٔ اکانت؛ `openai/gpt-image-1-mini` → 404؛
`google/gemini-3.1-flash-image` → سهمیهٔ صفر. نیازمند فعال‌سازی مدل تصویری
در یکی از اکانت‌ها.

## تست‌ها
`tests/test_otp.py` — ۱۶ تست: تولید امن، عدم ذخیره/لاگ/بازگشت کد خام،
ماسک شماره، تأیید ارقام فارسی/عربی، single-use و replay، انقضای سمت سرور،
سقف تلاش (کد درست هم پس از سقف رد می‌شود)، cooldown و ابطال کد قبلی در
resend، سقف ساعتی شماره، رندر صفحه با pet و گروه accessible.
