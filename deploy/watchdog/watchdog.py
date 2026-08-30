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

# run_cycle (Task 2) keeps the persisted state JSON here, outside the app
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
