# Monitoring (Prometheus `/metrics`)

Verified against the code on 2026-09-19. The multi-worker section and the
line numbers that moved with it were updated on 2026-10-01. The backup
metrics (section «متریک‌های پشتیبان‌گیری») were changed on 2026-10-01.

## What exists, and what does not

Be clear about this before reading the rest. This repo ships **one thing**: an
authenticated `GET /metrics` endpoint that serves Prometheus text.

| Piece | State |
|---|---|
| `/metrics` endpoint | **Exists.** `app/routers/metrics.py` |
| Metric definitions and registry | **Exists.** `app/services/metrics.py` |
| Instrumentation hooks (HTTP, chat, AI, circuit, backup, health) | **Exists.** Wired at the call sites listed below |
| Tests | **Exist.** `tests/test_metrics.py` (13 tests), `tests/test_metrics_multiprocess.py` (45 tests, each multiprocess case in a real subprocess), `tests/test_backup_metrics.py` (22 tests) and `tests/test_backup_interval_metric.py` (22 tests) |
| A Prometheus server that scrapes it | **Does not exist.** No scrape config anywhere in the repo |
| A Grafana (or any) dashboard | **Does not exist.** No dashboard file in the repo |
| Metric retention | **Does not exist.** Retention is a property of a Prometheus server, and there is none |
| Alerting rules | **Does not exist.** No alert rule file in the repo |
| Distributed tracing (OpenTelemetry, Jaeger) | **Does not exist.** No tracing dependency in `requirements.txt` |

So today the numbers below are produced correctly, held in memory (with several
workers: in memory-mapped files in one shared directory, see the section
«چند worker و /metrics» below), and **nothing reads them** except a human who opens
`/metrics` in a browser with an admin session. The moment the app restarts,
every counter goes back to zero, because nothing has stored them. The one
exception is `backup_last_success_timestamp_seconds`: at startup it is read
back from the backup manifests on disk.

Verify the gaps yourself:

```bash
grep -rniI "grafana\|scrape_config\|alertmanager\|opentelemetry" \
  --include="*.yml" --include="*.yaml" --include="*.conf" --include="*.py" \
  app/ deploy/ .github/          # no matches
grep -n "prometheus" requirements.txt   # only: prometheus-client
```

This is a scrape surface waiting for a scraper. That is a real, useful step,
but it is not a monitoring stack.

## The registry

`/metrics` serves a **dedicated** `CollectorRegistry`, not the library default
(`app/services/metrics.py:80`). Anything a third-party package registers onto
`prometheus_client.REGISTRY` never appears. Only the twelve families below are
exposed.

With several workers (multiprocess mode) the dedicated registry alone is not
enough, because every metric of every library lands in the shared directory.
`exposition()` therefore filters the merged result by `FAMILY_NAMES`, which is
built from the dedicated registry. The rule is the same in both modes: twelve
families, nothing else.

The reason is review: every series on this endpoint was chosen by a person. A
shared global registry would let any future dependency publish series nobody
looked at.

## Metric list

All twelve are defined in `app/services/metrics.py:82-194`. The two `intent_*` gauges are the newest.

| Metric | Type | Labels | Meaning | Hooked at |
|---|---|---|---|---|
| `http_requests_total` | counter | `method`, `route`, `status` | Every HTTP request | `app/main.py:588` and `:595` (middleware) |
| `http_request_duration_seconds` | histogram | `method`, `route` | Request latency in seconds. Buckets 0.005s to 10s | `app/main.py:598` |
| `http_inflight` | gauge | none | Requests being served right now | `app/main.py:582` / `:592` |
| `chat_tier_served_total` | counter | `tier` | Chat turns, by the tier that served them | `app/routers/chat.py:165`, inside `_log_turn` |
| `ai_calls_total` | counter | `provider`, `outcome` | Routed AI requests by provider type and `success`/`failed` | `app/services/ai/engine.py:366`, inside `_record_usage` |
| `ai_circuit_state` | gauge | `instance` | Circuit breaker per provider instance: `0` closed, `1` half_open, `2` open | `app/services/ai/circuit.py:83-92` (`_metrics_state`) |
| `backup_outcome_total` | counter | `result` | PostgreSQL backup attempts made by the scheduler path: `success` = created AND verified, `failed` = any other ending. Both series start at 0 | `app/services/backup.py:187`, `:205` and `:207` (`_run_backup_now`) |
| `backup_last_success_timestamp_seconds` | gauge | none | Unix time (`created_at`) of the newest PostgreSQL backup that passed verification. `0` = none known. Seeded from disk at startup | `app/services/pg_backup.py:302` (`record_verified`), called from `verify()` (`:271`), `_run_backup_now` (`app/services/backup.py:204`) and the startup seed (`app/main.py:156`) |
| `backup_schedule_interval_seconds` | gauge | none | فاصله‌ی پشتیبان‌گیری خودکار به ثانیه (`backup_interval_hours × 3600`). `0` = پشتیبان‌گیری خودکار خاموش است. بخش «فاصله‌ی زمان‌بند پشتیبان» را ببینید | `app/services/backup.py:70` (`_set_schedule_metric`)، صدا زده از `:241` (شروع حلقه‌ی زمان‌بند) و `:249` (هر چک) |
| `health_score` | gauge | none | The 0 to 100 system health score | `app/services/health.py:339` |
| `intent_holdout_accuracy` | gauge | none | Holdout accuracy (0 to 1) of the intent model this install serves. NaN when there is no measurement | `app/services/intent.py:813` (`_publish_gauges`, called by `record_artifact` on every reindex) |
| `intent_model_version` | gauge | none | Version of the served intent model. Rises by one per newly trained model; a model loaded unchanged keeps its number. NaN when no recorded model is served | `app/services/intent.py:815` (same function) |

