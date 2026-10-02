# SPEC: Critical Watchdog — down-SMS + branded maintenance page

| Field | Value |
|-------|-------|
| Created | 2026-08-30 |
| Updated | 2026-10-01 |
| Status | Implemented |
| Domain | infrastructure |
| Author | تیم پادیار |
| Sources | The 2026-08-30 13:41 UTC incident (six consecutive 502s on the retired event install's domain during a deploy restart); owner confirmations of 2026-08-30 (Asanak credit unit is rial; free-text delivery enabled on this account); the shipped code, commits `6335fce..906a949` |

This document describes what IS shipped on branch `feat/critical-watchdog`, not
what was planned. Where a number matters — 3 fails, 60 s timer, 1800 s re-alert,
×10 rial — it is quoted from the code and is the contract.

---

## 1. Scenario

**The origin story.** On 2026-08-30 at 13:41 UTC, a routine deploy restarted
uvicorn on the (now retired) event install. For the duration of the restart,
its domain returned six consecutive raw **502** pages to visitors.
Nobody was paged — the deploy finished, the 502s stopped, and the only record
was the access log. Two failures stood in that window:

1. A visitor at the booth saw nginx's default 502 — an English error page with
   no brand, no Persian, and no promise of return.
2. If the app had NOT come back (crash, hang, wedged boot), nobody would have
   known until a human happened to open the site. Three minutes or three
   hours — identical silence.

This feature closes both, one watchdog per install on the host:

| Install | Domain | Probed at | Watchdog unit |
|---|---|---|---|
| `myevent` | `myevent.example.com` | `127.0.0.1:8010` | `padyar-watchdog@myevent` |

**Who triggers what, from where:**

- **Visitor** — hits the site from their phone while uvicorn is dead or
  restarting. nginx, not the app, answers. They must see a branded Persian
  page that reloads itself — never a raw 502.
- **Admin (off-site)** — receives an SMS on their personal phone when their
  install has been down for ~3 real minutes. They do nothing to arm this; the
  phone number sits in their install's admin panel.
- **Operator** — installs and verifies the machinery with one script and
  systemd commands; tests it end-to-end by stopping an app for 3 minutes.

## 2. What the visitor sees

When the app process is gone (crash, hang, deploy restart), nginx is the only
thing still listening. Each vhost (`deploy/nginx/*.padyar.com.conf`)
carries:

```nginx
proxy_intercept_errors on;
error_page 502 504 =503 /__maintenance.html;

location = /__maintenance.html {
    internal;
    root /var/www/padyar/maintenance/{slug};   # per install
    add_header Cache-Control "no-store" always;
    add_header Retry-After 60 always;
}
```

- **502 and 504 only** are replaced, with status **503** and the static
  branded page (`deploy/nginx/maintenance.html`), which says:
  «چند دقیقه‌ای دیگر برمی‌گردیم.» and
  «در حال به‌روزرسانی هستیم؛ همین صفحه به‌زودی خودش دوباره بارگذاری می‌شود.»
- The page **meta-refreshes every 30 s**, so a visitor holding their phone
  rides through a deploy restart without touching anything.
- `Cache-Control: no-store` — an error page must never be cached by any
  middlebox; `Retry-After: 60` — well-behaved crawlers back off a minute.
- The location is `internal`: it is not a URL anyone can browse to; it exists
  only as an `error_page` target.
- The installer (`deploy/17-watchdog.sh`) renders one copy per install with
  the site title substituted, so nginx never templates anything at request
  time.

**Why the app's own 503 passes through untouched.** The app has its own
in-app maintenance mode, and its 503 carries a JSON body that is the app's
deliberate answer — turning that mode on is an operator decision that must
reach the client as the app wrote it. `proxy_intercept_errors on` only
replaces statuses that have a matching `error_page` line; only 502/504 have
one. So: **app says 503 → visitor sees the app's JSON; nginx can't reach the
app (502/504) → visitor sees the branded page.** Collapsing the two would
mean nginx silently overwriting the app's maintenance payload with a generic
page — the exact class of "built but not wired" surprise this repo forbids.

## 3. What the admin receives

A systemd timer runs one watchdog cycle per minute per install
(`padyar-watchdog@.timer`: `OnUnitActiveSec=60s`, `AccuracySec=10s`,
`Persistent=true`). Each cycle GETs `http://127.0.0.1:{port}/api/health`
(5 s timeout); anything but HTTP 200 — refused, reset, timeout — counts as
down.

- **3 consecutive failed probes** (`FAILS_BEFORE_ALERT = 3`) at ~1 probe/min
  means **~3 minutes of real visitor-facing downtime** before the first SMS.
- The SMS reports the time the install **actually went down** —
  `down_since` is anchored at the first failure of the streak, not the third.
- While the outage continues, at most **one reminder every 1800 s** (30 min).
- **Recovery is silent.** One healthy probe resets `fail_count` and
  `down_since`; there is no "recovered" SMS. Recovery needs no human action,
  and the alert budget is spent only on states that do.

The two SMS texts, verbatim from `deploy/watchdog/watchdog.py` (Tehran time,
`+03:30` — Iran has no DST):

```
پادیار | هشدار بحرانی: چت‌بات {name} از ساعت {HH:MM} (به وقت تهران) پاسخ نمی‌دهد.
```

```
یادآوری — پادیار | هشدار بحرانی: چت‌بات {name} از ساعت {HH:MM} (به وقت تهران) پاسخ نمی‌دهد.
```

`{name}` is the install's display name; the reminder is the same text with the
`یادآوری — ` prefix.

## 4. What NEVER alerts

By design — each of these was a deliberate trade, not an omission:

- **In-app task errors.** A failing AI tier, a wedged retrieval index, a
  rejected upload — the process still answers `/api/health` with 200. Those
  are admin-panel/ops concerns; an SMS at 2am for them would train the owner
  to ignore the channel.
- **Single blips.** One healthy probe between failures resets the streak, so
  a flapping install can never accumulate 3 fails by accident.
- **Deploys shorter than ~3 min.** A deploy restart takes ~60 s, during which
  visitors see the maintenance page (§2). It cannot reach the 3-fail
  threshold, so it never SMSes. **The page covers the visitor's experience;
  the phone is reserved for outages that need a human.** An operator who
  wants a longer maintain window takes that knowingly.
- **Credit checks during downtime.** The wallet trip-wire runs only on
  healthy cycles — during an outage the down-SMS is the priority.

## 5. Credit watch (the SMS wallet that carries the alerts)

Once per UTC day, on the first healthy cycle where the wallet is below the
floor, the watchdog sends:

```
پادیار | اعتبار پیامک آسانک به {credit} تومان رسید؛ کمتر از حد {threshold} تومان است. لطفاً شارژ کنید.
```

Amounts are in **toman** with the Persian thousands separator (U+066C) and
Latin digits, so they render identically in every SMS client.

- Asanak's `getcredit` returns the wallet in **rial**. The comparison in code
  is `credit_rial < threshold_toman * 10` (`RIAL_PER_TOMAN = 10`); the SMS
  shows `credit_rial // 10` next to the toman threshold. A threshold of
  300,000 toman means 3,000,000 rial at the gateway.
- The unit (rial) and the fact that this account may send **free-text** SMS
  (the old "templates only" restriction was lifted by Asanak support) were
  both confirmed by the owner on 2026-08-30. Free-text delivery is what makes
  the alert texts above possible at all.
- Once-per-day dedup is by UTC day-string in state; a below-floor wallet
  nags once, not every minute.
- The check is skipped entirely when Asanak is not configured on the install
  (`asanak_configured()`), and a gateway error on `getcredit` is a journal
  note, never a crash.

## 6. Failure modes

The watchdog is "the thing that watches the things"; it is allowed exactly
one failure mode of its own: **none**. Every degradation below becomes a
`[watchdog] …` line on stdout → journald (`SyslogIdentifier=padyar-watchdog-%i`),
and the cycle always exits 0 — a oneshot that "failed" because it reported
bad news would train operators to ignore the unit state.

| Condition | Behavior |
|---|---|
| App package not importable (the unit's `PYTHONPATH` is missing or wrong) | Journal ONE line per cycle: `cannot import the app (ModuleNotFoundError): SMS disabled; check PYTHONPATH in the unit`. The probe, the fail streak and `cached_phone` keep working. The credit check, every send and the alert SMS step (§11) are skipped, so no `send failed` line follows and no alert is marked as sent: it still goes out once the unit is fixed. No SMS can go out until then (see §8, "Rolling out the PYTHONPATH fix"). |
| PostgreSQL down (settings unreadable) | The settings read raises. Use `cached_phone` and `cached_threshold` from state (the last read that succeeded; `300000` if the threshold was never read) and journal `settings unreadable (<Type>), using cached phone`. The down-SMS and the alert SMS still go out. Alerting the right person on stale data beats alerting nobody. See "During a PostgreSQL outage" below. |
| PostgreSQL down, and no phone was ever read (fresh install) | Journal `DOWN but no alert_critical_phone configured`, no SMS, no crash. |
| Alert phone empty (read from a database that answered) | Journal `DOWN but no alert_critical_phone configured`, no SMS. State (fail streak) is still persisted. The empty value is cached, so clearing the phone in the admin panel turns the SMS off. |
| SMS send fails | Journal `send failed: SmsError` — the exception's **class name** only (`type(e).__name__`, e.g. `SmsError`), never its message; state still persisted, so the streak is not re-lived next tick. |
| Threshold row is garbage | Falls back to the documented default `300000`, never to 0 (which would alert every cycle). |
| Corrupt state file | Journal note, reset to fresh state — loses one alert cycle at most. |
| Probe raises (reset, DNS, …) | Counts as down; the exception never escapes the cycle. |
| Unknown `--install` key | Journal note, cycle skipped, `None` returned. |
| Bad CLI usage (`argparse`) | Exit 2 before any cycle — a deployment typo SHOULD be loud. |

One row above deserves its own warning: **a spent `sms_daily_budget` also
kills the outage SMS.** The down-SMS travels through `send_asanak`, whose
`_spend_budget` raises `SmsError` *before* any gateway request once the
operator-set daily budget is spent — so during a long day the alert dies as a
`send failed: SmsError` journal note instead of reaching the phone. The
default budget is 0 (no cap), so out-of-the-box installs are unaffected; an
operator who sets a budget should keep headroom for critical alerts, because
the watchdog's messages come off the same Asanak credit as everything else.

### During a PostgreSQL outage

Fixed 2026-10-02. Before that, the reader went through `get_setting()`,
which never raises: on a database error it returns its default. So during an
outage the reader returned an empty phone, the cached-phone fallback never
ran, and the cycle saved `cached_phone = ""`. Measured with PostgreSQL at a
closed port: 0 SMS, not the down-SMS and not `HostPostgresDown`.

Now:

- `_read_settings()` reads both keys in one query through
  `read_settings_strict()` (`app/db/queries.py`), which raises on a database
  error. An empty phone comes back only from a database that answered.
- Only a successful read changes `cached_phone` and `cached_threshold`.
- After a failed read the watchdog calls `pg.set_unavailable()`
  (`app/db/pg.py`). Every later database access in that one-shot process
  then fails at once instead of waiting `DB_CONNECT_TIMEOUT`. Without it one
  SMS made 11 database attempts (the gateway settings in
  `app/services/sms.py`, the log levels, the log write): about 110 s per SMS
  at the production timeout of 10 s. The app server never calls it.
- The Asanak credentials still come from the settings -> `.env` fallback in
  `app/services/sms.py`, unchanged.

Measured 2026-10-02 on a developer machine, one cycle with the real reader,
the real `_send` and the real probe, a local stub as the Asanak gateway, the
down-SMS and one `HostPostgresDown` SMS sent:

| PostgreSQL | `DB_CONNECT_TIMEOUT` | Cycle |
|---|---|---|
| Closed port (connection refused) | 3 s | 4.17 s |
| Closed port (connection refused) | 10 s | 11.16 s |
| Host that drops packets | 3 s | 3.78 s |
| Host that drops packets | 10 s | 10.57 s |

So an outage costs one connect timeout plus about one second, far inside
`TimeoutStartSec=300s`.

Journal lines that are expected in an outage cycle and need no action:

- `couldn't stop thread '<name>' within 0 seconds` (psycopg): up to 4 per
  cycle, one per pool thread still running when `set_unavailable()` closes
  the pool without waiting (`padyar-worker-0` to `padyar-worker-2` and
  `padyar-scheduler`). Measured 1 to 4 in 19 cycles. Waiting for them cost
  10 s with a host that drops packets, which is why the close does not wait.
- Per SMS sent: `[applog] dropped sms/sms.send.queued: DatabaseUnavailable`
  and `[sms-outbox] record failed: database marked unavailable in this
  process`. The log store and the SMS outbox live in the database that is
  down.

Two conditions for an SMS during an outage, both unchanged by this fix:
the Asanak credentials must be in the install's `.env` (with `SECRET_KEY`
set, if they are stored encrypted there), and `SMS_DAILY_BUDGET` in `.env`
must be empty or `0`. A budget above 0 makes `_spend_budget` write to the
database before the send, so every SMS fails with `send failed` until the
database is back (see `docs/features/monitoring-stack/SPEC.md`, the DB-down
rows of section 8).

## 7. State

One JSON file per install: `/var/lib/padyar-watchdog/{install}/state.json`
(`fail_count`, `down_since`, `last_alert`, `credit_day`, `credit_alerted`,
`cached_phone`, `cached_threshold`, plus the alert SMS keys of §11).
`cached_phone` and `cached_threshold` hold the last settings read that
succeeded; a failed read never changes them.

- **Why per-install directories:** the per-install watchdog services run as
  distinct service users (the pattern is
  `padyar-{slug}`) under one
  root-owned parent (`/var/lib/padyar-watchdog`). Each user owns exactly its
  own subdirectory. A flat file in the parent would be writable by neither
  (or by one only), and a persist failing on permissions would reset
  `fail_count` every run — silently muting all alerts. The installer creates
  the directories with the right owners; the unit's
  `ReadWritePaths=/var/lib/padyar-watchdog` matches.
- Writes are atomic (tmp file + `os.replace`), so a crash mid-write can
  never leave a half-written JSON.
- The state lives outside the app tree so a broken (or wiped) install cannot
  erase the watchdog's memory of an ongoing outage.

## 8. Ops runbook

Install (after `10-install-app.sh` for the install and `15-nginx-and-ssl.sh`;
safe to re-run):

```bash
sudo bash deploy/17-watchdog.sh
```

Verify the timers are scheduled and a cycle runs clean:

```bash
systemctl list-timers 'padyar-watchdog@*'
journalctl -u padyar-watchdog@myevent.service -n 20
```

Change the alert phone or the credit threshold — per install, in the admin
panel: `/secure-panel-admin/settings/sms` (تنظیمات → ثبت‌نام و پیامک), card
«هشدارهای بحرانی», saved with the page's «ذخیره تنظیمات» button. The
watchdog re-reads both from the database **every cycle** — no restart, no
reload. An empty phone means alerts off. The threshold is typed in toman
(Persian digits accepted); default 300,000.

End-to-end test (this is the scenario test — do it once after install):

```bash
sudo systemctl stop padyar-myevent
journalctl -u padyar-watchdog@myevent.service -f
# wait ≥ 3 minutes: three cycles log, the third sends the SMS
sudo systemctl start padyar-myevent
```

Expect: exactly one down-SMS (~3 min in), silence on recovery, and
`https://myevent.example.com` serving the branded maintenance page for the
whole window.

### Rolling out the PYTHONPATH fix

Until this fix, `padyar-watchdog@.service` had no `PYTHONPATH`. systemd runs
`/opt/padyar-watchdog/watchdog.py`, and Python puts the script's directory on
`sys.path`, not the working directory `/opt/padyar-<slug>`. So every lazy
`from app...` import failed with `ModuleNotFoundError`: the watchdog could not
read the alert phone, could not read the credit, and could not send any SMS.
The journal only said `settings unreadable (ModuleNotFoundError), using cached
phone`. The unit now sets `Environment=PYTHONPATH=/opt/padyar-%i`.

**This was reproduced on a developer machine, not verified on the server.**

A code deploy does not change the unit on a host. `deploy/17-watchdog.sh`
copies the unit to `/etc/systemd/system/` and runs `systemctl daemon-reload`,
so re-run it once per install, from a checkout that has this fix:

Note the time right after the installer, in the same shell. The check
below reads only the journal after that time. `START` comes after the
installer because a timer run before its `daemon-reload` still uses the old
unit and would leave a `ModuleNotFoundError` line after `START`.

```bash
sudo MAINTENANCE_TITLE='<visitor-facing name>' bash deploy/17-watchdog.sh <slug> <port> <domain>
START="$(date '+%Y-%m-%d %H:%M:%S')"
```

`MAINTENANCE_TITLE` is required here: the script also re-renders the
maintenance page, and without it the page goes back to the default title. The
timer picks up the new unit on its next tick; nothing else needs a restart.

Wait for two timer runs (about two minutes). The first command must count at
least 2 runs, and the second must print nothing:

```bash
journalctl -u padyar-watchdog@<slug> --since "$START" | grep -c "Finished"
journalctl -u padyar-watchdog@<slug> --since "$START" | grep -i "cannot import\|ModuleNotFound"
```

Do not use `journalctl -n 50` for this check. The last 50 lines still hold the
`ModuleNotFoundError` lines from before the fix for several minutes, so a
correct fix would look failed.

Then do the end-to-end test above once, because before this fix no down-SMS
could have reached the phone.

## 9. Wiring — reader/writer pairs that must never drift

| Writer | Reader | What breaks if they drift |
|---|---|---|
| Admin API stores `alert_critical_phone` (`set_setting`, `app/routers/admin.py`) | `_read_settings()` reads it every cycle (`deploy/watchdog/watchdog.py`) | SMS goes nowhere or to a stale number |
| Admin API stores `alert_credit_threshold_toman` (same route) | Same reader; compared ×10 as rial | Wallet floor silently wrong |
| `read_settings_strict()` raises on a database error (`app/db/queries.py`); `pg.set_unavailable()` makes later connections fail at once (`app/db/pg.py`) | `_read_settings()` in `watchdog.py` | A strict read that swallows errors again turns an outage into "no phone": no SMS at all. Without the switch, each SMS waits `DB_CONNECT_TIMEOUT` per setting, about 110 s at 10 s |
| `deploy/17-watchdog.sh` renders pages at `/var/www/padyar/maintenance/{slug}/__maintenance.html` | vhost `location = /__maintenance.html` `root` in the vhost rendered from `deploy/nginx/instance.conf.template` | 502 falls through to nginx's default error page — the incident again |
| Installer creates `/var/lib/padyar-watchdog/{slug}` owned by `padyar-{slug}`; unit `ReadWritePaths=/var/lib/padyar-watchdog` | `STATE_DIR` + `run_cycle` state path in `watchdog.py` | Persist fails on permissions; fail streak resets every run; alerts muted |
| systemd instance names (generally `{slug}` — `User=padyar-%i`, `WorkingDirectory=/opt/padyar-%i`) and `APP_PORT` in each install's `.env` (the unit's `EnvironmentFile`) | `install_port()` in `watchdog.py` reads `APP_PORT` from that EnvironmentFile | Cycle journals "unknown install" forever, or probes the wrong port |
| Unit `WorkingDirectory=/opt/padyar-%i` + `Environment=PYTHONPATH=/opt/padyar-%i`; the script itself lives in `/opt/padyar-watchdog` | The lazy `from app...` imports in `_read_settings()`, `_send()` and `_read_credit()` | `ModuleNotFoundError` every cycle: no phone, no credit, no SMS. The journal says `cannot import the app` |
| Timer `OnUnitActiveSec=60s` | `FAILS_BEFORE_ALERT=3` (docs claim "~3 min") | The detection-lag promise silently changes |
| `asanak_credit()` returns rial (`app/services/sms.py`) | `RIAL_PER_TOMAN = 10` conversion + toman rendering in the SMS | Alerts fire an order of magnitude early or late |
| Admin stores phone canonical `+98…`; `asanak_destination()` strips the `+` at the gateway edge (`app/services/sms.py`) | `_send()` in `watchdog.py` applies it | Asanak rejects the destination (HTTP 406) and the alert dies in a journal note |

