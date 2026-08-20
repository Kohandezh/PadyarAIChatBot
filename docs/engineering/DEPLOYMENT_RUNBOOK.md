# راهنمای استقرار (Runbook)

## استقرار تازه

```bash
pip install -r requirements.txt
cp .env.example .env        # سپس مقادیر واقعی: OPENAI_API_KEY، SECRET_KEY، ADMIN_*، ALLOWED_ORIGINS، COOKIE_SECURE=true
python main.py              # dev — یا در تولید:
gunicorn -k uvicorn.workers.UvicornWorker -w 4 -b 127.0.0.1:8000 app.main:app
```

Docker: `docker compose up -d` (Dockerfile و docker-compose.yml موجود است).

## بررسی سلامت
- Liveness: `GET /api/health` (ارزان، بدون فراخوانی خارجی)
- Readiness: `GET /api/ready` — تا آماده‌شدن ایندکس بازیابی 503 می‌دهد؛
  `?deep=true` سرویس خارجی AI را هم می‌سنجد (فقط دستی/مانیتورینگ کم‌بسامد).
- لاگ ساخت‌یافته: `LOG_FORMAT=json` در env.

## به‌روزرسانی دانش
```bash
python3 scripts/refresh-inotex-context.py      # exit 2 یعنی صفحهٔ رسمی تغییر کرده
# بازبینی انسانی content/review-queue.md → به‌روزرسانی app/default_content.py
# ارتقای knowledge_version در content/sources.json و settings
python3 scripts/reset-content-to-defaults.py   # پشتیبان خودکار + seed جدید
# ری‌استارت سرویس تا ایندکس بازسازی شود
```

## پشتیبان و بازیابی
- خودکار: زمان‌بندی پنل ادمین (app/services/backup.py) → پوشهٔ backups/
- دستی: `python backup_db.py`
- بازیابی: توقف سرویس → جایگزینی chat_history.db از پشتیبان → شروع سرویس
  → بررسی `/api/ready` و شمارش dataset در `/api/health`.

## بازگشت (Rollback) دانش
هر اجرای reset یک `chat_history.backup.<timestamp>.db` می‌سازد؛ بازگشت =
همان مسیر بازیابی با فایل پشتیبان قبلی.

## عیب‌یابی سریع
| علامت | اقدام |
|---|---|
| /api/ready → 503 | لاگ «Embedding backend init failed» را ببین؛ fallback خودکار TF-IDF فعال است؛ model2vec و data/models را بررسی کن |
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
tests are opt-in:

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
