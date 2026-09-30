# Monitoring (Prometheus `/metrics`)

Verified against the code on 2026-09-19. The multi-worker section and the
line numbers that moved with it were updated on 2026-10-01.

## What exists, and what does not

Be clear about this before reading the rest. This repo ships **one thing**: an
authenticated `GET /metrics` endpoint that serves Prometheus text.

| Piece | State |
|---|---|
| `/metrics` endpoint | **Exists.** `app/routers/metrics.py` |
| Metric definitions and registry | **Exists.** `app/services/metrics.py` |
| Instrumentation hooks (HTTP, chat, AI, circuit, backup, health) | **Exists.** Wired at the call sites listed below |
| Tests | **Exist.** `tests/test_metrics.py` (13 tests) and `tests/test_metrics_multiprocess.py` (38 tests, each multiprocess case in a real subprocess) |
| A Prometheus server that scrapes it | **Does not exist.** No scrape config anywhere in the repo |
| A Grafana (or any) dashboard | **Does not exist.** No dashboard file in the repo |
| Metric retention | **Does not exist.** Retention is a property of a Prometheus server, and there is none |
| Alerting rules | **Does not exist.** No alert rule file in the repo |
| Distributed tracing (OpenTelemetry, Jaeger) | **Does not exist.** No tracing dependency in `requirements.txt` |

So today the numbers below are produced correctly, held in memory (with several
workers: in memory-mapped files in one shared directory, see the section
«چند worker و /metrics» below), and **nothing reads them** except a human who opens
`/metrics` in a browser with an admin session. The moment the app restarts,
every counter goes back to zero, because nothing has stored them.

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
(`app/services/metrics.py:74`). Anything a third-party package registers onto
`prometheus_client.REGISTRY` never appears. Only the eight families below are
exposed.

With several workers (multiprocess mode) the dedicated registry alone is not
enough, because every metric of every library lands in the shared directory.
`exposition()` therefore filters the merged result by `FAMILY_NAMES`, which is
built from the dedicated registry. The rule is the same in both modes: eight
families, nothing else.

The reason is review: every series on this endpoint was chosen by a person. A
shared global registry would let any future dependency publish series nobody
looked at.

## Metric list

All eight are defined in `app/services/metrics.py:76-127`.

| Metric | Type | Labels | Meaning | Hooked at |
|---|---|---|---|---|
| `http_requests_total` | counter | `method`, `route`, `status` | Every HTTP request | `app/main.py:582` and `:589` (middleware) |
| `http_request_duration_seconds` | histogram | `method`, `route` | Request latency in seconds. Buckets 0.005s to 10s | `app/main.py:592` |
| `http_inflight` | gauge | none | Requests being served right now | `app/main.py:576` / `:586` |
| `chat_tier_served_total` | counter | `tier` | Chat turns, by the tier that served them | `app/routers/chat.py:165`, inside `_log_turn` |
| `ai_calls_total` | counter | `provider`, `outcome` | Routed AI requests by provider type and `success`/`failed` | `app/services/ai/engine.py:366`, inside `_record_usage` |
| `ai_circuit_state` | gauge | `instance` | Circuit breaker per provider instance: `0` closed, `1` half_open, `2` open | `app/services/ai/circuit.py:83-92` (`_metrics_state`) |
| `backup_outcome_total` | counter | `result` | PostgreSQL backup attempts, `success` or `failed` | `app/services/pg_backup.py:174` and `:203` |
| `health_score` | gauge | none | The 0 to 100 system health score | `app/services/health.py:339` |

Two hook points are worth knowing about, because they are why the numbers are
trustworthy:

- **`_log_turn`** is the single function every answering branch of the chat
  pipeline already passes through. Putting the tier counter there means a new
  tier cannot be added and then forgotten by the metric.
- **`_record_usage`** fires once per completed AI request. Retries and
  failovers are counted inside that request as attempts, not as extra rows, so
  `ai_calls_total` counts requests, not attempts.

`ai_circuit_state` is a number, not a label, on purpose. One series per
provider instance means "alert when open" is a single PromQL comparison
(`ai_circuit_state > 0`) instead of a label match.

Every update is a counter increment or a gauge set. With one process it is an
in-memory change. In multiprocess mode it is a write into a memory-mapped file.
Either way there is no network call, no database call and no fsync, so
instrumentation adds no latency to the chat path.

## Cardinality: why `route` is a template

Prometheus memory grows with the number of distinct label combinations. If a
raw URL path ever became a label value, any visitor could mint unlimited
combinations just by requesting random paths, and the Prometheus server would
run out of memory. That is the one failure mode this design must never have.

`route_template()` (`app/services/metrics.py:146-163`) is the only function that
produces the `route` label. It has three branches and none can return an
unbounded value:

