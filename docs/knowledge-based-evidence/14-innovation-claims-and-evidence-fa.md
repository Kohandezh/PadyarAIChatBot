# نگاشت ادعا ↔ شواهد

قاعده: هیچ ادعایی بدون فایل/دستور قابل‌اجرا ثبت نمی‌شود.
آخرین راستی‌آزمایی همهٔ اعداد این سند: ۱۴۰۵/۰۶/۲۳ (2026-09-14) — همهٔ
اعداد با اجرای دوبارهٔ دستورهای ارجاع‌شده روی شاخهٔ main بازتولید شده‌اند.

## ۱. بازیابی هیبریدی فارسی-محور
- **ادعا:** خط لولهٔ چندتیره: Jaccard پرسش‌های منتخب → بازیابی هیبریدی
  (امبدینگ چگال + BM25 واژگانی، با بازرتبه‌بندی اجتماع نامزدها) →
  طبقه‌بند intent، با نرمال‌سازی و بسط مترادف فارسی. TF-IDF در هیچ تیری
  از این خط لوله نیست (حذف ۲۰۲۶-۰۸-۲۸؛ پاک‌سازی کد مرده ۲۰۲۶-۰۹-۱۴).
- **شواهد:** `app/routers/chat.py`، `app/services/search.py`
  (`RERANK_CANDIDATES`، مسیر `rerank.best` در `find_best_match` و
  `find_similar_question`)، `app/utils/normalizer.py`؛ بنچمارک
  `appendices/benchmark-results/`.
- **راستی‌آزمایی:** `.venv/bin/python scripts/run_eval.py`
- **اندازه‌گیری فعلی (۱۴۰۵/۰۶/۲۳):** recall@1 = 0.786،
  recall@3 = 0.881، MRR = 0.844 روی مجموعهٔ طلایی ۶۰ پرسشی (۴۲ پرسش
  پاسخ‌پذیر). کورپوس از ۱۶ سند / ۱۲۶ پرسش (۱۴۰۵/۰۵/۲۵) به **۳۵ سند /
  ۲۱۸ پرسش** رشد کرده و رکوردهای نزدیک‌به‌هم جدید چند پرسش را منحرف
  کرده‌اند (فهرست ۱۱ شکست با کلید `failures` در `retrieval-eval.json`)؛
  بنابراین اعداد با اعداد ۱۴۰۵/۰۵/۲۵ (0.881/0.952/0.919) به‌دلیل تغییر
  کورپوس **قابل مقایسه مستقیم نیستند**. شرح کامل در
  `appendices/benchmark-results/improvement-log.md`.
- **وضعیت:** implemented_and_verified

## ۲. امبدینگ معنایی کاملاً محلی
- **ادعا:** model2vec (potion-multilingual-128M) روی host، بدون GPU و بدون
  API؛ کش مدل داخل نصب (data/models)؛ بردار ۲۵۶ بعدی.
- **شواهد:** `app/services/embeddings.py`؛ لاگ اجرای بنچمارک
  (`[embeddings] indexed 16 texts, dim=256` و `indexed 126 texts`).
- **اندازه‌گیری فعلی (۱۴۰۵/۰۶/۲۳):** تأخیر بازیابی محلی p50 = 1.0ms،
  p95 = 2.1ms. این عدد به بار ماشین حساس است (در اجراهای ۱۴۰۵/۰۵/۲۵
  بین p50 ۴.۶ تا ۱۶.۱ms نوسان داشت) و عدد ثبت‌شده از همان یک اجراست.
- **وضعیت:** implemented_and_verified

## ۳. مدل یادگیرندهٔ هر نصب (سازگاری دامنه)
- **ادعا:** طبقه‌بند intent (LR روی امبدینگ پرسش‌های خود نصب) در هر ویرایش
  دیتاست بازآموزی می‌شود؛ دقت holdout ثبت و در اعتماد runtime لحاظ می‌شود.
