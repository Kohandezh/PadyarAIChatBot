"""Watchdog decision core: pure logic, zero I/O, injected clock.

WHAT THIS MODULE DECIDES
------------------------
A systemd timer probes each install and asks this core one question per probe:
given the persisted state and this probe's result, what should happen now?
The answer is "none" | "alert" | "realert" for downtime, plus an independent
once-per-day low-SMS-credit trip-wire and the exact Persian SMS texts for
both. Nothing here knows how to probe, persist, or send.

WHY THE CORE IS PURE
--------------------
Every function takes plain inputs (state dict, booleans, epoch seconds) and
returns a value or mutates the dict. No sockets, files, or SMS calls. That is
what makes the alert policy testable without a network, a SIM, or waking
anyone at 3am, and it is why the I/O shell (run_cycle: health probes, the
STATE_DIR state file, the Asanak SMS API) lives above this core in Task 2.
If a function here wants to touch the outside world, it belongs in run_cycle,
never in this file.

POLICY IN ONE BREATH
--------------------
Three consecutive failed probes earn one alert; while the streak continues,
at most one reminder every REALERT_SECONDS; one healthy probe wipes the
streak, so a flapping install cannot re-arm the threshold by accident. The
SMS credit floor is checked once per UTC day because the wallet moves slowly
and a daily nudge is enough to trigger a top-up.

On a host that runs the monitoring stack, a last step texts the install's
page="sms" Alertmanager alerts: at most one SMS per cycle, ten per UTC day.
Its decisions live in this core too (select_alerts and the functions after
it); a host without the stack never reaches that step.
"""
import argparse
import base64
import copy
import json
import os
import threading
import time
import urllib.request
from datetime import datetime, timedelta, timezone

# The guarded install is named by the systemd instance (%i of
# padyar-watchdog@<install>.service) and configured by that install's own
# EnvironmentFile: APP_PORT says where the app listens on localhost — the
# same key the app unit's ExecStart expands. No hardcoded install table: a
# per-customer platform cannot keep one here, and the unit's environment is
# the authority an unknown key fails against.


def install_port(install: str) -> int | None:
    """The install's localhost port from APP_PORT, or None when unset.

    None is how "unknown install" manifests: the unit template always loads
    the install's .env as its EnvironmentFile, so a key with no APP_PORT
    behind it is a deployment typo (or a test), and the caller reports it
    instead of probing some guessed port.
    """
    raw = os.environ.get("APP_PORT", "").strip()
    try:
        return int(raw)
    except ValueError:
        return None

# 3 fails absorb a single blip or a rolling restart before an admin is woken;
# 1800s reminds about a long outage twice an hour at most, trading detection
# lag against SMS fatigue. Both are policy knobs, not laws of nature.
FAILS_BEFORE_ALERT = 3
REALERT_SECONDS = 1800

# Asanak reports the wallet in rial; operators think and top up in toman.
RIAL_PER_TOMAN = 10

# run_cycle (Task 2) keeps each install's state in
# STATE_DIR/<install>/state.json — a per-install SUBDIRECTORY, not a flat
# file. Why: installs run as different service users sharing this
# root-owned parent; each user needs write access to its own state, so the
# deployment gives each service user its own directory here. Outside the app
# tree, so a broken install cannot also erase the watchdog's memory.
STATE_DIR = "/var/lib/padyar-watchdog"

# Iran has had no DST since 2022, so a fixed +03:30 is the whole story.
_TEHRAN = timezone(timedelta(minutes=210))


def next_action(state: dict, healthy: bool, now: float) -> str:
    """Fold one probe result into `state`; return "none" | "alert" | "realert".

    Mutates and normalizes the passed dict (fail_count / down_since /
    last_alert) so the caller can persist it verbatim after every probe.
    """
    state.setdefault("fail_count", 0)
    state.setdefault("down_since", 0.0)
    state.setdefault("last_alert", 0.0)
    if healthy:
        # One healthy probe ends the streak: counters reset so the next
        # outage earns a fresh, full FAILS_BEFORE_ALERT countdown.
        state["fail_count"] = 0
        state["down_since"] = 0.0
        return "none"
    if state["down_since"] == 0.0:
        # Anchor at the FIRST failure of the streak, so the SMS reports when
        # the install actually went down, not when we became sure of it.
        state["down_since"] = now
    state["fail_count"] += 1
    if state["fail_count"] == FAILS_BEFORE_ALERT:
        state["last_alert"] = now
        return "alert"
    if state["fail_count"] > FAILS_BEFORE_ALERT and now - state["last_alert"] >= REALERT_SECONDS:
        state["last_alert"] = now
        return "realert"
    return "none"


def credit_alert(state: dict, credit_rial: int, threshold_toman: int, today: str, now: float) -> bool:
    """True at most once per UTC day when the Asanak wallet is below the floor.

    `now` is unused; it exists so the I/O shell can pass one clock everywhere.
    """
    if state.setdefault("credit_day", "") != today:
        state["credit_day"] = today
        state["credit_alerted"] = False
    if not state.get("credit_alerted", False) and credit_rial < threshold_toman * RIAL_PER_TOMAN:
        state["credit_alerted"] = True
        return True
    return False


