# Monitoring (Prometheus `/metrics` and the host stack)

Verified against the code on 2026-10-01.

## What exists, and what does not

| Piece | State |
|---|---|
| `/metrics` endpoint | **Exists.** `app/routers/metrics.py` |
| Metric definitions and registry | **Exists.** `app/services/metrics.py` |
| Instrumentation hooks (HTTP, chat, AI, circuit, backup, health) | **Exists.** Wired at the call sites listed below |
| Tests for the endpoint | **Exist.** `tests/test_metrics.py` |
| A Prometheus server that scrapes it | **Exists in the repo, installed by hand.** `deploy/55-monitoring.sh` installs Prometheus 2.45 from the Ubuntu noble archive, with one scrape job per install. See "The monitoring stack" below |
| Alert rules | **Exist.** `deploy/monitoring/rules/padyar.rules.yml`, 11 rules. Three more wait for app changes (see below) |
| Alertmanager | **Exists, UI only.** One route, one receiver with no destination. It sends nothing anywhere |
| Metric retention | **Exists.** 30 days or a size cap, whichever comes first (see "Retention") |
| An SMS for a firing alert | **Does not exist yet.** The watchdog step that reads Alertmanager and texts `page="sms"` alerts is a separate change (SPEC monitoring-stack, WU2). Until it ships, an alert is only visible in the Prometheus and Alertmanager UIs. The watchdog's own "the app is down" SMS works as before |
| A Grafana (or any) dashboard | **Does not exist.** Not in this phase (ADR-024, decision 2). The Prometheus UI is the dashboard |
| Distributed tracing (OpenTelemetry, Jaeger) | **Does not exist.** No tracing dependency in `requirements.txt` |

Installing the stack is a separate, manual step. Merging the repo changes
nothing on a host. An operator runs `deploy/55-monitoring.sh` on the host,
and runs it again after every rule change.

## The monitoring stack

Decided in ADR-024 (`docs/engineering/DECISIONS.md`), specified in
`docs/features/monitoring-stack/SPEC.md`. One stack per host, shared by every
install on it.

```bash
sudo bash deploy/55-monitoring.sh <slug> [<slug>...] [--host-alerts <slug>]
```

### What it installs

| Service (systemd unit) | Listens on | Reads |
|---|---|---|
| `prometheus` | `127.0.0.1:9090` | each install's `/metrics` with that install's token, the exporters below |
| `prometheus-alertmanager` | `127.0.0.1:9093` (basic auth) | alerts from Prometheus. HA gossip (`:9094`) is turned off |
| `prometheus-node-exporter` | `127.0.0.1:9100` | disk space of the host |
| `prometheus-postgres-exporter` | `127.0.0.1:9187` | the PostgreSQL cluster, over the local socket, role `prometheus` with `pg_monitor` only |
| `prometheus-blackbox-exporter` | `127.0.0.1:9115` | each site through nginx: `https://127.0.0.1:443/api/health` with the site's own name and certificate |
| `cloudflared` (already there) | `127.0.0.1:20241` | its own tunnel connections. The script adds one `metrics:` line to `/etc/cloudflared/config.yml` |

Every listener is on `127.0.0.1`. The script checks this with `ss` after the
restart, waiting up to 3 minutes for every port (Prometheus replays its WAL
before it listens). A port that still has no listener is printed as "not
checked", never as part of an all-clear.

Exit codes: `0` done, `2` a monitoring port listens on a non-loopback
address, `1` every other failure, whatever code the failing command itself
returned (psql exits 2 when it cannot connect, apt-get 100).

apt starts each new package at once on its Debian defaults (every interface,
Alertmanager without a password, cluster gossip on `0.0.0.0:9094`); UFW
covers the seconds until the script restarts them with its own config. If a
run stops before that restart (a failed check, a failed database step, ...),
the script:

- puts back every config file it replaced, including `amtool`'s config;
- removes the Alertmanager passwords this run created (they would no longer
  match the hashes it just put back), so the next run makes them again;
- stops and disables the services that this run installed, says which, and
  leaves packages that were installed before the run alone. They stay off
  until a run succeeds.

Each install's token is checked against its running app before anything is
installed, from a temp copy of the `.env` value. The token file Prometheus
reads is written only after a 200, so a run that stops on a 403 leaves a
scrape that worked before the run working.