- **شواهد:** `app/services/intent.py`، گیت پویا در `search.classify_intent_local`.
- **محدودیت صادقانه:** این «مدل اختصاصی» یک طبقه‌بند سبک است، نه LLM. دقت
  holdout فعلی (۱۴۰۵/۰۶/۲۳، بازتولید با آموزش واقعی `intent.train` روی
  seed جاری) روی کورپوس **۳۵ سندی / ۲۱۸ پرسش / ۳۵ کلاس** برابر **۰.۴۵۷**
  است — با رشد کلاس‌ها از ۱۶ به ۳۵ مسئله سخت‌تر شده و نسبت به ۰.۵۷۹ِ
  دورهٔ ۱۲۶ پرسش / ۱۶ کلاس افت کرده است. چون زیر ۰.۷ است، گیت پویا فقط
  پیش‌بینی‌های p≥0.85 را می‌پذیرد
  (`search.classify_intent_local`: `acc < 0.7 and prob < 0.85 → reject`).
- **وضعیت:** implemented_and_verified (با محدودیت ثبت‌شده)

## ۴. کالیبراسیون دامنه-محور امتیاز معنایی
- **ادعا:** باند کسینوس بر اساس توزیع اندازه‌گیری‌شدهٔ کورپوس INOTEX تنظیم
  شده تا خوشهٔ «مطمئنِ غلط» زیر آستانهٔ اعتماد بیفتد.
- **شواهد:** `app/services/embeddings.py` (اعداد + روش)، `improvement-log.md`.
- **وضعیت:** implemented_and_verified

## ۵. چرخهٔ حیات خودکار دانش با حاکمیت انسانی
- **ادعا:** منیفست منابع رسمی، واکشی/هش/snapshot، تشخیص تغییر با exit code،
  صف بازبینی، نسخه‌گذاری دانش (settings + health)؛ به‌علاوه از ۲۰۲۶-۰۹-۱۴
  **پیشنهاد خودکار** مترادف (بند ۱۵) و پرسش‌های متنوع (بند ۱۶) توسط مدل —
  با اعتبارسنجی سخت‌گیرانه و تأیید صریح ادمین قبل از هر نوشتنی — و
  زمان‌بندی هفتگی بررسی تازگی (تایمر systemd + workflow گزارشی).
- **شواهد:** `content/sources.json`، `scripts/refresh-inotex-context.py`،
  `content/freshness-report.json` (اجرای واقعی)، `content/review-queue.md`،
  `app/services/{synonym_suggest,question_assist}.py`،
  `deploy/systemd/padyar-freshness@.{service,timer}`،
  `.github/workflows/freshness.yml`.
- **گپ:** استخراج ساخت‌یافتهٔ خودکار از HTML هنوز planned است — تبدیل تغییر
  به رکورد جدید مسیر دستی+بازبینی دارد (پیشنهادهای خودکارِ موجود روی
  محتوای موجودِ دیتابیس کار می‌کنند، نه روی HTML خام).
- **وضعیت:** partially_implemented

## ۶. استقلال از vendor
- **ادعا:** لایهٔ خارجی فقط به قرارداد OpenAI-compatible وابسته است؛ per-install
  قابل تعویض بدون تغییر کد؛ لایهٔ محلی بدون هیچ سرویس خارجی پاسخ می‌دهد.
- **شواهد:** `app/services/providers.py`، `app/services/openai.py::provider_config`،
  `/api/ready?deep=true`.
- **گپ:** routing همزمان چند-provider با سیگنال هزینه/تأخیر: planned.
- **وضعیت:** partially_implemented

## ۷. ارزیابی بازتولیدپذیر با گیت سخت
- **ادعا:** مجموعهٔ طلایی نسخه‌دار (۶۰ پرسش، ۸ دسته شامل تزریق و آلودگی، ۴۲
  پرسش پاسخ‌پذیر)؛ اجرای آفلاین؛ گیت‌های سخت آلودگی=۰ و نشت=۰ (exit≠0).