def tehran_clock(now_epoch: float) -> str:
    """HH:MM in Tehran for an epoch; the only clock the SMS texts ever show."""
    return datetime.fromtimestamp(now_epoch, tz=_TEHRAN).strftime("%H:%M")


def down_message(name: str, now_epoch: float, reminder: bool = False) -> str:
    """Critical-down SMS text; `reminder=True` is the re-alert variant."""
    base = (
        f"پادیار | هشدار بحرانی: چت‌بات {name} از ساعت {tehran_clock(now_epoch)} "
        f"(به وقت تهران) پاسخ نمی‌دهد."
    )
    return f"یادآوری — {base}" if reminder else base


def low_credit_message(credit_toman: int, threshold_toman: int) -> str:
    """Low-SMS-credit SMS text, amounts in toman with Persian separators."""

    def _toman(n: int) -> str:
        # U+066C is the Persian thousands separator; digits stay Latin so the
        # amount renders identically in every SMS client.
        return f"{n:,}".replace(",", "٬")

    return (
        f"پادیار | اعتبار پیامک آسانک به {_toman(credit_toman)} تومان رسید؛ "
        f"کمتر از حد {_toman(threshold_toman)} تومان است. لطفاً شارژ کنید."
    )


# ════════════════════════════════════════════════════════════════════════
# ALERTMANAGER SMS STEP: the pure decisions.
#
# Alertmanager on the host has no receiver of its own, so nothing leaves it.
# Each install's watchdog pulls the page="sms" alerts instead and texts them
# with the install's own phone and Asanak credentials. Pulling (not a push
# from Alertmanager) is what lets the watchdog notice when Alertmanager or
# Prometheus itself dies. The functions below decide; run_cycle reads,
# sends and persists.

# 6 h between reminders keeps a long-lived alert (a 26 h backup window) at a
# handful of SMS. Ten a day per install bounds what an alert flood, or a
# forged alert posted by another install on the host, can cost. 300 s after
# a failed send keeps a dead gateway from being called every minute.
ALERT_REMIND_SECONDS = 21600
ALERT_SMS_DAILY_CAP = 10
ALERT_RETRY_SECONDS = 300

# Most fingerprints alert_sent keeps. Real installs have a handful of rules;
# the bound is for forged alerts, which any install on the host can post.
ALERT_SENT_MAX = 1000

# This Prometheus rule is always firing. A live Alertmanager without it means
# Prometheus stopped evaluating rules, so every other alert went blind.
HEARTBEAT_ALERT = "MonitoringHeartbeat"

# The SMS text comes ONLY from this table, keyed by alertname. Label values
# and annotations are written by whoever posts the alert, and any install on
# the host can post one, so none of them ever reaches the phone.
ALERT_LABELS = {
    "PadyarHigh5xxRate": "خطای سرور زیاد",
    "PadyarChatLatencyHigh": "پاسخ چت کند",
    "PadyarAICircuitOpen": "سرویس هوش مصنوعی قطع",
    "PadyarBackupFailed": "پشتیبان ناموفق",
    "PadyarBackupStale": "پشتیبان قدیمی",
    "HostDiskLow": "دیسک تقریباً پر",
    "HostPostgresDown": "پایگاه داده پایین",
    "HostCertExpiring": "گواهی رو به انقضا",
    "HostTunnelDown": "تونل Cloudflare قطع",
    "HostOriginProbeFailed": "سایت از راه nginx باز نمی‌شود",
}
OTHER_ALERT_LABEL = "هشدار دیگر"
SILENCE_LABEL = "یک silence تازه گذاشته شد"


def select_alerts(alerts: list, install: str, owner: str, app_down: bool) -> list:
    """The alerts this install texts, in the order Alertmanager listed them.

    Kept: page="sms", status "active" (so a silence or an inhibit rule is
    respected), and either labels.install == install, or no install label
    (a host alert) when this install is the host owner. While the app is
    down, its own alerts are dropped: the down-SMS already covers them and
    the inhibit rule may not have caught up yet. Host alerts still go.
    """
    picked = []
    for alert in alerts:
        if not isinstance(alert, dict):
            continue
        labels, status = alert.get("labels"), alert.get("status")
        if not isinstance(labels, dict) or not isinstance(status, dict):
            continue
        if labels.get("page") != "sms" or status.get("state") != "active":
            continue
        fingerprint = alert.get("fingerprint")
        if not isinstance(fingerprint, str) or not fingerprint:
            continue  # nothing to deduplicate on
        target = labels.get("install")
        if target:
            if target != install or app_down:
                continue
        elif owner != install:
            continue
        picked.append(alert)
    return picked