The UIs are reached through an SSH tunnel only:

```bash
ssh -N -L 9090:127.0.0.1:9090 -L 9093:127.0.0.1:9093 <user>@<host>
```

### Files on the host

| Path | What | Owner and mode |
|---|---|---|
| `/etc/prometheus/prometheus.yml`, `rules/`, `alertmanager.yml`, `blackbox.yml` | Copied or rendered from `deploy/monitoring/` | `root:root 0644` |
| `/etc/prometheus/scrape.d/padyar-<slug>.yml` | One scrape job per install | `root:root 0644` |
| `/etc/prometheus/secrets/<slug>.token` | The install's `METRICS_TOKEN` | `root:prometheus 0640` |
| `/etc/prometheus/alertmanager-web.yml` | bcrypt hashes of the three API users | `root:prometheus 0640` |
| `/etc/padyar-monitoring/installs/<slug>.conf` | `PORT` and `DOMAIN` of each install, no secret | `root:root 0644` |
| `/etc/padyar-monitoring/host-alerts-owner` | The install that owns host-level alerts | `root:root 0644` |
| `/etc/padyar-monitoring/alertmanager-watchdog.pass` | Password of the `watchdog` API user | `root:padyar-alertread 0640` |
| `/root/.secrets/alertmanager-operator.pass`, `/root/.config/amtool/config.yml` | The operator's password, and `amtool`'s config that carries it | `root:root 0600` |
| `/root/padyar-backups/monitoring-<UTC time>/` | The previous version of every config file the run replaced | `root:root 0700` |

No token or password is in the repo, in `prometheus.yml`, in the script's
output, or in any command line: the token check uses `curl -H @<0600 file>`.

**Known limit of the Alertmanager API auth.** Basic auth has no per-endpoint
permission. Every install's user can read the `watchdog` password (that is
how its watchdog reads alerts), so a compromised install could also post a
fake alert or a silence for another install. A fake alert's cost is bounded
by the watchdog's daily SMS cap; a silence is reported by SMS by the
host-alerts owner's watchdog once that step ships. The rest is an accepted
risk (ADR-024, section 5).

### How the script reads an install's `.env`

It is the first script in the kit that reads an install's `.env` as root,
and the app rewrites that file from admin-panel input. So the script never
sources or evaluates it. It reads only lines of the exact form `KEY=value`,
removes one layer of matching quotes, and checks each value against its own
pattern (`APP_PORT` digits, `METRICS_TOKEN` `[A-Za-z0-9_-]+`). A line it cannot
read with certainty (`export`, indentation, escapes, `$`, backticks, an inline
comment, the same key twice with different values) stops the run with the key
and file named, never the value.

### The alert rules

`deploy/monitoring/rules/padyar.rules.yml`. `severity="critical"` means
`page="sms"`, with one exception: `PadyarAppDown` is critical without `page`,
because the watchdog already texts "the app is down".

| Rule | Fires when | For |
|---|---|---|
| `PadyarAppDown` | Prometheus cannot read an install's `/metrics` (app down, or a 403 from a wrong token) | 2m |
| `PadyarHigh5xxRate` | More than 5% of an install's requests in 10 minutes are 5xx, with at least 20 requests. Requests to `/metrics` and `/api/health` are not counted (see below). 503 counts, so turning on maintenance mode needs a silence | 5m |
| `PadyarChatLatencyHigh` | p95 of `/chat` above 8 s, with at least 10 chats in 10 minutes | 10m |
| `HostDiskLow` | Under 10% free on a writable, non-tmpfs filesystem | 10m |
| `HostPostgresDown` | `pg_up == 0`, or the exporter does not answer | 2m |
| `HostCertExpiring` | The origin's Let's Encrypt certificate has under 14 days left | 1h |
| `HostTunnelDown` | cloudflared holds no connection, or its metrics do not answer | 2m |
| `HostOriginProbeFailed` | The site does not open through nginx and TLS while the app is up | 3m |
| `HostDiskFilling` (warning) | Under 20% free | 30m |
| `MonitoringTargetDown` (warning) | node_exporter or the origin probe cannot be read | 5m |
| `MonitoringHeartbeat` | Always. Its absence means Prometheus is not evaluating rules | 0m |

