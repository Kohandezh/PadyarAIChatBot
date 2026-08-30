# SPEC: Critical Watchdog — down-SMS + branded maintenance page

| Field | Value |
|-------|-------|
| Created | 2026-08-30 |
| Updated | 2026-08-30 |
| Status | Implemented |
| Domain | infrastructure |
| Author | تیم پادیار |
| Sources | The 2026-08-30 13:41 UTC incident (six consecutive 502s on `inotex.padyar.com` during a deploy restart); owner confirmations of 2026-08-30 (Asanak credit unit is rial; free-text delivery enabled on this account); the shipped code, commits `6335fce..906a949` |

This document describes what IS shipped on branch `feat/critical-watchdog`, not
what was planned. Where a number matters — 3 fails, 60 s timer, 1800 s re-alert,
×10 rial — it is quoted from the code and is the contract.

---

## 1. Scenario

**The origin story.** On 2026-08-30 at 13:41 UTC, a routine deploy restarted
uvicorn on the INOTEX install. For the duration of the restart,
`inotex.padyar.com` returned six consecutive raw **502** pages to visitors.
Nobody was paged — the deploy finished, the 502s stopped, and the only record
was the access log. Two failures stood in that window:

1. A visitor at the booth saw nginx's default 502 — an English error page with
   no brand, no Persian, and no promise of return.
2. If the app had NOT come back (crash, hang, wedged boot), nobody would have
   known until a human happened to open the site. Three minutes or three
   hours — identical silence.

This feature closes both, for two installs on one host:

| Install | Domain | Probed at | Watchdog unit |
|---|---|---|---|
| INOTEX | `inotex.padyar.com` | `127.0.0.1:8001` | `padyar-watchdog@inotex` |
| ELECOMP | `elecomp.padyar.com` | `127.0.0.1:8002` | `padyar-watchdog@elecomp` |

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
thing still listening. Each vhost (`deploy/nginx/{inotex,elecomp}.padyar.com.conf`)
carries:

```nginx
proxy_intercept_errors on;
error_page 502 504 =503 /__maintenance.html;

location = /__maintenance.html {
    internal;
    root /var/www/padyar/maintenance/inotex;   # per install
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
  the site title substituted (INOTEX → «چت‌بات اینوتکس», ELECOMP → «چت‌بات
  الکامپ»), so nginx never templates anything at request time.

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
`Persistent=true`). Each cycle GETs `http://127.0.0.1:{8001|8002}/api/health`
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

`{name}` is `INOTEX` or `ELECOMP`; the reminder is the same text with the
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
| PostgreSQL down (settings unreadable) | Use `cached_phone` from state (last healthy read) and the default threshold; journal `settings unreadable (…), using cached phone`. Alerting the right person on stale data beats alerting nobody. |
| Alert phone empty | Journal `DOWN but no alert_critical_phone configured`, no SMS. State (fail streak) is still persisted. |
| SMS send fails | Journal `send failed: {Exception}`; state still persisted, so the streak is not re-lived next tick. |
| Threshold row is garbage | Falls back to the documented default `300000`, never to 0 (which would alert every cycle). |
| Corrupt state file | Journal note, reset to fresh state — loses one alert cycle at most. |
| Probe raises (reset, DNS, …) | Counts as down; the exception never escapes the cycle. |
| Unknown `--install` key | Journal note, cycle skipped, `None` returned. |
| Bad CLI usage (`argparse`) | Exit 2 before any cycle — a deployment typo SHOULD be loud. |

## 7. State

One JSON file per install: `/var/lib/padyar-watchdog/{install}/state.json`
(`fail_count`, `down_since`, `last_alert`, `credit_day`, `credit_alerted`,
`cached_phone`).

- **Why per-install directories:** the two watchdog services run as two
  different service users (`padyar-inotex`, `padyar-elecomp`) under one
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

Install (after `10-install-app.sh` for both installs and `15-nginx-and-ssl.sh`;
safe to re-run):

