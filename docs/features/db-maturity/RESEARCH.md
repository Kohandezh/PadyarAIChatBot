# SPIKE: بلوغ پایگاه داده و استقرار برای یک نصب تک‌مشتری

**Status:** In Review
**Owner:** dbmat-t1-res (عامل AI)، بازبینی انسانی: pending (Sina)
**Timebox:** یک نشست کاری (حدود 6 ساعت، شامل آزمایش‌های Docker)
**Date:** 2026-09-30 (نوبت 1)، به‌روزرسانی 2026-10-01 (نوبت 2: تصمیم‌های مالک محصول و یافته‌های بازبین)
**Base commit:** نوبت‌های 1 تا 3 روی `3a4a415`؛ از نوبت 4 (2026-10-02) روی `534e797` (`origin/main`)

> قاعدهٔ شواهد این سند: هر ادعا دربارهٔ کد یک `file:line` در commit `534e797`
> دارد (همهٔ ارجاع‌ها در نوبت 4 دوباره با `origin/main` سنجیده شدند). هر عدد یا «اندازه‌گیری‌شده روی Mac توسعه در Docker، نه روی سرور» است،
> یا «تخمین» با دلیلش. هر ادعای بیرونی (ابزار، نسخه، مجوز) منبع اصلی و تاریخ
> دارد. استنتاج با کلمهٔ «استنتاج» مشخص شده است.

## 1. Question

یک مشتری سازمانی برای **ماندگاری داده** و **استقرار** یک CMS تک‌نصبی (یک نصب
برای هر مشتری، روی یک سرور) واقعاً به چه چیزی نیاز دارد، و ما چطور آن را بدون
پیچیدگی اضافه می‌سازیم و در همین مخزن (اسکریپت تست‌شده با Docker،
`tests/postgres`، CI) ثابت می‌کنیم؟

زیرسؤال‌ها، همان نه مورد قرارداد: بازیابی تا یک لحظهٔ مشخص (PITR)، replica،
تمرین بازیابی (restore drill)، محیط staging، هدف‌های RPO و RTO، بازگشت
(rollback)، connection pooling، تفکیک نقش‌های پایگاه داده، و برآورد ظرفیت.

## 2. Why It Matters

ارزیاب دانش‌بنیان این دلیل را برای رد نوشت: «پایگاه داده و استقرار برای سطح
سازمانی بالغ نیستند» (دلیل 4)، و «شواهد کافی از پایداری عملیاتی، فرایند
استاندارد استقرار، مانیتورینگ و مستندات رسمی نیست» (دلیل 6).

تا این سؤال جواب نگیرد، هیچ PR پیاده‌سازی در این حوزه نمی‌تواند شروع شود: معلوم
نیست PITR با چه ابزاری، drill کجا و با چه نقشی، staging با چه شکلی، و rollback
با چه قاعده‌ای ساخته شود. خروجی این سند یک ADR (ADR-028) و یک فهرست PR مرتب است.

## 3. Context

### وضع موجود (بررسی‌شده در کد)