## 10. Tests

- `tests/test_watchdog_logic.py` — the pure core: 3-fail threshold, 30-min
  re-alert, recovery reset, blip immunity, once-per-UTC-day credit dedup,
  verbatim Persian texts, Tehran half-hour clock, `APP_PORT` resolution.
- `tests/test_watchdog_io.py` — `run_cycle` with every dependency injected:
  exactly one SMS per 3 fails, cached-phone fallback on DB-down, no-phone
  journal note, corrupt-state reset, per-install state directory, and one
  `cannot import the app` line per cycle when the app package is missing,
  with no send tried and no alert marked as sent.
- `tests/test_watchdog_unit_import.py`: runs a copy of the script from a
  directory outside the repo, with cwd = a temp install dir and only the
  unit's `Environment=` values, the way systemd runs it. It fails if the
  script cannot import `app` in that shape, or if it reads the repo's `.env`.
- `tests/test_watchdog_db_down.py`: the REAL settings reader against a
  database that does not answer: it raises, the cached phone and threshold
  are used and kept, the down-SMS and `HostPostgresDown` still go out, and
  against PostgreSQL at a closed port one cycle waits for the pool once and
  sends both SMS with the `.env` credentials. Allow-controls with the
  database up.
- `tests/test_settings_strict_read.py`, `tests/test_pg_fail_fast.py`,
  `tests/postgres/test_settings_strict_read_pg.py`: the two app additions
  the reader relies on.