1. A matched route returns the **route template** from `request.scope["route"]`
   (`/chat`, `/api/things/{thing_id}`), never the raw path. This holds even
   when the request 404s or 422s inside the route.
2. A path under a static mount collapses to its fixed prefix. The set is
   `("/static", "/themes", "/media", "/LOGO")` (`metrics.py:139`).
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

The router is included unconditionally in `app/main.py:618`, outside the
`ENABLED_MODULES` system. It is not an optional module, so every install has
the endpoint and every install relies on the auth above.

### Reverse proxy: the token is the only gate today

The old version of this file said to keep `/metrics` out of the public nginx
server block. **That was never implemented.** Checked today:

```bash
grep -n "location" deploy/nginx/instance.conf.template
grep -rn "metrics" deploy/nginx/    # no match: no nginx file mentions /metrics
```

`deploy/nginx/instance.conf.template:184` is a catch-all
`location / { proxy_pass ... }`. There is no `location = /metrics` and no
`deny`. So on a deployed install, `/metrics` **is** reachable from the
internet, and the in-app 403 is the only thing standing in front of it.

That is not broken (the endpoint was built to defend itself), but it is weaker
than defence in depth. If you want the second layer, the options are:

- Add a `location = /metrics { deny all; }` block above the catch-all, and let
  the scraper reach uvicorn on loopback instead.
- Or reach the endpoint over a private network or tunnel only.

Either change belongs in `deploy/nginx/instance.conf.template`. Nobody has made
it yet.

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
| `http_request_duration_seconds` | histogram | جمع `_count` و `_sum` و همه‌ی bucketها | همان دلیل |
| `http_inflight` | gauge | `livesum`: جمع روی worker های زنده | درخواست‌های در حال انجام همه‌ی worker ها باید جمع شوند. فایل worker ای که خارج شود حذف می‌شود |
| `ai_circuit_state` (به ازای هر `instance`) | gauge | `mostrecent`: آخرین مقداری که هر process set کرده | دلیلش پایین‌تر آمده |
| `health_score` | gauge | `mostrecent` | همه‌ی worker ها از همان چک‌ها همان امتیاز را حساب می‌کنند، پس تازه‌ترین مقدار درست است |

**چرا `ai_circuit_state` حالت `mostrecent` دارد، نه `max` و نه `live`.**
وضعیت circuit در دیتابیس مشترک است. هر تغییر را فقط یک worker منتشر می‌کند:
همان که UPDATE شرطی را برده است (`app/services/ai/circuit.py`). با `max` این
اتفاق می‌افتد: worker A عدد ۲ (open) را set می‌کند، بعد worker B عدد ۰ (closed)
را set می‌کند، A دیگر هرگز چیزی نمی‌نویسد، و scrape برای همیشه «open» نشان می‌دهد.
با `live` هم درست نیست: تغییری که یک worker منتشر کرده و بعد خارج شده هنوز
درست است و نباید گم شود. پس آخرین نوشته حقیقت است.

### فقط هشت خانواده

پوشه‌ی مشترک هر متریکی را نگه می‌دارد که هر کدی در هر worker ساخته، از جمله
متریک‌های کتابخانه‌های دیگر. برای همین `exposition()` نتیجه‌ی جمع‌شده را با
`FAMILY_NAMES` فیلتر می‌کند. این مجموعه از registry اختصاصی ساخته می‌شود، پس
هنوز همان یک منبع حقیقت است. در هر دو حالت همان هشت خانواده دیده می‌شود.
خانواده‌ای که هنوز هیچ worker در آن ننوشته (مثلاً تا وقتی هیچ backup اجرا نشده)
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
sed "s/{{SLUG}}/<slug>/g" /opt/padyar-<slug>/deploy/systemd/padyar-app.service.template > "$TMP"
if grep -qF '{{' "$TMP"; then
  echo "STOP: unfilled placeholder. Nothing was installed."
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

unit اول در یک فایل موقت ساخته می‌شود، پس ساخت خراب هیچ‌وقت نصب نمی‌شود و اگر
placeholder خالی بماند هیچ چیز عوض نمی‌شود. `--since` فقط خط‌های همین restart
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

## Checking it yourself

```bash
# Run the endpoint's tests
.venv/bin/python -m pytest tests/test_metrics.py tests/test_metrics_multiprocess.py -q

# Look at the live output with an admin session, on a running dev server
curl -s -H "Authorization: Bearer $METRICS_TOKEN" http://127.0.0.1:8000/metrics
```

Related files: `app/services/metrics.py`, `app/routers/metrics.py`,
`app/main.py` (the `prometheus_metrics` middleware and the `lifespan` hooks),
`deploy/systemd/padyar-app.service.template`,
`docs/features/metrics-endpoint/SPEC.md`.