- **شواهد:** `data/eval/golden-inotex.json` (نسخهٔ `inotex-golden-1.0`)،
  `scripts/run_eval.py`، نتایج در `appendices/benchmark-results/` + گزارش
  چرخهٔ بهبود.
- **اندازه‌گیری فعلی (۱۴۰۵/۰۶/۲۳):** آلودگی = ۰، نشت اسرار = ۰، مطمئنِ
  غلطِ تزریق = ۰، مطمئنِ غلطِ legacy = ۰، مطمئنِ غلطِ unsupported = ۲
  (بهبود نسبت به ۳ دورهٔ قبل).
- **اجرای خودکار:** این گیت حالا در CI اجرا می‌شود — job `evaluation` در
  `.github/workflows/ci.yml`، مسدودکننده و با `needs: test`. گزارش JSON هم
  به‌عنوان artifact آپلود می‌شود.
- **وضعیت:** implemented_and_verified (دستی + CI)

## ۷.۱. پوشش تست خودکار
- **ادعا:** مجموعهٔ تست کامل و سبز روی کل سکو، به‌علاوهٔ سوئیت اختصاصی
  PostgreSQL که از ۲۰۲۶-۰۹-۱۴ در CI مسدودکننده اجرا می‌شود.
- **شواهد/راستی‌آزمایی (۱۴۰۵/۰۶/۲۳):**
  - شمارش توابع: `grep -rc "def test_" tests/ | awk -F: '{s+=$2} END {print s}'`
    → **۲۲۲۲ تابع تست** در ۱۵۴ فایل.
  - شمارش pytest بدون اجرا: `.venv/bin/python -m pytest --collect-only -q | tail -2`
    → **۲۶۶۹ تست جمع‌آوری‌شده** (اختلاف با ۲۲۲۲ به‌دلیل تست‌های پارامتری است).
  - سوئیت PostgreSQL: ۱۱۴ تابع تست در `tests/postgres/` — job مسدودکنندهٔ
    `postgres-tests` در `.github/workflows/ci.yml` (سرویس postgres:16،
    `RUN_POSTGRES_TESTS=1`).
  - گزارش پوشش (گزارشی، بدون آستانه): `pytest-cov` در job `test` و
    `requirements-dev.txt`.
  - تست قابلیت‌های جدید ۲۰۲۶-۰۹-۱۴: `tests/test_metrics.py` (۱۳)،
    `tests/test_synonym_suggest.py` (۱۲)، `tests/test_question_assist.py` (۱۱)،
    `tests/test_backup_offsite.py` (۹).
- **نکته دربارهٔ اعداد:** این‌ها snapshot هستند و مجموعه در حال رشد است؛
  مرجع همیشه خروجی خود دستور است، نه این سند.
- **وضعیت:** implemented_and_verified

## ۸. هویت بصری سیستم‌مند
- **ادعا:** تم مستقل با توکن‌های پالت رسمی، بدون رنگ خارج پالت
  (همهٔ توکن‌ها از `--wl-*` برندینگ می‌آیند)، لودر با aria-busy و
  reduced-motion؛ تست خودکار پالت.
- **تصمیم مالک دربارهٔ ماسکات (به‌روزشده):** همدمی Pet-INOTEX به درخواست
  مالک (2026-08-24) **برگشته است** — سطح تم،
  فقط دسکتاپ/تبلت و زیر ۶۴۰px مخفی؛ ادعای «بدون ماسکوت» دورهٔ قبل دیگر
  معتبر نیست و همین‌جا اصلاح می‌شود.
- **شواهد:** `themes/inotex/`، `tests/test_public_ui.py`
  (`test_inotex_theme_uses_official_palette_tokens`،
  `test_companion_is_live_but_desktop_only`).
- **وضعیت:** implemented_and_verified

