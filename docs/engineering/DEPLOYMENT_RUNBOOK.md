# راهنمای استقرار (Runbook)

> **استقرار روی سرور اوبونتو ۲۴.۰۴ (دو نصب + TTS):** اسکریپت‌ها و پیکربندی
> آماده در `deploy/` قرار دارند و `deploy/README.md` ترتیب اجرا را توضیح
> می‌دهد. آن مسیر شامل nginx، systemd، PostgreSQL، گواهی SSL از طریق
> Cloudflare DNS-01، و سرویس تبدیل متن به گفتار روی Tesla P40 است.

## استقرار تازه

```bash
pip install -r requirements.txt
cp .env.example .env        # سپس مقادیر واقعی: OPENAI_API_KEY، SECRET_KEY، ADMIN_*، ALLOWED_ORIGINS، COOKIE_SECURE=true
python main.py              # dev — یا در تولید:
gunicorn -k uvicorn.workers.UvicornWorker -w 4 -b 127.0.0.1:8000 app.main:app
```

 Docker: `docker compose up -d` (Dockerfile و docker-compose.yml موجود است).

## استقرار و به‌روزرسانی production (gpuserver)

نصب اولیه با `deploy/` (README همان پوشه). استقرار خودکار وجود ندارد. CI فقط
بررسی‌ها را روی runnerهای GitHub اجرا می‌کند و هیچ کاری روی سرور production
انجام نمی‌دهد. بعد از merge به `main` و سبز شدن CI، اپراتور روی سرور دستی
مستقر می‌کند:

```bash
sudo PADYAR_GIT_TOKEN=<توکن فقط-خواندنی> /usr/local/bin/padyar-deploy myevent 8010 <sha>
```

`myevent 8010` همان slug و `APP_PORT` نصب است. مخزن خصوصی است، پس fetch یک
اعتبار لازم دارد: یک توکن fine-grained فقط-خواندنی روی همین مخزن، یا یک deploy
key فقط-خواندنی برای کاربر اپ (آن‌وقت `PADYAR_GIT_TOKEN` لازم نیست). مراحل آن اسکریپت، به
ترتیب: پشتیبان pg_dump → checkout کد جدید (پروسهٔ قدیم هنوز سرو می‌کند) →
`pip install` → `apply_migrations.py` (هر فایل یک تراکنش) → ری‌استارت →
بررسی `/api/health` تا ۱۲ تلاش × ۵ ثانیه (یک دقیقه کامل؛ بوت با بارگذاری
dataset و ایندکسِ بازیابی کند است و پنجرهٔ کوتاه قبلاً کد سالم را
برمی‌گرداند — رخداد ۲۰۲۶-۰۸-۲۷).

**بازگشت کد خودکار است:** سلامت نرسید → reset به commit قبلی + ری‌استارت.
**بازگشت دیتابیس خودکار نیست و نباید باشد:** مهاجرت‌های این پروژه تا امروز
فقط افزوده‌اند و کد قدیمی جداول ناشناخته را نادیده می‌گیرد؛ اگر روزی
مهاجرتی مخرب رسید، بازگردانی از پشتیبانِ قبل از استقرار یک اقدام دستی و
مصرحانه از پنل ادمین است (زیرساخت → پشتیبان‌ها).

**در زمان رویداد مستقر نکنید.** دو اجرای هم‌زمان با یک قفل روی سرور
(`/run/padyar-deploy-myevent.lock`) پشت‌سرهم می‌شوند؛ اجرای دوم تا ۱۵ دقیقه
منتظر می‌ماند و اولی را له نمی‌کند.

**«SUPERSEDED» در لاگ خطا نیست:** اگر `main` از `<sha>` داده‌شده جلوتر رفته
باشد، اسکریپت چیزی را تغییر نمی‌دهد و با پیام SUPERSEDED و کد موفق تمام
می‌شود. دوباره با sha جدیدتر اجرا کنید.

**چرا استقرار خودکار نداریم:** تا ۲۰۲۶-۱۰ یک job به نام `deploy` در
`.github/workflows/ci.yml` روی runner خودمیزبان همین سرور اجرا می‌شد. حذف شد
تا هیچ job ی از CI روی سرور production اجرا نشود.

تغییر خودِ اسکریپت deploy: فایل روی سرور root-owned است و از مخزن جدا نصب
شده؛ بعد از merge، یک بار روی سرور:

