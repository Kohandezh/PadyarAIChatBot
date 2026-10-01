# Monitoring (Prometheus `/metrics`)

Verified against the code on 2026-09-19.

## What exists, and what does not

Be clear about this before reading the rest. This repo ships **one thing**: an
authenticated `GET /metrics` endpoint that serves Prometheus text.

| Piece | State |
|---|---|
| `/metrics` endpoint | **Exists.** `app/routers/metrics.py` |
| Metric definitions and registry | **Exists.** `app/services/metrics.py` |
| Instrumentation hooks (HTTP, chat, AI, circuit, backup, health) | **Exists.** Wired at the call sites listed below |
| Tests | **Exist.** `tests/test_metrics.py`, 13 tests |
| A Prometheus server that scrapes it | **Does not exist.** No scrape config anywhere in the repo |
| A Grafana (or any) dashboard | **Does not exist.** No dashboard file in the repo |
| Metric retention | **Does not exist.** Retention is a property of a Prometheus server, and there is none |
| Alerting rules | **Does not exist.** No alert rule file in the repo |
| Distributed tracing (OpenTelemetry, Jaeger) | **Does not exist.** No tracing dependency in `requirements.txt` |

So today the numbers below are produced correctly, held in process memory, and
**nothing reads them** except a human who opens `/metrics` in a browser with an
admin session. The moment the app restarts, every counter goes back to zero,
because nothing has stored them.

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
(`app/services/metrics.py:35`). Anything a third-party package registers onto
`prometheus_client.REGISTRY` never appears. Only the eight families below are
exposed.

The reason is review: every series on this endpoint was chosen by a person. A
shared global registry would let any future dependency publish series nobody
looked at.

## Metric list

All eight are defined in `app/services/metrics.py:37-75`.

| Metric | Type | Labels | Meaning | Hooked at |
|---|---|---|---|---|
| `http_requests_total` | counter | `method`, `route`, `status` | Every HTTP request | `app/main.py:568` and `:575` (middleware) |
| `http_request_duration_seconds` | histogram | `method`, `route` | Request latency in seconds. Buckets 0.005s to 10s | `app/main.py:578` |
| `http_inflight` | gauge | none | Requests being served right now | `app/main.py:562` / `:572` |
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

Every update is an in-memory counter increment or gauge set. No hook does I/O,
so instrumentation adds no latency to the chat path.

## Cardinality: why `route` is a template

Prometheus memory grows with the number of distinct label combinations. If a
raw URL path ever became a label value, any visitor could mint unlimited
combinations just by requesting random paths, and the Prometheus server would
run out of memory. That is the one failure mode this design must never have.

`route_template()` (`app/services/metrics.py:87-104`) is the only function that
produces the `route` label. It has three branches and none can return an
unbounded value:

1. A matched route returns the **route template** from `request.scope["route"]`
   (`/chat`, `/api/things/{thing_id}`), never the raw path. This holds even
   when the request 404s or 422s inside the route.
2. A path under a static mount collapses to its fixed prefix. The set is
   `("/static", "/themes", "/media", "/LOGO")` (`metrics.py:80`).
3. Everything else collapses to the fixed string `unmatched`.

`tests/test_metrics.py:55` pins this: it requests a junk path and a junk asset,
then asserts neither string ever appears as a `route` label.

The other labels are bounded too. `status` is an HTTP status code. `tier` is
the closed list of chat sources in `app/routers/chat.py`. `provider` is the
provider type from the AI control plane. `instance` is bounded by the provider
instance rows an operator creates by hand.

## Security: `/metrics` is never anonymous

The endpoint authenticates every scrape itself
(`app/routers/metrics.py:32-46`). There are two modes.

1. **`METRICS_TOKEN` set.** The scrape must send
   `Authorization: Bearer <METRICS_TOKEN>`, compared with
   `secrets.compare_digest` (timing-safe, the same compare the repo uses for
   chat tokens). This is the mode for a Prometheus server on another host.
2. **`METRICS_TOKEN` empty (the default).** An authenticated admin session is
   required, through the exact same `verify_admin` every admin API uses.

Any failure is a **403**, never a 401. A 401 would tell a probe that some
credential exists and would work. 403 tells it nothing. `verify_admin`'s 401 is
caught and re-raised as 403 for that reason
(`app/routers/metrics.py:44-46`).

In session mode a Bearer header is meaningless and is not a side door.
`tests/test_metrics.py:120` pins that.

The router is included unconditionally in `app/main.py:604`, outside the
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
(`app/routers/metrics.py:33`), which is what lets a test monkeypatch it. It
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

## Checking it yourself

```bash
# Run the endpoint's tests
.venv/bin/python -m pytest tests/test_metrics.py -q

# Look at the live output with an admin session, on a running dev server
curl -s -H "Authorization: Bearer $METRICS_TOKEN" http://127.0.0.1:8000/metrics
```

Related files: `app/services/metrics.py`, `app/routers/metrics.py`,
`app/main.py` (the `prometheus_metrics` middleware),
`docs/features/metrics-endpoint/SPEC.md`.