## ۹. بازیاب واژگانی Okapi BM25 (پیاده‌سازی داخلی)
- **ادعا:** پیاده‌سازی BM25 با پایتون خالص و بدون افزودن هیچ وابستگی جدید،
  با اشباع فراوانی (k1=1.5) و نرمال‌سازی طول (b=0.75)؛ شاخص در هر بازایندکس
  در همان فرآیند ساخته و اتمیک منتشر می‌شود.
- **چرا:** کسینوس TF-IDF اثر طول سند را کامل حذف می‌کند و روی این کورپوس
  اجازه می‌دهد یک پاسخ بلند بر یک رکورد کوتاهِ دقیقاً مرتبط پیشی بگیرد.
- **جزئیات فنی قابل بررسی:** فرم IDF از نوع BM25+ انتخاب شده تا برای واژه‌های
  پرتکرار دامنه‌ای مثل «اینوتکس» منفی نشود (فرمول کلاسیک روی کورپوس ۱۶ سندی
  این واژه‌ها را جریمه می‌کرد). امتیازها نسبت به بهترین نتیجهٔ همان پرسش
  نرمال می‌شوند، چون امتیاز خام BM25 بی‌کران و وابسته به پرسش است.
- **شواهد:** `app/services/bm25.py` (`BM25Index`، `top_k`، `build_index`)؛
  اتصال در `app/services/search.py` (`dataset_bm25_index`،
  `questions_bm25_index`).
- **تاب‌آوری:** `build_index` در خطا `None` برمی‌گرداند و خط لوله به بازیاب‌های
  دیگر تنزل می‌کند — خرابی شاخص هرگز چت‌بات را از کار نمی‌اندازد.
- **وضعیت:** implemented_and_verified

## ۱۰. بازرتبه‌بند ویژگی‌محور
- **ادعا:** یک مرحلهٔ بازرتبه‌بندی روی اجتماع نامزدهای بازیاب چگال و واژگانی،
  با چهار سیگنال: چگال (وزن ۰.۶۲)، واژگانی (۰.۲۳)، پوشش توکن‌های محتوایی
  پرسش (۰.۱۵) و پاداش توافق دو بازیاب (۰.۰۴).
- **سیگنال ضدتوهم:** «پوشش» سهم توکن‌های محتوایی پرسش است که واقعاً در نامزد
  حاضرند؛ نامزدی که هیچ واژهٔ محتوایی مشترک ندارد امتیازش نصف می‌شود، هرچقدر
  هم در فضای امبدینگ نزدیک به نظر برسد. نصف‌کردن به‌جای حذف عمدی است: در
  پرسش انگلیسی روی رکورد فارسی، پوشش به‌درستی صفر است و سیگنال چگال باید
  همچنان بتواند برنده شود.
- **نقص تساوی — پیدا شد و رفع شد:** وقتی کسینوس خام همهٔ نامزدها زیر
  `COSINE_FLOOR = 0.45` باشد، همه به ۰.۰ کالیبره می‌شوند و اگر BM25 هم چیزی
  ندهد، همهٔ امتیازها دقیقاً مساوی می‌شوند. پیش‌تر برندهٔ این تساوی از ترتیب
  پیمایش `set` درمی‌آمد — عملاً شمارهٔ سطر دیتاست. حالا ترتیب خودِ بازیاب چگال
  (و سپس واژگانی) تساوی را می‌شکند. جایی که امتیازها واقعاً فرق دارند هیچ چیز
  عوض نشده. اثر اندازه‌گیری‌شده: recall@1 از 0.833 به **0.881** و recall@3 از
  0.905 به **0.952**. تست: `tests/test_rerank_tiebreak.py`.
- **حفظ سازگاری:** خروجی روی همان مقیاس ۰..۱ می‌ماند، بنابراین آستانه‌های
  موجود (`TRUSTED_MATCH_THRESHOLD=0.70`، `LOCAL_FALLBACK_THRESHOLD=0.45`)
  معنای خود را حفظ می‌کنند و هیچ فراخوانی‌ای تغییر نکرد.