Two hook points are worth knowing about, because they are why the numbers are
trustworthy:

- **`_log_turn`** is the single function every answering branch of the chat
  pipeline already passes through. Putting the tier counter there means a new
  tier cannot be added and then forgotten by the metric.
- **`_record_usage`** fires once per completed AI request. Retries and
  failovers are counted inside that request as attempts, not as extra rows, so
  `ai_calls_total` counts requests, not attempts.

The two `intent_*` gauges start at NaN, not 0. A 0 would read as "0% accurate"
or "version 0", and an alert rule cannot tell that from a real bad model. To
alert on "no model" use `intent_holdout_accuracy != intent_holdout_accuracy`
(true only for NaN). The model and its files are described in
`docs/features/intent-model/MODEL_CARD.md`.

`ai_circuit_state` is a number, not a label, on purpose. One series per
provider instance means "alert when open" is a single PromQL comparison
(`ai_circuit_state > 0`) instead of a label match.

Every update is a counter increment or a gauge set. With one process it is an
in-memory change. In multiprocess mode it is a write into a memory-mapped file.
Either way there is no network call, no database call and no fsync, so
instrumentation adds no latency to the chat path.

## متریک‌های پشتیبان‌گیری

این دو متریک باید بگویند «یک پشتیبان قابل بازگردانی داریم»، نه فقط «یک فایل dump
نوشته شد». قبلاً `success` داخل `pg_backup.create()` و قبل از `verify()` زیاد
می‌شد. `failed` هم فقط وقتی زیاد می‌شد که خود `pg_dump` شکست بخورد. پس یک dump
خراب هم «موفق» شمرده می‌شد.

**`backup_outcome_total{result=...}`** یک شمارش برای هر تلاش پشتیبان‌گیری است.
این شمارش در `_run_backup_now` (`app/services/backup.py`) انجام می‌شود. این تابع را
زمان‌بند شبانه صدا می‌زند، و هر چیزی که `create_backup_now` را صدا بزند
(endpoint قدیمی `POST /admin/api/backups/create` در `app/routers/admin.py`، و
عملیات «پشتیبان‌گیری» در `app/services/service_control.py`).

| مقدار `result` | یعنی |
|---|---|
| `success` | dump ساخته شد **و** `verify()` گفت `verified`. برای هر تلاش دقیقاً یک بار |
| `failed` | هر پایان دیگر: خطا قبل از dump (مثلاً ساختن پوشه یا خواندن `DATABASE_URL`)، شکست `pg_dump`، `verify()` که `failed` بگوید، یا `verify()` که خطا بدهد. برای هر تلاش دقیقاً یک بار |

- هر دو سری از شروع process با مقدار ۰ وجود دارند. پس
  `increase(backup_outcome_total{result="failed"}[1h])` بعد از restart هم سری
  دارد که بخواند.
- **چه چیزی شمرده نمی‌شود.** ساختن دستی پشتیبان از صفحه‌ی
  Infrastructure → Backups (`POST /admin/api/infra/backups`) و پشتیبان ایمنی
  قبل از restore مستقیم `pg_backup.create()` را صدا می‌زنند و verify نمی‌کنند.
  این دو در `backup_outcome_total` هیچ چیزی نمی‌شمارند. تا قبل از این تغییر،
  همین‌ها `success` می‌شمردند.
- رفتار خود پشتیبان‌گیری عوض نشده است: همان فایل‌ها، همان prune، همان کپی
  off-site، همان رخدادهای applog و همان پاسخ API. شکست verify هنوز اجرا را
  متوقف نمی‌کند و prune بعد از آن انجام می‌شود.

**`backup_last_success_timestamp_seconds`** زمان Unix (ثانیه) ساخته‌شدن
(`created_at` در manifest) تازه‌ترین پشتیبانی است که verify آن `verified` بوده.

- **مقدار ۰ یعنی هیچ پشتیبان verify شده‌ای شناخته نیست**: نه از این start، نه روی
  دیسک. یک gauge بدون label در `prometheus_client` قبل از اولین `set()` مقدار ۰
  نشان می‌دهد، پس «sample ندارد» ممکن نیست. قاعده‌ی «پشتیبان کهنه است»
  (`time() - backup_last_success_timestamp_seconds > 26*3600`) برای ۰ هم فعال
  می‌شود، و این همان رفتاری است که می‌خواهیم.
- پشتیبانی که ساخته شد ولی verify آن `failed` بود، یا هنوز verify نشده، این عدد
  را تکان نمی‌دهد.
- هر verify موفق این عدد را جلو می‌برد، نه فقط verify زمان‌بند. اگر اپراتور یک
  پشتیبان دستی را بعداً با دکمه‌ی verify بررسی کند، زمان آن حساب می‌شود.