```bash
sudo install -m 0755 -o root -g root \
  /opt/padyar-myevent/deploy/padyar-deploy.sh /usr/local/bin/padyar-deploy
```

**خواندن PDF در ماژول `ingest`:** اسکریپت راه‌اندازی `poppler-utils` را نصب می‌کند، اما روی سرورهای موجود یک بار `sudo apt-get install -y poppler-utils` لازم است؛ تا آن موقع آپلود PDF با پیام «خواندن PDF روی این سرور فعال نیست» رد می‌شود و بقیهٔ قالب‌ها کار می‌کنند.

## بررسی سلامت
- Liveness: `GET /api/health` (ارزان، بدون فراخوانی خارجی)
- Readiness: `GET /api/ready` — تا آماده‌شدن ایندکس بازیابی 503 می‌دهد؛
  `?deep=true` سرویس خارجی AI را هم می‌سنجد (فقط دستی/مانیتورینگ کم‌بسامد).
- لاگ ساخت‌یافته: `LOG_FORMAT=json` در env.

## به‌روزرسانی دانش
```bash
# ویرایش دانش از پنل ادمین (dataset + questions + synonyms) یا
# به‌روزرسانی app/default_content.py برای مقادیر پیش‌فرض بسته‌بندی‌شده
python3 scripts/reset-content-to-defaults.py   # پشتیبان خودکار + seed جدید
# ری‌استارت سرویس تا ایندکس بازسازی شود
```

## پشتیبان و بازیابی
- خودکار: زمان‌بندی پنل ادمین (app/services/backup.py) → هر dump در
  `backups/postgres/<backup_id>/` (فرمت `pg_dump --format=custom`، `app/services/pg_backup.py`)
- کپی خارج از سرور (2026-09-14): اگر `OFFSITE_BACKUP_TARGET` در env تنظیم
  شده باشد، هر dump که verify موفق داشته باشد خودکار به مقصد دوم کپی
  می‌شود — `rsync:user@host:/path` (با `rsync -a --chmod=F600` روی ssh؛
  کلید بدون رمز لازم است چون ساعت ۳ صبح بدون تعامل انسانی اجرا می‌شود)
  یا `dir:/mnt/...` (مسینت از قبل mount شده). نتیجهٔ هر کپی در بلوک
  `offsite` همان manifest.json ثبت می‌شود. شکست کپی هرگز پشتیبان محلی را
  باطل نمی‌کند؛ فقط رخداد `backup.offsite.failed` در لاگ سرویس ثبت می‌شود
  — هر شب مقصد را از روی همین رخداد ببینید، نه با فرض.
- پیش از هر استقرار: `deploy/padyar-deploy.sh` (گام ۱) قبل از هر تغییر یک dump
  با `reason=deploy` می‌گیرد. اگر dump شکست بخورد، استقرار هیچ چیز را تغییر نمی‌دهد.
- دستی: پنل ادمین → Infrastructure → Backups → ساخت پشتیبان.
- بازیابی (پایگاه داده PostgreSQL 16 است، نه SQLite): پنل ادمین → Infrastructure →
  Backups → بازیابی. باید دقیقاً `RESTORE BACKUP <id>` تایپ شود
  (`app/services/pg_backup.py` تابع `restore()`). خود برنامه این مراحل را انجام می‌دهد:
  تأیید سلامت dump، روشن‌کردن حالت تعمیر، ساخت پشتیبان ایمنی از وضعیت فعلی،
  `pg_restore --single-transaction`، اعتبارسنجی. شناسهٔ پشتیبان ایمنی در نتیجه
  برمی‌گردد. اگر چند پروسه اجرا می‌شود، بعد از بازیابی بقیه را ری‌استارت کنید.
  سپس `/api/ready` و شمارش dataset در `/api/health` را بررسی کنید.

## بازگشت (Rollback)
- بازگشت دانش (reset): `scripts/reset-content-to-defaults.py` پیش از هر کار با
  `pg_backup.create(reason="reset-content")` یک dump می‌گیرد
  (`scripts/reset-content-to-defaults.py:80`). بازگشت = همان مسیر بازیابی بالا
  با آن dump.