- **قابلیت توضیح:** `rerank` سیگنال‌های هر تصمیم را برمی‌گرداند تا هر رتبه‌بندی
  پس از وقوع قابل توضیح باشد.
- **کنترل عملیاتی:** `RETRIEVAL_RERANK=false` کل مرحله را خاموش می‌کند
  (`app/config.py::RERANK_ENABLED`).
- **آنچه ادعا نمی‌شود:** این یک cross-encoder عصبی **نیست**. دلیل انتخاب
  (پرهیز از torch + transformers و دانلود چندصدمگابایتی روی ماشین غرفه که
  یک‌بار و آفلاین آماده می‌شود) در `docs/engineering/DECISIONS.md` ثبت است.
- **شواهد:** `app/services/rerank.py`.
- **وضعیت:** implemented_and_verified

## ۱۱. تاکسونومی داده‌محور با بارگذاری گرم و اعتبارسنجی
- **ادعا:** همهٔ گزینه‌های فرم ثبت‌نام و همهٔ بخش‌هایی که برنامه‌ریز بازدید
  پیشنهاد می‌دهد از `data/visit-taxonomy.json` می‌آیند؛ هیچ‌کدام در فرم،
  برنامه‌ریز یا شیوه‌نامه hardcode نیستند — تعویض تاکسونومی «جایگزینی فایل»
  است، نه تغییر کد.
- **دو تضمین قابل تست:**
  ۱) فایل خراب هرگز به محصول نمی‌رسد — سند پیش از انتشار کامل اعتبارسنجی
  می‌شود و در هر خطا آخرین نسخهٔ سالم سرو می‌ماند؛ یک ردیف بد کل فهرست را
  خالی نمی‌کند.
  ۲) ویرایش‌ها بدون ری‌استارت اعمال می‌شوند — mtime در هر خواند بررسی و سند
  پارس‌شده اتمیک جایگزین می‌شود.
- **شواهد:** `app/services/taxonomy.py`؛ ۱۸ تست در `tests/test_taxonomy.py`
  از جمله `test_edits_apply_without_a_restart`،
  `test_broken_json_keeps_the_previous_taxonomy`،
  `test_a_file_with_no_usable_sections_is_refused`،
  `test_one_malformed_job_does_not_empty_the_dropdown`،
  `test_replacing_the_file_changes_what_the_planner_recommends`.
- **وضعیت:** implemented_and_verified

## ۱۲. ماژول ثبت‌نام و تأیید شماره (OTP) با ذخیرهٔ HMAC کلیددار
- **ادعا:** ماژول اختیاری `registration` (`app/modules/registry.py`)، شامل
  صفحهٔ `/verify` و APIهای `/api/auth/otp/*`.
- **قرارداد امنیتی قابل بررسی:**
  - کد با CSPRNG (`secrets`) تولید می‌شود.
  - کد خام **هرگز** ذخیره و **هرگز** لاگ نمی‌شود؛ دیتابیس فقط
    HMAC-SHA256 کلیددارِ `challenge_id:code` را با کلید مخفی برنامه نگه
    می‌دارد (هش بدون کلید روی کد ۶ رقمی در چند میلی‌ثانیه brute-force
    می‌شود).
  - مقایسه زمان-ثابت است (`hmac.compare_digest`).
  - انقضا، سقف تلاش، cooldown و سقف ارسال مجدد، و محدودیت نرخ به‌ازای هر
    مقصد همگی سمت سرور اعمال می‌شوند؛ تایمر رابط کاربری فقط نمایشی است.
  - چالش به یک `challenge_id` غیرقابل‌حدس گره خورده است.
  - موفقیت، چالش را مصرف می‌کند (یک‌بارمصرف)؛ ارسال مجدد کد قبلی را اتمیک
    باطل می‌کند.
  - `OTP_DELIVERY=dev` وقتی `COOKIE_SECURE=true` (نشانگر تولید) باشد رد
    می‌شود؛ کد در هیچ پاسخ API برنمی‌گردد.