- این عدد **هرگز پایین نمی‌آید**. verify یک پشتیبان قدیمی‌تر (دکمه‌ی verify یا
  بررسی قبل از restore) آن را کم نمی‌کند (`metrics.set_backup_last_success`).
- **زمان آینده نادیده گرفته می‌شود.** اگر `created_at` یک پشتیبان بیشتر از یک
  ساعت در آینده باشد (ساعت اشتباه یا manifest دستی)، عدد تکان نمی‌خورد و در لاگ
  ثبت می‌شود. چون این عدد هرگز پایین نمی‌آید، یک زمان آینده آن را برای همیشه نگه
  می‌داشت و قاعده‌ی «کهنه» دیگر هرگز فعال نمی‌شد.
- **متریک هرگز verify را خراب نمی‌کند.** اگر نوشتن متریک خطا بدهد، فقط در لاگ
  ثبت می‌شود. `verify()`، کپی off-site بعد از آن و بررسی قبل از restore مثل قبل
  کار می‌کنند.
- **بعد از restart هم می‌ماند.** موقع شروع برنامه،
  `pg_backup.seed_last_success_metric()` همه‌ی manifest های روی دیسک را می‌خواند
  و تازه‌ترین پشتیبان verify شده را برمی‌دارد. manifest خراب (JSON نامعتبر،
  `verification` که object نیست، یا `created_at` نامعتبر) رد می‌شود و در لاگ ثبت
  می‌شود. خطا برای هر manifest جدا گرفته می‌شود، پس یک فایل خراب جلوی بقیه را
  نمی‌گیرد. نبودن پوشه‌ی پشتیبان یا
  هر خطای دیگر شروع برنامه را متوقف نمی‌کند. این کار به `DB_BACKEND` وابسته نیست.
  روی نصب SQLite پوشه‌ی پشتیبان پستگرس خالی است، پس عدد ۰ می‌ماند.

### فاصله‌ی زمان‌بند پشتیبان

**`backup_schedule_interval_seconds`** زمان‌بند پشتیبان‌گیری خودکار را به
Prometheus نشان می‌دهد. زمان‌بند در جدول `settings` است
(`backup_auto_enabled` و `backup_interval_hours`) و Prometheus آن را نمی‌خواند.
بدون این عدد، قاعده‌ی «پشتیبان کهنه است» فرق «پشتیبان خودکار عمداً خاموش است» یا
«این نصب هر ۴۸ ساعت پشتیبان می‌گیرد» را با «پشتیبان‌گیری متوقف شده» نمی‌فهمید و
هشدار دروغ می‌داد.

- **مقدار** برابر `backup_interval_hours × 3600` است وقتی پشتیبان‌گیری خودکار
  روشن است. اگر سطری در `settings` نباشد، پیش‌فرض‌های `backup.DEFAULTS` به کار
  می‌روند (۲۴ ساعت، یعنی `86400`).
- **مقدار ۰ یعنی زمان‌بند هیچ پشتیبان خودکاری نمی‌گیرد.** «روشن» دقیقاً همان
  آزمونی است که خود زمان‌بند می‌کند (`get_schedule()["enabled"]`، یعنی مقدار
  سطر دقیقاً رشته‌ی `true` باشد). پس این عدد هرگز با کار واقعی زمان‌بند فرق
  ندارد. قاعده‌ی «کهنه» (برنامه‌ریزی شده، هنوز در این مخزن نیست) وقتی این عدد ۰
  است فعال نمی‌شود.
- **کی به‌روز می‌شود.** حلقه‌ی زمان‌بند (`scheduler_loop`) در هر worker موقع
  شروع برنامه یک بار آن را set می‌کند، و بعد در هر چک (هر `CHECK_EVERY_SECONDS`،
  یعنی ۶۰ ثانیه) دوباره. پس تغییر زمان‌بند در پنل مدیریت حداکثر حدود ۶۰ ثانیه
  بعد در همه‌ی worker ها دیده می‌شود. حلقه یا timer تازه‌ای اضافه نشده است.
- **خواندن ناموفق مقدار قبلی را نگه می‌دارد.** اگر خواندن `settings` خطا بدهد
  (دیتابیس پایین است، یا `backup_interval_hours` با دست به چیزی غیر عددی عوض
  شده)، عدد تکان نمی‌خورد و فقط در لاگ ثبت می‌شود. حدس زدن بدتر بود: ۰ هشدار
  «کهنه» را خاموش می‌کرد و پیش‌فرض به نصبی با بازه‌ی ۴۸ ساعت هشدار دروغ می‌داد.
  خطای نوشتن متریک هم فقط در لاگ ثبت می‌شود و جلوی پشتیبان‌گیری را نمی‌گیرد.
- **اگر دیتابیس موقع شروع یک worker پایین باشد**، عدد تا اولین خواندن موفق
  مقدار پیش‌فرض gauge یعنی ۰ را نشان می‌دهد و قاعده‌ی «کهنه» ساکت است. این
  پذیرفته شده است، چون در این مدت هشدار «PostgreSQL پایین است»
  (`HostPostgresDown`، برنامه‌ریزی شده) خودش فعال است و هیچ پشتیبانی هم
  نمی‌تواند اجرا شود.