- بازگشت کد بعد از استقرار قرمز: خودکار است. اگر `/api/health` پس از ری‌استارت
  سالم نشود، `padyar-deploy.sh` کد را به commit قبلی برمی‌گرداند. دیتابیس برنگردانده
  می‌شود (گام ۶ در اسکریپت).
- بازگشت دستی کد: `git revert` روی `main`، سبز شدن CI، سپس اپراتور با sha جدید
  (همان commit برگردان) `padyar-deploy` را دستی اجرا می‌کند. صدا زدن
  اسکریپت با sha قدیمی بازگشت نیست: برای هر sha که نوک `main` نباشد با پیام
  `SUPERSEDED` بدون تغییر خارج می‌شود (`deploy/padyar-deploy.sh`، بررسی `FETCHED`).
- اگر استقرارِ برگشت‌خورده migration مخرب داشت (مثل `0028`)، `git revert` به‌تنهایی
  ستون یا جدول حذف‌شده را برنمی‌گرداند. dump گام ۱ همان استقرار لازم است (مسیر
  بازیابی بالا).

## عیب‌یابی سریع
| علامت | اقدام |
|---|---|
| /api/ready → 503 | لاگ «Embedding backend init failed» را ببین؛ بازیابی به BM25 واژگانی برمی‌گردد؛ model2vec و data/models را بررسی کن |
| 503 از /chat | سرویس خارجی AI در دسترس نیست و تطبیق محلی قوی وجود ندارد — کلید/base را در پنل ادمین بررسی کن |
| پاسخ‌های قدیمی پس از تغییر تم/CSS | cache-buster خودکار است؛ hard-refresh مرورگر |
| قفل «database is locked» | WAL فعال است؛ اگر تکرار شد پروسه‌های موازی نویسنده را بررسی کن |

## Speech-to-text credentials (since the AI Control Plane landed)

Transcription resolves its key through the AI Control Plane, not the legacy
`ai_api_key` setting. Resolution order (`app/services/ai/stt.py`):

1. An explicit binding, `ai_stt_provider_instance_id`, if set.
2. Otherwise the single enabled, secret-bearing, STT-capable provider instance.
   This is why rotating the key in **Admin → AI → Providers** now fixes voice
   as well as chat, with no extra configuration.
3. Only if neither exists, the legacy `ai_api_base` / `ai_api_key` settings —
   a compatibility path for an install that never migrated.

Only OpenAI-shaped providers serve `/audio/transcriptions`, so binding is
restricted to `openai` and `openai_compatible` (`STT_CAPABLE_TYPES`). Anthropic
and Gemini are not eligible and must not be listed as if they were.

The transcription model is `ai_model_stt` (default `whisper-1`), still editable
under Settings → AI.

## Model selection is under AI → Routing

Settings → AI no longer offers "chat model" / "classification model" inputs.
Those wrote `ai_model_chat` / `ai_model_classify`, which the runtime stopped
reading when routing moved to the Control Plane — the form reported success and
changed nothing. Per-task model and provider order now live in
**AI → Routing**. The endpoint still accepts the old fields so a cached admin
page does not break, but no longer persists them.

## Duplicate dataset id

`POST /admin/api/dataset` with an existing id returns **409 Conflict** (it
returned 400 before, and 500 on PostgreSQL). The existing row is never
overwritten. Backend-neutral detection lives in `app/db/dberrors.py`.

## Running the PostgreSQL integration tests

The default suite runs on SQLite for speed. Production-critical PostgreSQL
tests run in CI on every push (the `postgres-tests` job) and are also
available locally, opt-in:

```bash
RUN_POSTGRES_TESTS=1 .venv/bin/python -m pytest tests/postgres -q
```

## Environment marker and the production gate

`PADYAR_ENV` (`development` | `staging` | `production`) is the only thing that
decides whether the production configuration gate runs. It is deliberately
independent of every setting the gate checks.

A production install **refuses to start** on: `COOKIE_SECURE` not true ·
non-PostgreSQL backend · passwordless or placeholder `DATABASE_URL` · empty or
`*` `ALLOWED_ORIGINS` · `OTP_DELIVERY=dev` · placeholder `ADMIN_PASSWORD`. The
refusal names every problem at once and never prints a value.

Staging evaluates the same rules and logs what would block, but boots.
Development skips them. An unrecognised value is an error, not a fallback.