def prune_alert_sent(state: dict, selected: list) -> None:
    """Forget every fingerprint that is no longer selected. No "resolved" SMS.

    Call it only with a successful Alertmanager answer: a failed call says
    nothing about what still fires, and pruning on it would re-send every
    alert after a one-cycle blip.
    """
    keep = {alert["fingerprint"] for alert in selected}
    sent = state.get("alert_sent") or {}
    state["alert_sent"] = {fp: at for fp, at in sent.items() if fp in keep}


def due_alerts(state: dict, selected: list, now: float) -> list:
    """[(alert, is_reminder)] to text now: every new fingerprint, and every
    old one whose last SMS is at least ALERT_REMIND_SECONDS old."""
    sent = state.setdefault("alert_sent", {})
    due = []
    for alert in selected:
        last = sent.get(alert["fingerprint"])
        if last is None:
            due.append((alert, False))
        elif now - last >= ALERT_REMIND_SECONDS:
            due.append((alert, True))
    return due


def remember_sent(state: dict, fingerprints: list, now: float) -> None:
    """Mark fingerprints as texted at `now`. Past ALERT_SENT_MAX entries the
    oldest go first, so forged alerts cannot grow the state file without
    bound. Builds a new dict, so a caller's copy of the old one stays intact.
    """
    sent = dict(state.get("alert_sent") or {})
    for fingerprint in fingerprints:
        sent[fingerprint] = now
    if len(sent) > ALERT_SENT_MAX:
        sent = dict(sorted(sent.items(), key=lambda item: item[1])[-ALERT_SENT_MAX:])
    state["alert_sent"] = sent


def alert_label(alertname) -> str:
    """Persian label for one alertname; anything unknown is "another alert"."""
    if not isinstance(alertname, str):
        return OTHER_ALERT_LABEL
    return ALERT_LABELS.get(alertname, OTHER_ALERT_LABEL)


def alert_message(name: str, labels: list, reminder: bool) -> str:
    """One SMS for every item of a cycle. A repeated label is named once, at
    most three are named, and the rest are counted ("و k مورد دیگر")."""
    distinct = list(dict.fromkeys(labels))
    body = "، ".join(distinct[:3])
    if len(distinct) > 3:
        body += f" و {len(distinct) - 3} مورد دیگر"
    text = f"پادیار | هشدار {name}: {body}. جزئیات در صفحهٔ هشدار."
    return f"یادآوری | {text}" if reminder else text


def cap_message(name: str) -> str:
    """The tenth alert SMS of a UTC day, sent in place of whatever was due."""
    return (f"پادیار | سقف پیامک هشدار امروز برای {name} پر شد. "
            f"بقیهٔ هشدارهای امروز فقط در صفحهٔ هشدار دیده می‌شوند.")


def monitoring_down_message(since_epoch: float) -> str:
    """Alertmanager unreachable, or Prometheus no longer evaluating rules."""
    return (f"پادیار | سیستم پایش از ساعت {tehran_clock(since_epoch)} (به وقت تهران) "
            f"کار نمی‌کند. هشدارها تا رفع آن پیامک نمی‌شوند.")


def cap_gate(state: dict, now: float) -> str:
    """"send" | "cap" | "closed" for this step's next SMS on the UTC day of `now`.

    Nine ordinary SMS, then the tenth slot carries the cap notice instead,
    then nothing until the next UTC day. Only rolls the day: the caller
    adds one to alert_sms_today after a send that went through.
    """
    today = datetime.fromtimestamp(now, tz=timezone.utc).date().isoformat()
    if state.get("alert_day") != today:
        state["alert_day"] = today
        state["alert_sms_today"] = 0
    used = state.setdefault("alert_sms_today", 0)
    if used < ALERT_SMS_DAILY_CAP - 1:
        return "send"
    if used == ALERT_SMS_DAILY_CAP - 1:
        return "cap"
    return "closed"


def monitoring_down_due(state: dict, ok: bool, now: float) -> bool:
    """Fold one cycle's view of the monitoring stack (host owner only).

    The same shape as next_action: FAILS_BEFORE_ALERT bad cycles in a row
    earn one SMS, then a reminder every ALERT_REMIND_SECONDS; one good cycle
    resets the streak. Unlike next_action it does not mark the SMS as sent.
    The caller sets am_last_alert only after the sender succeeded, so an SMS
    lost to the daily cap or to the gateway stays due.
    """
    state.setdefault("am_fail_count", 0)
    state.setdefault("am_down_since", 0.0)
    state.setdefault("am_last_alert", 0.0)
    if ok:
        end_monitoring_streak(state)
        return False
    if state["am_down_since"] == 0.0:
        state["am_down_since"] = now
    state["am_fail_count"] += 1
    if state["am_fail_count"] < FAILS_BEFORE_ALERT:
        return False
    if state["am_last_alert"] < state["am_down_since"]:
        return True  # this streak has not been texted yet
    return now - state["am_last_alert"] >= ALERT_REMIND_SECONDS