- `tests/test_critical_alert_settings.py` — the writer side of the settings
  pair: defaults, `0912…` → `+98912…` canonicalization, refusals, empty-phone
  disables, Persian-digit threshold.

## 11. Alert SMS step (monitoring stack)

Added 2026-10-01. The full contract is section 5.5 of
`docs/features/monitoring-stack/SPEC.md` (REQ-040 to REQ-054). This is the
short version.

On a host that runs the monitoring stack (`deploy/55-monitoring.sh`), every
cycle ends with one more step, after the probe, the down-SMS and the credit
check. Alertmanager itself sends nothing; the watchdog pulls, so it also
notices when Alertmanager or Prometheus dies.

- It reads `GET http://127.0.0.1:9093/api/v2/alerts?active=true` with basic
  auth user `watchdog` (password from
  `/etc/padyar-monitoring/alertmanager-watchdog.pass`, 5 s timeout per
  socket read, 8 s for the whole GET, no proxy, no redirects followed). An
  answer over 1 MiB is treated as a failure and never parsed.
- It texts alerts with `page="sms"` and state `active` that carry this
  install's `install` label, or no `install` label when
  `/etc/padyar-monitoring/host-alerts-owner` names this install. While the
  install is down (3 failed probes), its own alerts wait: the down-SMS
  covers them.