- رفتار پشتیبان‌گیری و API مدیریت عوض نشده است. فقط این متریک اضافه شده.

**موتور SQLite.** مسیر `backup_center` (فقط backend تست و نسخه‌ی بازگشت) مثل
قبل است. این دو متریک فقط پشتیبان‌های پستگرس (`pg_dump`) را توصیف می‌کنند.

## Cardinality: why `route` is a template

Prometheus memory grows with the number of distinct label combinations. If a
raw URL path ever became a label value, any visitor could mint unlimited
combinations just by requesting random paths, and the Prometheus server would
run out of memory. That is the one failure mode this design must never have.

`route_template()` (`app/services/metrics.py:184-201`) is the only function that
produces the `route` label. It has three branches and none can return an
unbounded value:

1. A matched route returns the **route template** from `request.scope["route"]`
   (`/chat`, `/api/things/{thing_id}`), never the raw path. This holds even
   when the request 404s or 422s inside the route.
2. A path under a static mount collapses to its fixed prefix. The set is
   `("/static", "/themes", "/media", "/LOGO")` (`metrics.py:177`).
3. Everything else collapses to the fixed string `unmatched`.

`tests/test_metrics.py:55` pins this: it requests a junk path and a junk asset,
then asserts neither string ever appears as a `route` label.

The other labels are bounded too. `status` is an HTTP status code. `tier` is
the closed list of chat sources in `app/routers/chat.py`. `provider` is the
provider type from the AI control plane. `instance` is bounded by the provider
instance rows an operator creates by hand.

## Security: `/metrics` is never anonymous

The endpoint authenticates every scrape itself
(`app/routers/metrics.py:35-49`). There are two modes.

1. **`METRICS_TOKEN` set.** The scrape must send
   `Authorization: Bearer <METRICS_TOKEN>`, compared with
   `secrets.compare_digest` (timing-safe, the same compare the repo uses for
   chat tokens). This is the mode for a Prometheus server on another host.
2. **`METRICS_TOKEN` empty (the default).** An authenticated admin session is
   required, through the exact same `verify_admin` every admin API uses.

Any failure is a **403**, never a 401. A 401 would tell a probe that some
credential exists and would work. 403 tells it nothing. `verify_admin`'s 401 is
caught and re-raised as 403 for that reason
(`app/routers/metrics.py:47-49`).

In session mode a Bearer header is meaningless and is not a side door.
`tests/test_metrics.py:120` pins that.

The router is included unconditionally in `app/main.py:624`, outside the
`ENABLED_MODULES` system. It is not an optional module, so every install has
the endpoint and every install relies on the auth above.

### Reverse proxy: nginx answers `/metrics` with 404

The public vhost never proxies `/metrics` to the app. In the HTTPS server
block of `deploy/nginx/instance.conf.template`, above the catch-all
`location / { proxy_pass ... }`, there is:

```nginx
location = /metrics {
    return 404;
}
```

So a request from the internet gets a 404 from nginx and never reaches
uvicorn. The in-app auth above is now the second layer, not the only one.
Before this block existed, the catch-all proxied `/metrics` and the app's 403
was the only thing in front of it.

Why 404 and not `deny all` (403): a 404 says nothing about whether the
endpoint exists. A 403 from nginx would confirm it.

What the exact match covers, and what it does not:

| Request | Result |
|---|---|
| `/metrics` | 404 from nginx |
| `/metrics?x=1` | 404. nginx matches a location on the path only, without the query string |
| `//metrics`, `/%6Detrics` | 404. nginx merges slashes and decodes the path before it matches |
| `/metrics/`, `/metricsx`, `/METRICS` | Not matched. Proxied to the app, the same as before this change |
| `http://` (port 80) | The port 80 block proxies nothing. It redirects to HTTPS, which then gives the 404 |

Only the HTTPS server block proxies to the app, so it is the only block that
needs the location. `tests/test_nginx_template.py` pins this: the block exists
in every server block that has a `proxy_pass`, it returns 404, it comes before
`location / {`, and no other location mentions `metrics`. The test reads the
template text, because nginx is not installed on CI.

Nothing legitimate needs `/metrics` through nginx. A scraper reads the app's
loopback port directly (`http://127.0.0.1:<APP_PORT>/metrics`). A scraper on
another host must reach that port over a private network or an SSH tunnel,
not through the public domain.

**An existing host does not get this change from a deploy.**
`deploy/padyar-deploy.sh` does not re-render the vhost. Re-render it once per
install with one of the two scripts that do: re-run
`sudo bash deploy/15-nginx-and-ssl.sh <slug> <port> <domain>`, or run
`sudo MAINTENANCE_TITLE='<visitor-facing name>' bash deploy/17-watchdog.sh <slug> <port> <domain>`.
With `17-watchdog.sh`, `MAINTENANCE_TITLE` is required. That script also
re-renders the maintenance page visitors see when the app is down, and without
the variable the page goes back to the default title instead of the install's
own name. The full commands and the check are in `deploy/README.md`, section
"Closing `/metrics` on an existing host".

### Setting the token

`METRICS_TOKEN` is read from the environment once, at import, in
`app/config.py:422`:

