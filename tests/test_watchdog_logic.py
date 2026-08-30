# tests/test_watchdog_logic.py
"""Watchdog decision core — pure functions, no I/O, injected clock."""
import importlib.util
import pathlib

WATCHDOG = pathlib.Path(__file__).resolve().parents[1] / "deploy" / "watchdog" / "watchdog.py"
spec = importlib.util.spec_from_file_location("watchdog", WATCHDOG)
watchdog = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watchdog)


def _state():
    return {"fail_count": 0, "down_since": 0.0, "last_alert": 0.0, "credit_day": "", "credit_alerted": False}


def test_first_and_second_fail_do_not_alert():
    s = _state()
    assert watchdog.next_action(s, healthy=False, now=1000) == "none"
    assert watchdog.next_action(s, healthy=False, now=1060) == "none"
    assert s["fail_count"] == 2


def test_third_consecutive_fail_alerts_once():
    s = _state()
    watchdog.next_action(s, False, 1000)
    watchdog.next_action(s, False, 1060)
    assert watchdog.next_action(s, False, 1120) == "alert"
    assert s["down_since"] == 1000  # anchored at the FIRST failure, not the third
    assert watchdog.next_action(s, False, 1180) == "none"  # 4th fail: silent


def test_realert_after_30_minutes_only():
    s = _state()
    for now in (1000, 1060, 1120):
        watchdog.next_action(s, False, now)  # alert fired at 1120, last_alert=1120
    assert watchdog.next_action(s, False, 1120 + 1799) == "none"
    assert watchdog.next_action(s, False, 1120 + 1800) == "realert"
    assert s["last_alert"] == 1120 + 1800


def test_recovery_resets_the_counter():
    s = _state()
    for now in (1000, 1060, 1120):
        watchdog.next_action(s, False, now)
    assert watchdog.next_action(s, True, 1180) == "none"
    assert s["fail_count"] == 0 and s["down_since"] == 0.0


def test_single_blip_between_failures_does_not_count_as_three():
    s = _state()
    watchdog.next_action(s, False, 1000)
    watchdog.next_action(s, False, 1060)
    watchdog.next_action(s, True, 1100)  # blip up
    assert watchdog.next_action(s, False, 1120) == "none"  # fresh count: fail #1


def test_credit_alert_fires_once_per_day_and_resets_next_day():
    s = _state()
    assert watchdog.credit_alert(s, 2_000_000, 300_000, "2026-08-30", 1000) is True
    assert s["credit_alerted"] is True
    assert watchdog.credit_alert(s, 2_000_000, 300_000, "2026-08-30", 2000) is False
    assert watchdog.credit_alert(s, 2_000_000, 300_000, "2026-08-31", 3000) is True  # new day
    s2 = _state()
    assert watchdog.credit_alert(s2, 4_000_000, 300_000, "2026-08-30", 1000) is False  # above threshold


def test_messages_are_persian_and_short():
    m = watchdog.down_message("INOTEX", 1788091200, reminder=False)  # 2026-08-30 12:00 UTC -> 15:30 Tehran
    assert "INOTEX" in m and "پاسخ نمی‌دهد" in m
    assert watchdog.down_message("INOTEX", 1788091200, reminder=True).startswith("یادآوری")
    c = watchdog.low_credit_message(200_000, 300_000)
    assert "اعتبار" in c and "300٬000" in c and "200٬000" in c


def test_tehran_clock_is_half_hour_offset():
    # 12:00 UTC -> 15:30 Tehran
    assert watchdog.tehran_clock(1788091200) == "15:30"


def test_installs_ports_are_the_production_ports():
    assert watchdog.INSTALLS["inotex"]["port"] == 8001
    assert watchdog.INSTALLS["elecomp"]["port"] == 8002
