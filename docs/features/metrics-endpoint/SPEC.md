# SPEC: نقطهٔ پایانی متریک‌های پرومتئوس (`metrics-endpoint`)

| Field | Value |
|-------|-------|
| Created | 2026-09-14 |
| Updated | 2026-09-14 |
| Status | Implemented |
| Domain | infrastructure |
| Author | تیم پادیار |
| Sources | ارزیابی ۲۰۲۶ دانش‌بنیان («نبود مانیتورینگ») + برنامهٔ اصلاح `docs/superpowers/plans/2026-09-14-assessment-remediation.md` تک ۲ |

این سند همان چیزی را مستند می‌کند که الان در کد هست (قانون ضد doc-fiction).
راهنمای بهره‌بردار: `docs/engineering/MONITORING.md`.

---

## ۱. سناریو

**چه کسی، از کجا:** اپراتور نصب (یا سرور Prometheus او) می‌خواهد بدون ورود
به پنل ادمین بداند برنامه زیر فشار چطور رفتار می‌کند — چند درخواست، چه
تأخیری، کدام تیر پاسخ داده، مدار کدام سرویس‌دهندهٔ هوش مصنوعی باز شده،
پشتیبان شب گذشته موفق بوده یا نه.

**شرط واقعی:** نمایشگاه، یک فرایند uvicorn، پایگاه PostgreSQL. متریک‌ها
درون همان پروسه زندگی می‌کنند (in-memory) و اسکرپ بیرونی با توکن می‌آید.

## ۲. آنچه ارسال شده است

### نقطهٔ پایانی

`GET /metrics` (`app/routers/metrics.py`) — همیشه بارگذاری می‌شود (چرخهٔ
حیات این قابلیت داخل خودِ روتر است، نه ماژول اختیاری)، محتوای
`text/plain; version=0.0.4` از رجیستری اختصاصی `app/services/metrics.py`.

### امنیت (هر اسکرپ خودش احراز هویت می‌شود)

- `METRICS_TOKEN` تنظیم شده → فقط `Authorization: Bearer <token>` با مقایسهٔ
  زمان-ثابت (`secrets.compare_digest`).
- `METRICS_TOKEN` خالی → نشست ادمین معتبر (همان `verify_admin`).
- هر شکست → **403** (نه 401). هیچ پیکربندی‌ای نیست که در آن `/metrics` به
  درخواست ناشناس جواب بدهد. nginx نباید `/metrics` را به اینترنت بفرستد —
  توکن لایهٔ دوم است، نه درِ ورود (جزئیات در MONITORING.md).

### متریک‌ها (فهرست کامل با برچسب‌ها در MONITORING.md)

`http_requests_total{method,route,status}` ·
`http_request_duration_seconds{method,route}` ·
`http_inflight` ·
`chat_tier_served_total{tier}` ·
`ai_calls_total{provider,outcome}` ·
`ai_circuit_state{instance}` (0=bسته، 1=نیم‌باز، 2=باز) ·
`backup_outcome_total{result}` ·
`health_score`

قاعدهٔ کاردینالیتی: برچسب `route` فقط تمپلیت مسیر است (`route_template`
در `app/services/metrics.py`)؛ مسیر ناموجود → `unmatched`، فایل‌های سرو
شده → پیشوند ثابت. هیچ مقدار برچسبی از ورودی بازدیدکننده ساخته نمی‌شود.

## ۳. نقاط اتصال (hook) و چرا همان‌جا

| متریک | نقطهٔ اتصال | چرا |
|---|---|---|
| `chat_tier_served_total` | `_log_turn` در `app/routers/chat.py` | تنگهٔ واحدی که همهٔ شاخه‌های پاسخ (۱۹ مورد، از pick تا no_answer) از آن گذر می‌کنند؛ `source` همان نام تیر است. |
| `ai_calls_total` | `_record_usage` در `app/services/ai/engine.py` | یک‌بار در پایان هر درخواست مسیریابی‌شده، موفق یا ناموفق؛ retry/failover به‌عنوان تلاش داخل همان سطر شمرده می‌شود. |
| `ai_circuit_state` | گذارهای اثبات‌شده در `app/services/ai/circuit.py` (گیرِ اجارهٔ half-open، بازیابی، سه مسیر باز‌شدن، ریست ادمین) | همان مسیرهایی که رخداد applog را می‌سازند؛ گیج و لاگ و پایگاه همیشه یک داستان می‌گویند. |
| `backup_outcome_total` | `create` در `app/services/pg_backup.py` (موفق/شکست) | نتیجهٔ نهاییِ خودِ ساخت پشتیبان. |
| `health_score` | `health_score` در `app/services/health.py` | هر جا امتیاز محاسبه می‌شود، گیج به‌روز می‌شود. |
| متریک‌های HTTP | میدل‌ور `prometheus_metrics` در `app/main.py` (بیرونی‌ترین لایه) | حتی 403/413 میدل‌ورهای امنیتی هم شمرده می‌شوند. |

## ۴. تست‌ها (قرمز-اول)

`tests/test_metrics.py` — ۱۳ تست: شمارندهٔ HTTP با برچسب تمپلیت مسیر،
ممنوع بودن مسیر خام به‌عنوان برچسب، هیستوگرام تأخیر، 403 بدون احراز هویت،
توکن Bearer (درست/غلط/غایب)، نشست ادمین، رد شدن Bearer در حالت نشست،
شمارندهٔ تیر روی یک نوبت واقعی چت (با خاموش‌کردن AI برای قطعیت)، موفق/شکست
پشتیبان با `_run` ماک‌شده، شمارندهٔ فراخوانی AI، گذارهای مدار، و گیج امتیاز
سلامت. هر تست سیم‌کشی را از مسیر واقعی آزمایش می‌کند؛ حذف هر هوک، تست
مربوطه را قرمز می‌کند.

## ۵. پیکربندی

`METRICS_TOKEN` (متغیر محیطی، `app/config.py`) — خالی یعنی حالت نشست ادمین.
در زمان درخواست خوانده می‌شود.