```python
METRICS_TOKEN = (os.getenv("METRICS_TOKEN") or "").strip()
```

The router reads `config.METRICS_TOKEN` as an attribute at request time
(`app/routers/metrics.py:36`), which is what lets a test monkeypatch it. It
does **not** mean an operator can change `.env` and see the new value take
effect. `os.getenv` already ran. **Changing the token in `.env` needs an app
restart.**

`METRICS_TOKEN` is not currently listed in `.env.example`
(`grep -i metrics .env.example` returns nothing). It is documented in
`CLAUDE.md`. Add it to `.env.example` when someone next touches that file.

Treat the token like any other secret. Never in the repo, never in a
screenshot.

## Retention

There is no metric retention here, because there is nothing storing metrics.
`/metrics` is a scrape surface, not a store, and its values reset on restart.

The operational history this install actually keeps for humans is a different
system: the applog store (`app/services/applog.py`, tables in the
`observability` schema, created by `migrations/0002_observability.sql`, read in
the admin panel under Logs). It has three retention windows, all settings rows:

| Class | Setting key | Default |
|---|---|---|
| Operational (`app_logs`) | `log_retention_days` | 90 days |
| Audit (`audit_logs`) | `log_audit_retention_days` | 365 days |
| Security (`security_events`) | `log_security_retention_days` | 365 days |

See `app/services/applog.py:150-152` for the defaults and `:558-579` for the
readers. `0` means keep forever, an explicit operator choice. Audit and security retention are separate
on purpose, so an operator lowering the operational window to 7 days cannot
quietly delete the evidence of their own actions.

If a Prometheus server is ever added, its retention is configured on that
server (for example `--storage.tsdb.retention.time=30d`), not in this repo.

## چند worker و /metrics

### مشکل

در production سرویس با `uvicorn --workers ${WEB_CONCURRENCY}` اجرا می‌شود
(پیش‌فرض ۳). هر worker یک process جدا است و registry خودش را در حافظه‌ی خودش
دارد. یک scrape به هر worker برسد که هسته‌ی سیستم انتخاب کند. نتیجه این بود که
counterها بین سه عدد مستقل می‌پریدند (Prometheus آن را reset می‌بیند) و gaugeها
فقط یک worker را نشان می‌دادند.

### حالا چطور کار می‌کند

وقتی متغیر `PROMETHEUS_MULTIPROC_DIR` تنظیم باشد، کتابخانه‌ی
`prometheus_client` به حالت multiprocess می‌رود. هر worker عددهایش را در فایل‌های
memory-mapped داخل همان پوشه می‌نویسد. با هر `GET /metrics`، تابع
`metrics.exposition()` فایل‌های همه‌ی process ها را می‌خواند و جمع می‌کند. پس
مهم نیست کدام worker جواب scrape را بدهد.

یک به‌روزرسانی متریک در این حالت یک نوشتن در فایل memory-mapped است. شبکه و
دیتابیس و fsync ندارد.

هر خانواده این‌طور جمع می‌شود:

| خانواده | نوع | روش جمع‌کردن | دلیل |
|---|---|---|---|
| `http_requests_total`، `chat_tier_served_total`، `ai_calls_total`، `backup_outcome_total` | counter | جمع (sum) | هر worker فقط سهم خودش را می‌شمارد |
| `backup_last_success_timestamp_seconds` | gauge | `max`: بیشترین مقدار بین همه‌ی process ها | worker ای که backup گرفته زمان را set می‌کند. worker های دیگر ۰ یا یک زمان قدیمی‌تر دارند و نباید آن را پنهان یا کم کنند. `live` نیست، چون backup بعد از خروج worker هنوز روی دیسک هست |
| `http_request_duration_seconds` | histogram | جمع `_count` و `_sum` و همه‌ی bucketها | همان دلیل |
| `http_inflight` | gauge | `livesum`: جمع روی worker های زنده | درخواست‌های در حال انجام همه‌ی worker ها باید جمع شوند. فایل worker ای که خارج شود حذف می‌شود |
| `ai_circuit_state` (به ازای هر `instance`) | gauge | `mostrecent`: آخرین مقداری که هر process set کرده | دلیلش پایین‌تر آمده |
| `backup_schedule_interval_seconds` | gauge | `mostrecent` | همه‌ی worker ها همان سطر جدول settings را می‌خوانند، پس تازه‌ترین مقدار همان زمان‌بند فعلی است. با `max`، خاموش کردن پشتیبان خودکار (۰) پشت یک ۸۶۴۰۰ قدیمی در فایل worker دیگر پنهان می‌ماند. `live` نیست، چون زمان‌بند بعد از خروج worker هنوز درست است |
| `health_score` | gauge | `mostrecent` | همه‌ی worker ها از همان چک‌ها همان امتیاز را حساب می‌کنند، پس تازه‌ترین مقدار درست است |
| `intent_holdout_accuracy`، `intent_model_version` | gauge | `mostrecent` | هر worker مدلی را که سرو می‌کند هنگام boot و بعد از هر reindex منتشر می‌کند، پس تازه‌ترین مقدار تازه‌ترین مدل است. `live` نیست، چون مدل بعد از خروج worker هنوز روی دیسک است و سرو می‌شود. مقدار NaN هنگام import فقط در حالت تک‌process نوشته می‌شود؛ در حالت چند worker همان نوشتن، تازه‌ترین نوشته می‌شد و مدل واقعی را پنهان می‌کرد |