- At most one SMS per cycle and ten send attempts per UTC day per
  install. The tenth is the "cap reached" notice. A still-firing alert is
  reminded every 6 h. There is no "resolved" SMS.
- These limits live only in the state file. So each SMS is recorded and
  saved before the sender runs (write-ahead), and when the state cannot be
  saved no alert SMS goes out at all. This is at most once: a process
  killed between the save and the send loses that SMS, and a still-firing
  alert is texted again only at its 6 h reminder.
- The text comes only from a fixed table keyed by `alertname`. Label values
  and annotations never reach the phone, because any install on the host can
  post an alert.
- The host-owner install also texts "monitoring is down" after 3 cycles in
  a row in which Alertmanager does not answer or the always-firing
  `MonitoringHeartbeat` alert is missing, and one notice for every new
  silence. A cycle in which the install is not the owner, or skips the
  step, ends that streak.

| Condition | Behavior |
|---|---|
| Password file absent | Step skipped without a journal line. The install behaves exactly as before. |
| Password file unreadable | Journal `monitoring alerts OFF: cannot read alertmanager-watchdog.pass (<Type>); re-run deploy/55-monitoring.sh <slug>` on every cycle. |
| Alertmanager unreachable or bad answer (a body over 1 MiB, or a GET still running after 8 s, included) | Journal `alertmanager unreadable (<Type>)`. The host owner texts after 3 cycles. |
| State cannot be saved (disk full, read-only directory) | Journal `alert SMS skipped: state not saved` on every cycle in which an alert SMS is due. No alert SMS until the state can be written. |
| Send fails (a spent `sms_daily_budget` included) | Journal `alert send failed: <Type>`. Nothing is marked as sent, so the alert is tried again after 300 s, but the attempt counts against the daily cap. This deviates from REQ-051 of the feature SPEC, which says the counter does not go up. Reason: a gateway that accepts the SMS and then fails the call (a timeout after the accept) would otherwise deliver one SMS every 300 s, 288 a day. Counting attempts keeps the daily cap a bound in every failure mode. So about 45 minutes of gateway outage (ten tries, 300 s apart) uses up that day's alert cap. |
| Alert phone empty | Journal `alert pending but no alert_critical_phone configured`. |