def end_monitoring_streak(state: dict) -> None:
    """Forget the bad-cycle streak of monitoring_down_due.

    Also called when this install stops watching the monitoring stack (it is
    not the host owner this cycle, or the step is skipped). A streak frozen
    there would resume later at its old count and old start time, and text
    "monitoring is down" on the first bad cycle with the wrong clock.
    am_last_alert stays: it only records when the last such SMS went out.
    """
    state["am_fail_count"] = 0
    state["am_down_since"] = 0.0


def new_silences(state: dict, silences: list) -> list:
    """Ids of active silences not texted yet. Ids gone from the answer are
    forgotten, so the list stays as small as the answer.

    Every new silence is texted, the operator's own too: createdBy is
    written by the client, so the only way to see a forged silence (one
    that mutes another install's real alerts) is to see every silence.
    """
    listed = [s for s in silences if isinstance(s, dict) and isinstance(s.get("id"), str)]
    present = {s["id"] for s in listed}
    seen = [i for i in state.get("silence_seen") or [] if i in present]
    state["silence_seen"] = seen
    fresh = []
    for silence in listed:
        status = silence.get("status")
        if (isinstance(status, dict) and status.get("state") == "active"
                and silence["id"] not in seen):
            fresh.append(silence["id"])
    return fresh


# ════════════════════════════════════════════════════════════════════════
# I/O SHELL — everything that touches sockets, files, or the SMS gateway.
#
# WHY EVERY DEPENDENCY IS A PARAMETER
# -----------------------------------
# run_cycle's signature lists the ways this script reaches the outside
# world: probe (health check), settings_reader (DB), credit_reader + sender
# (SMS gateway), and alerts_reader + owner_reader + silences_reader (the
# host's Alertmanager). Each has a production default; the app-backed ones
# import the app LAZILY,
# inside the function body — the decision core above must stay importable on
# a box where the app tree (and its env vars) does not exist. Each can also
# be replaced by a plain lambda in tests, which is why the whole cycle is
# testable with zero network, zero DB, zero SIM.
#
# WHY THE SHELL MUST NEVER RAISE
# ------------------------------
# systemd runs this as a oneshot per timer tick. An unhandled exception would
# mark the unit failed and, with it, the whole watchdog story ("the thing
# that watches the things" is allowed exactly one failure mode: none). So
# every failure becomes a `[watchdog] …` line on stdout — journald collects
# stdout, and the note is the audit trail an operator greps for.


def _fresh_state() -> dict:
    """The zero point: no failures, no alerts, nothing cached, no phone."""
    return {
        "fail_count": 0,
        "down_since": 0.0,
        "last_alert": 0.0,
        "credit_day": "",
        "credit_alerted": False,
        "cached_phone": "",
        "cached_threshold": "",
        # Alertmanager SMS step. A host without the monitoring stack never
        # moves these off their defaults.
        "alert_sent": {},
        "alert_day": "",
        "alert_sms_today": 0,
        "alert_retry_after": 0.0,
        "am_fail_count": 0,
        "am_down_since": 0.0,
        "am_last_alert": 0.0,
        "silence_seen": [],
    }


def _load_state(path: str, install: str) -> dict:
    """Read {install}.json; a missing file is a normal first run, and a
    corrupt one loses one alert cycle at most — never the whole process."""
    try:
        with open(path, encoding="utf-8") as fh:
            loaded = json.load(fh)
        if isinstance(loaded, dict):
            # Merge over the defaults so a state file written by an older
            # version (missing a newer key) still yields a complete dict.
            state = _fresh_state()
            state.update(loaded)
            return state
        print(f"[watchdog] {install}: state not a dict, resetting", flush=True)
    except FileNotFoundError:
        pass  # first boot — nothing has happened yet, which is not news
    except Exception as e:  # noqa: BLE001 — a corrupt file must not kill the cycle
        print(f"[watchdog] {install}: state unreadable ({type(e).__name__}), resetting", flush=True)
    return _fresh_state()


def _persist(path: str, state: dict) -> bool:
    """Write atomically: tmp file + os.replace, so a crash mid-write can
    never leave a half-written JSON that the next cycle would choke on.
    True when the state is on disk; the alert step refuses to send without it.

    The makedirs targets the PARENT of the state file, so it works both for
    the default layout (STATE_DIR/<install>/state.json — the per-install
    directory the service user owns) and for any explicit state_path.
    """
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(state, fh, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception as e:  # noqa: BLE001 — persist failure is journaled, not raised
        print(f"[watchdog] state not persisted: {type(e).__name__}", flush=True)
        return False
    return True


def _probe(port: int) -> bool:
    """GET /api/health on localhost; healthy iff HTTP 200.

    ANY exception (refused, timeout, DNS, reset) counts as down: for a
    liveness probe there is no meaningful difference between "broken" and
    "unreachable" — the visitors see the same thing.
    """
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/api/health", timeout=5
        ) as response:
            return response.status == 200
    except Exception:  # noqa: BLE001
        return False