**چرا `ai_circuit_state` حالت `mostrecent` دارد، نه `max` و نه `live`.**
وضعیت circuit در دیتابیس مشترک است. هر تغییر را فقط یک worker منتشر می‌کند:
همان که UPDATE شرطی را برده است (`app/services/ai/circuit.py`). با `max` این
اتفاق می‌افتد: worker A عدد ۲ (open) را set می‌کند، بعد worker B عدد ۰ (closed)
را set می‌کند، A دیگر هرگز چیزی نمی‌نویسد، و scrape برای همیشه «open» نشان می‌دهد.
با `live` هم درست نیست: تغییری که یک worker منتشر کرده و بعد خارج شده هنوز
درست است و نباید گم شود. پس آخرین نوشته حقیقت است.

### فقط ده خانواده

پوشه‌ی مشترک هر متریکی را نگه می‌دارد که هر کدی در هر worker ساخته، از جمله
متریک‌های کتابخانه‌های دیگر. برای همین `exposition()` نتیجه‌ی جمع‌شده را با
`FAMILY_NAMES` فیلتر می‌کند. این مجموعه از registry اختصاصی ساخته می‌شود، پس
هنوز همان یک منبع حقیقت است. در هر دو حالت همان ده خانواده دیده می‌شود.
خانواده‌ای که هنوز هیچ worker در آن ننوشته (مثلاً `ai_circuit_state` قبل از اولین
تغییر وضعیت circuit)
بدون sample لیست می‌شود، مثل حالت یک process.

دو تفاوت کوچک در حالت multiprocess:

- gaugeهای `*_created` (مثل `http_requests_created`) نمایش داده نمی‌شوند.
  زمان ساخته‌شدن یک counter وقتی چند process آن را دارند معنی ندارد. در حالت یک
  process چیزی عوض نشده است.
- `health_score` تا اولین باری که محاسبه شود هیچ sample ندارد. در حالت یک
  process تا آن موقع عدد ۰ نشان می‌داد.

### اتصال به systemd

این سه خط در `deploy/systemd/padyar-app.service.template` اضافه شده‌اند:

```ini
RuntimeDirectory=padyar-{{SLUG}}
RuntimeDirectoryMode=0700
Environment=PROMETHEUS_MULTIPROC_DIR=/run/padyar-{{SLUG}}
```

- systemd پوشه‌ی `/run/padyar-<slug>` را در هر start **خالی** می‌سازد و در هر stop
  پاک می‌کند. restart خودکار `Restart=always` هم همین‌طور است.
  `RuntimeDirectoryPreserve` به‌طور پیش‌فرض `no` است. هرگز آن را تنظیم نکنید.
- مالک پوشه User و Group سرویس است. حالت `0700` جلوی خواندن فایل‌های خام
  توسط کاربران دیگر همان ماشین را می‌گیرد.
- `ProtectSystem=full` پوشه‌ی `/run` را read-only نمی‌کند، و systemd یک
  RuntimeDirectory را به هر حال قابل‌نوشتن نگه می‌دارد. هیچ خط سخت‌گیری
  (`NoNewPrivileges`، `PrivateTmp`، `ProtectSystem`، `ProtectHome`،
  `ReadWritePaths`) عوض نشده است.
- **پوشه باید در هر start خالی باشد**، چون فایل‌ها با هم جمع می‌شوند. فایلی که از
  اجرای قبلی مانده باشد در اجرای جدید دوباره شمرده می‌شود. خود برنامه نباید
  پوشه را پاک کند: با چند worker، یکی از آن‌ها فایل‌هایی را پاک می‌کرد که worker
  دیگر همین الان نوشته است. systemd این کار را قبل از شروع هر worker انجام می‌دهد.
- متغیر را در `.env` نگذارید. systemd اجازه می‌دهد `EnvironmentFile=` روی
  `Environment=` غلبه کند. پس مقدار داخل `.env` مقدار unit را عوض می‌کند.

### هشدار موقع شروع

اگر `WEB_CONCURRENCY` بیشتر از ۱ باشد و `PROMETHEUS_MULTIPROC_DIR` تنظیم نشده
باشد، هر process موقع شروع یک خط WARNING می‌نویسد که می‌گوید `/metrics` اعداد
فقط یک worker را نشان می‌دهد (`one worker only`). برنامه بالا می‌آید و کار
می‌کند. با ۳ worker سه خط می‌بینید، از هر process یکی. اگر `WEB_CONCURRENCY` عدد
نباشد این هشدار نمی‌آید، چون `app/prodcheck.py` آن را گزارش می‌کند.

### اگر پوشه درست نباشد، برنامه بالا نمی‌آید