Warnings (never fatal): unpinned `SECRET_KEY` · remote DSN without `sslmode` ·
`DB_POOL_MAX_SIZE × WEB_CONCURRENCY` above the connection budget · placeholder
`visit-taxonomy.json`.

`SECRET_KEY` is generated and persisted in `settings.app_secret_key`, so it does
not rotate on restart. Pin it explicitly before running a second host or
rebuilding the database — otherwise each host mints its own and stored `enc:`
secrets (provider keys, SMS credentials) stop decrypting.

## Provider endpoint security

Provider base URLs are validated by `app/services/ai/endpoint_policy.py` under
two trust classes: `public` (https, public addresses only) and `internal`
(privileged; permits RFC1918 and loopback, and plain http, for on-prem Ollama /
vLLM / LiteLLM servers).

**Cloud instance metadata is denied in every trust class**, by an explicit list
checked before any trust-class branch: `169.254.169.254` (AWS/Azure/GCP),
`169.254.170.2` (ECS task role), `fd00:ec2::254` (IMDSv2 over IPv6), plus three
that live outside link-local space and each escaped by a different route —
`100.100.100.200` (Alibaba, CGNAT, not reported private, so reachable from
*both* classes), `192.0.0.192` (Oracle OCI, reported private, so `public`
refused it but `internal` did not) and `168.63.129.16` (Azure WireServer,
reported *global*, so it looked like ordinary internet to both).

The list is enumerated rather than derived from address class, so it needs a new
entry when a cloud adds an endpoint. Ordinary CGNAT is NOT banned: blocking
100.64/10 to stop one address would break on-prem installs that use it.

**DNS rebinding is closed by pinning.** `endpoint_policy.pin()` resolves once,
validates *every* answer, and the adapter connects to those exact IPs while
sending `Host:` (the original host **and port**) and TLS `sni_hostname` (the
original hostname) — so SNI and certificate verification are unchanged. TLS
verification is never disabled. Redirects are not followed
(`follow_redirects=False`); honouring one must go through
`assert_safe_redirect()`.

Pinning must not cost the address fallback. `pin()` returns `connect_urls` —
every validated address in resolution order — and the adapter tries them in
turn, because `localhost` resolves to `['::1', '127.0.0.1']` and an Ollama
server bound to 127.0.0.1 (its default) is unreachable if only the first is
tried. The resolver call runs in a worker thread; on the event loop a slow DNS
server would stall every concurrent request in the process.

## Circuit-breaker observability

State changes emit `llm.circuit.opened`, `llm.circuit.half_open` and
`llm.circuit.closed` through `applog`. Transitions only — a successful request
on an already-closed circuit logs nothing — and the half-open event is emitted
by the worker that wins the probe lease, so racing workers cannot each log the
same transition. The recovery event is gated the same way — on the conditional
UPDATE's `rowcount`, not on a pre-read — because two workers whose probes both
succeed would otherwise each report a recovery that happened once. That race is
only reproducible on PostgreSQL (SQLite has a single writer), so it is pinned by
`tests/postgres/test_circuit_recovery_concurrency.py`.

A failing probe (`half_open → open`) is logged too: without it an operator sees
`half_open` and then silence, which is indistinguishable from a probe still in
flight. All circuit logging is best-effort — a logging failure must never stop
a circuit from opening or recovering.

## Restore drill

After each scheduled backup is made and verified, the app restores that backup
into a separate database called `padyar_<slug>_drill`. It checks the result,
then empties the drill database again. By default this is once a day.

Why: `verify` only proves the backup file can be read. It never builds a
database from the file. The drill proves a database really comes back.

The drill checks three things:

1. Row counts of every table, compared with the counts recorded when the backup
   was taken.
2. The applied migrations, compared with the list recorded at backup time.
3. The same health checks a real restore runs.

It only runs on PostgreSQL installs. Backups made by hand do not start a drill.
The result is saved inside that backup's `manifest.json`.

### How to read the result

- Admin panel, Infrastructure > Backups. The line «آخرین تمرین بازیابی» shows
  the newest drill: موفق (passed), ناموفق (failed) or انجام نشد (skipped).
- Each backup row has its own result. Open it to see the reason and the list of
  tables with the expected and the actual row count.
- `/metrics` has `backup_drill_last_success_timestamp_seconds`,
  `backup_drill_last_duration_seconds` and `backup_drill_last_ok`. See
  `docs/engineering/MONITORING.md`. No alert rule reads them yet, so someone has
  to look.

