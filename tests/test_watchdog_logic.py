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
    m = watchdog.down_message("MYEVENT", 1788091200, reminder=False)  # 2026-08-30 12:00 UTC -> 15:30 Tehran
    assert "MYEVENT" in m and "پاسخ نمی‌دهد" in m
    assert watchdog.down_message("MYEVENT", 1788091200, reminder=True).startswith("یادآوری")
    c = watchdog.low_credit_message(200_000, 300_000)
    assert "اعتبار" in c and "300٬000" in c and "200٬000" in c


def test_tehran_clock_is_half_hour_offset():
    # 12:00 UTC -> 15:30 Tehran
    assert watchdog.tehran_clock(1788091200) == "15:30"


def test_install_port_comes_from_the_environment(monkeypatch):
    # No hardcoded install table anymore: the port is whatever the install's
    # EnvironmentFile provided, and a missing APP_PORT means "unknown
    # install" — a per-customer platform cannot pin ports in source.
    monkeypatch.setenv("APP_PORT", "8010")
    assert watchdog.install_port("myevent") == 8010
    monkeypatch.delenv("APP_PORT", raising=False)
    assert watchdog.install_port("myevent") is None


# ── Alertmanager SMS step: the pure decisions (monitoring-stack SPEC 5.5) ──
#
# These pin the policy that turns an Alertmanager answer into at most one SMS
# per cycle. The I/O around them (readers, sender, state file) is covered in
# tests/test_watchdog_io.py.

NOON_UTC = 1788091200  # 2026-08-30 12:00 UTC


def _am_alert(name, fp, install="myevent", state="active", page="sms"):
    labels = {"alertname": name}
    if install:
        labels["install"] = install
    if page:
        labels["page"] = page
    return {"labels": labels, "fingerprint": fp, "status": {"state": state}}


def test_select_keeps_only_active_sms_alerts_of_this_install():
    alerts = [
        _am_alert("PadyarHigh5xxRate", "a"),
        _am_alert("PadyarHigh5xxRate", "b", state="suppressed"),
        _am_alert("PadyarAppDown", "c", page=None),
        _am_alert("PadyarHigh5xxRate", "d", install="otherevent"),
    ]
    picked = watchdog.select_alerts(alerts, "myevent", owner="otherevent", app_down=False)
    assert [a["fingerprint"] for a in picked] == ["a"]


def test_select_gives_host_alerts_only_to_the_host_owner():
    alerts = [_am_alert("HostDiskLow", "h", install=None)]
    assert watchdog.select_alerts(alerts, "myevent", owner="myevent", app_down=False) == alerts
    assert watchdog.select_alerts(alerts, "myevent", owner="otherevent", app_down=False) == []
    assert watchdog.select_alerts(alerts, "myevent", owner="", app_down=False) == []


def test_select_drops_own_install_alerts_while_the_app_is_down_but_keeps_host_alerts():
    own = _am_alert("PadyarHigh5xxRate", "a")
    host = _am_alert("HostPostgresDown", "h", install=None)
    picked = watchdog.select_alerts([own, host], "myevent", owner="myevent", app_down=True)
    assert picked == [host]


def test_select_survives_malformed_items():
    alerts = ["junk", {"labels": None}, {"labels": {"page": "sms"}, "status": {"state": "active"}}]
    assert watchdog.select_alerts(alerts, "myevent", owner="myevent", app_down=False) == []


def test_due_alerts_sends_new_ones_and_reminds_after_six_hours():
    s = {"alert_sent": {}}
    alert = _am_alert("PadyarHigh5xxRate", "a")
    assert watchdog.due_alerts(s, [alert], 1000) == [(alert, False)]
    s["alert_sent"]["a"] = 1000
    assert watchdog.due_alerts(s, [alert], 1000 + watchdog.ALERT_REMIND_SECONDS - 1) == []
    assert watchdog.due_alerts(s, [alert], 1000 + watchdog.ALERT_REMIND_SECONDS) == [(alert, True)]


def test_prune_drops_fingerprints_that_are_no_longer_selected():
    s = {"alert_sent": {"a": 1000, "gone": 900}}
    watchdog.prune_alert_sent(s, [_am_alert("PadyarHigh5xxRate", "a")])
    assert s["alert_sent"] == {"a": 1000}