| حوزه | امروز چه هست | شاهد |
|---|---|---|
| موتور | PostgreSQL 16 از بستهٔ اوبونتو، schemaهای `app` و `observability` | نسخه: `deploy/00-bootstrap-server.sh:40`، `:44-48`؛ schemaها: `deploy/05-create-databases.sh:63-64` |
| ماشین | سرور یک ماشین مجازی روی VMware است | `deploy/README.md:194`، `:213` |
| نقش و دیتابیس | برای هر slug یک نقش `padyar_<slug>` و یک دیتابیس `padyar_<slug>`؛ نقش `NOSUPERUSER NOCREATEDB NOCREATEROLE` و صاحب هر دو schema | `deploy/05-create-databases.sh:33-34`، `:49`، `:59`، `:63-64` |
| یک cluster مشترک | هر دو نصب روی سرور، دو دیتابیس در **یک** cluster پستگرس هستند | `deploy/05-create-databases.sh:24-67` (حلقه روی چند slug، یک سرور) |
| پشتیبان منطقی | `pg_dump --format custom --compress 6 --no-owner --no-privileges`، با نقش خود برنامه، سقف زمان 300 ثانیه | `app/services/pg_backup.py:51`، `:168-173` |
| زمان‌بند | هر شب ساعت 03:00، بعد verify، بعد prune به 14 عدد | `app/services/backup.py:21-29`، `:182-212` |
| کپی بیرون از سرور | کد اختیاری است، با `OFFSITE_BACKUP_TARGET` (فقط `rsync:` یا `dir:`)، فقط بعد از verify موفق. **روی سرور تنظیم نشده است** (مالک محصول در گفتگو تأیید کرد، 2026-09-30). پس امروز هیچ نسخه‌ای از سرور بیرون نمی‌رود | `app/services/pg_backup.py:272-274`، `app/services/backup_offsite.py:38-81` |
| بازیابی | از پنل ادمین، با عبارت تأیید دقیق، پشتیبان ایمنی قبلش، `--single-transaction`، و اعتبارسنجی بعدش | `app/services/pg_backup.py:443-563`، `:568-637` |
| استقرار | شش مرحله: dump، checkout، pip، migrate، restart، health با 12 تلاش | `deploy/padyar-deploy.sh:108-203` |
| CI و استقرار | CI فقط بررسی‌ها را روی runnerهای GitHub اجرا می‌کند؛ job `deploy` حذف شد (#155). بعد از merge و سبز شدن CI، اپراتور روی سرور دستی `padyar-deploy` را اجرا می‌کند | `.github/workflows/ci.yml:13-17`، `docs/engineering/DEPLOYMENT_RUNBOOK.md:21-27`، `:53`، `deploy/README.md:52-56` |
| متریک | `/metrics` در حالت multiprocess است (ADR-022 روی main): همهٔ workerها در یک پوشهٔ مشترک می‌نویسند. سه متریک پشتیبان: `backup_outcome_total` (شمارش در `_run_backup_now`)، `backup_last_success_timestamp_seconds` (gauge با `max`، از manifestها در شروع برنامه پر می‌شود)، `backup_schedule_interval_seconds` | `app/services/metrics.py:127-130`، `:140-144`، `:155-158`؛ `docs/engineering/MONITORING.md:73-75` |
| scraper | هیچ Prometheus یا scraper در مخزن نیست | `docs/engineering/MONITORING.md:18` |
| pool | پیش‌فرض کد 2/10؛ قالب نصب 1/5 با `WEB_CONCURRENCY=3` | `app/db/pg.py:60-66`، `deploy/env/instance.env.template:26-27`، `:35` |

`grep` برای `archive_mode|pgbackrest|wal-g|standby|PITR|RPO|RTO` در کد و `deploy/`
چیز واقعی پیدا نمی‌کند. پس امروز نه آرشیو WAL هست، نه PITR، نه replica، نه
drill، نه RPO/RTO نوشته‌شده.

### پنج اصلاح قرارداد (همه در کد تأیید شد)

1. **نقش production `padyar_<slug>` است، نه `padyar_app`.** `padyar_app` نقش
   dev و CI است (`.github/workflows/ci.yml:95-98`، `app/db/pg.py:48-51`). در
   CI این نقش `POSTGRES_USER` ایمیج Docker است و در نتیجه superuser است؛ پس CI
   امروز نمی‌تواند رفتار «بدون CREATEDB» را ثابت کند، مگر اینکه تست خودش یک
   نقش محدود بسازد. `tests/postgres/conftest.py:30-32` همین محدودیت را می‌گوید.
2. **دو نصب یک cluster مشترک دارند.** آرشیو WAL و PITR در سطح cluster کار
   می‌کنند، نه در سطح یک دیتابیس. آزمایش 3 (پایین) نشان می‌دهد این یعنی چه.
3. **rollback با sha قدیمی کار نمی‌کند.** توضیح بالای اسکریپت می‌گوید «صدا زدن
   با sha قدیمی همان مسیر rollback است» (`deploy/padyar-deploy.sh:18-19`)، ولی
   اسکریپت هر sha که نوک `main` نباشد را با `SUPERSEDED` و کد موفق رها می‌کند
   (`deploy/padyar-deploy.sh:129-139`). پس مسیر rollback مستند، امروز کار نمی‌کند.
4. **migrationها فقط افزایشی نیستند.** اسکریپت دو بار می‌گوید دیتابیس
   «additive-only» است (`deploy/padyar-deploy.sh:34-36`، `:187-188`). ولی چهار
   migration داده یا ساختار حذف می‌کنند:
   - `migrations/0006_lead_status.sql:51` و `:56` (یک ستون و جدول `edit_sessions`)
   - `migrations/0013_companies.sql:116` و `:119` (ردیف‌های `dataset` و جدول `company_profiles`)
   - `migrations/0028_drop_pwa_leftovers.sql:14-15` (دو ستون)
   - `migrations/0029_drop_search_backend_setting.sql:11` (یک ردیف settings)
5. **`/metrics` scraper ندارد.** در `3a4a415` هر worker registry جدای خودش را
   داشت. روی `main` این حل شد (ADR-022 روی main، #156): حالت multiprocess
   `prometheus_client`، با پوشهٔ `PROMETHEUS_MULTIPROC_DIR` که systemd می‌سازد
   (`app/services/metrics.py:59-60`،
   `deploy/systemd/padyar-app.service.template:41-43`). سرویس هنوز با
   `--workers ${WEB_CONCURRENCY}` اجرا می‌شود (`:51-53`). ولی هنوز هیچ Prometheus،
   scraper یا قاعدهٔ هشداری در مخزن نیست (`docs/engineering/MONITORING.md:18-21`).
   پشتهٔ پایش یک spec جدا است (ADR-024 روی main). پس یک gauge به‌تنهایی هنوز
   مانیتورینگ نیست؛ نتیجهٔ drill باید روی صفحهٔ Backups هم دیده شود.

### چند یافتهٔ دیگر از خواندن کد

- بخش «بازیابی» در `docs/engineering/DEPLOYMENT_RUNBOOK.md:92` هنوز جایگزینی
  `chat_history.db` (SQLite) را توضیح می‌دهد. برای production پستگرس غلط است.
- توضیح مرحلهٔ 6 اسکریپت «`/health × 3`» می‌گوید (`deploy/padyar-deploy.sh:33`)،
  ولی کد 12 تلاش روی `/api/health` می‌کند (`:65`، `:176-184`).
- `backup_offsite.py` هیچ‌وقت کپی‌های قدیمی مقصد را پاک نمی‌کند (در فایل هیچ
  prune یا `--delete` نیست). مقصد دوم بی‌سقف رشد می‌کند.
- `pg_backup.create()` هیچ شمارشی از محتوای dump ثبت نمی‌کند
  (`app/services/pg_backup.py:183-196`). پس امروز نمی‌شود گفت «این پشتیبان
  چند ردیف داشت» و drill چیزی برای مقایسهٔ دقیق ندارد.
- 36 دستور DDL در زمان اجرا در سرویس‌ها هست (`ensure_table()` در
  `app/services/otp.py`، `leads.py`، `sms_outbox.py`، `campaigns.py`،
  `applog.py`). شمارش با
  `grep -rn "CREATE TABLE IF NOT EXISTS\|CREATE INDEX IF NOT EXISTS\|ALTER TABLE" app/services/*.py app/db/*.py | grep -v connection.py | wc -l`.
  این روی تفکیک نقش‌ها اثر مستقیم دارد (بخش 5.8).
- `deploy/05-create-databases.sh:63-66` فقط `SCHEMA public` را از `PUBLIC` پس
  می‌گیرد، نه `CONNECT ON DATABASE`. و `deploy/00-bootstrap-server.sh:81-82` برای
  `host all all 127.0.0.1/32` رمز می‌خواهد، یعنی هر نقش به هر دیتابیس می‌تواند
  وصل شود. امروز این خطرناک نیست، چون نقش یک نصب روی schema `app` نصب دیگر
  دسترسی ندارد (بازبین آزمود: `ERROR:  permission denied for schema app`). ولی هر
  نقش تازه با دسترسی گسترده این مرز را می‌شکند (بخش 5.8).

### بررسی در برابر ADRهای موجود

ADR-001 تا ADR-027 (همهٔ ADRهای روی `main`) خوانده شد. هیچ‌کدام با این پیشنهاد
تعارض تصمیمی ندارد. ADR-022 روی main (multiprocess `/metrics`) الگویی است که
gaugeهای drill دنبال می‌کنند، و ADR-024 روی main (پشتهٔ پایش) جایی است که هشدارها
می‌نشینند. یک برخورد نام‌گذاری هست: ADR-024 اسکریپت `deploy/55-monitoring.sh` را
برنامه‌ریزی کرده و PR C این سند `deploy/55-pitr.sh` را؛ PR C باید شمارهٔ دیگری
بگیرد. ADR-027 روی main (llama-server روی همان سرور P40) تصمیمی از این سند را
عوض نمی‌کند، ولی روی همان دیسک جا می‌گیرد (بخش 5.9). نزدیک‌ترین‌ها
ADR-014 و ADR-015 هستند (جدول‌های قفل و محدودکنندهٔ نرخ که هنگام خرابی باز
می‌شوند). این پیشنهاد به آن الگو دست نمی‌زند.

### تصمیم‌های مالک محصول (نوبت 2)

این چهار مورد را مالک محصول در گفتگو تأیید کرد (2026-09-30 و 2026-10-01). این
تأیید در گفتگو است، نه بازبینی این سند. ADR-028 همچنان Proposed است.

1. هدف‌های RPO و RTO بخش 6، بند 5، همان‌طور که نوشته شده‌اند پذیرفته شدند.
2. staging فقط محتوا می‌گیرد (`dataset`، `questions`، `synonyms`، `settings`). هیچ
   دادهٔ شخصی بازدیدکننده به staging نمی‌رود. یک نکته برای spec PR E: از ADR-021
   شرکت‌ها جدول خودشان را دارند (`companies`). بدون آن، staging تیرهای شرکت را تست
   نمی‌کند. ولی `companies` اطلاعات تماس هم دارد، پس اینکه «محتوا» حساب شود یا
   نه، سؤال spec PR E است، نه این ADR.
3. `OFFSITE_BACKUP_TARGET` امروز روی سرور تنظیم نیست. مقصد دلخواه برای کپی
   بیرون از سرور Google Drive بود. **در نوبت 3 عوض شد** (پایین).
4. pgBackRest از apt خود اوبونتو نصب می‌شود (نسخهٔ 2.50). آزمایش 4 همین نسخه را اجرا کرد.

### تصمیم‌های مالک محصول (نوبت 3)

مالک محصول این‌ها را در گفتگو تأیید کرد (2026-10-01). ADR-028 همچنان Proposed است.

1. **مقصد بیرون از سرور SFTP است، نه Google Drive.** دلیل: خود Google می‌نویسد
   «Google restricts access to some of its business services in certain countries
   or regions, such as Crimea, Cuba, ... Iran, North Korea, and Syria»
   (https://knowledge.workspace.google.com/admin/support/troubleshooting/countries-or-regions-where-google-workspace-is-available،
   «last updated 2026-09-30 UTC»؛ همان صفحه‌ای که support.google.com/a/answer/2891389
   به آن می‌رود). پس حساب Google یا client_id که rclone حالا لازم دارد ممکن است
   با سیاست Google رد یا بعداً معلق شود، نه فقط با بستن شبکه، و آن هم بعد از
   اینکه دادهٔ شخصی (رمزشده) آنجا گذاشته شده است. تست شبکه از سرور این را رد
   نمی‌کند. این منبع فقط به‌عنوان تاریخچهٔ تصمیم در سند می‌ماند.
2. **هنوز هیچ سرور SFTP وجود ندارد.** PR H کد را می‌سازد و آن را در برابر یک
   کانتینر SFTP موقت تست می‌کند. روی سرور واقعی `OFFSITE_BACKUP_TARGET` و repo2
   خالی می‌مانند تا مقصد ساخته شود. تا آن روز، از دست رفتن سرور یعنی از دست
   رفتن همه‌چیز.
3. **WAL هم در خود PR H بیرون می‌رود، نه بعداً.** pgBackRest repo2 از نوع
   `sftp` با رمزنگاری خودش: WAL و پشتیبان‌های پایه. dumpها از یک نوع هدف تازهٔ
   `sftp:` در `backup_offsite.py`.
4. **رمزنگاری قبل از آپلود، روی خود سرور.** کلید روی کاغذ در گاوصندوق نگه داشته
   می‌شود، نه داخل هیچ پشتیبانی و نه در مقصد. (بخش 6، بند 10، توضیح می‌دهد
   «نه روی سرور» در عمل چه معنی دارد.)
5. **VMware از این سرور snapshot می‌گیرد.** کجا و برای چند وقت، نامعلوم است
   (سؤال باز). در هیچ عدد RPO یا RTO از snapshot استفاده نمی‌شود.
6. **rclone و Google Drive از برنامه حذف شدند.** یافتهٔ «مقصد بیرونی prune
   نمی‌شود» داخل PR H می‌ماند.

### تصمیم‌های مالک محصول (نوبت 4)

مالک محصول این‌ها را در گفتگو تأیید کرد (2026-10-02). ADR-028 همچنان Proposed است.

1. **pgBackRest همان 2.50 اوبونتو می‌ماند**، با بازیابی دو قدمی از SFTP (اول دانلود
   repo با `sftp`، بعد restore از repo محلی). علت شکست restore مستقیم با 2.50
   «نامعلوم» می‌ماند. این شکست به یک فایل خاص گره نخورده است: در اجراهای من روی
   `2602_vm.zst` ایستاد، و در بازتولید بازبین روی فایل دیگری
   (`pg_data/base/1/13450.gz`).
2. **dumpها با کلید عمومی gpg رمز می‌شوند.** کلید خصوصی فقط روی کاغذ در گاوصندوق
   است، پس سرور نمی‌تواند dumpهای خودش را باز کند. رمز repo2 در pgBackRest متقارن
   است: روی سرور در یک فایل `0600` و یک نسخهٔ کاغذی. سؤال باز دربارهٔ «کلید نه
   روی سرور» با همین بسته شد.

یک عدد را مالک محصول **نگفته** است: هدف 5 دقیقه برای RPO از دست رفتن سرور.
این عدد از تصمیم «WAL هم در PR H بیرون برود» نتیجه گرفته شده (آرشیو هر حداکثر 60
ثانیه)، ولی مالک محصول آن را تأیید نکرده است (بخش 6، بند 5، و سؤال باز 2 در بخش 9).

## 4. Investigation

### Evidence

منابع بیرونی، همه در 2026-09-30 خوانده شدند:

| ادعا | منبع | تاریخ صفحه |
|---|---|---|
| pgBackRest آخرین نسخه 2.59.2، مجوز MIT | https://github.com/pgbackrest/pgbackrest (README و releases) | release در 2026-09-27 |
| pgBackRest در 2026-04-27 اعلام کرد «دیگر نگهداری نمی‌شود»، در 2026-05-18 با گروهی از اسپانسرها اعلام کرد «ادامه می‌دهد» | https://pgbackrest.org/news.html | «Updated September 27, 2026» |
| اوبونتو 24.04: بستهٔ `pgbackrest` نسخهٔ 2.50-1build2 در universe | https://packages.ubuntu.com/noble/pgbackrest | بدون تاریخ |
| مخزن PGDG برای noble نسخهٔ 2.59.1 دارد | https://apt.postgresql.org/pub/repos/apt/dists/noble-pgdg/main/binary-amd64/Packages.gz | Release file 2026-09-28 |
| انواع repo: posix (پیش‌فرض)، sftp، s3 و دیگران؛ `repo-retention-full`؛ `repo-retention-archive` | https://pgbackrest.org/configuration.html | «Updated July 20, 2026» |
| `restore --type=time`، `--delta`، دستور `check` | https://pgbackrest.org/command.html | «Updated July 20, 2026» |
| wal-g آخرین نسخه 3.0.9، مجوز Apache 2.0، فقط باینری در GitHub releases؛ بستهٔ اوبونتو و PGDG ندارد | https://github.com/wal-g/wal-g (releases، `docs/README.md`)، جستجوی packages.ubuntu.com | release در 2026-08-20 |
| مستند wal-g: نگه‌داشتن پشتیبان روی همان دیسک «برنامهٔ بازیابی از فاجعه نیست» | https://github.com/wal-g/wal-g/blob/master/docs/STORAGES.md | commit 2026-08-20 |
| Barman مجوز GPL 3، مستندش می‌گوید روی سرور جدا نصب شود | https://github.com/EnterpriseDB/barman، https://docs.pgbarman.org/release/3.20.1/user_guide/architectures.html | release در 2026-09-29 |
| `pg_dump` نمی‌تواند بخشی از آرشیو پیوسته باشد (پس PITR نمی‌دهد) | https://www.postgresql.org/docs/16/continuous-archiving.html | بدون تاریخ |
| `archive_command`، `restore_command`، `recovery.signal`، `recovery_target_time`، `archive_timeout` | همان صفحه و https://www.postgresql.org/docs/16/runtime-config-wal.html | بدون تاریخ |
| backup افزایشی (`pg_combinebackup`) از نسخهٔ 17 است، نه 16 | https://www.postgresql.org/docs/16/app-pgcombinebackup.html (404) و صفحهٔ نسخهٔ 17 | بدون تاریخ |
| standby با `standby.signal` و `primary_conninfo`؛ promote با `pg_promote()` | https://www.postgresql.org/docs/16/warm-standby.html | بدون تاریخ |
| `pg_restore --jobs` با فرمت custom کار می‌کند و با `--single-transaction` جمع نمی‌شود | https://www.postgresql.org/docs/16/app-pgrestore.html | بدون تاریخ |
| `pg_dump --snapshot` (dump از یک snapshot صادرشده) | خروجی `pg_dump --help` در ایمیج `postgres:16` (اجراشده، پایین) | 2026-09-30 |
| PgBouncer 1.26.0 (سه CVE)؛ حالت transaction برخی ویژگی‌های session را می‌شکند | https://www.pgbouncer.org/، https://www.pgbouncer.org/features.html | 2026-09-23 |
| psycopg 3 بعد از 5 اجرا خودش statement را prepare می‌کند (`prepare_threshold=5`)؛ با PgBouncer به libpq 17 به بالا نیاز دارد | https://www.psycopg.org/psycopg3/docs/advanced/prepare.html | مستند 3.3 |
| `pg_basebackup` به نقش با `REPLICATION` یا superuser نیاز دارد | https://www.postgresql.org/docs/16/app-pgbasebackup.html | بدون تاریخ |
| `pg_read_all_data` خواندن همهٔ جدول‌ها را می‌دهد، «as if having SELECT rights on those objects» | https://www.postgresql.org/docs/16/predefined-roles.html | بدون تاریخ |
| `archive_mode` فقط در شروع سرور تنظیم می‌شود (یعنی restart لازم است) | https://www.postgresql.org/docs/16/runtime-config-wal.html | بدون تاریخ |
| پشتیبانی چند repo همزمان از pgBackRest v2.33: «Multi-Repository and GCS Support - Released April 5, 2021». repo نوع SFTP از v2.46 (2023-05-22) | https://pgbackrest.org/release.html (خوانده‌شده 2026-10-01) | بدون تاریخ کلی |
| `archive-push-queue-max` در 2.50 وجود دارد: بعد از سقف، WAL را «successfully archived» اعلام و «DROP IT» می‌کند تا دیسک پر نشود | خروجی `pgbackrest help archive-push archive-push-queue-max` در آزمایش 4 | 2026-10-01 |
| pgBackRest repo از نوع `sftp` (`repo-sftp-host`، `repo-sftp-host-user`، `repo-sftp-private-key-file`، بررسی کلید میزبان پیش‌فرض `strict`) | https://pgbackrest.org/configuration.html | «Updated July 20, 2026» |
| رمزنگاری repo: `repo-cipher-type` با `none` یا `aes-256-cbc`، و `repo-cipher-pass`؛ «encryption is always performed client-side even if the repository type (e.g. S3) supports encryption» | همان صفحه | «Updated July 20, 2026» |
| Google دسترسی به برخی سرویس‌های کسب‌وکارش را در ایران محدود می‌کند: «Google restricts access to some of its business services in certain countries or regions, such as ... Iran» (دلیل کنار گذاشتن Drive؛ فقط تاریخچه) | https://knowledge.workspace.google.com/admin/support/troubleshooting/countries-or-regions-where-google-workspace-is-available | «last updated 2026-09-30 UTC» |
| `gnupg` همین امروز در نصب پایهٔ سرور نصب می‌شود | `deploy/00-bootstrap-server.sh:37` | کد در `3a4a415` |

### Experiments / Prototypes

همهٔ عددهای این بخش **اندازه‌گیری‌شده روی Mac توسعه (Apple M3، 16GB) در Docker
هستند، نه روی سرور**. Docker روی مک I/O کندتری از یک سرور لینوکس دارد، پس این
عددها را فقط برای مقایسهٔ ترتیب بزرگی به کار ببرید. همهٔ کانتینرها با نام
`dbmat-t1-*` ساخته و بعد پاک شدند. هیچ کد آزمایشی وارد مخزن نشده است.
دستورهای اصلی و خطوط خروجیِ پشت هر عدد در **پیوست الف** همین سند آمده است.

**آزمایش 1: اندازهٔ داده و زمان `pg_dump`.**
کانتینر `postgres:16` (16.15). نقش `padyar_demo` با **همان دسترسی‌های**
`deploy/05-create-databases.sh` ساخته شد (`NOSUPERUSER NOCREATEDB NOCREATEROLE`)،
**بدون** دو timeout آن اسکریپت (`:52-53`). آزمایش 4 و آزمایش بازبین (پایین) این
کمبود را پوشش می‌دهند. هر 29 migration روی آن اجرا شد. دادهٔ مصنوعی برای «یک رویداد بزرگ»، با متن
فارسی به‌اضافهٔ md5 تا فشرده‌سازی بیش از حد خوش‌بینانه نشود:

| جدول | ردیف | اندازهٔ کل (با index) | بایت برای هر ردیف |
|---|---|---|---|
| `app.messages` | 2,000,000 | 870 MB | 456 |
| `app.chat_logs` | 1,000,000 | 687 MB | 721 |
| `observability.app_logs` | 1,000,000 | 291 MB | 305 |
| `app.conversations` | 200,000 | 38 MB | 201 |
| `app.visitors` | 100,000 | 29 MB | 301 |

اندازهٔ دیتابیس: 1929 MB. `pg_dump` با **همان argv** در
`app/services/pg_backup.py:168-173` و با نقش محدود: **1 دقیقه و 49.5 ثانیه**،
فایل 131,113,223 بایت (حدود 15 برابر کوچک‌تر؛ متن تکراری مصنوعی این نسبت را
احتمالاً خوش‌بینانه می‌کند)، 232 مدخل در فهرست آرشیو.

**آزمایش 2: drill با نقش بدون CREATEDB.**
- نقش برنامه نمی‌تواند دیتابیس بسازد:
  `ERROR:  permission denied to create database` (تأیید شد).
- یک بار superuser دیتابیس `padyar_demo_drill` را با مالکیت `padyar_demo` ساخت
  (کاری که در زمان نصب انجام می‌شود). بعد **همهٔ drill با نقش برنامه** اجرا شد.
- `pg_restore` با همان flagهای `pg_backup.restore()`
  (`--clean --if-exists --no-owner --no-privileges --single-transaction`):
  **4 دقیقه و 22.7 ثانیه**. شمارش ردیف‌ها با مبدأ برابر بود
  (2000000 / 1000000 / 1000000 / 3000).
- اجرای دوباره روی همان دیتابیس با `--jobs 4` (بدون single-transaction):
  **3 دقیقه و 2.9 ثانیه**.
- پاک‌سازی با `DROP SCHEMA app CASCADE; DROP SCHEMA observability CASCADE;` با نقش
  برنامه کار کرد و دیتابیس drill به 8.6 MB برگشت. پس نیازی به `DROP DATABASE` نیست.
- آزمایش جدا: 29 migration با خود `scripts/apply_migrations.py` اجرا شد، dump و
  restore به دیتابیس drill انجام شد. جدول `app.schema_migrations` در هر دو 29
  ردیف و md5 یکسان (`626f76f120530b742904a127ddc0d14c`) داشت. پس مقایسهٔ
  checksum migrationها یک بررسی قابل اعتماد برای drill است.

**آزمایش 3: PITR با pgBackRest روی یک cluster با دو نصب (نسخهٔ 2.59.1، PGDG، Debian).**
- `apt-get install pgbackrest` داخل ایمیج Debian‌پایهٔ `postgres:16` نسخهٔ
  **2.59.1 را از PGDG** نصب کرد. این نسخه و این منبع، همان چیزی نیست که برای
  سرور پیشنهاد می‌شود. آزمایش 4 نسخهٔ پیشنهادی را اجرا می‌کند. تنظیم: repo از نوع posix روی دیسک محلی، `repo1-retention-full=2`،
  `compress-type=zst`، `archive_command=pgbackrest --stanza=main archive-push %p`،
  `archive_timeout=60`.
- دو دیتابیس `padyar_a` و `padyar_b`، هر کدام 400,000 ردیف. اندازهٔ cluster: 500 MB.
- پشتیبان کامل: **33.4 ثانیه**، اندازه در repo **31.2 MB**.
- بعد از پشتیبان: 1000 ردیف تازه در A و 500 ردیف در B. زمان T ثبت شد. بعد از T:
  در A جدول حذف شد (حادثه)، در B یک ردیف درست و قانونی نوشته شد.
- restore به زمان T در **یک پوشهٔ جدا** (`--pg1-path=/tmp/restore`)، بدون دست
  زدن به cluster زنده: **38.0 ثانیه**. بالا آمدن آن نمونه روی پورت 5433 تا
  آمادهٔ اتصال: **12.4 ثانیه**. در لاگ: `recovery stopping before commit of transaction 750`.
- نتیجه در نمونهٔ بازیابی‌شده: جدول A با **401,000** ردیف برگشت (شامل 1000 ردیف
  بعد از پشتیبان). B فقط ردیف‌های قبل از T را داشت: **ردیف قانونی B که بعد از T
  نوشته شده بود، در نمونهٔ بازیابی‌شده نیست.**

این آخرین نتیجه مهم‌ترین یافتهٔ آزمایش است: بازگرداندن کل cluster به زمان T،
نصب دوم را هم به عقب می‌برد و نوشته‌های درستش را از دست می‌دهد.

**آزمایش 4 (نوبت 2): همان مسیر با نسخهٔ پیشنهادی، و زنجیرهٔ کامل بازیابی یک نصب.**
- کانتینر `ubuntu:24.04`. از apt خود اوبونتو: `pgbackrest 2.50-1build2` و
  `postgresql-16 16.15-0ubuntu0.24.04.1`. یعنی همان منبع سرور.
- آرشیو با `ALTER SYSTEM` روشن شد، همان‌طور که PR C خواهد کرد، و بعد cluster
  ری‌استارت شد (2.6 ثانیه در این کانتینر خالی).
- دو نصب `padyar_a` و `padyar_b`، این بار **دقیقاً** مثل `05`، با
  `statement_timeout = '30s'` و `idle_in_transaction_session_timeout = '60s'`.
  هر کدام 400,000 ردیف. اندازهٔ cluster: 500 MB. پشتیبان کامل: 26.4 ثانیه، 31.1 MB.
- نقش برنامه `pg_stat_archiver` را می‌خواند (`archived_count>0`، `failed_count=0`).
  پس صفحهٔ Backups می‌تواند وضعیت آرشیو را بدون دسترسی تازه نشان دهد.
- بعد از T: در A جدول حذف شد، در B یک ردیف درست نوشته شد. **زنجیرهٔ کامل برای
  نصب A**، مرحله به مرحله:

  | مرحله | زمان |
  |---|---|
  | 1. restore به T در پوشهٔ جدا (`--pg1-path=/var/lib/postgresql/side`) | 24.9 ثانیه |
  | 2. بالا آوردن نمونهٔ جدا روی پورت 5433 تا پایان replay و promote | 9.3 ثانیه |
  | 3. `pg_dump` فقط دیتابیس A از نمونهٔ جدا، با نقش خود A | 3.2 ثانیه |
  | 4. dump ایمنی از A زنده (همان کاری که پنل قبل از restore می‌کند) | 0.9 ثانیه |
  | 5. `pg_restore --clean --if-exists --single-transaction` در A زنده، با نقش A | 9.6 ثانیه |
  | 6. خاموش کردن و پاک کردن نمونهٔ جدا | 1.9 ثانیه |
  | **جمع، بدون ری‌استارت برنامه** | **49.9 ثانیه** |

- نتیجه: A زنده 401,000 ردیف دارد (شامل 1000 ردیف بعد از پشتیبان). **B ردیف
  درستِ بعد از T را نگه داشت.** آرشیو زنده بعد از کار: `failed=0`.
- یک خطر عملی پیدا شد: `pgbackrest restore` تنظیم‌های `ALTER SYSTEM` را هم در
  `postgresql.auto.conf` نمونهٔ جدا کپی می‌کند، از جمله `archive_mode = 'on'` و
  همان `archive_command`. اگر نمونهٔ جدا بدون `-c archive_mode=off` بالا بیاید،
  یک timeline تازه را در repo همان production می‌فرستد. پس دستور شروع نمونهٔ جدا
  باید این را صریح داشته باشد. در اوبونتو پیکربندی در `/etc/postgresql/16/main`
  است، نه در پوشهٔ داده، پس دستور شروع باید `config_file` و `data_directory` و
  `port` را هم بدهد (پیوست الف).
- `pgbackrest help archive-push archive-push-queue-max` در 2.50 این گزینه را نشان
  داد (متن در Evidence).

**آزمایش 5 (نوبت 3): کپی بیرون از سرور با SFTP، و از دست رفتن کامل سرور.**
- سه کانتینر روی یک شبکهٔ Docker: `dbmat-t1-db` (اوبونتو 24.04 با
  `pgbackrest 2.50-1build2`، `postgresql-16 16.15`، `gnupg 2.4.4`، `rsync 3.2.7`، همه
  از apt اوبونتو)، `dbmat-t1-sftp` (اوبونتو با `openssh-server`، یک حساب **فقط SFTP**:
  `ChrootDirectory` و `ForceCommand internal-sftp`، ورود فقط با کلید)، و برای
  یک آزمون تشخیصی `dbmat-t1-r259` (pgBackRest از PGDG).
- repo1 محلی (posix)، repo2 از نوع `sftp` با `repo2-cipher-type=aes-256-cbc`.
  `stanza-create` و `check` موفق. پشتیبان کامل به repo2: 5.8 ثانیه، 7.9 MB.
  `pgbackrest info`: `repo1: none`، `repo2: aes-256-cbc`.
- **آنچه مقصد نگه می‌دارد خوانا نیست.** فایل WAL در مقصد با سرآیند `Salted__`
  شروع می‌شود و جستجوی شماره‌تلفن‌ها در همهٔ فایل‌های مقصد صفر نتیجه داد.
- **قطع شدن مقصد، آرشیو محلی را هم متوقف کرد.** sshd مقصد خاموش شد و سه فایل WAL
  ساخته شد. بعد از 20 ثانیه: سه فایل `.ready` در انتظار، `failed_count=0`
  (فرمان هنوز منتظر اتصال بود)، و repo1 محلی هم **هیچ** WAL تازه‌ای نگرفته بود
  (`...09`). بعد از برگشتن مقصد، همه‌چیز رسید (`...0D`، `failed=0`). پس با دو repo،
  قطع مقصد بیرونی کل آرشیو را نگه می‌دارد و WAL در `pg_wal` جمع می‌شود (همان
  خطر بخش 4، که `archive-push-queue-max` مرزش را می‌گذارد)، و `failed_count` به‌تنهایی
  علامت کافی نیست؛ سن `last_archived_time` علامت درست است.
- **از دست رفتن کامل سرور.** پوشهٔ داده، repo1، `pgbackrest.conf` و کلید SSH پاک
  شدند. بازیابی فقط با: نشانی و کاربر SFTP (از runbook)، یک کلید SSH **تازه** که
  اپراتور روی مقصد مجاز می‌کند، و کلید رمز که از روی کاغذ تایپ می‌شود.
  - کلید رمز غلط: `ERROR: [075]: no backup set found to restore`. بدون کلید درست،
    حتی فهرست پشتیبان‌ها خوانده نمی‌شود.
  - **restore مستقیم از SFTP با 2.50 شکست خورد.** در دو اجرای من روی یک فایل
    ایستاد؛ بازبین آن را روی فایل دیگری (`pg_data/base/1/13450.gz`) بازتولید کرد،
    پس به یک فایل خاص گره نخورده است:
    `unable to open file '.../2602_vm.zst' for read: libssh2 error [-31]: sftp error [4]`
    (سه تلاش، 40 ثانیه). همان فایل با `sftp` معمولی دانلود شد و
    `pgbackrest verify` روی همان repo بدون خطا تمام شد. علت را پیدا نکردم؛ در
    release notes بعد از 2.50 موردی که دقیقاً همین را بگوید نیافتم.
  - **همان repo، همان کلید، با pgBackRest 2.59.2 از PGDG:** restore در 3.1 ثانیه
    موفق، نصب A 500,000 و نصب B 200,001 ردیف (برابر مبدأ)، شامل آخرین نوشتهٔ
    قبل از حادثه.
  - **راه دور زدن با 2.50:** کل repo رمزشده با `sftp` معمولی دانلود شد (1.3 ثانیه،
    42 MB)، و 2.50 از یک repo محلی posix با همان کلید restore کرد (3.0 ثانیه)،
    بالا آمدن 3.4 ثانیه، **جمع 7.7 ثانیه**. همهٔ ردیف‌ها، شامل آخرین نوشته، برگشت.
- **مسیر dump:** `rsync` (همان کاری که هدف `rsync:` امروز می‌کند) به حساب فقط-SFTP
  شکست خورد: `rsync error: protocol incompatibility (code 2)`. `gpg --symmetric
  --cipher-algo AES256` با یک فایل کلید، بعد `sftp put` و `get` و رمزگشایی: sha256
  برابر. کلید غلط: `gpg: decryption failed: Bad session key`. در فایل رمزشده هیچ
  شماره‌تلفنی پیدا نشد. یک آزمون رمزنگاری با کلید عمومی gpg هم اجرا کردم، ولی
  ساختن کلید بی‌صدا شکست خورد (کلید `cv25519` نمی‌تواند کلید اصلی باشد). آزمایش 6
  همین را درست تکرار کرد.

**آزمایش 6 (نوبت 4): رمزنگاری dump با کلید عمومی gpg.** کانتینر `dbmat-t1-gpg`
(اوبونتو 24.04، `gnupg 2.4.4` از apt)، بعد پاک شد. روی یک «گاوصندوق» جدا: کلید
اصلی `ed25519` (فقط تأیید) و زیرکلید `cv25519` (فقط رمزنگاری). روی «سرور» فقط
کلید عمومی وارد شد (هیچ کلید خصوصی در `--list-secret-keys` نبود). سرور dump را با
`gpg -e -r <fingerprint>` رمز کرد؛ در فایل رمزشده هیچ شماره‌تلفنی نبود. سرور
نتوانست آن را باز کند: `gpg: public key decryption failed: No secret key`. کلید
گاوصندوق آن را باز کرد و sha256 برابر مبدأ بود (`69c2cf8142634f85`). خروجی متنی
کلید خصوصی 719 بایت بود؛ یعنی چاپ آن روی کاغذ ممکن است، ولی تایپ دوباره‌اش
حدود ده خط base64 است و خطاپذیر (روش چاپ و آزمودن آن کار runbook PR H است).

**آزمایش‌های بازبین (dbmat-t1-crit)، نه این عامل.** بازبین این دو را در Docker
روی Mac توسعه اجرا کرد و گزارش داد. اینجا با انتساب درست آمده‌اند:
- نقشی با `statement_timeout = '1s'` و `idle_in_transaction_session_timeout = '2s'`:
  `pg_dump` با argv `app/services/pg_backup.py:168-173` روی 3,000,000 ردیف موفق شد (10.3 ثانیه)،
  و `pg_restore --clean --if-exists --no-owner --no-privileges --single-transaction`
  هم موفق شد (28.1 ثانیه). خود آرشیو `SET statement_timeout = 0;` و
  `SET idle_in_transaction_session_timeout = 0;` صادر می‌کند. پس timeout نقش، drill
  را نمی‌شکند.
- نقش `padyar_a_ro` با `pg_read_all_data` توانست در دیتابیس **نصب دیگر**
  `SELECT phone FROM app.visitors` اجرا کند و `09120000000` برگرداند. نقش معمولی
  `padyar_a` روی همان دیتابیس `permission denied for schema app` گرفت (بخش 5.8).

### Constraints Discovered

- **CREATEDB نداریم.** drill نمی‌تواند دیتابیس موقت بسازد. راه حل اندازه‌گیری‌شده:
  یک دیتابیس `padyar_<slug>_drill` که در زمان نصب ساخته می‌شود و مالکش نقش برنامه است.
- **PITR در سطح cluster است.** با دو نصب روی یک cluster، restore درجا برای خطای
  یک نصب، داده‌های نصب دیگر را هم عقب می‌برد (آزمایش 3).
- **سقف 300 ثانیه، و restore سقف تنگ‌تر است، نه dump.** `_TIMEOUT = 300`
  (`app/services/pg_backup.py:51`) را همان `_run()` (`:107-110`) هم برای
  `pg_dump` و هم برای `pg_restore` پنل (`:512-517`) به کار می‌برد. در آزمایش 2،
  dump یک دیتابیس 1.9 GB 110 ثانیه طول کشید ولی restore آن **262.7 ثانیه**، یعنی
  88 درصد سقف. **تخمین** با رشد خطی: restore حدود **2.2 GB** به سقف می‌رسد (dump
  حدود 5 GB). restore پنل قبلش یک dump ایمنی هم می‌گیرد (`:499`) و هر دو فراخوان
  سقف جدای خود را دارند. پیشنهاد ما همین مسیر را برای drill شبانه به کار می‌برد.
  پس وقتی دیتابیس از حدود 2.2 GB رد شود، drill هر شب قرمز می‌شود و **restore
  پنل** وسط حادثه شکست می‌خورد. آخرین قدم بازیابی یک نصب با PITR یک
  `pg_restore` دستی بیرون از پنل است (بخش 6، بند 1)، از `_run()` نمی‌گذرد و این
  سقف را ندارد.
  dump قبل از deploy هم بعداً (حدود 5 GB) deploy را متوقف می‌کند
  (`deploy/padyar-deploy.sh:112-115`). سرور واقعی احتمالاً سریع‌تر است؛
  اندازه‌گیری نشده است. restore آزمایش 4 (یک جدول، 250 MB) در 9.6 ثانیه تمام شد؛
  آزمایش 2 چندین جدول و index دارد، پس این دو عدد مستقیم قابل مقایسه نیستند.
- **شکست آرشیو می‌تواند دیسک را پر کند.** وقتی `archive-push` شکست بخورد (repo
  پر، مقصد در دسترس نیست)، PostgreSQL فایل‌های WAL را در `pg_wal` نگه می‌دارد تا
  آرشیو موفق شود (مستند continuous archiving). روی یک cluster مشترک، پر شدن دیسک
  داده **هر دو نصب** را متوقف می‌کند. این بدتر از نداشتن PITR است. مرز:
  `archive-push-queue-max` در pgBackRest 2.50. بعد از سقف، WAL دور ریخته می‌شود،
  PITR از آن نقطه تا پشتیبان کامل بعدی ممکن نیست، ولی پستگرس بالا می‌ماند. علامت:
  `pg_stat_archiver.failed_count` و `last_failed_time` روی صفحهٔ Backups.
- **روشن کردن `archive_mode` یک restart کل cluster لازم دارد** (مستند
  runtime-config-wal). یعنی هر دو نصب چند ثانیه قطع می‌شوند. این کار در ساعت
  آرام انجام می‌شود، با همان قاعدهٔ «در زمان رویداد مستقر نکنید»
  (`deploy/padyar-deploy.sh:40-42`).
- **نمونهٔ جدای PITR باید آرشیو خاموش داشته باشد** (آزمایش 4). وگرنه در repo
  production یک timeline تازه می‌نویسد.
- **با repo بیرونی، قطع مقصد کل آرشیو را نگه می‌دارد** (آزمایش 5). repo1 محلی
  هم WAL تازه نمی‌گیرد. پس هر قطعی طولانی مقصد SFTP، WAL را در `pg_wal` جمع
  می‌کند تا `archive-push-queue-max` آن را دور بریزد. علامت روی صفحهٔ Backups باید
  سن `last_archived_time` باشد، نه فقط `failed_count`، چون در قطعی کوتاه
  `failed_count` صفر ماند. اینکه حالت async pgBackRest (`archive-async`) اجازه
  می‌دهد repo1 جلو برود، آزموده نشده است.
- **pgBackRest 2.50 نمی‌تواند مستقیم از SFTP بازیابی کند** (در آزمایش 5، هر بار
  شکست خورد؛ 2.59.2 موفق شد). نوشتن به SFTP با 2.50 کار می‌کند. پس بازیابی بعد از
  از دست رفتن سرور با 2.50 دو قدم دارد: اول کل repo رمزشده با `sftp` معمولی
  دانلود می‌شود، بعد از repo محلی restore می‌شود (اندازه‌گیری‌شده).
- **دانلود از GitHub در ایران ممکن است بسته یا کند باشد** (فرض قرارداد). wal-g
  فقط از GitHub نصب می‌شود. pgBackRest از مخزن اوبونتو نصب می‌شود.
- **امنیت.** pgBackRest به‌عنوان کاربر سیستمی `postgres` اجرا می‌شود و به کل
  cluster دسترسی کامل دارد. این یک مرز اعتماد تازه است. قاعدهٔ پیشنهادی: برنامه
  هرگز آن را صدا نمی‌زند؛ فقط اسکریپت‌های root در `deploy/` و timerهای systemd
  آن را اجرا می‌کنند، و برنامه فقط یک فایل وضعیت یا `pg_stat_archiver` را می‌خواند.
  برای drill مرز تازه‌ای لازم نیست: همان نقش برنامه، روی دیتابیسی که مالکش است.
  یک خطر مشخص drill: اگر به اشتباه به دیتابیس زنده اشاره کند، `--clean` آن را
  پاک می‌کند. پس نام دیتابیس drill باید از نام زنده مشتق شود و کد در صورت
  برابری رد کند، و یک تست منفی همین را قفل کند.
- **امنیت، جدایی دو نصب.** دو مشتری یا دو رویداد روی یک cluster هستند. هر نقشی
  که دسترسی سطح cluster بگیرد (مثل `pg_read_all_data`، یا کاربر `postgres` که
  pgBackRest با آن اجرا می‌شود) دادهٔ نصب دیگر، از جمله نام و شمارهٔ تلفن
  بازدیدکننده، را می‌بیند. بازبین همین را با `pg_read_all_data` بازتولید کرد
  (آزمایش‌های بازبین، بالا). پس هر نقش تازه باید فقط به دیتابیس خود نصب محدود
  باشد. pgBackRest یک استثنای آگاهانه است: فقط root و timerهای systemd آن را
  اجرا می‌کنند.
- **کپی بیرون از سرور یعنی دادهٔ شخصی بیرون از سرور.** dumpها نام و شمارهٔ تلفن
  بازدیدکننده دارند، و WAL و پشتیبان‌های پایهٔ pgBackRest همهٔ داده‌های هر دو نصب
  را دارند. مقصد SFTP فقط نسخه‌ای را می‌گیرد که روی خود سرور رمز شده است، و کلید
  هرگز در مقصد نیست.
- **کلید رمز و بازیابی.** رمزنگاری بدون حضور انسان (WAL هر دقیقه، dump ساعت 3
  صبح) یعنی سرور باید بتواند رمز کند. دو کلید با دو قاعده (تصمیم مالک محصول،
  نوبت 4): رمز متقارن pgBackRest روی خود سرور هست (یک فایل `0600`) و یک نسخهٔ
  کاغذی دارد؛ برای dumpها فقط کلید **عمومی** gpg روی سرور است و کلید خصوصی فقط
  روی کاغذ، پس سرور dumpهای خودش را باز نمی‌کند (آزمایش 6). اگر سرور از دست
  برود، کسی باید هر دو را از روی کاغذ تایپ کند، وگرنه نسخهٔ بیرونی بی‌فایده است.
- **داده‌های شخصی در staging.** دادهٔ production نام و شمارهٔ تلفن بازدیدکننده
  دارد (`app.visitors`). کپی آن به staging یعنی یک جای دیگر برای داده‌های شخصی.
  تصمیم مالک محصول: staging فقط محتوا می‌گیرد (بخش 3).
- **محصول.** هیچ‌کدام از این کارها صفحهٔ بازدیدکننده را تغییر نمی‌دهد. تنها
  سطح کاربری، صفحهٔ Backups پنل ادمین است و باید برای کارمند غیر فنی قابل فهم
  بماند: «آخرین تمرین بازیابی: موفق، دیروز 03:12، 4 دقیقه».

## 5. Findings

### 5.1 PITR

- `pg_dump` شبانه در بدترین حالت حدود 24 ساعت داده از دست می‌دهد (پشتیبان
  ساعت 03:00، `app/services/backup.py:21-24`). PITR با `pg_dump` ممکن نیست
  (مستند PostgreSQL 16).
- **pgBackRest** در دو نسخه اجرا شد: 2.59.1 از PGDG در آزمایش 3، و 2.50 از apt
  خود اوبونتو (نسخهٔ پیشنهادی و تصمیم مالک محصول) در آزمایش 4. هر دو PITR را
  درست انجام دادند. repo محلی و sftp دارد، چند repo همزمان از v2.33 دارد،
  retention و `check` و restore به زمان را خودش دارد. ریسک: پروژه در آوریل 2026 تقریباً رها شد و هنوز عمدتاً
  به یک نگه‌دارنده وابسته است (استنتاج از news.html).
- **wal-g** از نظر فنی خوب است ولی فقط از GitHub نصب می‌شود. در ایران این یک
  ریسک واقعی برای نصب و به‌روزرسانی است (استنتاج).
- **`archive_command` ساده + `pg_basebackup`** بدون وابستگی تازه است، ولی
  retention، پاک کردن WAL قدیمی، بررسی سلامت آرشیو و restore به زمان را باید
  خودمان در bash بنویسیم و نگه داریم. این همان چیزی است که pgBackRest آماده دارد.
- **Barman** برای یک سرور جدا طراحی شده (مستند خودش). ما سرور دوم نداریم.
- `pg_dump` جایگزین نمی‌شود، **مکمل** می‌ماند: فقط `pg_dump` یک نصب را جدا از
  نصب دیگر برمی‌گرداند، و فقط `pg_dump` از پنل ادمین قابل بازیابی است.
- **کپی بیرون از سرور با SFTP.** امروز هیچ کپی از سرور بیرون نمی‌رود (بخش 3).
  Google Drive کنار گذاشته شد (تصمیم مالک محصول در نوبت 3، دلیل در بخش 3). مقصد
  تازه یک حساب SFTP است، که هنوز وجود ندارد. یک حساب SFTP هر دو را می‌پذیرد:
  - **WAL و پشتیبان‌های پایه:** pgBackRest repo2 از نوع `sftp` (از v2.46) با
    `repo2-cipher-type=aes-256-cbc`. رمزنگاری همیشه در سمت سرور است (مستند
    pgBackRest). در آزمایش 5 با 2.50 کار کرد. با این، RPO از دست رفتن سرور به
    همان فاصلهٔ آرشیو WAL می‌رسد (هدف، نه اندازه‌گیری؛ بخش 6، بند 5).
  - **dumpها:** نوع هدف تازهٔ `sftp:` در `backup_offsite.py`. چرا `rsync:` موجود
    کافی نیست: `rsync` روی سرور مقصد به یک shell و خود برنامهٔ `rsync` نیاز دارد، و
    یک حساب فقط-SFTP (رایج‌ترین شکل فضای پشتیبان اجاره‌ای، و امن‌تر چون shell
    نمی‌دهد) آن را رد می‌کند. در آزمایش 5 دقیقاً همین شد. `sftp` همراه
    `openssh-client` نصب است. رمزنگاری dump با **کلید عمومی** gpg
    (`gpg -e -r <fingerprint>`) قبل از آپلود؛ سرور فقط کلید عمومی را دارد و
    نمی‌تواند dump را باز کند (آزمایش 6). `gnupg` همین امروز در نصب پایه هست
    (`deploy/00-bootstrap-server.sh:37`)، پس وابستگی تازه‌ای نیست.
  - **چرا dump هم، وقتی repo2 هست:** dump یک قالب مستقل از pgBackRest است. اگر
    روزی خود pgBackRest یا repo آن مشکل داشته باشد (همان ریسک نگهداری پروژه)،
    dump با `pg_restore` معمولی هر نصب را جدا برمی‌گرداند.
- **بازیابی بعد از از دست رفتن سرور با 2.50 دو قدم دارد**: دانلود repo رمزشده با
  `sftp`، بعد restore از repo محلی. restore مستقیم از SFTP با 2.50 در آزمایش 5
  شکست خورد و با 2.59.2 کار کرد (بخش 4).
- **کلید رمز روی کاغذ.** بعد از از دست رفتن سرور، کسی باید کلید را از روی کاغذ
  تایپ کند، و یک کلید SSH تازه روی مقصد مجاز کند. بدون کلید رمز درست، pgBackRest
  حتی فهرست پشتیبان‌ها را نمی‌خواند (آزمایش 5). این قدم‌ها در runbook و در
  تمرین بازیابی کامل می‌آیند.

### 5.2 Replica

- replica روی **همان سرور** از از دست رفتن سرور محافظت نمی‌کند (استنتاج؛ مستند
  PostgreSQL فقط برای دیسک مشترک همین را صریح می‌گوید). سرور دوم امروز وجود ندارد.
- replica جلوی خطای انسانی و migration بد را هم نمی‌گیرد: یک `DROP` بلافاصله
  روی replica هم اجرا می‌شود. آن کار PITR است.
- یک نصب تک‌مشتری برای یک رویداد یا یک سازمان، بدون قرارداد SLA، سود کمی از
  failover خودکار می‌برد و هزینهٔ عملیاتی بالایی دارد (استنتاج).

### 5.3 Restore drill

- طرح اندازه‌گیری‌شده کار می‌کند: دیتابیس drill از قبل ساخته‌شده، نقش برنامه،
  `pg_restore` با همان flagها، شمارش، پاک کردن schemaها.
- «شمارش در برابر مبدأ» فقط وقتی دقیق است که شمارش از **همان snapshot** dump
  باشد. شمارش زندهٔ دیتابیس در لحظهٔ drill با dump دیشب فرق دارد. راه دقیق:
  در `create()` یک تراکنش `REPEATABLE READ` باز شود، `pg_export_snapshot()`،
  شمارش جدول‌ها، و `pg_dump --snapshot=<id>`. شمارش‌ها در `manifest.json` ثبت
  می‌شوند و drill با آن‌ها مقایسه می‌کند (سازوکار `--snapshot` وجود دارد؛
  اجرای کامل این زنجیره هنوز تست نشده و کار PR است).
- بررسی `app.schema_migrations` (تعداد و checksum) در آزمایش 2 کار کرد.
- `validate_restored_database()` امروز فقط روی دیتابیس زنده کار می‌کند
  (`app/services/pg_backup.py:582-588`). برای drill باید روی یک اتصال دلخواه
  کار کند، نه اینکه کپی شود.
- زمان‌بندی: زمان‌بند فعلی (`app/services/backup.py:234-280`) با claim اتمی
  یک بار در هر slot اجرا می‌شود و الان همین کار را برای dump و verify می‌کند.
  drill منطقاً قدم بعدی همان زنجیره است: «پشتیبانی که همین الان گرفتیم را
  واقعاً بازیابی کن». timer جدای systemd یک الگوی دوم برای همین کار می‌شد.
- دیده شدن نتیجه: نتیجه **پایدار** ذخیره می‌شود (در `manifest.json` همان پشتیبان،
  مثل `verification`) و صفحهٔ Backups آن را نشان می‌دهد، چون هنوز هیچ scraper یا
  هشداری نیست. gaugeها همان الگوی `main` را دنبال می‌کنند: در حالت multiprocess،
  زمان آخرین موفقیت یک gauge با `multiprocess_mode="max"` است و در شروع برنامه از
  manifestهای روی دیسک پر می‌شود، درست مثل `backup_last_success_timestamp_seconds`
  (`app/services/metrics.py:134-144`، `app/services/pg_backup.py:302`،
  `app/main.py:168`). مدت و نتیجهٔ آخرین drill با `mostrecent`. هشدار روی این
  متریک‌ها کار پشتهٔ پایش است (ADR-024 روی main)؛ این سند فقط نام متریک‌ها را
  پیشنهاد می‌کند.
- **سقف زمان:** drill همان `pg_restore` را اجرا می‌کند که به سقف 300 ثانیه نزدیک
  است (بخش 4، محدودیت‌ها). پس PR B2، که اولین جایی است که drill آن را صدا
  می‌زند، باید سقف restore و drill را از سقف dump جدا یا بزرگ‌تر کند. مدت
  restore در هر drill ثبت می‌شود، پس همین drill هر شب نزدیک شدن به سقف را
  اندازه می‌گیرد.
- **دیسک:** drill هر شب یک کپی کامل می‌سازد (در آزمایش 2، 1.9 GB) و بعد پاک
  می‌کند. یعنی برای چند دقیقه حجم دیتابیس روی دیسک تقریباً دو برابر می‌شود.
  drill باید قبل از شروع فضای آزاد را بررسی کند. بررسی پنل
  (`app/services/pg_backup.py:480-486`) سه برابر اندازهٔ **فایل dump** را می‌خواهد؛
  برای drill کافی نیست، چون در آزمایش 2 دیتابیس بازیابی‌شده حدود 15 برابر
  فایل dump بود. پس معیار drill باید اندازهٔ دیتابیس زنده باشد.
- **نگه‌داری:** پشتیبان‌های قبل از deploy هم در شمارش `keep=14` در `prune()`
  حساب می‌شوند (`app/services/pg_backup.py:419-435`). چند deploy در یک روز،
  پشتیبان‌های شبانه را زودتر بیرون می‌کند و پنجرهٔ بازگشت روزانه کوتاه‌تر می‌شود.
  تصمیم اینکه شمارش آن‌ها جدا شود، در PR B2 گرفته می‌شود.

### 5.4 Staging

- `PADYAR_ENV=staging` از قبل وجود دارد و gate production را اجرا می‌کند ولی
  فقط لاگ می‌کند (`docs/engineering/DEPLOYMENT_RUNBOOK.md`، بخش Environment marker).
- `docker-compose.yml` شکل production نیست: نه systemd، نه nginx، نه
  `padyar-deploy.sh`، نه نقش محدود (`docker-compose.yml:5-6`، کاربر superuser
  ایمیج). staging با آن، مسیر واقعی استقرار را تست نمی‌کند.
- یک slug دوم (`staging`) روی همان سرور، با `05` و `10` و همان
  `padyar-deploy.sh` ساخته می‌شود. یعنی همان مسیر، همان اسکریپت، همان نقش.
  هزینه: RAM و CPU همان سرور، و یک دیتابیس دیگر در همان cluster.
- **عوارض بیرونی staging.** `PADYAR_ENV=staging` gate را فقط لاگ می‌کند. نگهبان
  SMS فقط provider `dev` را در production رد می‌کند (`app/services/sms.py:300`).
  پس staging با اعتبار واقعی درگاه، SMS واقعی می‌فرستد و با کلید AI واقعی هزینهٔ
  واقعی می‌سازد. staging باید provider `dev` برای SMS و کلید AI جدا با سقف
  هزینهٔ خودش داشته باشد. چون staging فقط محتوا می‌گیرد (تصمیم مالک محصول)،
  `settings` کپی‌شده از production نباید اعتبار SMS و کلید AI را با خود بیاورد.

### 5.5 RPO و RTO

امروز هیچ هدفی نوشته نشده. وضع امروز، به‌عنوان مبنا:

| سناریو | RPO امروز | RTO امروز |
|---|---|---|
| خطای داده یا migration بد، سرور سالم | تا 24 ساعت (از dump شبانه)؛ برای deploy، صفر (dump قبل از deploy) | اندازه‌گیری نشده روی سرور. restore یک dump با 1.9 GB روی مک: 4 دقیقه و 23 ثانیه، ولی با سقف 300 ثانیه، بالای حدود 2.2 GB شکست می‌خورد (تخمین) |
| از دست رفتن دیسک یا سرور | **همه‌چیز.** `OFFSITE_BACKUP_TARGET` روی سرور تنظیم نیست، پس هیچ نسخه‌ای از سرور بیرون نمی‌رود (تأیید مالک محصول) | بازیابی ممکن نیست، فقط ساخت از صفر |

یک نکته دربارهٔ سطر آخر: سرور یک ماشین مجازی VMware است و مالک محصول تأیید کرد
که VMware از آن snapshot می‌گیرد (2026-10-01). کجا نگه داشته می‌شود و برای چند
وقت، نامعلوم است. اگر snapshotها روی همان میزبان فیزیکی باشند، از دست رفتن آن
میزبان آن‌ها را هم می‌برد. تا وقتی این معلوم و آزموده نشده، در هیچ عدد RPO یا
RTO از snapshot استفاده نمی‌شود.

### 5.6 Rollback

- **کد:** rollback خودکار بعد از health قرمز کار می‌کند
  (`deploy/padyar-deploy.sh:185-200`). rollback دستی به یک sha قدیمی کار
  نمی‌کند (اصلاح 3). راهی که امروز **بدون تغییر اسکریپت** کار می‌کند: یک
  `git revert` روی `main`، سبز شدن CI، و بعد اپراتور روی سرور `padyar-deploy` را
  با sha تازه (همان revert) اجرا می‌کند. چون آن sha نوک `main` است، قاعدهٔ
  `SUPERSEDED` آن را رد نمی‌کند. استقرار خودکار از CI دیگر وجود ندارد
  (`docs/engineering/DEPLOYMENT_RUNBOOK.md:21-27`).
- **داده:** dump قبل از deploy (`deploy/padyar-deploy.sh:108-115`) و restore از
  پنل ادمین. این برای یک نصب کار می‌کند و به نصب دیگر دست نمی‌زند.
- **مشکل اصلی:** با migration مخرب، rollback کد به‌تنهایی کافی نیست. کد قدیمی
  ممکن است ستونی را بخواند که دیگر نیست (مثلاً کد قبل از `0028` ستون
  `visitor_settings` را می‌خواند). پس قاعده باید در زمان نوشتن migration اعمال
  شود، نه در زمان rollback: حذف یک ستون یا جدول در یک deploy **جدا و بعدی**
  انجام شود، وقتی کدی که از آن استفاده نمی‌کند قبلاً مستقر و پایدار شده است
  (الگوی expand/contract). آن‌وقت rollback کد یک deploy هیچ‌وقت از روی یک حذف
  عبور نمی‌کند. `0028` تقریباً همین را رعایت کرد (خوانندهٔ آن در همان تغییر حذف
  شد، طبق توضیح خود فایل)، ولی در یک deploy.

### 5.7 Connection pooling

- قالب نصب: `WEB_CONCURRENCY=3` × `DB_POOL_MAX_SIZE=5` = **15 اتصال** برای هر نصب
  (`deploy/env/instance.env.template:27`، `:35`؛ همین عدد در
  `deploy/05-create-databases.sh:72`). دو نصب: 30. staging پیشنهادی: 45.
  به‌اضافهٔ اتصال‌های `pg_dump`، drill و pgBackRest (هر کدام چند اتصال کوتاه).
  `max_connections` پیش‌فرض PostgreSQL 100 است (اسکریپت `05` زیر 60 هشدار
  می‌دهد، `:74-76`). حاشیه کافی است.
- PgBouncer در حالت transaction با prepare خودکار psycopg 3 تداخل دارد، مگر
  libpq 17 به بالا و تنظیم `max_prepared_statements`، یا خاموش کردن prepare
  (مستند psycopg). یعنی یک سرویس دیگر، یک پیکربندی دیگر، و یک ریسک رفتاری،
  برای مشکلی که عددها نشان نمی‌دهند.
- `app/prodcheck.py:207-216` بالای 80 اتصال هشدار می‌دهد. این هشدار کافی است.

### 5.8 تفکیک نقش‌ها

امروز یک نقش همه کار می‌کند: مالک schemaها، اجرای برنامه، migration، dump و
restore. `NOSUPERUSER NOCREATEDB NOCREATEROLE` و `statement_timeout = 30s` و
`idle_in_transaction_session_timeout = 60s` خوب است
(`deploy/05-create-databases.sh:49-53`). ولی:

- یک SQL injection در برنامه می‌تواند `DROP TABLE` اجرا کند، چون نقش برنامه
  مالک جدول‌هاست.
- جدا کردن «نقش مالک برای migration» از «نقش اجرای برنامه فقط با DML» این را
  می‌بندد، ولی هزینهٔ واقعی دارد: 36 دستور DDL در زمان اجرا در سرویس‌ها باید
  یا به migration منتقل شوند یا ثابت شود با نقش غیر مالک بی‌خطر اجرا می‌شوند
  (تست نشده)، و restore از پنل ادمین (`--clean`، یعنی DROP) به نقش مالک نیاز
  دارد. پس برنامه باز هم به رمز نقش مالک نیاز پیدا می‌کند.
- **نقش فقط خواندنی با `pg_read_all_data` غلط است** (نسخهٔ نوبت 1 این سند همین را
  پیشنهاد کرده بود). `pg_read_all_data` خواندن همهٔ جدول‌ها را در **هر** دیتابیسی
  که نقش به آن وصل شود می‌دهد، و روی cluster مشترک، دادهٔ نصب دیگر را هم می‌خواند
  (بازبین بازتولید کرد: شمارهٔ تلفن بازدیدکنندهٔ نصب دیگر برگشت). این یک مسیر
  خواندن بین دو نصب است که امروز وجود ندارد.
- **آنچه الان ارزش دارد، بستن اتصال بین دو نصب است.** `REVOKE CONNECT ON DATABASE
  padyar_<slug> FROM PUBLIC` و `GRANT CONNECT` فقط به نقش خود نصب. امروز یک نقش
  می‌تواند به دیتابیس نصب دیگر وصل شود و فقط سد schema جلویش را می‌گیرد (بخش 3).
  این یک لایهٔ دوم دفاع است، بدون هیچ نقش تازه.
- **نقش فقط خواندنی تا وقتی مصرف‌کنندهٔ مشخص نداشته باشد ساخته نمی‌شود.** اگر
  روزی لازم شد، فقط با دسترسی محدود به همان دیتابیس: `GRANT CONNECT` روی همان
  دیتابیس، `USAGE` روی `app` و `observability`، `SELECT` روی همهٔ جدول‌ها، و
  `ALTER DEFAULT PRIVILEGES` برای جدول‌های آینده. هرگز `pg_read_all_data`.
- نقش پشتیبان جدا فعلاً لازم نیست: `pg_dump` با نقش مالک همه چیز را می‌خواند،
  و pgBackRest با کاربر سیستمی `postgres` و از راه socket محلی کار می‌کند.

### 5.9 برآورد ظرفیت (همه تخمین)

- **هر نوبت گفتگو:** یک ردیف `chat_logs` (کد: `app/db/queries.py:64-73`) و دو ردیف
  `messages` (پرسش و پاسخ؛ `app/services/conversations.py:535`). **فرض:** حدود
  یک ردیف `app_logs`. با اندازه‌های اندازه‌گیری‌شده در آزمایش 1:
  721 + 2 × 456 + 305 ≈ **1.9 KB برای هر نوبت**، با index. متن واقعی ممکن است
  کوتاه‌تر یا بلندتر باشد.
- **یک رویداد:** 10,000 بازدیدکننده × 10 نوبت = 100,000 نوبت ≈ **190 MB**
  (تخمین، با فرض‌های بالا).
- **رشد بی‌سقف:** `chat_log_retention_days` پیش‌فرض 0 یعنی «برای همیشه نگه دار»
  (`app/routers/admin.py:663`، `app/db/queries.py:193-210`). `app_logs` پیش‌فرض
  90 روز نگه‌داری دارد (`docs/engineering/MONITORING.md:335-338`).
- **مرز 300 ثانیه:** مرز تنگ restore است، نه dump (بخش 4). با این نرخ، رسیدن به
  حدود 2.2 GB یعنی حدود 11 رویداد مثل بالا بدون پاک‌سازی (تخمین؛ dump حدود 5 GB،
  یعنی حدود 25 رویداد). پیش از آن، drill و بازیابی پنل شکست می‌خورند، پس باید
  زودتر دیده شود. drill شبانه مدت restore را اندازه می‌گیرد و همان علامت است.
- **WAL:** در آزمایش 3، نوشتن حدود 500 MB داده 36 فایل WAL (هر کدام 16 MB،
  حدود 576 MB بدون فشرده‌سازی) ساخت. پشتیبان کامل در repo 31.2 MB شد. با
  `repo1-retention-full=2`، repo محلی تقریباً «دو پشتیبان کامل فشرده + WAL
  فشردهٔ بین آن‌ها» جا می‌گیرد (تخمین؛ حجم WAL فشرده اندازه‌گیری نشد).
- **مدل زبانی محلی روی همان سرور (ADR-027 روی main):** عددهای دیتابیس این بخش را
  عوض نمی‌کند، ولی یک فایل مدل حدود 16 GiB روی همان دیسکی می‌گذارد که دیتابیس،
  dumpها، repo محلی WAL، کپی drill و نمونهٔ جدای PITR روی آن هستند. سند آن تیم
  حدود 111 GB فضای آزاد روی سرور ثبت کرده است
  (`docs/features/local-inference/RESEARCH.md:140-141`؛ عدد آن تیم، نه اندازه‌گیری
  من). پس بررسی‌های فضای آزاد قبل از drill و قبل از restore جدا مهم‌ترند.

## 6. Decision / Recommendation

**خلاصه:** pgBackRest برای PITR کل cluster، کنار `pg_dump` برای هر نصب (نه به
جای آن). drill شبانه از روی همان dump شبانه، در زمان‌بند موجود، با نقش خود
برنامه، روی یک دیتابیس drill از پیش ساخته. بدون replica. بدون PgBouncer.
staging با یک slug دوم روی همان سرور. قاعدهٔ expand/contract برای migration
مخرب. بستن اتصال بین دو نصب (`REVOKE CONNECT ... FROM PUBLIC`) الان؛ نقش فقط
خواندنی و جدا کردن مالک از اجرا بعداً. کپی رمزشده به یک حساب SFTP: WAL و
پشتیبان پایه با pgBackRest repo2، dumpها با `gpg` و یک هدف تازهٔ `sftp:`. امروز هیچ
کپی بیرون از سرور نیست و هنوز سرور SFTP هم وجود ندارد.

```mermaid
flowchart TB
    classDef proposed fill:#fff4d6,stroke:#b8860b,stroke-dasharray:5 5,color:#000
    classDef shipped fill:#e8f0fe,stroke:#2d5ca7,color:#000
    classDef fallback fill:#f2f2f2,stroke:#666,color:#000

    subgraph INST["per install"]
        DUMP["pg_dump per install<br/>app/services/pg_backup.py"]:::shipped
        SNAP["row counts from the dump snapshot<br/>stored in manifest.json"]:::proposed
        DRILL["nightly restore drill<br/>into padyar_slug_drill, app role"]:::proposed
        SHOW["result stored in manifest<br/>Backups page + multiprocess gauges<br/>(max, seeded at start, like main)"]:::proposed
    end

    subgraph CLU["whole cluster"]
        PITR["pgBackRest 2.50 from Ubuntu apt<br/>one stanza, local posix repo<br/>archive-push-queue-max bounds pg_wal"]:::proposed
        SIDE["PITR restore into a SIDE data dir, archive off<br/>pg_dump one install, manual pg_restore<br/>as padyar_slug (measured 49.9 s on a Mac)"]:::proposed
        FB["built-in PostgreSQL tools<br/>archive_command + pg_basebackup"]:::fallback
    end

    subgraph OFFG["off-site (PR H)"]
        OFF["one SFTP account, all encrypted on the server<br/>repo2 sftp aes-256-cbc: WAL + base<br/>dumps: gpg public key + new sftp: target"]:::proposed
        NOOFF["no SFTP destination yet:<br/>server loss loses everything"]:::fallback
    end

    subgraph DEP["deploy rule"]
        ROLL["rollback = git revert on main, CI green<br/>operator runs padyar-deploy (no CI deploy)<br/>data = pre-deploy dump"]:::proposed
        EC["destructive migration ships<br/>in its own later deploy"]:::proposed
    end

    DUMP -->|"DF01 · snapshot export works"| SNAP
    SNAP -->|"DF02"| DRILL
    DRILL -->|"DF03"| SHOW
    DUMP -.->|"DF04 · snapshot fails: check<br/>schema_migrations + non-empty tables"| DRILL

    PITR -->|"DF05 · one install broke"| SIDE
    PITR -.->|"DF06 · pgBackRest unusable"| FB

    PITR -->|"DF08 · repo2"| OFF
    DUMP -->|"DF09 · after verify"| OFF
    OFF -.->|"DF10 · until a destination exists"| NOOFF

    ROLL -->|"DF07 · only safe if"| EC
```

راهنما: خط‌چین زرد یعنی پیشنهادی (هنوز ساخته نشده). آبی یعنی امروز وجود
دارد (`app/services/pg_backup.py:150-206`). خاکستری یعنی مسیر جایگزین اگر یک
شرط شکست بخورد. یال‌های خط‌چین مسیر جایگزین هستند.

### تصمیم برای هر زیرسؤال

1. **PITR:** pgBackRest 2.50 از apt خود اوبونتو (تصمیم مالک محصول؛ همان منبع خود
   PostgreSQL؛ در آزمایش 4 اجرا شد). repo1 از نوع posix روی دیسک محلی. برنامه:
   پشتیبان کامل هفتگی، differential روزانه، `repo1-retention-full=2` (پنجرهٔ PITR
   بین 7 تا 14 روز)، `archive_timeout=60`. `pg_dump` می‌ماند.
   - **مرز دیسک:** `archive-push-queue-max` (مثلاً 1 تا 2 GiB، بسته به دیسک آزاد
     سرور). بعد از سقف، WAL دور ریخته می‌شود و PITR تا پشتیبان کامل بعدی قطع
     است، ولی پستگرس و هر دو نصب بالا می‌مانند. صفحهٔ Backups
     `pg_stat_archiver.failed_count` و `last_failed_time` و سن آخرین آرشیو را
     نشان می‌دهد (نقش برنامه آن را می‌خواند، آزمایش 4).
   - **روشن کردن:** یک restart کل cluster؛ هر دو نصب چند ثانیه قطع می‌شوند؛ فقط
     در ساعت آرام و با تأیید مالک محصول.
   - **کپی بیرون از سرور:** repo2 از نوع `sftp` با رمز `aes-256-cbc`، در خود PR H
     (بند 10). روی سرور واقعی repo2 خالی می‌ماند تا مقصد SFTP ساخته شود.
   - **دو نصب روی یک cluster:** restore درجای کل cluster فقط برای خرابی کل cluster.
     برای خطای یک نصب، همان زنجیرهٔ آزمایش 4: **اول بررسی فضای آزاد دیسک در
     برابر اندازهٔ کل cluster** (نمونهٔ جدا یک کپی از کل cluster است، یعنی هر دو
     نصب و بعد از PR E staging هم، به‌اضافهٔ WAL برای replay؛ اگر دیسک وسط حادثه پر
     شود، cluster زنده هم می‌خوابد)، بعد restore به زمان T در یک پوشهٔ جدا
     روی پورت دیگر **با `archive_mode=off`**، `pg_dump` فقط دیتابیس آن نصب از
     نمونهٔ جدا با نقش `padyar_<slug>`، یک dump ایمنی از دیتابیس زنده، و بعد
     **`pg_restore` دستی** با نقش `padyar_<slug>` و همان flagهای پنل، بیرون از
     پنل. پنل فقط پشتیبان‌هایی را می‌پذیرد که شناسه و `manifest.json` با sha256
     خودش را دارند (`app/services/pg_backup.py:47-50`، `:211-242`، `:473-475`)،
     و dump نمونهٔ جدا هیچ‌کدام را ندارد. ساختن «وارد کردن dump بیرونی» به پنل
     کار این مرحله نیست؛ runbook مسیر دستی را قدم به قدم می‌نویسد. قبل از
     restore دستی، سرویس همان نصب متوقف می‌شود (`systemctl stop padyar-<slug>`)
     و بعد از آن دوباره شروع می‌شود، تا هیچ worker اتصال قدیمی نگه ندارد (همان
     دلیل `:557-559`). نصب دیگر دست نمی‌خورد.
2. **Replica:** نه، الان. نه اسکریپت، نه failover. در عوض یک بند در runbook:
   «اگر مشتری در دسترس‌پذیری بالا خواست، این ADR باز می‌شود». شرط بازبینی در ADR.
3. **Drill:** شبانه، بلافاصله بعد از dump و verify در همان slot زمان‌بند موجود.
   مقصد: `padyar_<slug>_drill` که `deploy/05-create-databases.sh` می‌سازد،
   مالک نقش برنامه. کد نام را از نام زنده مشتق می‌کند و اگر برابر باشد رد می‌کند.
   بررسی‌ها: شمارش ردیف هر جدول در برابر شمارش ثبت‌شده از snapshot dump،
   `app.schema_migrations` (تعداد و checksum)، و همان بررسی‌های
   `validate_restored_database()` روی اتصال drill. بعد schemaهای drill پاک
   می‌شوند. نتیجه در بلوک `drill` در `manifest.json` همان پشتیبان.
   متریک‌ها، با همان الگوی multiprocess که `main` برای
   `backup_last_success_timestamp_seconds` دارد (بخش 5.3):
   `backup_drill_last_success_timestamp_seconds` (`max`، در شروع برنامه از
   manifestها پر می‌شود)، `backup_drill_last_duration_seconds` و
   `backup_drill_last_ok` (0 یا 1) با `mostrecent`.
   صفحهٔ Backups: یک خط وضعیت ساده («آخرین تمرین بازیابی: موفق، زمان،
   مدت»)، و با کلیک، جدول شمارش‌ها. هشدار با پشتهٔ پایش (ADR-024 روی main).
   سقف زمان restore و drill از سقف dump جدا و بزرگ‌تر می‌شود (در PR B2).
   فضای آزاد بر اساس اندازهٔ دیتابیس زنده بررسی می‌شود. اینکه پشتیبان‌های قبل از
   deploy جدا از شبانه شمرده شوند، در PR B2 تصمیم گرفته می‌شود.
4. **Staging:** slug دوم `staging` روی همان سرور، `PADYAR_ENV=staging`.
   جریان، با استقرار دستی که `main` حالا دارد: merge به `main` ← CI سبز ←
   اپراتور روی سرور `padyar-deploy staging <port> <sha>` ← بررسی staging (health و
   یک نگاه کوتاه در مرورگر) ← اپراتور همان sha را روی production مستقر می‌کند.
   اگر بین انتخاب sha و اجرای دستور، `main` جلو رفته باشد، `padyar-deploy` با پیام
   `SUPERSEDED` و کد 0 تمام می‌شود و هیچ چیزی مستقر نمی‌کند
   (`deploy/padyar-deploy.sh:129-139`)؛ اپراتور همان مسیر را با sha تازه دوباره
   اجرا می‌کند، از staging.
   CI هیچ job استقراری ندارد و نباید دوباره بگیرد؛ این قدم یک دستور در runbook
   است، نه یک job. داده: فقط محتوا (`dataset`، `questions`، `synonyms`،
   `settings`)، بدون هیچ دادهٔ شخصی بازدیدکننده (تصمیم مالک محصول). از `settings`
   کپی‌شده، اعتبار SMS و کلید AI برداشته می‌شوند: staging provider `dev` برای SMS
   دارد و یک کلید AI جدا با سقف هزینهٔ خودش، تا staging نه SMS واقعی بفرستد نه
   هزینهٔ AI production را مصرف کند. `WEB_CONCURRENCY=1`. جدول `companies` در این
   فهرست نیست؛ تصمیمش با spec PR E است (بخش 3، تصمیم 2).
5. **RPO و RTO.** مالک محصول در گفتگو هدف‌های سطرهای 1 و 2 را همان‌طور که نوشته
   شده‌اند تأیید کرد (2026-09-30 و 2026-10-01). هدف RPO سطر 3 را **تأیید نکرده
   است** (پایین). **این‌ها هدف‌اند، نه چیزی که امروز برآورده می‌شود.** هیچ هدف RTO هنوز روی سرور و از سر تا ته اندازه‌گیری نشده است.

   | سناریو | هدف RPO | چطور برآورده می‌شود | چطور اندازه گرفته می‌شود | هدف RTO | چطور اندازه گرفته می‌شود |
   |---|---|---|---|---|---|
   | خطای یک نصب، سرور سالم | 5 دقیقه | آرشیو WAL با `archive_timeout=60` | سن آخرین WAL آرشیوشده (`pg_stat_archiver.last_archived_time`) | 1 ساعت | **هنوز از سر تا ته اندازه‌گیری نشده.** زنجیرهٔ کامل (restore جدا، replay تا T، `pg_dump` یک نصب، dump ایمنی، `pg_restore`، ری‌استارت سرویس، بوت برنامه) را اسکریپت PR C روی سرور زمان می‌گیرد. اولین عدد: 49.9 ثانیه برای یک نصب 250 MB روی Mac، بدون ری‌استارت و بوت برنامه (آزمایش 4). بوت برنامه تا یک دقیقه طول می‌کشد (`deploy/padyar-deploy.sh:167-178`) |
   | خرابی cluster، سرور سالم | 5 دقیقه | همان | همان | 2 ساعت | هنوز اندازه‌گیری نشده؛ تست PITR دوره‌ای (اسکریپت PR C) |
   | از دست رفتن سرور | **امروز: همه‌چیز از دست می‌رود.** بعد از PR H **و** وجود یک مقصد SFTP: 5 دقیقه، **هدف مشتق‌شده، تأییدنشده** | WAL هر حداکثر 60 ثانیه به repo2 روی SFTP، رمزشده؛ dump شبانهٔ رمزشده هم به همان مقصد | سن `pg_stat_archiver.last_archived_time` (آرشیو منتظر هر دو repo می‌ماند، آزمایش 5)، `pgbackrest verify` روی repo2، و وضعیت `offsite` در manifest | 1 روز کاری (تخمین) | فقط با یک تمرین بازسازی کامل روی سرور دیگر؛ امروز انجام نشده. همان زنجیره روی Mac، برای 42 MB، 7.7 ثانیه بود (آزمایش 5)، که سرور تازه، نصب بسته‌ها و کلید را شامل نمی‌شود |

   **عدد 5 دقیقهٔ سطر آخر یک هدف مشتق‌شده است، نه گفتهٔ مالک محصول.** مالک محصول
   تصمیم گرفت «WAL هم در PR H بیرون برود» (2026-10-01)؛ 5 دقیقه از همین نتیجه
   گرفته شده (آرشیو هر حداکثر 60 ثانیه، به‌اضافهٔ حاشیه). مالک محصول آن را تأیید
   نکرده است (سؤال باز 2، بخش 9). این عدد اندازه‌گیری هم نیست، و تا وقتی مقصد
   SFTP وجود ندارد اصلاً برآورده نمی‌شود. snapshotهای VMware در هیچ عددی حساب
   نشده‌اند.

6. **Rollback:** مسیر استاندارد کد: `git revert` روی `main`، CI سبز، و اپراتور sha
   تازه را با `padyar-deploy` روی سرور مستقر می‌کند (همان مسیر استقرار عادی). برای
   اورژانس، وقتی صبر برای CI ممکن نیست، اسکریپت یک حالت
   rollback صریح می‌گیرد که sha قدیمی را فقط اگر جد (ancestor) `main` باشد
   می‌پذیرد، و قبل از هر کار، migrationهایی که دیتابیس دارد و کد قدیمی ندارد را
   فهرست می‌کند و بدون تأیید صریح اپراتور ادامه نمی‌دهد. توضیح‌های غلط اسکریپت
   («additive-only»، «`/health × 3`»، «sha قدیمی») اصلاح می‌شوند. قاعدهٔ
   expand/contract در `docs/engineering/DATABASE.md` نوشته می‌شود.
7. **Pooling:** همان `psycopg_pool` داخل برنامه. PgBouncer نه. شرط بازبینی: اگر
   جمع اتصال‌ها بالای 80 رفت (همان هشدار `prodcheck`) یا بیش از سه نصب روی یک
   cluster آمد.
8. **نقش‌ها:** الان: `REVOKE CONNECT ON DATABASE padyar_<slug> FROM PUBLIC` و
   `GRANT CONNECT` فقط به نقش همان نصب. **نه** `pg_read_all_data` (بخش 5.8). نقش
   فقط خواندنی تا مصرف‌کنندهٔ مشخص نیست ساخته نمی‌شود، و اگر ساخته شود فقط با
   دسترسی محدود به دیتابیس خودش. بعداً (ADR جدا): جدا کردن مالک از اجرا، بعد از
   اینکه DDLهای زمان اجرا بررسی و مسیر restore پنل برای آن طراحی شد.
9. **ظرفیت:** یک هشدار در صفحهٔ Backups وقتی مدت **restore در drill** از 50 درصد
   سقف restore گذشت (restore سقف تنگ‌تر است، بخش 4)، و یک بند در runbook دربارهٔ
   `chat_log_retention_days`.
10. **کپی بیرون از سرور (PR H): یک حساب SFTP، هر چیزی که بیرون می‌رود رمزشده.**
    - **WAL و پشتیبان پایه:** pgBackRest repo2 با `repo2-type=sftp`،
      `repo2-cipher-type=aes-256-cbc`، `repo2-cipher-pass`، و
      `repo2-sftp-host-key-check-type=strict`. کلید SSH مخصوص کاربر سیستمی
      `postgres` روی سرور. کلید میزبان مقصد با fingerprint ثبت‌شده در runbook
      بررسی می‌شود (در آزمایش 5، فقط ثبت کلید ed25519 کافی نبود و libssh2 نوع
      دیگری را انتخاب کرد؛ همهٔ کلیدهای میزبان باید ثبت شوند).
    - **dumpها:** نوع هدف تازهٔ `sftp:` در `app/services/backup_offsite.py`، کنار
      `rsync:` و `dir:`، با argv ثابت (همان قاعدهٔ امروز آن فایل). قبل از آپلود،
      `gpg -e -r <fingerprint>` با **کلید عمومی** (آزمایش 6). پاک کردن کپی‌های قدیمی
      مقصد هم جزو همین PR است (امروز هیچ مقصدی prune نمی‌شود، بخش 3).
    - **کلیدها (تصمیم مالک محصول، نوبت 4):**
      - **dumpها، کلید عمومی gpg.** روی سرور فقط کلید عمومی است. کلید خصوصی فقط
        روی کاغذ در گاوصندوق است. پس سرور نمی‌تواند dumpهای خودش را باز کند، و کسی
        که سرور را بگیرد هم dumpهای مقصد را نمی‌خواند (آزمایش 6).
      - **repo2 در pgBackRest، رمز متقارن.** pgBackRest حالت کلید عمومی ندارد و WAL
        هر دقیقه بدون حضور انسان رمز می‌شود، پس رمز روی سرور است: در
        `/etc/pgbackrest.conf` با `0600` مال `postgres`، و یک نسخهٔ کاغذی در
        گاوصندوق. کسی که سرور را بگیرد این رمز را می‌خواند و می‌تواند نسخهٔ repo2
        در مقصد را هم بخواند؛ ولی همان شخص همین حالا به خود دیتابیس دسترسی دارد.
      - هیچ کلیدی **داخل هیچ پشتیبانی** نیست (`pg_dump` فقط دیتابیس را می‌گیرد، نه
        `/etc`؛ repo هم پیکربندی را نمی‌گیرد) و **هرگز در مقصد** نیست.
      - نتیجه برای بازیابی: بعد از از دست رفتن سرور، کسی باید رمز repo2 را از روی
        کاغذ تایپ کند (کلید غلط یعنی «no backup set found»، آزمایش 5)، و برای باز
        کردن dumpها کلید خصوصی gpg را از روی کاغذ وارد کند (حدود 720 بایت متن،
        آزمایش 6). runbook PR H روش چاپ، نگه‌داری و آزمودن هر دو را می‌نویسد.
    - **بازیابی از مقصد با 2.50:** دانلود کل repo با `sftp`، بعد restore از repo
      محلی (آزمایش 5). runbook همین دو قدم را می‌نویسد.
    - **تا وقتی مقصد نیست:** PR H کد را می‌سازد و در CI در برابر یک کانتینر SFTP
      موقت تست می‌کند (همان شکل آزمایش 5). روی سرور واقعی `OFFSITE_BACKUP_TARGET`
      و repo2 خالی می‌مانند، و صفحهٔ Backups به زبان ساده می‌گوید «هیچ نسخه‌ای
      بیرون از سرور نیست».

### چه چیزی را از دست می‌دهیم

| از دست می‌دهیم | چرا قبولش می‌کنیم | کاهش ریسک |
|---|---|---|
| یک ابزار تازه با دسترسی کامل به cluster (pgBackRest) | PITR بدون آن یعنی نوشتن و نگه داشتن retention و بررسی سلامت آرشیو در bash | فقط اسکریپت root اجرایش می‌کند؛ برنامه فقط وضعیت می‌خواند |
| وابستگی به پروژه‌ای که در 2026 تقریباً رها شد | امروز فعال است و از مخزن اوبونتو نصب می‌شود | مسیر جایگزین با ابزار خود PostgreSQL (DF06)؛ اسکریپت‌ها پشت دو فایل در `deploy/` می‌مانند |
| نسخهٔ 2.50 اوبونتو قدیمی است | تصمیم مالک محصول؛ منبع apt تازه در ایران شاید در دسترس نباشد | 2.50 در آزمایش 4 درست کار کرد؛ job CI در PR C از همان منبع نصب می‌کند |
| PITR نمی‌تواند یک نصب را جدا برگرداند | cluster مشترک واقعیت سرور است | restore در پوشهٔ جدا و بعد انتقال با `pg_dump` و `pg_restore` دستی (آزمایش 4) |
| سقف صف آرشیو، WAL را دور می‌ریزد و PITR را تا پشتیبان کامل بعدی قطع می‌کند | پر شدن دیسک هر دو نصب را متوقف می‌کند؛ از دست دادن PITR بهتر از از دست دادن سرویس است | `failed_count` روی صفحهٔ Backups؛ پشتیبان کامل تازه بعد از رفع مشکل |
| روشن کردن آرشیو یک restart کل cluster لازم دارد | فقط یک بار، در ساعت آرام | با تأیید مالک محصول و خارج از زمان رویداد |
| بازیابی یک نصب از PITR بیرون از پنل و دستی است | ساختن «وارد کردن dump بیرونی» به پنل کار بزرگ‌تری است | runbook قدم به قدم؛ همان flagهای پنل؛ نقش `padyar_<slug>` |
| WAL، پشتیبان پایه و dump رمزشده روی یک سرور SFTP بیرونی، یعنی دادهٔ شخصی بیرون از سرور | امروز از دست رفتن سرور یعنی از دست رفتن همه‌چیز | رمزنگاری در سمت سرور (`aes-256-cbc` در pgBackRest، کلید عمومی gpg برای dump)؛ هیچ کلیدی در مقصد یا داخل پشتیبان نیست |
| رمز repo2 روی سرور است (متقارن) | WAL بی‌حضور انسان رمز می‌شود و pgBackRest کلید عمومی ندارد (تصمیم مالک محصول، نوبت 4) | فایل `0600`؛ هرگز داخل پشتیبان یا مقصد؛ نسخهٔ کاغذی. dumpها جدا با کلید عمومی رمز می‌شوند و سرور بازشان نمی‌کند |
| کلید خصوصی gpg فقط روی کاغذ است | سرور نباید dumpهای خودش را باز کند (تصمیم مالک محصول، نوبت 4) | حدود 720 بایت متن؛ روش چاپ و آزمودن در runbook PR H |
| قطع مقصد SFTP آرشیو محلی را هم نگه می‌دارد | دو repo یعنی آرشیو منتظر هر دو می‌ماند (آزمایش 5) | `archive-push-queue-max`؛ هشدار روی سن `last_archived_time` |
| بازیابی از مقصد با 2.50 دو قدم است (دانلود، بعد restore) | restore مستقیم از SFTP با 2.50 شکست خورد | دو قدم اندازه‌گیری شد (7.7 ثانیه روی Mac)؛ runbook همین را می‌نویسد |
| بدون replica، خرابی سرور یعنی ساعت‌ها قطعی | سرور دوم نیست و replica روی همان سرور محافظت نمی‌کند | کپی بیرون از سرور + runbook بازسازی؛ شرط بازبینی در ADR |
| drill هر شب چند دقیقه CPU و I/O ساعت 3 صبح می‌گیرد | همان ساعتی است که dump هم اجرا می‌شود و ترافیک کم است | مدت در manifest ثبت و در صفحه دیده می‌شود |
| staging روی همان سرور منابع production را می‌خورد | ساده‌ترین راهی است که همان مسیر استقرار را تست می‌کند | staging با `WEB_CONCURRENCY=1`، provider `dev` برای SMS، کلید AI جدا |
| نقش برنامه هنوز می‌تواند جدول حذف کند | تفکیک کامل امروز مسیر restore پنل را می‌شکند | ADR جدا با شرط مشخص |
| بدون نقش فقط خواندنی برای گزارش‌گیری | `pg_read_all_data` دادهٔ نصب دیگر را هم می‌خواند؛ مصرف‌کنندهٔ مشخصی هم نیست | اگر لازم شد، فقط با دسترسی محدود به همان دیتابیس |

**چقدر برگشت‌پذیر است:** pgBackRest و آرشیو WAL برگشت‌پذیرند (خاموش کردن
`archive_mode` و حذف بسته). drill و staging کاملاً برگشت‌پذیرند. تنها تصمیم
یک‌طرفه‌تر، قاعدهٔ migration است، که آن هم فقط روی migrationهای آینده اثر دارد.

## 7. Alternatives Considered

| گزینه | چرا انتخاب نشد |
|---|---|
| wal-g به جای pgBackRest | فقط باینری GitHub؛ نصب و به‌روزرسانی در ایران ریسک دارد. مستند خودش repo روی همان دیسک را «برنامهٔ بازیابی نیست» می‌داند، پس به‌هرحال به مقصد بیرونی نیاز داریم |
| `archive_command` + `pg_basebackup` بدون ابزار | صفر وابستگی، ولی retention، پاک کردن WAL، `check` و restore به زمان را باید خودمان بنویسیم. به‌عنوان جایگزین (DF06) نگه داشته شد |
| Barman | برای سرور پشتیبان جدا طراحی شده؛ GPL 3؛ ما سرور دوم نداریم |
| هیچ کاری نکردن برای PITR | RPO 24 ساعت برای یک مشتری سازمانی با داده‌های ثبت‌نام و سرنخ فروش زیاد است؛ و دقیقاً همان چیزی است که ارزیاب رد کرد |
| تکیه بر snapshot ماشین مجازی (VMware) | سرور یک VM است (`deploy/README.md:194`، `:213`) و مالک محصول تأیید کرد VMware از آن snapshot می‌گیرد. ولی کجا و چند وقت نامعلوم است (سؤال باز). snapshot کل دیسک بدون هماهنگی با پستگرس فقط در حد «بعد از قطع برق» سازگار است، یک نصب را جدا برنمی‌گرداند، و ما آن را تست یا مانیتور نمی‌کنیم. جای PITR و drill را نمی‌گیرد و در هیچ عدد RPO یا RTO نیست |
| Google Drive با rclone (تصمیم نوبت 2، در نوبت 3 کنار گذاشته شد) | خود Google می‌نویسد دسترسی به برخی سرویس‌های کسب‌وکارش را در ایران محدود می‌کند (https://knowledge.workspace.google.com/admin/support/troubleshooting/countries-or-regions-where-google-workspace-is-available، «last updated 2026-09-30 UTC»). پس حساب یا client_id ممکن است با سیاست Google رد یا بعداً معلق شود، نه فقط با شبکه، و آن هم بعد از گذاشتن دادهٔ شخصی رمزشده آنجا. تست شبکه این را رد نمی‌کند |
| هدف `rsync:` موجود برای dump | به shell و برنامهٔ `rsync` روی مقصد نیاز دارد؛ یک حساب فقط-SFTP آن را رد کرد (`protocol incompatibility (code 2)`، آزمایش 5). برای مقصدی با shell هنوز کار می‌کند و حذف نمی‌شود |
| فقط repo2 بدون dump بیرونی | dump قالبی مستقل از pgBackRest است و هر نصب را جدا برمی‌گرداند؛ با ریسک نگهداری pgBackRest، یک راه دوم بیرون از سرور ارزش دارد |
| مقصد بیرونی بدون رمزنگاری | dumpها نام و شمارهٔ تلفن بازدیدکننده دارند. رد شد |
| جایگزین کردن `pg_dump` با pgBackRest | بازیابی یک نصب جدا از نصب دیگر و بازیابی از پنل ادمین از دست می‌رود |
| replica روی همان سرور | در برابر خرابی سرور و خطای انسانی محافظت نمی‌کند |
| replica روی سرور دوم با failover | سرور دوم وجود ندارد؛ هزینهٔ عملیاتی بالا برای یک نصب تک‌مشتری |
| drill با timer جدای systemd | الگوی دوم برای همان کار زمان‌بند موجود؛ روی نصب Docker کار نمی‌کند |
| drill در schema موقت داخل دیتابیس زنده | dump نام schemaهای `app` و `observability` را دارد و `pg_restore` نمی‌تواند آن‌ها را تغییر نام دهد؛ و restore کنار دادهٔ زنده خطر اشتباه دارد |
| drill در کانتینر Docker موقت | سرور production برای برنامه Docker ندارد؛ یک وابستگی تازه روی سرور |
| drill با گرفتن CREATEDB برای نقش برنامه | دسترسی بیشتر به نقشی که در معرض وب است، برای کاری که یک دیتابیس ثابت هم انجامش می‌دهد |
| staging با docker compose | شکل production نیست (بدون systemd، nginx، `padyar-deploy.sh`، نقش محدود) |
| PgBouncer | عددها نیاز نشان نمی‌دهند؛ با prepare خودکار psycopg 3 تداخل دارد |
| تفکیک کامل نقش‌ها الان | 36 DDL زمان اجرا و restore پنل را می‌شکند؛ به ADR جدا منتقل شد |

## 8. Remaining Risks / Unknowns

| مورد | وضعیت |
|---|---|
| مشخصات دیسک، CPU و حجم آزاد سرور | تأییدنشده. همهٔ زمان‌ها روی مک است |
| دسترسی به apt.postgresql.org و GitHub از سرور | تأییدنشده؛ فرض قرارداد |
| پشتیبانی همزمان repo1 و repo2 در pgBackRest 2.50 | **بسته شد.** از v2.33 (2021-04-05) وجود دارد (release notes). خود دو repo با هم تست نشد |
| `pg_dump --snapshot` همراه با شمارش در یک تراکنش | flag تأیید شد؛ کل زنجیره تست نشد |
| `statement_timeout = 30s` نقش روی `pg_restore` در drill | **بسته شد.** بازبین با timeout یک ثانیه تست کرد و آرشیو خودش timeout را صفر می‌کند؛ آزمایش 4 هم با timeoutهای واقعی `05` درست کار کرد |
| خواندن `pg_stat_archiver` با نقش برنامه | **بسته شد.** در آزمایش 4 خوانده شد |
| pgBackRest 2.50 اوبونتو با PostgreSQL 16 | **بسته شد.** آزمایش 4 (نوبت 1 فقط 2.59.1 را اجرا کرده بود) |
| مقصد SFTP | **وجود ندارد.** تا وقتی ساخته نشود، از دست رفتن سرور یعنی از دست رفتن همه‌چیز |
| علت شکست restore مستقیم از SFTP با 2.50 | **نامعلوم** (تصمیم مالک محصول، نوبت 4: همین‌طور ثبت شود). به یک فایل خاص گره نخورده: در اجراهای من روی `2602_vm.zst`، در بازتولید بازبین روی `pg_data/base/1/13450.gz`. `verify` و `sftp` معمولی فایل را خواندند؛ 2.59.2 موفق شد. راه دو قدمی اندازه‌گیری شد و انتخاب مالک محصول است |
| حالت async pgBackRest با دو repo هنگام قطع مقصد | تأییدنشده (آزمایش 5 حالت sync بود) |
| رمزنگاری dump با کلید عمومی gpg | **بسته شد** در آزمایش 6: سرور فقط با کلید عمومی رمز کرد و نتوانست باز کند؛ کلید گاوصندوق باز کرد، sha256 برابر |
| فرایند کلید کاغذی (چه کسی، کجا، چطور تایپ و آزموده می‌شود) | نوشته نشده؛ کار runbook PR H |
| snapshotهای VMware: کجا و چند وقت | VMware snapshot می‌گیرد (تأیید مالک محصول)؛ محل و مدت نامعلوم؛ در هیچ عددی استفاده نمی‌شود |
| رفتار `archive-push-queue-max` وقتی واقعاً پر شود | متن help در 2.50 خوانده شد؛ خود پر شدن تست نشد. در PR C تست شود |
| زنجیرهٔ کامل بازیابی یک نصب روی سرور، با ری‌استارت و بوت برنامه | اندازه‌گیری نشده؛ فقط 49.9 ثانیه روی Mac بدون بوت برنامه |
| هزینهٔ CPU فشرده‌سازی WAL روی سرور GPU | تأییدنشده |
| یک ردیف `app_logs` برای هر نوبت | فرض، برای برآورد ظرفیت |
| نسبت فشرده‌سازی 15 برابر dump | اندازه‌گیری روی دادهٔ مصنوعی؛ دادهٔ واقعی احتمالاً کمتر فشرده می‌شود |
| آیا اسکریپت root نصب‌شدهٔ `padyar-deploy` روی سرور با نسخهٔ مخزن یکی است | تأییدنشده؛ به سرور دست نزدم |
| سقف 300 ثانیه روی سرور واقعی کی می‌رسد | تخمین: restore حدود 2.2 GB، dump حدود 5 GB؛ اندازه‌گیری نشده روی سرور |
| `backup_outcome_total` برای هر worker جداست | **بسته شد روی main** (ADR-022 روی main، #156، #157): multiprocess، و شمارش در `_run_backup_now`. scraper و هشدار هنوز نیست |

## 9. Production Impact

خود این spike هیچ کد production ندارد. همهٔ آزمایش‌ها در کانتینرهای موقت بودند
و پاک شدند.

خروجی: **→ ADR** (ADR-028 در `docs/engineering/DECISIONS.md`، وضعیت Proposed).
شرط‌هایی که ADR را لازم کرد: یک وابستگی خارجی تازه با دسترسی کامل به cluster
(pgBackRest)، یک مرز اعتماد تازه (کاربر `postgres` در timerها)، و یک قاعدهٔ
عرضی تازه برای همهٔ migrationهای آینده (expand/contract). بعد از ADR، هر PR
زیر یک SPEC یا PLAN کوتاه خودش را دارد.

### فهرست کار (PRها، به ترتیب وابستگی، هر کدام یک علت ریشه‌ای)

«تغییر روی سرور» یعنی کاری روی gpuserver لازم است، که فقط با تأیید مالک محصول
انجام می‌شود. deploy معمولی کد از CI در این ستون حساب نمی‌شود.

| # | PR | علت ریشه‌ای | فایل‌هایی که لمس می‌کند | وابسته به | تغییر روی سرور |
|---|---|---|---|---|---|
| A | اصلاح مستندات و توضیح‌های غلط | اسناد و توضیح‌ها با کد نمی‌خوانند (SQLite در runbook، additive-only، `/health × 3`، sha قدیمی) | `docs/engineering/DEPLOYMENT_RUNBOOK.md`، `docs/engineering/DATABASE.md` (قاعدهٔ expand/contract)، `deploy/padyar-deploy.sh` (فقط توضیح) | هیچ | نه |
| B1 | شمارش ردیف در manifest | پشتیبان ثبت نمی‌کند چه چیزی در آن است | `app/services/pg_backup.py` (`create()` با snapshot صادرشده)، `tests/postgres/test_pg_backup_counts.py` | هیچ | نه |
| B2 | تمرین بازیابی شبانه | هیچ‌کس ثابت نمی‌کند پشتیبان‌ها قابل بازیابی‌اند | `app/services/restore_drill.py` (تازه)، `app/services/pg_backup.py` (`validate_restored_database` روی اتصال دلخواه؛ **سقف زمان restore و drill جدا از سقف dump و بزرگ‌تر**؛ بررسی فضای آزاد بر اساس اندازهٔ دیتابیس)، `app/services/backup.py` (قدم drill بعد از verify؛ تصمیم شمارش جدای پشتیبان‌های deploy در `prune`)، `deploy/05-create-databases.sh` (دیتابیس `_drill`)، `app/routers/backups.py`، `templates/admin/infra_backups.html` و JS آن، `app/services/metrics.py` (سه gauge با `multiprocess_mode`، الگوی `backup_last_success_timestamp_seconds`) و پر کردن آن‌ها در شروع برنامه از manifestها (مثل `pg_backup.seed_last_success_metric()`)، `tests/test_metrics_multiprocess.py`، `tests/postgres/test_restore_drill.py` (با نقش بدون CREATEDB که خود تست می‌سازد، تست منفی «دیتابیس زنده را هدف نگیر»، و تست سقف زمان)، `docs/engineering/MONITORING.md` | B1 | بله: ساخت `padyar_<slug>_drill` با superuser |
| C | آرشیو WAL و PITR | امروز هیچ بازیابی تا یک لحظه ممکن نیست | `deploy/55-pitr.sh` (تازه: نصب از apt اوبونتو، پیکربندی با `archive-push-queue-max`، `ALTER SYSTEM`، **restart کل cluster در ساعت آرام**، `stanza-create`، `check`، اولین full)، `deploy/systemd/padyar-pgbackrest-*.service/.timer` (تازه)، `deploy/pitr-restore-test.sh` (تازه: زنجیرهٔ کامل آزمایش 4 برای یک نصب، **با زمان هر مرحله و جمع**، نمونهٔ جدا با `archive_mode=off`، و بررسی اینکه نصب دیگر دست نخورده)، یک job در `.github/workflows/ci.yml` که این اسکریپت را در کانتینر **`ubuntu:24.04` با pgBackRest از apt اوبونتو** (همان منبع production) اجرا می‌کند، نمایش `pg_stat_archiver` (`failed_count`، `last_failed_time`، سن آخرین آرشیو) در `app/routers/backups.py` و `templates/admin/infra_backups.html`، `deploy/README.md`، runbook (مسیر دستی «پوشهٔ جدا، `pg_dump` یک نصب، توقف سرویس، `pg_restore` با نقش `padyar_<slug>`») | A | بله: نصب بسته، restart cluster، timerها |
| D | حالت rollback امن | rollback دستی به sha قدیمی کار نمی‌کند | `deploy/padyar-deploy.sh` (حالت rollback اورژانسی با بررسی ancestor و فهرست migrationهای جلوتر)، runbook (مسیر عادی: `git revert`، CI سبز، اپراتور sha تازه را مستقر می‌کند) | A | بله: نصب دوبارهٔ `/usr/local/bin/padyar-deploy` |
| E | staging | مسیر استقرار قبل از production تست نمی‌شود | **بدون job استقرار در CI** (job `deploy` روی main حذف شد و نباید برگردد)؛ `deploy/README.md` و runbook (قدم دستی: اپراتور اول `padyar-deploy staging` و بعد همان sha را روی production مستقر می‌کند)، `deploy/env/instance.env.template` (نکتهٔ staging: SMS `dev`، کلید AI جدا، `WEB_CONCURRENCY=1`)، اسکریپت کپی فقط محتوا (`dataset`، `questions`، `synonyms`، `settings` بدون اعتبارها)، runbook | D | بله: نصب slug تازه |
| F | جدایی اتصال بین دو نصب | هر نقش به دیتابیس نصب دیگر وصل می‌شود؛ فقط سد schema جلویش را می‌گیرد | `deploy/05-create-databases.sh` (`REVOKE CONNECT ON DATABASE padyar_<slug> FROM PUBLIC` و `GRANT CONNECT` به نقش خود نصب؛ **بدون** نقش فقط خواندنی و **بدون** `pg_read_all_data`)، `docs/engineering/SECURITY.md` | هیچ | بله: اجرای دوبارهٔ دستورها روی دیتابیس‌های موجود |
| H | کپی رمزشده به یک مقصد SFTP | امروز هیچ نسخه‌ای از سرور بیرون نمی‌رود | `deploy/55-pitr.sh` (repo2: `repo2-type=sftp`، `repo2-cipher-type=aes-256-cbc`، کلید SSH کاربر `postgres`، ثبت همهٔ کلیدهای میزبان مقصد)، `app/services/backup_offsite.py` (نوع هدف `sftp:` با argv ثابت، رمزنگاری با **کلید عمومی** gpg (`gpg -e -r <fingerprint>`) قبل از آپلود؛ سرور فقط کلید عمومی دارد، و prune کپی‌های قدیمی مقصد)، `app/config.py`، `.env.example`، `tests/test_backup_offsite.py`، و یک job در `.github/workflows/ci.yml` که در برابر یک **کانتینر SFTP موقت فقط-SFTP** (همان شکل آزمایش 5) این‌ها را ثابت می‌کند: آپلود رمزشده، خوانا نبودن مقصد بدون کلید، شکست با کلید غلط، و بازیابی کامل بعد از پاک کردن «سرور» با روش دو قدمی 2.50 (دانلود، بعد restore). صفحهٔ Backups: «هیچ نسخه‌ای بیرون از سرور نیست» تا وقتی مقصد تنظیم نشده. runbook: فرایند کلید کاغذی و بازیابی بعد از از دست رفتن سرور. **بدون rclone و بدون Google Drive.** | C | بله، ولی فقط وقتی مقصد SFTP ساخته شد: کلید SSH، کلید رمز، پیکربندی repo2 و `OFFSITE_BACKUP_TARGET`. تا آن روز روی سرور خالی می‌ماند |
| G | RPO و RTO و ظرفیت | هیچ هدف نوشته‌شده‌ای نیست | `docs/engineering/DEPLOYMENT_RUNBOOK.md` (یا یک فایل تازه در `docs/engineering/`)، با عددهای واقعی drill، زنجیرهٔ PR C روی سرور، و وضعیت PR H | B2، C، H | نه |

خارج از این track: هشدار روی متریک‌ها (پشتهٔ پایش، ADR-024 روی main). شکل
multiprocess متریک‌ها روی main حل شده است (ADR-022 روی main).

### سؤال‌های پاسخ‌داده (مالک محصول در گفتگو تأیید کرد، 2026-09-30 و 2026-10-01)

1. هدف‌های RPO و RTO بخش 6: پذیرفته شد، همان‌طور که نوشته شده.
2. staging: فقط محتوا، بدون دادهٔ شخصی بازدیدکننده.
3. `OFFSITE_BACKUP_TARGET`: روی سرور تنظیم نیست. مقصد دلخواه در نوبت 2: Google Drive (در نوبت 3 عوض شد، پایین).
4. pgBackRest: از apt اوبونتو (2.50).

### سؤال‌های پاسخ‌داده در نوبت 3 (مالک محصول در گفتگو تأیید کرد، 2026-10-01)

5. مقصد بیرونی: SFTP، نه Google Drive. هنوز سرور SFTP نیست.
6. WAL هم در PR H بیرون می‌رود.
7. رمزنگاری قبل از آپلود؛ کلید روی کاغذ در گاوصندوق.
8. VMware snapshot می‌گیرد.

### سؤال‌های پاسخ‌داده در نوبت 4 (مالک محصول در گفتگو تأیید کرد، 2026-10-02)

9. pgBackRest همان 2.50 اوبونتو، با بازیابی دو قدمی از SFTP. علت شکست restore
   مستقیم «نامعلوم» می‌ماند. (سؤال باز 4 نوبت 3 بسته شد.)
10. dumpها با کلید عمومی gpg؛ کلید خصوصی فقط روی کاغذ. رمز repo2 متقارن، روی سرور
    در فایل `0600` و یک نسخهٔ کاغذی. (سؤال باز 3 نوبت 3 بسته شد.)

### سؤال‌های باز برای مالک محصول

1. snapshotهای VMware کجا نگه داشته می‌شوند و برای چند وقت؟
2. **هدف 5 دقیقه برای RPO از دست رفتن سرور را تأیید می‌کنید؟** این عدد از تصمیم
   «WAL هم در PR H بیرون برود» نتیجه گرفته شده، ولی شما آن را نگفته‌اید (بخش 6،
   بند 5). این عدد وعده به مشتری می‌شود.
3. مقصد SFTP کجا و کی ساخته می‌شود؟ تا آن روز، از دست رفتن سرور یعنی از دست
   رفتن همه‌چیز.
4. کلیدهای کاغذی (رمز repo2 و کلید خصوصی gpg) دست چه کسی است، و هر چند وقت یک
   بار تایپ و آزموده می‌شوند؟

## 10. Related Artifacts

- PRD: ندارد
- Spec: ندارد (هر PR بالا SPEC یا PLAN کوتاه خودش را می‌گیرد)
- ADR: ADR-028 در `docs/engineering/DECISIONS.md` (Proposed)
- Plan: ندارد
- اسناد مرتبط: `docs/engineering/DATABASE.md`، `docs/engineering/DEPLOYMENT_RUNBOOK.md`،
  `docs/engineering/MONITORING.md`
- خروجی آزمایش‌ها: پیوست الف همین سند

## واژه‌نامه

- **PITR:** بازیابی دیتابیس به وضعیت یک لحظهٔ مشخص، نه فقط به زمان آخرین پشتیبان.
- **WAL:** فایل‌های گزارش تغییرات پستگرس. آرشیو آن‌ها PITR را ممکن می‌کند.
- **cluster:** یک نمونهٔ در حال اجرای پستگرس، با همهٔ دیتابیس‌هایش.
- **RPO:** بیشترین مقدار داده‌ای که در یک حادثه از دست می‌رود، به زمان.
- **RTO:** بیشترین زمانی که سرویس بعد از حادثه قطع می‌ماند.
- **drill:** تمرین منظم بازیابی واقعی یک پشتیبان، برای اثبات اینکه قابل بازیابی است.
- **expand/contract:** اول کد را طوری مستقر کن که دیگر از ستون استفاده نکند، و در یک deploy بعدی ستون را حذف کن.
- **SFTP:** انتقال فایل روی SSH. یک «حساب فقط-SFTP» فایل می‌گیرد ولی shell نمی‌دهد.

## پیوست الف: دستورها و خروجی پشت هر عدد

همه روی Mac توسعه (Apple M3، 16 GB، Docker 29.8.0) در کانتینرهای موقت
`dbmat-t1-*`، **نه روی سرور**. کانتینرها بعد از کار پاک شدند. اسکریپت‌ها موقت
بودند و وارد مخزن نشدند؛ قسمت‌های اصلی آن‌ها اینجا آمده است.

### الف.1: نقش و دیتابیس مثل `05` (آزمایش‌های 1 و 2)

```bash
docker run -d --name dbmat-t1-pg -e POSTGRES_PASSWORD=su_pw -v "$PWD/migrations:/migrations:ro" postgres:16 \
  -c wal_level=replica -c archive_mode=on -c "archive_command=/bin/true"
# as postgres:
CREATE ROLE padyar_demo WITH LOGIN PASSWORD 'demo_pw';
ALTER ROLE padyar_demo NOSUPERUSER NOCREATEDB NOCREATEROLE;   -- the two timeouts of 05:52-53 were NOT set here
CREATE DATABASE padyar_demo OWNER padyar_demo ENCODING 'UTF8' TEMPLATE template0 LC_COLLATE 'C.UTF-8' LC_CTYPE 'C.UTF-8';
CREATE SCHEMA app AUTHORIZATION padyar_demo; CREATE SCHEMA observability AUTHORIZATION padyar_demo;
# then every file in migrations/ with: psql -U padyar_demo -d padyar_demo -v ON_ERROR_STOP=1 -1 -f <file>
```
خروجی: `PostgreSQL 16.15 (Debian 16.15-1.pgdg13+2)`، و `app|37`، `observability|4` جدول.

داده: `generate_series` با متن فارسی و `md5(g::text)`؛ 2,000,000 ردیف
`app.messages`، 1,000,000 `app.chat_logs`، 1,000,000 `observability.app_logs`،
200,000 `conversations`، 100,000 `visitors`، 3,000 `companies`، 500 `dataset`.
خروجی: `database size: 1929 MB`؛ اندازه‌ها و بایت در هر ردیف از
`pg_total_relation_size(c.oid)/c.reltuples` (جدول آزمایش 1).

### الف.2: `pg_dump` (آزمایش 1)

```bash
time pg_dump --host 127.0.0.1 --port 5432 --username padyar_demo --dbname padyar_demo \
  --format custom --compress 6 --no-owner --no-privileges --file /tmp/bk/padyar.dump
```
```
real	1m49.503s
-rw-r--r-- 1 root root 131113223 Sep 30 00:14 /tmp/bk/padyar.dump
232            # pg_restore --list | grep -vc "^;"
```

### الف.3: drill با نقش بدون CREATEDB (آزمایش 2)

```bash
# as postgres, once (install time):
CREATE DATABASE padyar_demo_drill OWNER padyar_demo ENCODING 'UTF8' TEMPLATE template0 LC_COLLATE 'C.UTF-8' LC_CTYPE 'C.UTF-8';
# as padyar_demo:
psql -U padyar_demo -d padyar_demo -c "CREATE DATABASE x"
time pg_restore --host 127.0.0.1 --username padyar_demo --dbname padyar_demo_drill \
  --clean --if-exists --no-owner --no-privileges --single-transaction /tmp/bk/padyar.dump
time pg_restore ... --jobs 4 /tmp/bk/padyar.dump          # same flags without --single-transaction
psql -U padyar_demo -d padyar_demo_drill -c "DROP SCHEMA app CASCADE; DROP SCHEMA observability CASCADE;"
```
```
ERROR:  permission denied to create database
real	4m22.675s                                   # single transaction
padyar_demo|2000000|1000000|1000000|3000|0|        # messages, chat_logs, app_logs, companies
padyar_demo_drill|2000000|1000000|1000000|3000|0|
real	3m2.931s                                    # --jobs 4
drill db after cleanup: 8655 kB
```
دو ستون آخر خالی‌اند چون اینجا migrationها با `psql` اجرا شدند، نه
`apply_migrations.py`. آزمون جدا با خود `apply_migrations.py`:
```
DATABASE_URL=postgresql://padyar_demo:demo_pw@127.0.0.1:55439/padyar_demo .venv/bin/python scripts/apply_migrations.py
applied 29 migration(s)
padyar_demo|29|626f76f120530b742904a127ddc0d14c
padyar_demo_drill|29|626f76f120530b742904a127ddc0d14c
```

### الف.4: PITR با 2.59.1 از PGDG (آزمایش 3)

```bash
docker run -d --name dbmat-t1-pitr -e POSTGRES_PASSWORD=su_pw postgres:16 -c wal_level=replica -c archive_mode=on \
  -c "archive_command=pgbackrest --stanza=main archive-push %p" -c archive_timeout=60
apt-get install -y pgbackrest        # -> pgBackRest 2.59.1
# /etc/pgbackrest/pgbackrest.conf: repo1-path=/var/lib/pgbackrest, repo1-retention-full=2, start-fast=y, compress-type=zst, pg1-path=/var/lib/postgresql/data
pgbackrest --stanza=main stanza-create && pgbackrest --stanza=main check
time pgbackrest --stanza=main --type=full backup
time pgbackrest --stanza=main --pg1-path=/tmp/restore --type=time "--target=$T" --target-action=promote restore
pg_ctl -D /tmp/restore -o "-p 5433 -c archive_mode=off" -w start
```
```
cluster size: 500 MB
real	0m33.365s                     # full backup
repo1: backup set size: 31.2MB
real	0m37.954s                     # restore to T
real	0m12.433s                     # side start
A rows=401000 after-backup=1000
B rows=400500 after-T=0           # B's legitimate write after T is NOT in the side copy
LOG:  recovery stopping before commit of transaction 750
```

### الف.5: Ubuntu noble، pgBackRest 2.50، زنجیرهٔ کامل یک نصب (آزمایش 4)

```bash
docker run -d --name dbmat-t1-noble ubuntu:24.04 sleep infinity
apt-get install -y postgresql-16 pgbackrest
# -> pgbackrest 2.50-1build2, postgresql-16 16.15-0ubuntu0.24.04.1
# /etc/pgbackrest.conf adds: archive-push-queue-max=1GiB, pg1-path=/var/lib/postgresql/16/main
ALTER SYSTEM SET wal_level='replica'; ALTER SYSTEM SET archive_mode='on';
ALTER SYSTEM SET archive_command='pgbackrest --stanza=main archive-push %p'; ALTER SYSTEM SET archive_timeout='60';
time pg_ctlcluster 16 main restart
# padyar_a and padyar_b exactly like 05, including:
ALTER ROLE padyar_a SET statement_timeout = '30s'; ALTER ROLE padyar_a SET idle_in_transaction_session_timeout = '60s';
psql -U padyar_a -d padyar_a -c "select archived_count>0, failed_count, last_archived_time is not null from pg_stat_archiver"
# recovery chain for install A:
pgbackrest --stanza=main --pg1-path=/var/lib/postgresql/side --type=time "--target=$T" --target-action=promote restore
pg_ctl -D /var/lib/postgresql/side -w -o "-c config_file=/etc/postgresql/16/main/postgresql.conf \
  -c data_directory=/var/lib/postgresql/side -c hba_file=/etc/postgresql/16/main/pg_hba.conf \
  -c ident_file=/etc/postgresql/16/main/pg_ident.conf -c port=5433 -c archive_mode=off \
  -c external_pid_file=/tmp/side.pid" start
pg_dump --port 5433 --username padyar_a --dbname padyar_a --format custom --compress 6 --no-owner --no-privileges --file /tmp/a_pitr.dump
pg_dump --port 5432 --username padyar_a --dbname padyar_a ... --file /tmp/a_safety.dump
pg_restore --port 5432 --username padyar_a --dbname padyar_a --clean --if-exists --no-owner --no-privileges --single-transaction /tmp/a_pitr.dump
pg_ctl -D /var/lib/postgresql/side -m fast stop; rm -rf /var/lib/postgresql/side
```
```
real	0m2.582s                          # cluster restart for archive_mode
t|0|t                                    # app role reads pg_stat_archiver
real	0m26.391s                         # full backup, database size 500.4MB, backup set 31.1MB
archive_mode = 'on'                      # copied into the side dir's postgresql.auto.conf
archive_command = 'pgbackrest --stanza=main archive-push %p'
step 1 side restore:      24.9 s
step 2 side start+replay: 9.3 s
step 3 dump A from side:  3.2 s
step 4 safety dump live A:0.9 s
step 5 restore into A:    9.6 s
step 6 stop+remove side:  1.9 s
TOTAL (no app restart):   49.9 s
live A rows=401000 after-backup=1000
live B rows=400501 after-T kept=1
live archiver after chain: failed=0
```
و متن `pgbackrest help archive-push archive-push-queue-max` در 2.50:
```
pgBackRest 2.50 - 'archive-push' command - 'archive-push-queue-max' option help
* pgBackRest will notify PostgreSQL that the WAL was successfully archived,
then DROP IT.
If this occurs then the archive log stream will be interrupted and PITR will
not be possible past that point. A new backup will be required to regain full
restore capability.
```

### الف.6: یک flag و یک شمارش

```
docker run --rm postgres:16 pg_dump --help | grep -i snapshot
  --snapshot=SNAPSHOT          use given snapshot for the dump
grep -rn "CREATE TABLE IF NOT EXISTS\|CREATE INDEX IF NOT EXISTS\|ALTER TABLE" app/services/*.py app/db/*.py | grep -v "^app/db/connection.py" | wc -l
36
```

### الف.7: SFTP، رمزنگاری، و از دست رفتن سرور (آزمایش 5)

```bash
# destination: an SFTP-only account
Match User pgrepo
    ChrootDirectory /srv/sftp/pgrepo
    ForceCommand internal-sftp
    AllowTcpForwarding no
    PasswordAuthentication no
# /etc/pgbackrest.conf on the db host (0600, owner postgres)
repo1-path=/var/lib/pgbackrest
repo2-type=sftp
repo2-path=/upload/pgbackrest
repo2-sftp-host=dbmat-t1-sftp
repo2-sftp-host-user=pgrepo
repo2-sftp-private-key-file=/var/lib/postgresql/.ssh/id_ed25519
repo2-sftp-public-key-file=/var/lib/postgresql/.ssh/id_ed25519.pub
repo2-sftp-host-key-hash-type=sha1
repo2-cipher-type=aes-256-cbc
repo2-cipher-pass=<the key on paper>
archive-push-queue-max=1GiB
time pgbackrest --stanza=main --repo=2 --type=full backup
```
```
gnupg 2.4.4-2ubuntu17.6 / openssh-client 1:9.6p1-3ubuntu13.19 / pgbackrest 2.50-1build2 / postgresql-16 16.15-0ubuntu0.24.04.1 / rsync 3.2.7-1ubuntu1.5
real	0m5.792s
    cipher: mixed
        repo1: none
        repo2: aes-256-cbc
            repo2: backup set size: 7.9MB, backup size: 7.9MB
0000000   S   a   l   t   e   d   _   _ ...      # first bytes of a WAL file at the destination
0                                               # files at the destination containing "0912000"
```
قطع مقصد (`pkill -x sshd` روی مقصد، سه `pg_switch_wal()`، 20 ثانیه صبر):
```
before:  archived=11 failed=0 last_archived=000000010000000000000008.00000028.backup pg_wal_files=8 ready=0
outage:  archived=11 failed=0 last_archived=000000010000000000000008.00000028.backup pg_wal_files=8 ready=3
        wal archive min/max (16): 000000010000000000000001/000000010000000000000009     # repo1 (local) did not move
after:   archived=15 failed=0 last_archived=00000001000000000000000D pg_wal_files=8 ready=0
```
از دست رفتن سرور (پاک کردن پوشهٔ داده، repo1، `pgbackrest.conf`، کلید SSH؛ کلید
SSH تازه روی مقصد؛ پیکربندی تازه فقط با repo از نوع sftp و کلید تایپ‌شده):
```
source: a=500000 b=200001
wrong key typed:  ERROR: [075]: no backup set found to restore
2.50 direct:      ERROR: [041]: raised from local-1 protocol: unable to open file '/upload/pgbackrest/backup/main/20261001-165010F/pg_data/base/16398/2602_vm.zst' for read: libssh2 error [-31]: sftp error [4]
                  [FileOpenError] on 2 retries from 1-15013ms ...
                  real 0m40.474s
pgbackrest verify (2.50, same sftp repo):  no warnings, real 0m1.955s
2.59.2 (PGDG) direct:  real 0m3.142s ; a=500000 b=200001 last-before-loss=1
2.50 two steps:   download: 1.3 s (42M); restore (2.50, posix): 3.0 s; start+replay: 3.4 s; total: 7.7 s
                  a=500000 b=200001 last-before-loss=1
```
مسیر dump:
```
rsync -a --chmod=F600 /tmp/padyar.dump pgrepo@dbmat-t1-sftp:/upload/
rsync error: protocol incompatibility (code 2) at compat.c(622) [sender=3.2.7]
gpg --batch --pinentry-mode loopback --passphrase-file /tmp/pass --symmetric --cipher-algo AES256 -o padyar.dump.gpg padyar.dump
0                                   # phone numbers found in the encrypted file
sha256 before: 69c2cf8142634f85     sha256 after sftp put/get + decrypt: 69c2cf8142634f85
wrong key: gpg: decryption failed: Bad session key
```

### الف.8: کلید عمومی gpg برای dump (آزمایش 6)

```bash
docker run -d --name dbmat-t1-gpg ubuntu:24.04 sleep infinity && apt-get install -y gnupg
# vault (offline): main key cert-only, subkey encrypt-only
export GNUPGHOME=/tmp/vault
gpg --batch --pinentry-mode loopback --passphrase "" --quick-gen-key "padyar-backup" ed25519 cert never
gpg --batch --pinentry-mode loopback --passphrase "" --quick-add-key "$FPR" cv25519 encr never
gpg --batch --armor --export "$FPR" > /tmp/pub.asc
# server: public key only
export GNUPGHOME=/tmp/server; gpg --batch --import /tmp/pub.asc
gpg --batch --trust-model always -r "$FPR" -e -o /tmp/pk.gpg /tmp/padyar.dump
gpg --batch -d /tmp/pk.gpg                       # on the server
GNUPGHOME=/tmp/vault gpg --batch -d /tmp/pk.gpg  # with the vault key
```
```
gpg (GnuPG) 2.4.4
source sha: 69c2cf8142634f85
secret key export size (bytes): 719
0                                    # secret keys on the server
encrypted on server with public key only
phone numbers in encrypted file: 0
server decrypt rc=2 gpg: public key decryption failed: No secret key
decrypted sha: 69c2cf8142634f85
```
