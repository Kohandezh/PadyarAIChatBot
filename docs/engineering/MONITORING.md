# Monitoring (Prometheus /metrics)

`GET /metrics` exposes this install's own Prometheus series from a dedicated
`CollectorRegistry` (`app/services/metrics.py`). Nothing any library
auto-registers appears there — only the metrics below.

## Metric list

| Metric | Type | Labels | Meaning |
|---|---|---|---|
| `http_requests_total` | counter | `method`, `route`, `status` | Every HTTP request. `route` is always the ROUTE TEMPLATE (`/api/things/{thing_id}`); unmatched paths collapse to `unmatched`, static mounts to their prefix (`/static`, `/themes`, `/media`, `/LOGO`). Never a raw path — an unbounded label would let any visitor balloon Prometheus memory. |
| `http_request_duration_seconds` | histogram | `method`, `route` | Request latency, same route label as above. |
| `http_inflight` | gauge | — | Requests currently being served. |
| `chat_tier_served_total` | counter | `tier` | Chat turns, by the tier/source that served them. Hooked in `_log_turn` (`app/routers/chat.py`) — the single chokepoint every answering branch already passes through. |
| `ai_calls_total` | counter | `provider`, `outcome` | Routed AI requests by provider type and `success`/`failed`. Hooked in the engine's `_record_usage` — once per completed request, retries/failovers included in attempts, not as extra rows. |
| `ai_circuit_state` | gauge | `instance` | Circuit breaker state per provider instance: `0` closed, `1` half_open, `2` open. Alert on `> 0`. Set on every proven transition (`app/services/ai/circuit.py`). |
| `backup_outcome_total` | counter | `result` | PostgreSQL backup attempts: `success`/`failed` (`app/services/pg_backup.py`). |
| `health_score` | gauge | — | The system health score (0–100) from `app/services/health.py`, updated whenever the score is computed. |

All updates are in-memory (counter increments / gauge sets). No I/O was added
to any request path.

## Security model — /metrics is NEVER public

The endpoint authenticates every scrape itself (`app/routers/metrics.py`):

1. **`METRICS_TOKEN` set** → the scrape must present
   `Authorization: Bearer <METRICS_TOKEN>` (compared with
   `secrets.compare_digest`). This is the mode for a Prometheus server on
   another host.
2. **`METRICS_TOKEN` empty** → an authenticated admin session is required
   (the same `verify_admin` every admin API uses).

Any failure is a **403** — deliberately not 401, so a probe learns nothing
about which credential would work. There is no configuration in which the
endpoint answers an anonymous request.

The token is a second layer, not the gate:

### nginx / Cloudflare note

**Do not proxy `/metrics` through to the internet.** In production, nginx
serves the app behind a reverse proxy; leave `/metrics` out of the public
server block (a `deny all;` / no-location is the desired state). A scraper
that needs the data should either run on the same host and hit uvicorn
directly (e.g. `proxy_pass http://127.0.0.1:8000` bound to loopback in a
*separate* internal server block), or reach it over a private network /
tunnel. Behind Cloudflare, do not create a public DNS record or access rule
for `/metrics` at all.

### Setting the token

`METRICS_TOKEN` is an environment variable (`.env`), read at request time —
changing it takes effect without a restart of the scrape path config. Treat
it like any other secret: never in the repo, never in a screenshot.

## Retention guidance

/metrics is a scrape surface, not a store. The operational history this
install keeps for humans is the applog store (`app/services/applog.py`,
tables under the `observability` schema, viewable in the admin panel under
Logs) with its own retention setting. Prometheus-side retention is a
property of the Prometheus server that scrapes this install — set it there
(e.g. `--storage.tsdb.retention.time=30d`), not here.
