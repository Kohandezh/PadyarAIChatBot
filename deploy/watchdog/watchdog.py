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
"""
import argparse
import json
import os
import time
import urllib.request
from datetime import datetime, timedelta, timezone

# The installs this watchdog guards, keyed by systemd instance name. Ports are
# the production ports; run_cycle (Task 2) probes them on localhost.
INSTALLS = {
    "inotex": {"port": 8001, "name": "INOTEX"},
    "elecomp": {"port": 8002, "name": "ELECOMP"},
}

# 3 fails absorb a single blip or a rolling restart before an admin is woken;
# 1800s reminds about a long outage twice an hour at most, trading detection
# lag against SMS fatigue. Both are policy knobs, not laws of nature.
FAILS_BEFORE_ALERT = 3
REALERT_SECONDS = 1800

# Asanak reports the wallet in rial; operators think and top up in toman.
RIAL_PER_TOMAN = 10

# run_cycle (Task 2) keeps each install's state in
# STATE_DIR/<install>/state.json — a per-install SUBDIRECTORY, not a flat
# file. Why: two installs run as two different service users sharing this
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
# I/O SHELL — everything that touches sockets, files, or the SMS gateway.
#
# WHY EVERY DEPENDENCY IS A PARAMETER
# -----------------------------------
# run_cycle's signature lists the four ways this script reaches the outside
# world: probe (health check), settings_reader (DB), credit_reader + sender
# (SMS gateway). Each has a production default that imports the app LAZILY,
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


def _persist(path: str, state: dict) -> None:
    """Write atomically: tmp file + os.replace, so a crash mid-write can
    never leave a half-written JSON that the next cycle would choke on.

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
    """(phone, threshold_str) from the app DB. Lazy import: the core must
    run without the app; only this default ever needs the database."""
    from app.db.queries import get_setting

    phone = (get_setting("alert_critical_phone", "") or "").strip()
    threshold = (get_setting("alert_credit_threshold_toman", "300000") or "").strip()
    return phone, threshold


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


def run_cycle(
    install: str,
    now: float | None = None,
    probe=None,
    sender=None,
    settings_reader=None,
    credit_reader=None,
    state_path: str | None = None,
) -> dict:
    """One full probe→decide→alert→persist cycle for one install.

    Returns the (persisted) new state; the only None is an unknown install
    key, which is a deployment typo, not a runtime condition — journal it
    and move on rather than raising.
    """
    if install not in INSTALLS:
        print(f"[watchdog] {install}: unknown install "
              f"(choices: {', '.join(sorted(INSTALLS))})", flush=True)
        return None
    if now is None:
        now = time.time()
    probe = _probe if probe is None else probe
    sender = _send if sender is None else sender
    settings_reader = _read_settings if settings_reader is None else settings_reader
    credit_reader = _read_credit if credit_reader is None else credit_reader
    # os.fspath: tests pass a pathlib.Path, __main__ passes nothing — both
    # must land as a plain str because _persist does `path + ".tmp"`.
    # Per-install subdirectory: each install's service user owns exactly its
    # own dir under the (root-owned) STATE_DIR parent, so a persist that
    # fails on permissions can NEVER happen by default — a silent persist
    # failure would reset fail_count every oneshot run and mute all alerts.
    path = (os.fspath(state_path) if state_path
            else os.path.join(STATE_DIR, install, "state.json"))
    state = _load_state(path, install)

    try:
        port, name = INSTALLS[install]["port"], INSTALLS[install]["name"]
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
            state["cached_phone"] = phone  # cache for the next DB-down cycle
        except Exception as e:  # noqa: BLE001
            print(f"[watchdog] {install}: settings unreadable "
                  f"({type(e).__name__}), using cached phone", flush=True)
            phone, threshold_raw = state.get("cached_phone", ""), "300000"
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
                    sender(phone, down_message(name, now, reminder=(action == "realert")))
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
    except Exception as e:  # noqa: BLE001 — the shell is total: journal, persist, return
        print(f"[watchdog] {install}: cycle error: {type(e).__name__}", flush=True)

    # Persist after EVERY mutation path, including the error path above —
    # the alternative is re-living this cycle's failures (or re-sending its
    # SMS) on the next tick.
    _persist(path, state)
    return state


if __name__ == "__main__":
    # systemd executes this as `watchdog.py --install inotex` per timer tick.
    # argparse exits 2 on a bad --install BEFORE any cycle runs — that is a
    # deployment error and SHOULD be loud. Once past parsing, the cycle never
    # raises, so a reporting run always exits 0: a oneshot that "fails"
    # because it reported bad news would train operators to ignore the unit.
    parser = argparse.ArgumentParser(
        description="Probe one Padyar install; alert on down/low-credit.")
    parser.add_argument("--install", required=True, choices=sorted(INSTALLS),
                        help="install key, as in INSTALLS")
    arguments = parser.parse_args()
    run_cycle(arguments.install)
    raise SystemExit(0)