What each status means and what to do:

| Status | Meaning | What to do |
|---|---|---|
| موفق (passed) | The backup came back and every check was right | Nothing |
| انجام نشد (skipped) | The drill did not run. The backup is not proven, and it is not known to be bad. `backup_drill_last_ok` is `0` | Read the reason on the page and fix it (see below) |
| ناموفق (failed) | The drill ran and a check was wrong | Read the reason and the per-table list (see below) |

If the drill is skipped, the reason is one of these:

- The drill database is missing. Run `sudo bash deploy/05-create-databases.sh <slug>`
  (see "Existing installs" below).
- Not enough free disk. The rule is: size of the live database x 1.2 + 512 MB.
  Free some space. The next drill checks again. The app cannot read
  PostgreSQL's data folder, so this check measures the disk of the backups
  folder. On the standard install that is the same disk as the database. If
  PostgreSQL's data is on another disk, the check does not see it, and you
  must watch that disk yourself.
- The database name cannot be used to build the drill name (it is empty, it
  already ends in `_drill`, or the new name is longer than 63 bytes). Nothing
  is touched.

If another drill is already running (one at a time per install), the nightly
drill does not run and nothing is saved, except a line in the app log. The
button answers that a drill is running.

If the drill failed:

- Read the reason, then the per-table list. If the restore itself did not
  finish, the reason holds the real error, and the details are in the log
  entry «backup.drill.restore_failed» (reports page) and in the server log.
  Read them first. The cause is not always the backup file.
  - The reason says the restore took longer than 30 minutes: the database is
    large or the server is slow. Look at the duration of earlier drills. A
    new backup will not help. Make the server faster, or raise
    `_RESTORE_TIMEOUT` in `app/services/pg_backup.py` (the panel restore uses
    it too).
  - Any other restore failure: the log entry says why (for example a
    permission problem on the drill database, or a full disk). Fix that cause.
    Make a new backup now only if the log shows the archive itself is broken
    (for example «input file does not appear to be a valid archive»).
- A row count that does not match can also come from DDL (a table structure
  change) that ran while the backup was being made. The counts are taken a
  moment before `pg_dump` locks the tables. So do not decide the backups are
  broken yet. Run a manual drill on the next backup first. If it fails the same
  way, treat the backups as broken.
- A failed drill never deletes the backup. Prune does not look at drill results.
- If the reason ends with a note that cleaning the drill database did not
  finish, the next drill drops the old schemas first, so it fixes itself. It
  does this before the disk check, so a leftover copy can not keep the drill
  skipped for lack of disk space.

### Drill time and the next backup

The scheduled drill runs inside the same scheduler step that made the backup.
That step waits for the drill, and a restore can take up to 30 minutes.

With one worker and a backup interval shorter than the drill, the next backup
can start late. With the default 24 hour interval this does not matter.

### Run a drill by hand

Infrastructure > Backups. Press «تمرین بازیابی» on the backup row. The page
says the drill started, and the result appears after a few minutes. Only one
drill can run at a time per install. A manual drill also updates the three
metrics.

### Existing installs

The app role has no `CREATEDB` right, so the drill database must be made ahead
of time by `deploy/05-create-databases.sh`. A new install gets it automatically.
For an install that already exists, run this once:

```bash
sudo bash deploy/05-create-databases.sh <slug>
```

The script is safe to run again. It leaves the live database alone and only
adds the missing `padyar_<slug>_drill` database.

**Warning: this also sets a new password for the database role.** The script
does this every time the role exists. The old `DATABASE_URL` in `.env` stops
working. Do these steps right away, or the app cannot reach its database:

1. Copy the `DATABASE_URL` line printed at the end of the script. It is shown
   only once.
2. Put it in `/opt/padyar-<slug>/.env`.
3. Restart the app: `sudo systemctl restart padyar-<slug>`.

If you do not re-run the script, nothing breaks. The drill records "skipped"
with the reason "drill database missing" every night.

### Disk use

While a drill runs, the data exists twice on disk. This lasts a few minutes.
The drill checks free space first and skips itself when there is not enough.
The restored copy holds the same visitor and admin records as the live database,
so the drill drops it as soon as the checks are done, also when they fail.