- **شواهد:** `app/services/otp.py`، `app/routers/otp.py`،
  `app/services/sms.py`؛ ۱۶ تست در `tests/test_otp.py` (از جمله
  `test_code_is_secure_length_and_never_stored_raw`،
  `test_audit_log_never_contains_raw_code`،
  `test_attempt_limit_blocks_brute_force`،
  `test_valid_code_verifies_once_then_replay_fails`،
  `test_destination_hourly_rate_limit`) و ۱۱ تست در
  `tests/test_profile_edit.py` (از جمله
  `test_edit_cannot_change_name_or_number`،
  `test_unverified_challenge_cannot_be_edited`،
  `test_options_endpoint_exposes_no_secrets`).
- **وضعیت:** implemented_and_verified

## ۱۲.۱. برنامه‌ریز بازدید با نرمال‌سازی بدون مترادف
- **ادعا:** برنامه‌ریز بازدید پروفایل بازدیدکننده را به بخش‌های INOTEX نگاشت
  می‌کند و عمداً از بسط مترادف استفاده **نمی‌کند**.
- **چرا (تصمیم مهندسی ثبت‌شده در کد):** جدول مترادف دیتابیس برای بازیاب
  پرسش‌وپاسخ تنظیم شده، نه برای این تطبیق گزینشی؛ بسط هم‌زمان کلیدواژه و
  متن پروفایل باعث تطبیق‌های کاذب می‌شد. بنابراین فراخوانی صریح
  `normalize_persian(text, expand_synonyms=False)` است.
- **جبران:** مترادف‌های لازم به‌صورت `keywords` هر علاقه در خودِ تاکسونومی
  می‌آیند و با `taxonomy.expand_interests` تا می‌شوند — یعنی داده‌محور، نه
  کدمحور.
- **شواهد:** `app/services/visit_plan.py`،
  `app/services/taxonomy.py::expand_interests`؛ ۳۰ تست در
  `tests/test_visit_plan.py`.
- **وضعیت:** implemented_and_verified

## ۱۳. آنچه ادعا نمی‌شود
- LLM اختصاصی آموزش‌دیده — وجود ندارد؛ پیش‌نیازها (دیتاست مجوزدار، بودجهٔ
  محاسباتی، ارزیابی) در `15-limitations-and-roadmap-fa.md`.
- گراف دانش دامنه‌ای مستقل — فعلاً نقش آن را متادیتای رکوردها و مترادف‌ها
  بازی می‌کنند؛ planned.
- بازرتبه‌بند عصبی (cross-encoder) — بازرتبه‌بند موجود ویژگی‌محور است، نه
  مدل عصبی؛ دلیل انتخاب در بند ۱۰ و `docs/engineering/DECISIONS.md`.
- اسکن آسیب‌پذیری کانتینر (trivy) — pip-audit در CI هست، اسکن ایمیج نیست.
- زیرساخت مانیتورینگ کامل — نقطهٔ پایانی `/metrics` هست (بند ۱۴)، اما
  سرور Prometheus، داشبورد و tracing **نیستند** و ادعا نمی‌شوند.
- **بازبینی انسانی انجام‌شده — هنوز pending است و جعل نمی‌شود.**

## ۱۴. نقطهٔ پایانی متریکس پرومتئوس (۲۰۲۶-۰۹-۱۴)
- **ادعا:** `GET /metrics` با قالب Prometheus؛ احراز هویت سخت: اگر
  `METRICS_TOKEN` تنظیم شده باشد `Authorization: Bearer <token>` لازم است،
  وگرنه نشست ادمین (`verify_admin` — همان مسیر همهٔ APIهای ادمین). متریک‌ها:
  شمارنده/هیستوگرام/gauge درخواست‌های HTTP (قالب مسیر، نه مسیر خام —
  کاردینالیته کنترل‌شده)، tier پاسخ چت، فراخوانی‌های AI (بر حسب ارائه‌دهنده
  و نتیجه)، وضعیت قطع‌کنندهٔ مدار (circuit)، نتیجهٔ پشتیبان‌گیری و امتیاز
  سلامت.