def _read_settings():
    """(phone, threshold_str) from the app DB in ONE query that RAISES when
    the database does not answer, so run_cycle falls back to the cached
    values. An empty phone comes back only from a database that answered.

    After a failed read, pg.set_unavailable() makes every later DB access in
    this one-shot process fail at once: the SMS path still gets its gateway
    credentials from the .env fallback in app/services/sms.py, without
    waiting DB_CONNECT_TIMEOUT per setting. Lazy import: the core must run
    without the app; only this default ever needs the database.
    """
    from app.db import pg
    from app.db.queries import read_settings_strict

    try:
        values = read_settings_strict(["alert_critical_phone", "alert_credit_threshold_toman"])
    except Exception:
        pg.set_unavailable()
        raise
    phone = (values.get("alert_critical_phone", "") or "").strip()
    threshold = (values.get("alert_credit_threshold_toman", "300000") or "").strip()
    return phone, threshold


def _app_unimportable(install: str) -> bool:
    """True, after one clear journal line, when the app package cannot be
    imported. Every app-backed reader would then fail on its own with a
    vague note; this line names the cause. `app/__init__.py` only reads
    VERSION, so the check touches no database and no network."""
    try:
        import app  # noqa: F401
    except Exception as e:  # noqa: BLE001
        print(f"[watchdog] {install}: cannot import the app ({type(e).__name__}): "
              "SMS disabled; check PYTHONPATH in the unit", flush=True)
        return True
    return False


def _send(destination: str, text: str) -> None:
    """Send via Asanak, translating to the gateway's number form at the edge
    (the app stores `+98…`; Asanak rejects the plus — see sms.py)."""
    from app.services.sms import asanak_destination, send_asanak

    send_asanak(asanak_destination(destination), text)


def _read_credit():
    """Wallet balance in RIAL, or None when Asanak is not configured.

    The configured-check lives HERE, inside the default reader, so an
    injected test credit_reader bypasses both the gateway and the DB check —
    and so "provider not set up" and "gateway call failed" stay one concern:
    reading credit is optional, and both answers mean "skip".
    """
    from app.services.sms import asanak_configured, asanak_credit

    if not asanak_configured():
        return None
    return asanak_credit()


# Where the monitoring stack (deploy/55-monitoring.sh) leaves what the alert
# step reads. The password file is root:padyar-alertread 0640, and the
# installer adds every registered install's service user to that group; the
# unit's User= then carries the group into each run.
ALERTMANAGER_URL = "http://127.0.0.1:9093"
ALERTMANAGER_USER = "watchdog"
ALERTMANAGER_PASS_FILE = "/etc/padyar-monitoring/alertmanager-watchdog.pass"
HOST_ALERTS_OWNER_FILE = "/etc/padyar-monitoring/host-alerts-owner"
# Largest API answer read. A real one is a few kB; anything past 1 MiB is
# treated as an API failure, never parsed.
ALERTMANAGER_MAX_BODY_BYTES = 1024 * 1024
# Wall-clock limit for one whole GET, headers and body. Two GETs plus the
# 5 s probe stay well inside the unit's TimeoutStartSec.
ALERTMANAGER_DEADLINE_SECONDS = 8


class PasswordFileUnreadable(Exception):
    """The Alertmanager password file exists but this user cannot read it."""


def _alertmanager_password() -> str:
    """The watchdog user's password.

    FileNotFoundError passes through untouched: it means "this host has no
    monitoring stack", and the step is skipped without a word. Every other
    failure is wrapped, so run_cycle can tell "re-run the installer for this
    install" apart from "Alertmanager is down".
    """
    try:
        with open(ALERTMANAGER_PASS_FILE, encoding="utf-8") as fh:
            return fh.read().strip()
    except FileNotFoundError:
        raise
    except Exception as e:  # noqa: BLE001 (the caller classifies it; nothing is swallowed)
        raise PasswordFileUnreadable(type(e).__name__) from e