```bash
sudo bash deploy/17-watchdog.sh
```

Verify both timers are scheduled and a cycle runs clean:

```bash
systemctl list-timers 'padyar-watchdog@*'
journalctl -u padyar-watchdog@inotex.service -n 20
journalctl -u padyar-watchdog@elecomp.service -n 20
```

Change the alert phone or the credit threshold — per install, in the admin
panel: `/secure-panel-inotex/settings/sms` (تنظیمات → ثبت‌نام و پیامک), card
«هشدارهای بحرانی», saved with the page's «ذخیره تنظیمات» button. The
watchdog re-reads both from the database **every cycle** — no restart, no
reload. An empty phone means alerts off. The threshold is typed in toman
(Persian digits accepted); default 300,000.

End-to-end test (this is the scenario test — do it once after install):

```bash
sudo systemctl stop padyar-inotex
journalctl -u padyar-watchdog@inotex.service -f
# wait ≥ 3 minutes: three cycles log, the third sends the SMS
sudo systemctl start padyar-inotex
```

Expect: exactly one down-SMS (~3 min in), silence on recovery, and
`https://inotex.padyar.com` serving the branded maintenance page for the
whole window.

## 9. Wiring — reader/writer pairs that must never drift

| Writer | Reader | What breaks if they drift |
|---|---|---|
| Admin API stores `alert_critical_phone` (`set_setting`, `app/routers/admin.py`) | `_read_settings()` reads it every cycle (`deploy/watchdog/watchdog.py`) | SMS goes nowhere or to a stale number |
| Admin API stores `alert_credit_threshold_toman` (same route) | Same reader; compared ×10 as rial | Wallet floor silently wrong |
| `deploy/17-watchdog.sh` renders pages at `/var/www/padyar/maintenance/{slug}/__maintenance.html` | vhost `location = /__maintenance.html` `root` in `deploy/nginx/*.padyar.com.conf` | 502 falls through to nginx's default error page — the incident again |
| Installer creates `/var/lib/padyar-watchdog/{slug}` owned by `padyar-{slug}`; unit `ReadWritePaths=/var/lib/padyar-watchdog` | `STATE_DIR` + `run_cycle` state path in `watchdog.py` | Persist fails on permissions; fail streak resets every run; alerts muted |
| systemd instance names `inotex`/`elecomp` (`User=padyar-%i`, `WorkingDirectory=/opt/padyar-%i`) | `INSTALLS` keys and ports 8001/8002 in `watchdog.py` | Cycle journals "unknown install" forever, or probes the wrong port |
| Timer `OnUnitActiveSec=60s` | `FAILS_BEFORE_ALERT=3` (docs claim "~3 min") | The detection-lag promise silently changes |
| `asanak_credit()` returns rial (`app/services/sms.py`) | `RIAL_PER_TOMAN = 10` conversion + toman rendering in the SMS | Alerts fire an order of magnitude early or late |
| Admin stores phone canonical `+98…`; `asanak_destination()` strips the `+` at the gateway edge (`app/services/sms.py`) | `_send()` in `watchdog.py` applies it | Asanak rejects the destination (HTTP 406) and the alert dies in a journal note |

## 10. Tests

- `tests/test_watchdog_logic.py` — the pure core: 3-fail threshold, 30-min
  re-alert, recovery reset, blip immunity, once-per-UTC-day credit dedup,
  verbatim Persian texts, Tehran half-hour clock, production ports.
- `tests/test_watchdog_io.py` — `run_cycle` with every dependency injected:
  exactly one SMS per 3 fails, cached-phone fallback on DB-down, no-phone
  journal note, corrupt-state reset, per-install state directory.
- `tests/test_critical_alert_settings.py` — the writer side of the settings
  pair: defaults, `0912…` → `+98912…` canonicalization, refusals, empty-phone
  disables, Persian-digit threshold.