def test_alert_message_names_three_labels_and_counts_the_rest():
    m = watchdog.alert_message("MYEVENT", ["الف", "ب", "ج", "د", "ه"], reminder=False)
    assert m == "پادیار | هشدار MYEVENT: الف، ب، ج و 2 مورد دیگر. جزئیات در صفحهٔ هشدار."


def test_alert_message_collapses_duplicate_labels_before_counting():
    m = watchdog.alert_message("MYEVENT", ["الف", "الف", "ب", "ج", "ج", "د"], reminder=False)
    assert m == "پادیار | هشدار MYEVENT: الف، ب، ج و 1 مورد دیگر. جزئیات در صفحهٔ هشدار."
    one = watchdog.alert_message("MYEVENT", ["الف", "الف"], reminder=False)
    assert one == "پادیار | هشدار MYEVENT: الف. جزئیات در صفحهٔ هشدار."


def test_alert_message_reminder_prefix():
    m = watchdog.alert_message("MYEVENT", ["الف"], reminder=True)
    assert m.startswith("یادآوری | پادیار | هشدار MYEVENT: ")


def test_alert_label_comes_from_the_fixed_table_only():
    assert watchdog.alert_label("PadyarHigh5xxRate") == "خطای سرور زیاد"
    assert watchdog.alert_label("HostOriginProbeFailed") == "سایت از راه nginx باز نمی‌شود"
    assert watchdog.alert_label("Anything <script>") == "هشدار دیگر"


def test_cap_and_monitoring_texts_match_the_spec():
    assert watchdog.cap_message("MYEVENT") == (
        "پادیار | سقف پیامک هشدار امروز برای MYEVENT پر شد. "
        "بقیهٔ هشدارهای امروز فقط در صفحهٔ هشدار دیده می‌شوند.")
    assert watchdog.monitoring_down_message(NOON_UTC) == (
        "پادیار | سیستم پایش از ساعت 15:30 (به وقت تهران) کار نمی‌کند. "
        "هشدارها تا رفع آن پیامک نمی‌شوند.")


def test_cap_gate_allows_nine_then_the_cap_text_then_nothing_until_the_next_utc_day():
    s = {}
    gates = []
    for _ in range(11):
        gate = watchdog.cap_gate(s, NOON_UTC)
        gates.append(gate)
        if gate != "closed":
            s["alert_sms_today"] += 1
    assert gates == ["send"] * 9 + ["cap", "closed"]
    assert watchdog.cap_gate(s, NOON_UTC + 86400) == "send"
    assert s["alert_sms_today"] == 0


def test_monitoring_down_is_due_on_the_third_bad_cycle_then_every_six_hours():
    s = {}
    assert watchdog.monitoring_down_due(s, ok=False, now=1000) is False
    assert watchdog.monitoring_down_due(s, ok=False, now=1060) is False
    assert watchdog.monitoring_down_due(s, ok=False, now=1120) is True
    assert s["am_down_since"] == 1000
    s["am_last_alert"] = 1120  # the shell marks it only after a successful send
    assert watchdog.monitoring_down_due(s, ok=False, now=1180) is False
    assert watchdog.monitoring_down_due(
        s, ok=False, now=1120 + watchdog.ALERT_REMIND_SECONDS) is True


def test_monitoring_down_stays_due_until_a_send_marks_it():
    s = {}
    for now in (1000, 1060, 1120):
        watchdog.monitoring_down_due(s, ok=False, now=now)
    assert watchdog.monitoring_down_due(s, ok=False, now=1180) is True


def test_one_healthy_monitoring_cycle_resets_the_streak():
    s = {}
    for now in (1000, 1060):
        watchdog.monitoring_down_due(s, ok=False, now=now)
    assert watchdog.monitoring_down_due(s, ok=True, now=1120) is False
    assert s["am_fail_count"] == 0 and s["am_down_since"] == 0.0
    assert watchdog.monitoring_down_due(s, ok=False, now=1180) is False


def test_new_silences_reports_unseen_active_ids_and_prunes_the_rest():
    s = {"silence_seen": ["old", "gone"]}
    silences = [
        {"id": "old", "status": {"state": "active"}},
        {"id": "new", "status": {"state": "active"}},
        {"id": "later", "status": {"state": "pending"}},
        {"id": "done", "status": {"state": "expired"}},
    ]
    assert watchdog.new_silences(s, silences) == ["new"]
    assert s["silence_seen"] == ["old"]