New state keys, with defaults in `_fresh_state` so an older state file still
loads: `alert_sent`, `alert_day`, `alert_sms_today`, `alert_retry_after`,
`am_fail_count`, `am_down_since`, `am_last_alert`, `silence_seen`.
`alert_sent` keeps at most 1000 fingerprints, the oldest dropped first, so
forged alerts cannot grow the file without bound. With more than 1000 alerts
held at once, the dropped ones look new on the next cycle and are texted
again, but only until the daily cap: the spend stays bounded.

The unit gained `TimeoutStartSec=300s`. A oneshot unit has no start timeout
by default, so a process that never finished held this install's watchdog
forever, the down-SMS included. 300 s ends only a truly stuck run. It is long
on purpose: an Alertmanager stall is already cut by the 8 s GET deadline, and
the down-SMS and the low-credit SMS save state only at the end of a cycle, so
a shorter timeout that killed a slow but working cycle after one of them
would make the next cycle send it again. A run still in progress makes the
60 s timer skip a tick, never overlap. The group membership needs no unit change: systemd adds the groups the
system group database lists for `User=` (so `padyar-alertread` from the
installer applies at the next run), and `ProtectSystem=full` leaves `/etc`
readable.

Tests: the pure decisions are in `tests/test_watchdog_logic.py`, the cycle in
`tests/test_watchdog_io.py`. Every Alertmanager reader and the SMS sender are
fakes there, so no test sends an SMS.