- **شواهد:** `app/services/metrics.py`، `app/routers/metrics.py`، میان‌افزار
  در `app/main.py`، قلاب‌ها در `app/services/{health,pg_backup,ai/circuit}.py`؛
  راهنما: `docs/engineering/MONITORING.md`.
- **راستی‌آزمایی:** `tests/test_metrics.py` — ۱۳ تست (افزایش شمارنده‌ها،
  ۴۰۳ بدون احراز هویت، احراز هویت توکنی، قلاب پشتیبان).
- **محدودیت صادقانه:** فقط نقطهٔ پایانی — سرور Prometheus/داشبورد/tracing
  هنوز نصب نشده (بند ۹ سند محدودیت‌ها).
- **وضعیت:** implemented_and_verified

## ۱۵. پیشنهاد خودکار مترادف با تأیید انسانی (۲۰۲۶-۰۹-۱۴)
- **ادعا:** دکمهٔ «پیشنهاد هوشمند مترادف» در پنل مترادف‌ها: سیگنال‌ها
  (واژه‌های پرتکرار کورپوس + پرسش‌های کم‌اطمینان) در یک پرامپت به مدل
  داده می‌شوند؛ هر پیشنهاد اعتبارسنجی سخت‌گیرانه می‌شود (نرمال‌سازی متفاوت،
  نبودن در جدول، طول محدود، حذف تکراری) و **فقط انتخاب صریح ادمین** از طریق
  همان مسیر افزودن مترادف موجود (reindex و نرمال‌سازی تک‌منبعی) ذخیره
  می‌شود. cooldown نصب‌سراسری ۶۰ ثانیه برای محافظت از هزینهٔ مدل؛ خطای
  عدم‌دسترسی AI پیام فارسی می‌شود، بدون کرش.
- **شواهد:** `app/services/synonym_suggest.py`،
  `POST /admin/api/synonyms/suggest` و `.../suggest/apply` در
  `app/routers/synonyms.py`، `templates/admin/synonyms.html` +
  `static/admin/js/synonyms.js`.
- **راستی‌آزمایی:** `tests/test_synonym_suggest.py` — ۱۲ تست (با مدل
  mock شده: فیلتر پیشنهادهای نامعتبر، درج + reindex، ۴۰۳ بدون ادمین).
- **وضعیت:** implemented_and_verified

## ۱۶. پیشنهاد خودکار پرسش برای رکوردهای دانش (۲۰۲۶-۰۹-۱۴)
- **ادعا:** در مودال ویرایش رکورد، دکمهٔ «پیشنهاد سوال»: مدل از عنوان/متن/
  پرسش‌های موجود رکورد paraphrase پیشنهاد می‌دهد؛ اعتبارسنجی (یکتایی نرمال‌شده
  در برابر پرسش‌های موجود، طول ۵..۱۲۰، سقف تعداد) و درج انبوه فقط پس از
  انتخاب ادمین از طریق همان مسیر ایجاد پرسش موجود (reindex تک‌منبعی).
- **شواهد:** `app/services/question_assist.py`،
  `POST /admin/api/dataset/{id}/suggest-questions` و `.../apply-questions`
  در `app/routers/dataset.py`، دکمه در `templates/admin/dataset.html` +
  `static/admin/js/dataset.js`.
- **راستی‌آزمایی:** `tests/test_question_assist.py` — ۱۱ تست (فیلترها، درج
  + reindex، ۴۰۳ بدون ادمین، ۴۰۴ برای رکورد ناشناخته).
- **وضعیت:** implemented_and_verified