اگر متغیر هست ولی پوشه وجود ندارد، پوشه نیست، این process نمی‌تواند در آن
بخواند و بنویسد (قابل‌خواندن و قابل‌نوشتن نیست)، یا مقدارش خالی است، import
ماژول `app/services/metrics.py` با `RuntimeError` قطع می‌شود. نوشتن را worker
لازم دارد و خواندن را scrape، چون `/metrics` فایل‌ها را فهرست می‌کند. پوشه‌ی
فقط‌نوشتنی بدون این بررسی بالا می‌آمد و `/metrics` بی‌صدا خالی می‌ماند. پیام
خطا اسم `PROMETHEUS_MULTIPROC_DIR` و مقدار آن را دارد و می‌گوید چه کنید. هیچ
worker ای با این تنظیم بالا نمی‌آید. برگشت بی‌صدا به یک process نداریم: همان
باگ (عدد غلط) برمی‌گشت و کسی نمی‌فهمید.

مقدار **خالی** هم خطا است. کتابخانه حالت multiprocess را وقتی روشن می‌کند که
**کلید** در محیط باشد، حتی با مقدار خالی. برنامه هم همین‌طور تصمیم می‌گیرد: با
وجود کلید، نه با درست‌بودن مقدار. نام قدیمی با حروف کوچک
(`prometheus_multiproc_dir`) همین رفتار را دارد. اگر متغیر اصلاً نباشد، همه‌چیز
مثل قبل است و یک process کار می‌کند.

با `--workers N` این خطا سرویس را از کار نمی‌اندازد، ولی جواب هم نمی‌دهد. هر
worker موقع import با همین پیام واضح می‌افتد، اما process اصلی uvicorn بالا
می‌ماند و worker ها را پشت سر هم دوباره راه می‌اندازد. پس `systemctl status`
سرویس را `active (running)` نشان می‌دهد در حالی که هیچ درخواستی جواب نمی‌گیرد.
خطا را در journal ببینید:
`sudo journalctl -u padyar-<slug> | grep PROMETHEUS_MULTIPROC_DIR`. حلقه‌ی صبر
در دستورهای rollout پایین همین حالت را با STOP نشان می‌دهد. (این رفتار با
uvicorn 0.53.0 روی یک ماشین محلی بررسی شد. نسخه‌ی uvicorn در `requirements.txt`
pin نشده است.)

### محدودیت‌ها

- اگر یک worker را kill -9 کنند (یا OOM بکشد)، فایل live gauge آن می‌ماند و
  `http_inflight` تا restart بعدی سرویس عدد کهنه‌ی آن را هم جمع می‌زند. خروج
  عادی worker این را تمیز می‌کند: پایان `lifespan` تابع `mark_process_dead` را
  برای pid همان worker صدا می‌زند. این فقط فایل‌های `gauge_live*` را پاک می‌کند.
  counterها و gaugeهای `mostrecent` می‌مانند، چون آنچه یک worker شمرده یا منتشر
  کرده بعد از خروجش هم درست است.
- restart سرویس همه‌ی counterها را از صفر شروع می‌کند، چون پوشه خالی می‌شود.
  Prometheus این را یک reset عادی می‌بیند.

### روی نصب‌های موجود چه باید کرد

اسکریپت deploy فقط سرویس را restart می‌کند و unit را دوباره نمی‌سازد. پس دو کار
لازم است: کد جدید، و unit جدید. ترتیب برای ایمنی مهم نیست. کد جدید با unit قدیمی
یعنی خروجی یک process به‌علاوه‌ی WARNING موقع شروع. unit جدید با کد قدیمی یعنی
همان خروجی امروز. ولی فقط با هر دو، عددها درست می‌شوند.

```bash
sudo /usr/local/bin/padyar-deploy <slug> <port> <commit-sha>      # or the CI deploy: gets the new code
TMP="$(mktemp)"
if ! sed "s/{{SLUG}}/<slug>/g" /opt/padyar-<slug>/deploy/systemd/padyar-app.service.template > "$TMP"; then
  echo "STOP: could not render the unit. Nothing was installed."
elif grep -qF '{{' "$TMP" || ! grep -q '^ExecStart=' "$TMP"; then
  echo "STOP: the rendered unit is incomplete. Nothing was installed."
else
  sudo install -m 0644 "$TMP" /etc/systemd/system/padyar-<slug>.service
  sudo systemctl daemon-reload
  SINCE="$(date '+%Y-%m-%d %H:%M:%S')"
  sudo systemctl restart padyar-<slug>
  # Wait until the app answers, so every worker has logged its startup lines.
  UP=""
  for i in $(seq 1 60); do
    curl -fsS --max-time 3 http://127.0.0.1:<port>/api/health >/dev/null 2>&1 && UP=1 && break
    sleep 1
  done
  if [ -z "$UP" ]; then
    echo "STOP: no answer from the app after 60 s. Read: sudo journalctl -u padyar-<slug> --since \"$SINCE\""
  else
    sudo ls -ld /run/padyar-<slug>     # expect: drwx------ padyar-<slug> padyar-<slug>
    sudo journalctl -u padyar-<slug> --since "$SINCE" | grep -i "one worker only"   # expect: no lines
  fi
fi
rm -f "$TMP"
```