**`PadyarHigh5xxRate` leaves out the stack's own requests. This differs from
the feature SPEC text on purpose.** Prometheus reads `/metrics` every 15 s,
the origin probe reads `/api/health` every 15 s and the watchdog every 60 s:
about 90 successful requests per 10 minutes that no visitor made. Counted,
they would meet the 20-request floor on an install nobody uses, and they
would dilute the error ratio of a busy one (5 errors in 30 visitor requests
is 17%, but 4% with the stack's 90 added). So `route!~"/metrics|/api/health"`
is on all three parts of the rule: the errors, the total, and the floor.
`deploy/monitoring/tests/padyar_rules_test.yml` has one install per part that
proves it. A real outage of `/api/health` is still seen: the watchdog texts
it and `HostOriginProbeFailed` fires.

Not in the file yet, on purpose: `PadyarAICircuitOpen` (waits for the circuit
state to be published at startup), `PadyarBackupFailed` and
`PadyarBackupStale` (wait for the verified-backup metrics and the backup
interval gauge). A rule whose metric does not exist would load, evaluate to
nothing, and never fire. `tests/test_monitoring_rules.py` keeps that list and
fails if a name is in both places or in neither.

Disks mounted under `/mnt`, `/media` or `/run` have no `node_filesystem_*`
series: the Ubuntu build of node_exporter excludes those mount points, so
`HostDiskLow` cannot see them. Source: the package's
`debian/patches/0001-Debian-defaults.patch` at the noble version
1.7.0-1ubuntu0.3, line 27, which sets the default to
`^/(dev|proc|run|sys|mnt|media|var/lib/docker/.+|var/lib/containers/storage/.+)($|/)`
(https://git.launchpad.net/ubuntu/+source/prometheus-node-exporter/tree/debian/patches/0001-Debian-defaults.patch?h=import/1.7.0-1ubuntu0.3#n27).
Before installing, run `df -h` on the host: a disk that matters and is
mounted under one of those paths is not watched.

Two caveats until the matching app changes land:

- Until `/metrics` merges every uvicorn worker (open PR, "DEP-1" in the
  SPEC), each scrape sees one worker of several. `PadyarHigh5xxRate` and
  `PadyarChatLatencyHigh` are then noisy. Do not install the stack on a
  production host before that change is deployed there.
- The rules reach the host only when someone re-runs
  `deploy/55-monitoring.sh`. `deploy/padyar-deploy.sh` does not touch the
  stack, because it deploys one install and the stack belongs to the host.

### What the tests prove, and what they do not

`tests/test_monitoring_rules.py` checks that every metric a rule reads exists
in its real source (the app registry, or the allowlist of the exporter
behind the selector's `job`, each name linked to its source at the packaged
version), that every label exists on the metric, and runs `promtool check
rules`, `promtool test rules`, `promtool check config` and `amtool
check-config` when the host's versions (2.45, 0.26) are on `PATH`. The CI job
`monitoring-rules` installs promtool from the Ubuntu archive; amtool is not
installed by any CI job, and the install script runs it on the host before
any restart.
`tests/test_monitoring_install_script.py` tests the script's functions one by
one with temp files and stub commands.

Neither proves the install itself. The script needs root, systemd and apt;
it has not been run by a test, and running it on a host is a separate step.

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

Either change belongs in `deploy/nginx/instance.conf.template`. It is a
separate change (the nginx pull request #159), not part of the monitoring
stack, and it is not in this tree yet. `deploy/55-monitoring.sh` checks each
site and prints a loud warning when `/metrics` does not answer 404 through
nginx. Until the nginx change is merged and deployed, that warning is
expected on every install, and the script says that re-rendering the site
would not help. Once the template has the block, the warning carries the
re-render command (`17-watchdog.sh` with the install's own
`MAINTENANCE_TITLE`; a wrong title changes the maintenance page visitors see).

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

On a host, the key is in `deploy/env/instance.env.template`, empty by
default (empty means `/metrics` answers only to an admin session).
Installs created before this key was in the template have no
`METRICS_TOKEN=` line at all, and a `sed` replace would change nothing. So run
`deploy/55-monitoring.sh` first: it stops on a missing or empty token and
prints the exact command for that install (an append when the line is
missing, a `sed` replace when it is empty). Run that command, which restarts
the app, then run the script again. After a token change, run the script
right away: between the restart and the run, every scrape gets a 403 and
`PadyarAppDown` fires after 2 minutes.

`METRICS_TOKEN` is not listed in `.env.example` (the local development file).

Treat the token like any other secret. Never in the repo, never in a
screenshot.

### Removing the stack

From a checkout of the kit, as root. This removes everything the script
creates; nothing of the installs themselves is touched.

```bash
UNITS="prometheus prometheus-alertmanager prometheus-node-exporter prometheus-postgres-exporter prometheus-blackbox-exporter"
sudo systemctl disable --now $UNITS
sudo apt-get purge $UNITS
for u in $UNITS; do sudo rm -f /etc/systemd/system/$u.service.d/padyar-limits.conf; sudo rmdir /etc/systemd/system/$u.service.d 2>/dev/null; done
sudo systemctl daemon-reload
sudo rm -rf /etc/prometheus /etc/padyar-monitoring /var/lib/prometheus   # the last one is the metric history
sudo rm -f /root/.secrets/alertmanager-operator.pass /root/.config/amtool/config.yml
sudo bash -c 'rm -rf /root/padyar-backups/monitoring-*'   # old config copies; they hold the operator password
sudo -u postgres psql -c 'DROP ROLE prometheus'
sudo groupdel padyar-alertread
```

The `metrics: 127.0.0.1:20241` line in `/etc/cloudflared/config.yml` can
stay: it is harmless. To remove it, delete that one line, check the config,
then restart the tunnel, outside event hours (every site on the host drops
for a few seconds):

```bash
sudo sed -i '/^metrics: 127\.0\.0\.1:20241$/d' /etc/cloudflared/config.yml
sudo cloudflared --config /etc/cloudflared/config.yml ingress validate \
  && sudo systemctl restart cloudflared
```

Do **not** restore the copy the first run made
(`/root/padyar-backups/cloudflared-config.yml.<UTC time>`) instead.
`deploy/40-cloudflare-tunnel.sh` rewrites the whole file for one domain, so
the file may have changed since that copy, and an old copy can drop the
ingress rule of a site added later.

The `rm` of the backup copies runs inside `sudo bash -c`: `/root` is not
readable by the operator's own shell, so a plain `sudo rm -rf /root/...-*`
would not expand the `*` and would remove nothing.

## Retention

`/metrics` itself stores nothing: its values reset on restart. The history is
kept by the Prometheus server that `deploy/55-monitoring.sh` installs:

- **30 days**, or a **size cap**, whichever comes first
  (`--storage.tsdb.retention.time=30d --storage.tsdb.retention.size=<cap>MB`
  in `/etc/default/prometheus`);
- the cap is the smaller of 5 GB and 30% of (the free space on the
  partition of `/var/lib/prometheus` + the TSDB's current size). If that is
  under 1 GB, the script stops;
- the script prints the number it chose.

**This differs from the feature SPEC on purpose.** SPEC REQ-016 says 30% of
the free space alone. But the TSDB's own data counts as used space, so with
the SPEC formula every re-run would lower the cap a little and Prometheus
would delete history to meet it. Counting the TSDB's current size fixes
that. The change is called out in the pull request for the owner's review.

The 30 days, the 5 GB and the memory caps (`MemoryMax=` drop-ins) are
estimates, not measurements. Review them after 30 days of real data.

The operational history this install keeps for humans is a different
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

## Checking it yourself

```bash
# Run the endpoint's tests
.venv/bin/python -m pytest tests/test_metrics.py -q

# Run the rule and install-script tests (promtool 2.45 / amtool 0.26 on PATH
# for the promtool cases; they skip with a reason otherwise)
.venv/bin/python -m pytest tests/test_monitoring_rules.py tests/test_monitoring_install_script.py -q
promtool test rules deploy/monitoring/tests/padyar_rules_test.yml

# Look at the live output with an admin session, on a running dev server
curl -s -H "Authorization: Bearer $METRICS_TOKEN" http://127.0.0.1:8000/metrics
```

Related files: `app/services/metrics.py`, `app/routers/metrics.py`,
`app/main.py` (the `prometheus_metrics` middleware),
`docs/features/metrics-endpoint/SPEC.md`, `deploy/55-monitoring.sh`,
`deploy/monitoring/`, `docs/features/monitoring-stack/SPEC.md`.