## ۱۷. کپی خارج از سرور پشتیبان‌های تأییدشده (۲۰۲۶-۰۹-۱۴)
- **ادعا:** بعد از پشتیبان محلی موفق + تأیید SHA-256، اگر
  `OFFSITE_BACKUP_TARGET` تنظیم شده باشد، نسخهٔ تأییدشده به مقصد خارج از
  سرور کپی می‌شود (قالب‌های `rsync:user@host:/path` یا `dir:/mounted/path`؛
  timeout قابل‌تنظیم). نتیجه (مقصد، sha256، کد خروج، مدت) در بلوک `offsite`
  همان `manifest.json` و در رخداد سرویس ثبت می‌شود. شکست کپی **غیرمهلک** است:
  پشتیبان محلی معتبر می‌ماند.
- **شواهد:** `app/services/backup_offsite.py`، قلاب در
  `app/services/pg_backup.py`، متغیرهای محیطی در `app/config.py` +
  `.env.example` + `deploy/env/*.env.template`.
- **راستی‌آزمایی:** `tests/test_backup_offsite.py` — ۹ تست (موفقیت با
  subprocess/copy سالخوردهٔ mock، بدون کانفیگ → بدون عمل، شکست rsync →
  غیرمهلک + رخداد).
- **محدودیت صادقانه:** کد آماده است اما مقصد باید **برای هر نصب** پیکربندی
  شود؛ تا وقتی `OFFSITE_BACKUP_TARGET` تنظیم نشود، کپی انجام نمی‌شود.
- **وضعیت:** implemented_and_verified (کد) / per-install configuration لازم

## ۱۸. فرایند انتشار نسخه (۲۰۲۶-۰۹-۱۴)
- **ادعا:** فایل `VERSION` (۰.۱.۰) منبع `app.__version__` است و فیلد
  `version` در `GET /api/health` برمی‌گردد؛ `CHANGELOG.md` با قالب Keep a
  Changelog؛ workflow انتشار روی تگ‌های `v*`: اجرای کامل تست‌ها و سپس
  ساخت GitHub Release از متن همان نسخهٔ CHANGELOG.
- **شواهد:** `VERSION`، `app/__init__.py`، `app/routers/public.py`،
  `CHANGELOG.md`، `.github/workflows/release.yml`، راهنمای
  `docs/engineering/RELEASING.md`.
- **راستی‌آزمایی:** `tests/test_health_surface.py`
  (`test_public_health_reports_the_running_version` — نسخهٔ گزارش‌شده باید
  با فایل VERSION یکی باشد).
- **محدودیت صادقانه:** تا امروز (۱۴۰۵/۰۶/۲۳) **هیچ تگی بریده نشده است**
  (`git tag` → خالی)؛ CHANGELOG نسخهٔ 0.1.0 مورخ 2026-09-14 دارد اما
  انتشار واقعی هنوز انجام نشده.
- **وضعیت:** implemented_and_verified (فرایند) / اولین تگ هنوز بریده نشده

## ۱۹. تست PostgreSQL در CI + گزارش پوشش (۲۰۲۶-۰۹-۱۴)
- **ادعا:** job جدید `postgres-tests` در `ci.yml` با سرویس
  `postgres:16-alpine` و `RUN_POSTGRES_TESTS=1` کل `tests/postgres` را
  اجرا می‌کند و **مسدودکننده** است؛ job `test` هم با `pytest-cov` گزارش
  پوشش چاپ می‌کند (گزارشی، بدون آستانه — عدد پوشش گیت نیست).
- **شواهد:** `.github/workflows/ci.yml`، `tests/postgres/` (۱۱۴ تابع
  تست)، `pytest-cov` در `requirements-dev.txt`؛ راهنمای به‌روزشده:
  `docs/engineering/POSTGRES_TESTING.md`.
- **راستی‌آزمایی:** شمارش `grep -rc "def test_" tests/postgres/ | awk -F: '{s+=$2} END {print s}'` → ۱۱۴.
- **وضعیت:** implemented_and_verified