unit اول در یک فایل موقت ساخته می‌شود و فقط وقتی نصب می‌شود که `sed` بدون خطا
تمام شده باشد، فایل یک خط `ExecStart=` داشته باشد و هیچ `{{` در آن نمانده
باشد. پس یک ساخت خراب یا خالی هیچ‌وقت نصب نمی‌شود. `--since` فقط خط‌های همین restart
را نشان می‌دهد، نه هشدارهای قبل از آن. حلقه‌ی `curl` صبر می‌کند تا برنامه جواب
بدهد، چون `Type=exec` قبل از بالا آمدن worker ها برمی‌گردد و بدون صبر ممکن است
هشدار را نبینید. اگر برنامه در ۶۰ ثانیه جواب ندهد، بررسی‌ها اجرا نمی‌شوند و STOP
چاپ می‌شود، چون «هیچ خطی پیدا نشد» در آن حالت معنی «درست است» ندارد.

(`deploy/10-install-app.sh <slug>` هم unit را دوباره می‌سازد، ولی آن نصب‌کننده‌ی
کامل است: کد را می‌گیرد و migrationها را اجرا می‌کند. فرمان‌های بالا قدم
کوچک‌تر است.)

### چطور بررسی کنیم

```bash
# بدون کلید یا با کلید اشتباه باید 403 بدهد
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:<port>/metrics

# چند بار scrape کنید. عدد counter نباید بین scrapeها پایین برود.
for i in 1 2 3 4 5 6; do
  curl -s -H "Authorization: Bearer $METRICS_TOKEN" http://127.0.0.1:<port>/metrics \
    | grep '^http_requests_total{method="GET",route="/api/health"'
done

# فایل‌های همه‌ی worker ها (برای هر pid: یک counter، یک histogram و دو gauge)
sudo ls /run/padyar-<slug>
```

تست‌ها: `.venv/bin/python -m pytest tests/test_metrics.py tests/test_metrics_multiprocess.py -q`.
هر حالت multiprocess در یک subprocess واقعی اجرا می‌شود، چون کتابخانه حالتش را
فقط موقع import انتخاب می‌کند.

## Watchdog alert SMS

The per-install watchdog (`deploy/watchdog/watchdog.py`, one oneshot run a
minute) is what turns an Alertmanager alert into an SMS. Alertmanager itself
has no receiver and sends nothing. The full contract is section 5.5 of
`docs/features/monitoring-stack/SPEC.md`; the operator view is §11 of
`docs/features/critical-watchdog/SPEC.md`.

The step stays idle until the host runs the monitoring stack. Its switch is
one file: without `/etc/padyar-monitoring/alertmanager-watchdog.pass` the
watchdog skips the step and behaves exactly as before.

| What | Value |
|---|---|
| Read | `GET http://127.0.0.1:9093/api/v2/alerts?active=true`, basic auth user `watchdog`, 5 s per socket read and 8 s for the whole GET, no proxy, no redirect followed, an answer over 1 MiB is a failure |
| Texted | `page="sms"`, state `active`, this install's `install` label (or no label, on the host-owner install only) |
| Not texted | `suppressed` alerts (silence or inhibit), and alerts of an install that is already down. There is no "resolved" SMS |
| Volume | At most one SMS per cycle and ten send attempts per UTC day per install; a still-firing alert is reminded every 6 h. Each SMS is saved to the state file before it is sent; no save, no SMS |
| Text | Fixed Persian labels keyed by `alertname` only. Labels and annotations never reach the phone |
| Monitoring down | Host owner only: one SMS after 3 cycles without an answer or without the `MonitoringHeartbeat` alert |
| Silences | Host owner only: one notice per new active silence, the operator's own included |
| Sender | The same `send_asanak` path as the down-SMS, so a spent `sms_daily_budget` blocks it too (journal `alert send failed`). A failed send is retried after 300 s and counts against the daily cap; this deviates from REQ-051, so a gateway that delivers and then fails the call cannot send 288 SMS a day. About 45 minutes of gateway outage uses up that day's alert cap |
| Stuck cycle | `TimeoutStartSec=300s` in `padyar-watchdog@.service` ends only a truly stuck run. An Alertmanager stall is already cut by the 8 s GET deadline, and a run in progress makes the 60 s timer skip a tick, never overlap |

Every journal line starts with `[watchdog] <slug>:`. To see them:

```bash
journalctl -u padyar-watchdog@<slug>.service -n 50
```

The three lines that mean "an operator must act": `monitoring alerts OFF:
cannot read alertmanager-watchdog.pass` (re-run `deploy/55-monitoring.sh
<slug>`, usually after creating a new install), `alert pending but no
alert_critical_phone configured` (set the phone in the admin panel), and
`alert SMS skipped: state not saved` (free disk space, or fix the owner of
`/var/lib/padyar-watchdog/<slug>`).

## Checking it yourself

```bash
# Run the endpoint's tests
.venv/bin/python -m pytest tests/test_metrics.py tests/test_metrics_multiprocess.py -q

# Run the watchdog tests (alert SMS step included; every SMS is faked)
.venv/bin/python -m pytest tests/test_watchdog_logic.py tests/test_watchdog_io.py -q

# Look at the live output with an admin session, on a running dev server
curl -s -H "Authorization: Bearer $METRICS_TOKEN" http://127.0.0.1:8000/metrics
```

Related files: `app/services/metrics.py`, `app/routers/metrics.py`,
`app/main.py` (the `prometheus_metrics` middleware and the `lifespan` hooks),
`deploy/systemd/padyar-app.service.template`,
`docs/features/metrics-endpoint/SPEC.md`.