class _RefuseRedirects(urllib.request.HTTPRedirectHandler):
    """Follow no redirect. urllib would re-send the Authorization header to
    whatever host the Location names; a 3xx becomes an HTTPError instead,
    which the alert step counts as an API failure."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _alertmanager_get(path: str):
    """GET one Alertmanager API path with basic auth; the parsed JSON body.

    Only 127.0.0.1 and only GET. The opener gets an empty ProxyHandler (an
    http_proxy in the install's .env must never carry this password off the
    host) and refuses every redirect, for the same reason. Anything but
    HTTP 200, a body over ALERTMANAGER_MAX_BODY_BYTES, or a GET still running
    after ALERTMANAGER_DEADLINE_SECONDS, raises.
    """
    password = _alertmanager_password()
    token = base64.b64encode(f"{ALERTMANAGER_USER}:{password}".encode("utf-8")).decode("ascii")
    request = urllib.request.Request(ALERTMANAGER_URL + path,
                                     headers={"Authorization": f"Basic {token}"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _RefuseRedirects())
    outcome = {}

    def fetch():
        try:
            with opener.open(request, timeout=5) as response:
                if response.status != 200:
                    raise RuntimeError(f"HTTP {response.status}")
                outcome["body"] = response.read(ALERTMANAGER_MAX_BODY_BYTES + 1)
        except Exception as e:  # noqa: BLE001 (raised again below, in the cycle's thread)
            outcome["error"] = e

    # timeout=5 bounds each socket read, not the whole GET: a listener that
    # sends one byte every few seconds could hold the cycle as long as it
    # likes. So the GET runs in a daemon thread and the cycle waits for it at
    # most ALERTMANAGER_DEADLINE_SECONDS. A thread still blocked then is left
    # behind; it ends with this oneshot process.
    worker = threading.Thread(target=fetch, daemon=True)
    worker.start()
    worker.join(ALERTMANAGER_DEADLINE_SECONDS)
    if worker.is_alive():
        raise TimeoutError("alertmanager GET over the deadline")
    if "error" in outcome:
        raise outcome["error"]
    body = outcome["body"]
    if len(body) > ALERTMANAGER_MAX_BODY_BYTES:
        raise ValueError("body over the size limit")
    return json.loads(body.decode("utf-8"))


def _read_alerts():
    """Every alert Alertmanager holds. The query has no label filter, so the
    always-firing heartbeat alert is in the answer too."""
    return _alertmanager_get("/api/v2/alerts?active=true")


def _read_silences():
    return _alertmanager_get("/api/v2/silences")


def _read_host_owner() -> str:
    """The slug that texts host alerts (alerts with no install label)."""
    try:
        with open(HOST_ALERTS_OWNER_FILE, encoding="utf-8") as fh:
            return fh.read().strip()
    except FileNotFoundError:
        return ""


# The state keys one alert SMS changes. All are saved before the send and
# restored when the save fails. A failed send restores only the marks: the
# attempt still counts against the daily cap and keeps its retry gap.
_MARK_KEYS = ("alert_sent", "am_last_alert", "silence_seen")
_SEND_KEYS = _MARK_KEYS + ("alert_sms_today", "alert_retry_after")


def _alert_step(install: str, state: dict, phone: str, now: float,
                alerts_reader, owner_reader, silences_reader, sender, persist) -> None:
    """Text this install's Alertmanager alerts: at most one SMS per cycle.

    Runs after the probe and the down-SMS, so state["fail_count"] already
    holds this cycle's verdict. Mutates `state`; `persist()` saves it and
    returns True on success. run_cycle persists the final state as usual.
    """
    name = install.upper()
    failure = "unexpected body"
    try:
        alerts = alerts_reader()
    except FileNotFoundError:
        end_monitoring_streak(state)
        return  # no monitoring stack on this host: exactly the old behaviour
    except (PermissionError, PasswordFileUnreadable) as e:
        # Seen when the install was created after the last installer run,
        # so its user is not in padyar-alertread yet. Loud on every cycle.
        cause = type(e.__cause__ or e).__name__
        print(f"[watchdog] {install}: monitoring alerts OFF: cannot read "
              f"alertmanager-watchdog.pass ({cause}); "
              f"re-run deploy/55-monitoring.sh {install}", flush=True)
        end_monitoring_streak(state)
        return
    except Exception as e:  # noqa: BLE001 (an unreachable API is data, not a crash)
        alerts, failure = None, type(e).__name__
    if not isinstance(alerts, list):
        print(f"[watchdog] {install}: alertmanager unreadable ({failure})", flush=True)
        alerts = None

    try:
        owner = owner_reader()
    except Exception as e:  # noqa: BLE001
        print(f"[watchdog] {install}: host-alerts-owner unreadable ({type(e).__name__})",
              flush=True)
        owner = ""

    due = []
    if alerts is not None:
        selected = select_alerts(alerts, install, owner,
                                 app_down=state.get("fail_count", 0) >= FAILS_BEFORE_ALERT)
        prune_alert_sent(state, selected)
        due = due_alerts(state, selected, now)

    # Watching the monitoring stack itself, and the silence notices, belong
    # to the host owner alone, so one event on the host is one SMS.
    monitoring_due, fresh = False, []
    if owner == install:
        ok = alerts is not None and any(
            isinstance(a, dict) and isinstance(a.get("labels"), dict)
            and a["labels"].get("alertname") == HEARTBEAT_ALERT for a in alerts)
        if alerts is not None:
            if not ok:
                print(f"[watchdog] {install}: {HEARTBEAT_ALERT} missing from alertmanager",
                      flush=True)
            try:
                silences = silences_reader()
                if not isinstance(silences, list):
                    raise ValueError("unexpected body")
            except Exception as e:  # noqa: BLE001
                print(f"[watchdog] {install}: alertmanager silences unreadable "
                      f"({type(e).__name__})", flush=True)
                ok = False
            else:
                fresh = new_silences(state, silences)
        monitoring_due = monitoring_down_due(state, ok, now)
    else:
        end_monitoring_streak(state)

    # One SMS per cycle. "Monitoring is down" goes first; anything else that
    # is due waits one cycle, because it is not marked as sent.
    if monitoring_due:
        text = monitoring_down_message(state["am_down_since"])
    elif due or fresh:
        # The silence notice leads, so it is never folded into "k more".
        labels = [SILENCE_LABEL] if fresh else []
        labels += [alert_label(alert["labels"].get("alertname")) for alert, _ in due]
        reminder = not fresh and all(is_reminder for _, is_reminder in due)
        text = alert_message(name, labels, reminder)
    else:
        return

    if not phone:
        print(f"[watchdog] {install}: alert pending but no alert_critical_phone configured",
              flush=True)
        return
    if now < state.get("alert_retry_after", 0.0):
        return  # a send failed less than ALERT_RETRY_SECONDS ago
    gate = cap_gate(state, now)
    if gate == "closed":
        return
    if gate == "cap":
        text = cap_message(name)

    # Write-ahead. The daily cap, the dedup and the retry gap exist only in
    # the state file, so a state that cannot be saved (disk full, read-only
    # directory) would mean a new SMS on every cycle, and nothing else bounds
    # the spend. So the send is recorded as done and saved BEFORE the sender
    # runs, with a retry gap in case the process dies mid-send: at most once.
    before = {key: copy.deepcopy(state[key]) for key in _SEND_KEYS}
    state["alert_sms_today"] += 1
    state["alert_retry_after"] = now + ALERT_RETRY_SECONDS
    if gate == "cap":
        pass  # nothing is marked as sent, so today's leftovers go tomorrow
    elif monitoring_due:
        state["am_last_alert"] = now
    else:
        remember_sent(state, [alert["fingerprint"] for alert, _ in due], now)
        state["silence_seen"] = state["silence_seen"] + fresh
    if not persist():
        state.update(before)
        print(f"[watchdog] {install}: alert SMS skipped: state not saved", flush=True)
        return
    try:
        sender(phone, text)
    except Exception as e:  # noqa: BLE001 (journal the class only, then wait)
        # A gateway can accept the SMS and then fail the call (a timeout after
        # the accept, a reply that does not parse). Were the attempt not
        # counted, such a gateway would deliver one SMS every
        # ALERT_RETRY_SECONDS, 288 a day. So the cap counter and the retry gap
        # stay, and only the marks are undone: the alert is tried again later,
        # and the daily cap bounds spend in every failure mode.
        for key in _MARK_KEYS:
            state[key] = before[key]
        print(f"[watchdog] {install}: alert send failed: {type(e).__name__}", flush=True)
        return
    state["alert_retry_after"] = before["alert_retry_after"]


def run_cycle(
    install: str,
    now: float | None = None,
    probe=None,
    sender=None,
    settings_reader=None,
    credit_reader=None,
    state_path: str | None = None,
    alerts_reader=None,
    owner_reader=None,
    silences_reader=None,
) -> dict:
    """One full probe→decide→alert→persist cycle for one install.

    Returns the (persisted) new state; the only None is an install with no
    APP_PORT in the environment, which is a deployment typo, not a runtime
    condition — journal it and move on rather than raising.
    """
    port = install_port(install)
    if port is None:
        print(f"[watchdog] {install}: no APP_PORT in the environment — unknown install "
              f"(padyar-watchdog@{install}.service must load the install's .env)",
              flush=True)
        return None
    if now is None:
        now = time.time()
    probe = _probe if probe is None else probe
    sender = _send if sender is None else sender
    settings_reader = _read_settings if settings_reader is None else settings_reader
    credit_reader = _read_credit if credit_reader is None else credit_reader
    alerts_reader = _read_alerts if alerts_reader is None else alerts_reader
    owner_reader = _read_host_owner if owner_reader is None else owner_reader
    silences_reader = _read_silences if silences_reader is None else silences_reader
    # os.fspath: tests pass a pathlib.Path, __main__ passes nothing — both
    # must land as a plain str because _persist does `path + ".tmp"`.
    # Per-install subdirectory: each install's service user owns exactly its
    # own dir under the (root-owned) STATE_DIR parent, so a persist that
    # fails on permissions can NEVER happen by default — a silent persist
    # failure would reset fail_count every oneshot run and mute all alerts.
    path = (os.fspath(state_path) if state_path
            else os.path.join(STATE_DIR, install, "state.json"))
    state = _load_state(path, install)
    # Without the app the default readers and the sender can only fail. Keep
    # the cached phone and threshold and skip the credit check and every send,
    # so the cycle reports the cause once and marks no alert as sent.
    app_missing = settings_reader is _read_settings and _app_unimportable(install)
    if app_missing:
        settings_reader = lambda: (state.get("cached_phone", ""), state.get("cached_threshold", ""))  # noqa: E731
        credit_reader = lambda: None  # noqa: E731
        sender = lambda destination, text: None  # noqa: E731

    try:
        # The SMS names the install by its slug, uppercased — exactly the
        # display name the old hardcoded table carried, derived instead of
        # configured, so a fresh install needs no watchdog-side edit.
        name = install.upper()
        try:
            healthy = bool(probe(port))
        except Exception:  # noqa: BLE001 — an exploding probe IS a failed probe
            healthy = False

        # Settings first, before any send needs the phone. A DB outage must
        # not blind the watchdog: fall back to the phone cached on the last
        # healthy read — alerting the right person on stale data beats
        # alerting nobody on fresh failure.
        try:
            phone, threshold_raw = settings_reader()
            # Only a successful read moves the cache, for the next DB-down cycle.
            state["cached_phone"] = phone
            state["cached_threshold"] = threshold_raw
        except Exception as e:  # noqa: BLE001
            print(f"[watchdog] {install}: settings unreadable "
                  f"({type(e).__name__}), using cached phone", flush=True)
            phone = state.get("cached_phone", "")
            threshold_raw = state.get("cached_threshold") or "300000"
        try:
            threshold_toman = int(threshold_raw)
        except (TypeError, ValueError):
            # A typo in the settings row must not become "threshold zero =
            # alert on every cycle"; the documented default is the floor.
            threshold_toman = 300000

        action = next_action(state, healthy, now)
        if action in ("alert", "realert"):
            if phone:
                try:
                    # down_since (anchored at the streak's FIRST failure by
                    # next_action), not `now`: the SMS must report when the
                    # install went down, not when the 3rd probe made us sure.
                    # `or now` guards a zeroed down_since (fresh/hand-edited
                    # state) so the message still carries a sane clock.
                    sender(phone, down_message(
                        name, state.get("down_since") or now,
                        reminder=(action == "realert")))
                except Exception as e:  # noqa: BLE001 — a failed SMS must not lose state
                    print(f"[watchdog] {install}: send failed: {type(e).__name__}", flush=True)
            else:
                # The install is DOWN and we cannot tell anyone. This note is
                # the loudest thing we can do — grep it in journald.
                print(f"[watchdog] {install}: DOWN but no alert_critical_phone configured",
                      flush=True)

        # Credit trip-wire runs only when the install is UP: during an outage
        # the down-SMS itself is the priority, and a wallet check against a
        # dead app's DB is noise. Day-string dedup lives in the pure core.
        if healthy:
            try:
                credit_rial = credit_reader()
            except Exception as e:  # noqa: BLE001 — credit is optional telemetry
                print(f"[watchdog] {install}: credit unreadable: {type(e).__name__}",
                      flush=True)
                credit_rial = None
            if credit_rial is not None:
                today = datetime.now(timezone.utc).date().isoformat()
                if credit_alert(state, credit_rial, threshold_toman, today, now) and phone:
                    try:
                        sender(phone, low_credit_message(
                            credit_rial // RIAL_PER_TOMAN, threshold_toman))
                    except Exception as e:  # noqa: BLE001
                        print(f"[watchdog] {install}: send failed: {type(e).__name__}",
                              flush=True)

        # Last: Alertmanager alerts. Its own try, so a bug in this step can
        # never cost the probe verdict or the down-SMS state above.
        # Without the app the step could only mark alerts as sent that never
        # went out, so it is skipped and its state stays as it was.
        try:
            if not app_missing:
                _alert_step(install, state, phone, now,
                            alerts_reader, owner_reader, silences_reader, sender,
                            persist=lambda: _persist(path, state))
        except Exception as e:  # noqa: BLE001
            print(f"[watchdog] {install}: alert step error: {type(e).__name__}", flush=True)
    except Exception as e:  # noqa: BLE001 — the shell is total: journal, persist, return
        print(f"[watchdog] {install}: cycle error: {type(e).__name__}", flush=True)

    # Persist after EVERY mutation path, including the error path above —
    # the alternative is re-living this cycle's failures (or re-sending its
    # SMS) on the next tick.
    _persist(path, state)
    return state


if __name__ == "__main__":
    # systemd executes this as `watchdog.py --install <slug>` per timer tick.
    # argparse exits 2 on a missing --install BEFORE any cycle runs — that is
    # a deployment error and SHOULD be loud. An install whose environment
    # carries no APP_PORT is reported by run_cycle (loud journal line, None)
    # rather than argparse, because only the unit's EnvironmentFile can say
    # which installs exist on this host. Once past parsing, the cycle never
    # raises, so a reporting run always exits 0: a oneshot that "fails"
    # because it reported bad news would train operators to ignore the unit.
    parser = argparse.ArgumentParser(
        description="Probe one Padyar install; alert on down/low-credit.")
    parser.add_argument("--install", required=True,
                        help="install slug; its .env (APP_PORT) must be in the environment")
    arguments = parser.parse_args()
    run_cycle(arguments.install)
    raise SystemExit(0)
