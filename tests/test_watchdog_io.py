# tests/test_watchdog_io.py
"""run_cycle: probe → decide → SMS → persist state. Every dependency injected."""
import importlib.util
import json
import pathlib

import pytest

WATCHDOG = pathlib.Path(__file__).resolve().parents[1] / "deploy" / "watchdog" / "watchdog.py"
spec = importlib.util.spec_from_file_location("watchdog", WATCHDOG)
watchdog = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watchdog)

INSTALL = "myevent"
APP_PORT = "8010"
PHONE = "09121234567"
SETTINGS = lambda: (PHONE, "300000")  # noqa: E731
RICH_CREDIT = lambda: 10_000_000  # noqa: E731 — far above any floor
SENT = []


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    # APP_PORT stands in for the systemd unit's EnvironmentFile, which is
    # how the guarded install's port reaches the watchdog in production.
    monkeypatch.setenv("APP_PORT", APP_PORT)
    SENT.clear()
    yield
    SENT.clear()


def _send(dest, text):
    SENT.append((dest, text))


def _no_monitoring_stack():
    # What the production alerts_reader raises on a host where
    # deploy/55-monitoring.sh never ran: the password file is not there.
    raise FileNotFoundError("/etc/padyar-monitoring/alertmanager-watchdog.pass")


def _cycle(tmp_path, **overrides):
    """One watchdog cycle with every outside dependency replaced."""
    defaults = dict(
        probe=lambda port: False,
        sender=_send,
        settings_reader=SETTINGS,
        credit_reader=RICH_CREDIT,
        alerts_reader=_no_monitoring_stack,
        state_path=tmp_path / f"{INSTALL}.json",
    )
    defaults.update(overrides)
    return watchdog.run_cycle(INSTALL, **defaults)


def _disk_state(tmp_path):
    return json.loads((tmp_path / f"{INSTALL}.json").read_text(encoding="utf-8"))


def test_three_bad_probes_send_exactly_one_alert_sms(tmp_path):
    for now in (1000, 1060, 1120):
        _cycle(tmp_path, now=now)
    assert len(SENT) == 1
    dest, text = SENT[0]
    assert dest == PHONE
    assert "MYEVENT" in text and "پاسخ نمی‌دهد" in text
    assert _disk_state(tmp_path)["fail_count"] == 3


def test_alert_sms_reports_down_since_not_probe_time(tmp_path):
    # The 3rd probe (now=1120) sends the SMS, but the clock inside it must be
    # the FIRST failure's (1000): the admin reads when the install went down,
    # not when the watchdog became sure.
    for now in (1000, 1060, 1120):
        _cycle(tmp_path, now=now)
    assert len(SENT) == 1
    text = SENT[0][1]
    assert watchdog.tehran_clock(1000) in text
    assert watchdog.tehran_clock(1120) not in text


def test_healthy_cycle_after_alert_resets_state_and_stays_silent(tmp_path):
    for now in (1000, 1060, 1120):
        _cycle(tmp_path, now=now)
    SENT.clear()
    _cycle(tmp_path, now=1180, probe=lambda port: True)
    assert SENT == []
    disk = _disk_state(tmp_path)
    assert disk["fail_count"] == 0 and disk["down_since"] == 0.0


def test_down_with_no_phone_configured_sends_nothing_but_persists(tmp_path, capsys):
    for now in (1000, 1060, 1120):
        _cycle(tmp_path, now=now, settings_reader=lambda: ("", "300000"))
    assert SENT == []
    assert _disk_state(tmp_path)["fail_count"] == 3
    assert "alert_critical_phone" in capsys.readouterr().out


def test_settings_db_down_falls_back_to_cached_phone(tmp_path):
    state_path = tmp_path / f"{INSTALL}.json"
    state_path.write_text(json.dumps({
        "fail_count": 2, "down_since": 1000.0, "last_alert": 0.0,
        "credit_day": "", "credit_alerted": False, "cached_phone": PHONE,
    }), encoding="utf-8")

    def db_is_down():
        raise RuntimeError("connection refused")

    _cycle(tmp_path, now=1180, settings_reader=db_is_down)
    assert len(SENT) == 1 and SENT[0][0] == PHONE


def test_low_credit_sms_fires_once_per_utc_day(tmp_path):
    for now in (1000, 1060):
        _cycle(tmp_path, now=now, probe=lambda port: True, credit_reader=lambda: 2_000_000)
    assert len(SENT) == 1
    text = SENT[0][1]
    assert "اعتبار" in text and "200٬000" in text and "300٬000" in text


def test_probe_exception_counts_as_unhealthy_without_crashing(tmp_path):
    def exploding_probe(port):
        raise OSError("connection reset")

    result = _cycle(tmp_path, now=1000, probe=exploding_probe)
    assert isinstance(result, dict) and result["fail_count"] == 1


def test_corrupt_state_file_resets_to_fresh_state(tmp_path):
    (tmp_path / f"{INSTALL}.json").write_text("not json", encoding="utf-8")
    result = _cycle(tmp_path, now=1000)
    assert result["fail_count"] == 1  # ran as a fresh cycle, not a crash
    assert _disk_state(tmp_path)["fail_count"] == 1


def test_install_without_app_port_returns_none_without_raising(tmp_path, monkeypatch, capsys):
    # A slug whose unit did not carry the install's .env (a typo'd instance
    # name) has no APP_PORT — that is the "unknown install" signal now, in
    # place of the retired hardcoded install table.
    monkeypatch.delenv("APP_PORT", raising=False)
    result = watchdog.run_cycle(
        "nosuch", probe=lambda port: False, sender=_send,
        settings_reader=SETTINGS, credit_reader=RICH_CREDIT,
        state_path=tmp_path / "nosuch.json",
    )
    assert result is None
    assert "no APP_PORT" in capsys.readouterr().out


def test_default_state_lives_in_per_install_directory(tmp_path, monkeypatch):
    # Production runs as a per-install service user; the state must land in
    # STATE_DIR/<install>/state.json (a dir that user owns), not flat in the
    # root-owned parent where every persist would fail silently.
    monkeypatch.setattr(watchdog, "STATE_DIR", str(tmp_path))
    result = watchdog.run_cycle(
        INSTALL, now=1000, probe=lambda port: True, sender=_send,
        settings_reader=SETTINGS, credit_reader=RICH_CREDIT,
        alerts_reader=_no_monitoring_stack,
    )
    state_file = tmp_path / INSTALL / "state.json"
    assert state_file.is_file()
    assert result["fail_count"] == 0
    assert json.loads(state_file.read_text(encoding="utf-8"))["fail_count"] == 0


# ── Alertmanager SMS step (monitoring-stack SPEC 5.5, REQ-040..REQ-054) ──
#
# run_cycle reads page="sms" alerts from Alertmanager and texts them through
# the same sender as the down-SMS. Every boundary is faked here: the sender
# appends to SENT, and alerts_reader / owner_reader / silences_reader are
# lambdas. No test reaches Alertmanager, Asanak or the dev outbox (REQ-052:
# send_asanak has no dev branch, so an unfaked sender would send for real).

import base64
import urllib.error
import urllib.request

OTHER = "otherevent"
NOON_UTC = 1788091200  # 2026-08-30 12:00 UTC, 15:30 in Tehran
HEARTBEAT = {"labels": {"alertname": "MonitoringHeartbeat"}, "fingerprint": "hb",
             "status": {"state": "active"}}
NEW_KEY_DEFAULTS = {
    "alert_sent": {}, "alert_day": "", "alert_sms_today": 0, "alert_retry_after": 0.0,
    "am_fail_count": 0, "am_down_since": 0.0, "am_last_alert": 0.0, "silence_seen": [],
}
SILENCE = {"id": "s1", "status": {"state": "active"}, "createdBy": "operator"}


class SmsError(Exception):
    """Stands in for app.services.sms.SmsError (a spent daily budget raises it)."""


def _sms_budget_spent(dest, text):
    raise SmsError("daily budget spent")


def _alertmanager_down():
    raise urllib.error.URLError(ConnectionRefusedError(111, "Connection refused"))


def _alert(name, fp, install=INSTALL, state="active", page="sms"):
    labels = {"alertname": name, "severity": "critical"}
    if install:
        labels["install"] = install
    if page:
        labels["page"] = page
    return {"labels": labels, "fingerprint": fp, "status": {"state": state},
            "annotations": {"summary": "annotation text never reaches the SMS"}}


def _am_cycle(tmp_path, now, alerts=(), owner=OTHER, silences=(), heartbeat=True, **overrides):
    """One cycle of a healthy app on a host that runs the monitoring stack."""
    body = list(alerts) + ([HEARTBEAT] if heartbeat else [])
    defaults = dict(
        now=now,
        probe=lambda port: True,
        alerts_reader=lambda: body,
        owner_reader=lambda: owner,
        silences_reader=lambda: list(silences),
    )
    defaults.update(overrides)
    return _cycle(tmp_path, **defaults)


def _texts():
    return [text for _, text in SENT]


def test_a_new_alert_sends_one_sms_and_the_next_cycle_stays_silent(tmp_path):
    alert = _alert("PadyarHigh5xxRate", "fp1")
    _am_cycle(tmp_path, NOON_UTC, alerts=[alert])
    assert SENT == [(PHONE, "پادیار | هشدار MYEVENT: خطای سرور زیاد. جزئیات در صفحهٔ هشدار.")]
    _am_cycle(tmp_path, NOON_UTC + 60, alerts=[alert])
    assert len(SENT) == 1
    disk = _disk_state(tmp_path)
    assert disk["alert_sent"] == {"fp1": NOON_UTC} and disk["alert_sms_today"] == 1


def test_a_still_firing_alert_is_reminded_after_six_hours(tmp_path):
    alert = _alert("PadyarHigh5xxRate", "fp1")
    _am_cycle(tmp_path, NOON_UTC, alerts=[alert])
    _am_cycle(tmp_path, NOON_UTC + watchdog.ALERT_REMIND_SECONDS - 60, alerts=[alert])
    assert len(SENT) == 1
    _am_cycle(tmp_path, NOON_UTC + watchdog.ALERT_REMIND_SECONDS, alerts=[alert])
    assert len(SENT) == 2
    assert SENT[1][1] == ("یادآوری | پادیار | هشدار MYEVENT: خطای سرور زیاد. "
                          "جزئیات در صفحهٔ هشدار.")


def test_a_resolved_alert_is_forgotten_without_a_resolved_sms(tmp_path):
    _am_cycle(tmp_path, NOON_UTC, alerts=[_alert("PadyarHigh5xxRate", "fp1")])
    _am_cycle(tmp_path, NOON_UTC + 60, alerts=[])
    assert len(SENT) == 1
    assert _disk_state(tmp_path)["alert_sent"] == {}


def test_a_suppressed_alert_is_never_sent(tmp_path):
    _am_cycle(tmp_path, NOON_UTC, alerts=[_alert("PadyarHigh5xxRate", "fp1", state="suppressed")])
    assert SENT == []


def test_a_host_alert_is_sent_only_by_the_host_owner(tmp_path):
    host = _alert("HostDiskLow", "disk", install=None)
    _am_cycle(tmp_path / "owner", NOON_UTC, alerts=[host], owner=INSTALL)
    assert _texts() == ["پادیار | هشدار MYEVENT: دیسک تقریباً پر. جزئیات در صفحهٔ هشدار."]
    SENT.clear()
    _am_cycle(tmp_path / "other", NOON_UTC, alerts=[host], owner=OTHER)
    assert SENT == []


def test_while_the_app_is_down_its_own_alerts_wait_but_host_alerts_go(tmp_path):
    for now in (NOON_UTC, NOON_UTC + 60, NOON_UTC + 120):
        _am_cycle(tmp_path, now, owner=INSTALL, probe=lambda port: False)
    assert len(SENT) == 1 and "پاسخ نمی‌دهد" in SENT[0][1]
    own = _alert("PadyarHigh5xxRate", "own")
    host = _alert("HostPostgresDown", "db", install=None)
    _am_cycle(tmp_path, NOON_UTC + 180, alerts=[own, host], owner=INSTALL,
              probe=lambda port: False)
    assert len(SENT) == 2
    assert SENT[1][1] == "پادیار | هشدار MYEVENT: پایگاه داده پایین. جزئیات در صفحهٔ هشدار."


def test_many_alerts_share_one_sms_naming_three_and_counting_the_rest(tmp_path):
    names = ["PadyarHigh5xxRate", "PadyarChatLatencyHigh", "PadyarAICircuitOpen",
             "PadyarBackupFailed", "PadyarBackupStale"]
    _am_cycle(tmp_path, NOON_UTC, alerts=[_alert(n, n) for n in names])
    assert _texts() == ["پادیار | هشدار MYEVENT: خطای سرور زیاد، پاسخ چت کند، "
                        "سرویس هوش مصنوعی قطع و 2 مورد دیگر. جزئیات در صفحهٔ هشدار."]
    assert set(_disk_state(tmp_path)["alert_sent"]) == set(names)


def test_sms_text_never_carries_label_or_annotation_values(tmp_path):
    forged = _alert("Call 0912 0000000 now", "forged")
    forged["labels"]["note"] = "pay to 6037-0000"
    forged["annotations"] = {"summary": "pay to 6037-0000"}
    _am_cycle(tmp_path, NOON_UTC, alerts=[forged])
    assert _texts() == ["پادیار | هشدار MYEVENT: هشدار دیگر. جزئیات در صفحهٔ هشدار."]


def test_the_tenth_sms_of_a_day_is_the_cap_text_and_the_eleventh_is_not_sent(tmp_path):
    for i in range(11):
        _am_cycle(tmp_path, NOON_UTC + 60 * i,
                  alerts=[_alert("PadyarHigh5xxRate", f"fp{i}")])
    assert len(SENT) == 10
    assert all("خطای سرور زیاد" in text for text in _texts()[:9])
    assert SENT[9][1] == watchdog.cap_message("MYEVENT")
    disk = _disk_state(tmp_path)
    assert disk["alert_sms_today"] == 10
    assert disk["alert_sent"] == {}, "alerts seen under the cap must not be marked as sent"
    _am_cycle(tmp_path, NOON_UTC + 86400, alerts=[_alert("PadyarHigh5xxRate", "fp10")])
    assert len(SENT) == 11 and "خطای سرور زیاد" in SENT[10][1]


def test_a_failed_send_marks_nothing_and_waits_before_the_next_try(tmp_path, capsys):
    alert = _alert("PadyarHigh5xxRate", "fp1")
    _am_cycle(tmp_path, NOON_UTC, alerts=[alert], sender=_sms_budget_spent)
    out = capsys.readouterr().out
    assert f"[watchdog] {INSTALL}: alert send failed: SmsError" in out
    assert "budget spent" not in out
    disk = _disk_state(tmp_path)
    assert disk["alert_sent"] == {}, "a failed send marks nothing, so the alert is retried"
    assert disk["alert_sms_today"] == 1, "every attempt counts against the daily cap"
    assert disk["alert_retry_after"] == NOON_UTC + watchdog.ALERT_RETRY_SECONDS
    _am_cycle(tmp_path, NOON_UTC + 60, alerts=[alert])
    assert SENT == []
    _am_cycle(tmp_path, NOON_UTC + watchdog.ALERT_RETRY_SECONDS, alerts=[alert])
    assert len(SENT) == 1


def test_no_alert_phone_journals_and_marks_nothing(tmp_path, capsys):
    _am_cycle(tmp_path, NOON_UTC, alerts=[_alert("PadyarHigh5xxRate", "fp1")],
              settings_reader=lambda: ("", "300000"))
    assert SENT == []
    assert (f"[watchdog] {INSTALL}: alert pending but no alert_critical_phone configured"
            in capsys.readouterr().out)
    assert _disk_state(tmp_path)["alert_sent"] == {}


def test_an_unreadable_password_file_journals_monitoring_off_every_cycle(tmp_path, capsys):
    def unreadable():
        raise PermissionError(13, "Permission denied")

    for now in (NOON_UTC, NOON_UTC + 60):
        _am_cycle(tmp_path, now, alerts_reader=unreadable)
    line = (f"[watchdog] {INSTALL}: monitoring alerts OFF: cannot read "
            f"alertmanager-watchdog.pass (PermissionError); "
            f"re-run deploy/55-monitoring.sh {INSTALL}")
    assert capsys.readouterr().out.count(line) == 2
    assert SENT == []


def test_a_missing_password_file_skips_the_step_without_a_word(tmp_path, capsys):
    _cycle(tmp_path, now=NOON_UTC, probe=lambda port: True)
    assert capsys.readouterr().out == ""
    assert SENT == []


def test_an_install_without_the_monitoring_stack_keeps_todays_sms_and_state(tmp_path):
    for now in (1000, 1060, 1120):
        _cycle(tmp_path, now=now)
    assert len(SENT) == 1 and "پاسخ نمی‌دهد" in SENT[0][1]
    disk = _disk_state(tmp_path)
    assert {k: disk[k] for k in NEW_KEY_DEFAULTS} == NEW_KEY_DEFAULTS
    assert set(disk) == set(NEW_KEY_DEFAULTS) | {
        "fail_count", "down_since", "last_alert", "credit_day", "credit_alerted", "cached_phone"}


def test_alertmanager_down_three_cycles_texts_only_the_host_owner(tmp_path, capsys):
    for now in (NOON_UTC, NOON_UTC + 60, NOON_UTC + 120, NOON_UTC + 180):
        _am_cycle(tmp_path / "owner", now, owner=INSTALL, alerts_reader=_alertmanager_down)
    assert _texts() == [watchdog.monitoring_down_message(NOON_UTC)]
    SENT.clear()
    capsys.readouterr()
    for now in (NOON_UTC, NOON_UTC + 60, NOON_UTC + 120, NOON_UTC + 180):
        _am_cycle(tmp_path / "other", now, owner=OTHER, alerts_reader=_alertmanager_down)
    assert SENT == []
    assert capsys.readouterr().out.count("URLError") == 4
    other = json.loads((tmp_path / "other" / f"{INSTALL}.json").read_text(encoding="utf-8"))
    assert other["am_fail_count"] == 0, "the streak lives only in the host owner's state"


def test_monitoring_down_reminds_after_six_hours_and_a_healthy_cycle_resets_it(tmp_path):
    for now in (NOON_UTC, NOON_UTC + 60, NOON_UTC + 120):
        _am_cycle(tmp_path, now, owner=INSTALL, alerts_reader=_alertmanager_down)
    later = NOON_UTC + 120 + watchdog.ALERT_REMIND_SECONDS
    _am_cycle(tmp_path, later, owner=INSTALL, alerts_reader=_alertmanager_down)
    assert _texts() == [watchdog.monitoring_down_message(NOON_UTC)] * 2
    _am_cycle(tmp_path, later + 60, owner=INSTALL)
    disk = _disk_state(tmp_path)
    assert disk["am_fail_count"] == 0 and disk["am_down_since"] == 0.0


def test_a_missing_heartbeat_three_cycles_means_monitoring_is_down(tmp_path):
    for now in (NOON_UTC, NOON_UTC + 60, NOON_UTC + 120):
        _am_cycle(tmp_path, now, owner=INSTALL, heartbeat=False)
    assert _texts() == [watchdog.monitoring_down_message(NOON_UTC)]


def test_a_garbage_alerts_body_is_an_api_failure_not_a_crash(tmp_path):
    for now in (NOON_UTC, NOON_UTC + 60, NOON_UTC + 120):
        result = _am_cycle(tmp_path, now, owner=INSTALL, alerts_reader=lambda: {"oops": 1})
        assert isinstance(result, dict)
    assert _texts() == [watchdog.monitoring_down_message(NOON_UTC)]


def test_monitoring_down_goes_first_and_a_due_alert_waits_one_cycle(tmp_path):
    host = _alert("HostDiskLow", "disk", install=None)
    _am_cycle(tmp_path, NOON_UTC, owner=INSTALL, heartbeat=False)
    _am_cycle(tmp_path, NOON_UTC + 60, owner=INSTALL, heartbeat=False)
    _am_cycle(tmp_path, NOON_UTC + 120, alerts=[host], owner=INSTALL, heartbeat=False)
    assert _texts() == [watchdog.monitoring_down_message(NOON_UTC)]
    _am_cycle(tmp_path, NOON_UTC + 180, alerts=[host], owner=INSTALL, heartbeat=False)
    assert len(SENT) == 2 and "دیسک تقریباً پر" in SENT[1][1]


def test_a_failed_alerts_call_does_not_forget_what_was_sent(tmp_path):
    alert = _alert("PadyarHigh5xxRate", "fp1")
    _am_cycle(tmp_path, NOON_UTC, alerts=[alert])
    _am_cycle(tmp_path, NOON_UTC + 60, alerts_reader=_alertmanager_down)
    assert _disk_state(tmp_path)["alert_sent"] == {"fp1": NOON_UTC}
    _am_cycle(tmp_path, NOON_UTC + 120, alerts=[alert])
    assert len(SENT) == 1


def test_a_new_silence_is_texted_once_by_the_host_owner_only(tmp_path):
    _am_cycle(tmp_path / "owner", NOON_UTC, owner=INSTALL, silences=[SILENCE])
    assert _texts() == ["پادیار | هشدار MYEVENT: یک silence تازه گذاشته شد. "
                        "جزئیات در صفحهٔ هشدار."]
    _am_cycle(tmp_path / "owner", NOON_UTC + 60, owner=INSTALL, silences=[SILENCE])
    assert len(SENT) == 1
    SENT.clear()
    _am_cycle(tmp_path / "other", NOON_UTC, owner=OTHER, silences=[SILENCE])
    assert SENT == []


def test_the_silence_notice_comes_first_so_it_is_never_folded_away(tmp_path):
    names = ["PadyarHigh5xxRate", "PadyarChatLatencyHigh", "PadyarAICircuitOpen",
             "PadyarBackupFailed"]
    _am_cycle(tmp_path, NOON_UTC, alerts=[_alert(n, n) for n in names],
              owner=INSTALL, silences=[SILENCE])
    assert _texts() == ["پادیار | هشدار MYEVENT: یک silence تازه گذاشته شد، "
                        "خطای سرور زیاد، پاسخ چت کند و 2 مورد دیگر. جزئیات در صفحهٔ هشدار."]


def test_a_silence_is_recorded_only_after_a_successful_send_and_pruned_when_gone(tmp_path):
    _am_cycle(tmp_path, NOON_UTC, owner=INSTALL, silences=[SILENCE], sender=_sms_budget_spent)
    assert _disk_state(tmp_path)["silence_seen"] == []
    _am_cycle(tmp_path, NOON_UTC + watchdog.ALERT_RETRY_SECONDS, owner=INSTALL,
              silences=[SILENCE])
    assert len(SENT) == 1 and _disk_state(tmp_path)["silence_seen"] == ["s1"]
    _am_cycle(tmp_path, NOON_UTC + watchdog.ALERT_RETRY_SECONDS + 60, owner=INSTALL)
    assert _disk_state(tmp_path)["silence_seen"] == []


def test_a_failed_silences_call_counts_toward_monitoring_down(tmp_path):
    for now in (NOON_UTC, NOON_UTC + 60, NOON_UTC + 120):
        _am_cycle(tmp_path, now, owner=INSTALL, silences_reader=_alertmanager_down)
    assert _texts() == [watchdog.monitoring_down_message(NOON_UTC)]


class _FakeResponse:
    def __init__(self, status, body):
        self.status = status
        self._body = json.dumps(body).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, amt=None):
        return self._body if amt is None else self._body[:amt]


def _fake_alertmanager(monkeypatch, status, body):
    """Replace urllib's opener; record every request and the handlers used."""
    calls, handlers = [], []

    class Opener:
        def open(self, request, timeout):
            calls.append((request.full_url, request.get_method(),
                          request.get_header("Authorization"), timeout))
            return _FakeResponse(status, body)

    def build_opener(*given):
        handlers.extend(given)
        return Opener()

    monkeypatch.setattr(urllib.request, "build_opener", build_opener)
    return calls, handlers


def test_default_reader_sends_one_basic_auth_get_to_loopback_without_a_proxy(
        tmp_path, monkeypatch, capsys):
    password_file = tmp_path / "alertmanager-watchdog.pass"
    password_file.write_text("s3cret\n", encoding="utf-8")
    monkeypatch.setattr(watchdog, "ALERTMANAGER_PASS_FILE", str(password_file))
    monkeypatch.setenv("http_proxy", "http://proxy.invalid:3128")
    calls, handlers = _fake_alertmanager(
        monkeypatch, 200, [_alert("PadyarHigh5xxRate", "fp1"), HEARTBEAT])
    _cycle(tmp_path, now=NOON_UTC, probe=lambda port: True, alerts_reader=None,
           owner_reader=lambda: OTHER)
    token = base64.b64encode(b"watchdog:s3cret").decode("ascii")
    assert calls == [("http://127.0.0.1:9093/api/v2/alerts?active=true", "GET",
                      f"Basic {token}", 5)]
    assert any(isinstance(h, urllib.request.ProxyHandler) and h.proxies == {}
               for h in handlers), "the password must never travel through a proxy"
    assert len(SENT) == 1
    assert "s3cret" not in capsys.readouterr().out


def test_default_reader_treats_a_non_200_answer_as_an_api_failure(tmp_path, monkeypatch, capsys):
    password_file = tmp_path / "alertmanager-watchdog.pass"
    password_file.write_text("s3cret", encoding="utf-8")
    monkeypatch.setattr(watchdog, "ALERTMANAGER_PASS_FILE", str(password_file))
    _fake_alertmanager(monkeypatch, 204, [_alert("PadyarHigh5xxRate", "fp1")])
    _cycle(tmp_path, now=NOON_UTC, probe=lambda port: True, alerts_reader=None,
           owner_reader=lambda: OTHER)
    assert SENT == []
    out = capsys.readouterr().out
    assert f"[watchdog] {INSTALL}: alertmanager unreadable" in out
    assert "s3cret" not in out


def test_default_reader_reports_any_password_read_error_as_monitoring_off(
        tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(watchdog, "ALERTMANAGER_PASS_FILE", str(tmp_path))  # a directory
    _cycle(tmp_path, now=NOON_UTC, probe=lambda port: True, alerts_reader=None)
    assert ("monitoring alerts OFF: cannot read alertmanager-watchdog.pass "
            "(IsADirectoryError)") in capsys.readouterr().out


def test_default_reader_without_the_password_file_is_the_silent_skip(
        tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(watchdog, "ALERTMANAGER_PASS_FILE", str(tmp_path / "absent.pass"))
    _cycle(tmp_path, now=NOON_UTC, probe=lambda port: True, alerts_reader=None)
    assert capsys.readouterr().out == "" and SENT == []


def test_default_owner_reader_reads_the_host_alerts_owner_file(tmp_path, monkeypatch):
    owner_file = tmp_path / "host-alerts-owner"
    owner_file.write_text(f"{INSTALL}\n", encoding="utf-8")
    monkeypatch.setattr(watchdog, "HOST_ALERTS_OWNER_FILE", str(owner_file))
    host = _alert("HostDiskLow", "disk", install=None)
    _am_cycle(tmp_path, NOON_UTC, alerts=[host], owner_reader=None)
    assert len(SENT) == 1
    SENT.clear()
    monkeypatch.setattr(watchdog, "HOST_ALERTS_OWNER_FILE", str(tmp_path / "absent"))
    _am_cycle(tmp_path / "second", NOON_UTC, alerts=[host], owner_reader=None)
    assert SENT == []


def test_a_monitoring_streak_does_not_survive_a_spell_as_non_owner(tmp_path):
    for now in (NOON_UTC, NOON_UTC + 60):
        _am_cycle(tmp_path, now, owner=INSTALL, alerts_reader=_alertmanager_down)
    _am_cycle(tmp_path, NOON_UTC + 120, owner=OTHER, alerts_reader=_alertmanager_down)
    for now in (NOON_UTC + 180, NOON_UTC + 240):
        _am_cycle(tmp_path, now, owner=INSTALL, alerts_reader=_alertmanager_down)
    assert SENT == [], "owner again needs three fresh bad cycles"
    _am_cycle(tmp_path, NOON_UTC + 300, owner=INSTALL, alerts_reader=_alertmanager_down)
    assert _texts() == [watchdog.monitoring_down_message(NOON_UTC + 180)]


def _password_unreadable():
    raise PermissionError(13, "Permission denied")


@pytest.mark.parametrize("skipped_reader", [_no_monitoring_stack, _password_unreadable])
def test_a_monitoring_streak_does_not_survive_a_skipped_step(tmp_path, skipped_reader):
    for now in (NOON_UTC, NOON_UTC + 60):
        _am_cycle(tmp_path, now, owner=INSTALL, alerts_reader=_alertmanager_down)
    _am_cycle(tmp_path, NOON_UTC + 120, owner=INSTALL, alerts_reader=skipped_reader)
    disk = _disk_state(tmp_path)
    assert disk["am_fail_count"] == 0 and disk["am_down_since"] == 0.0
    for now in (NOON_UTC + 180, NOON_UTC + 240):
        _am_cycle(tmp_path, now, owner=INSTALL, alerts_reader=_alertmanager_down)
    assert SENT == [], "resuming needs three fresh bad cycles"
    _am_cycle(tmp_path, NOON_UTC + 300, owner=INSTALL, alerts_reader=_alertmanager_down)
    assert _texts() == [watchdog.monitoring_down_message(NOON_UTC + 180)]


def test_default_reader_refuses_a_redirect_and_counts_it_as_a_failure(
        tmp_path, monkeypatch, capsys):
    import http.server
    import threading

    seen = []

    class RedirectEverything(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append((self.headers.get("Host"), self.path,
                         self.headers.get("Authorization")))
            self.send_response(302)
            # Another host name for the same listener: a followed redirect
            # would show up here as a second request carrying the password.
            self.send_header("Location", f"http://localhost:{port}/stolen")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), RedirectEverything)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        password_file = tmp_path / "alertmanager-watchdog.pass"
        password_file.write_text("s3cret", encoding="utf-8")
        monkeypatch.setattr(watchdog, "ALERTMANAGER_PASS_FILE", str(password_file))
        monkeypatch.setattr(watchdog, "ALERTMANAGER_URL", f"http://127.0.0.1:{port}")
        _cycle(tmp_path, now=NOON_UTC, probe=lambda port: True, alerts_reader=None,
               owner_reader=lambda: INSTALL)
    finally:
        server.shutdown()
        server.server_close()
    assert [path for _, path, _ in seen] == ["/api/v2/alerts?active=true"], seen
    assert _disk_state(tmp_path)["am_fail_count"] == 1
    out = capsys.readouterr().out
    assert f"[watchdog] {INSTALL}: alertmanager unreadable" in out
    assert "s3cret" not in out
    assert SENT == []


# ── Write-ahead: the cap, the dedup and the retry gap live only in state.json ──
#
# A state file that cannot be written (disk full, read-only directory) used to
# mean "every cycle sends again". The send is now recorded and saved BEFORE
# the sender runs, and a failed save means no SMS this cycle.

def _unwritable_state_path(tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("a file where the state directory should be", encoding="utf-8")
    return blocker / "state.json"


def test_no_alert_sms_goes_out_while_the_state_cannot_be_saved(tmp_path, capsys):
    path = _unwritable_state_path(tmp_path)
    for i in range(30):
        _am_cycle(tmp_path, NOON_UTC + 60 * i, owner=INSTALL, state_path=path,
                  alerts=[_alert("PadyarHigh5xxRate", "fp1")])
    assert SENT == []
    skipped = f"[watchdog] {INSTALL}: alert SMS skipped: state not saved"
    assert capsys.readouterr().out.count(skipped) == 30


def test_a_readable_but_unwritable_state_does_not_resend_every_cycle(tmp_path, monkeypatch, capsys):
    path = tmp_path / f"{INSTALL}.json"
    state = watchdog._fresh_state()
    state["cached_phone"] = PHONE
    path.write_text(json.dumps(state), encoding="utf-8")
    monkeypatch.setattr(watchdog, "_persist", lambda path, state: False)
    for i in range(5):
        _am_cycle(tmp_path, NOON_UTC + 60 * i, alerts=[_alert("PadyarHigh5xxRate", "fp1")])
    assert SENT == []
    assert capsys.readouterr().out.count("alert SMS skipped: state not saved") == 5


def test_control_a_writable_state_keeps_one_sms_per_cycle_and_the_cap(tmp_path):
    for i in range(30):
        _am_cycle(tmp_path, NOON_UTC + 60 * i, owner=INSTALL,
                  alerts=[_alert("PadyarHigh5xxRate", f"fp{i}")])
    assert len(SENT) == 10
    assert SENT[9][1] == watchdog.cap_message("MYEVENT")
    assert _disk_state(tmp_path)["alert_sms_today"] == 10


def test_the_send_is_on_disk_before_the_sender_runs(tmp_path):
    on_disk_during_send = []

    def sender(dest, text):
        on_disk_during_send.append(_disk_state(tmp_path))
        SENT.append((dest, text))

    _am_cycle(tmp_path, NOON_UTC, alerts=[_alert("PadyarHigh5xxRate", "fp1")], sender=sender)
    during = on_disk_during_send[0]
    assert during["alert_sent"] == {"fp1": NOON_UTC}
    assert during["alert_sms_today"] == 1
    assert during["alert_retry_after"] == NOON_UTC + watchdog.ALERT_RETRY_SECONDS
    after = _disk_state(tmp_path)
    assert after["alert_sent"] == {"fp1": NOON_UTC} and after["alert_sms_today"] == 1
    assert after["alert_retry_after"] == 0.0, "a successful send leaves no retry gap"
    _am_cycle(tmp_path, NOON_UTC + 60, alerts=[_alert("PadyarHigh5xxRate", "fp2")])
    assert len(SENT) == 2


def test_a_failed_send_after_a_saved_attempt_is_not_retried_in_the_same_cycle(tmp_path):
    calls = []

    def failing(dest, text):
        calls.append(text)
        raise SmsError("gateway down")

    alert = _alert("PadyarHigh5xxRate", "fp1")
    _am_cycle(tmp_path, NOON_UTC, alerts=[alert], sender=failing)
    assert len(calls) == 1
    disk = _disk_state(tmp_path)
    assert disk["alert_sent"] == {} and disk["alert_sms_today"] == 1
    assert disk["alert_retry_after"] == NOON_UTC + watchdog.ALERT_RETRY_SECONDS
    _am_cycle(tmp_path, NOON_UTC + watchdog.ALERT_RETRY_SECONDS - 1, alerts=[alert],
              sender=failing)
    assert len(calls) == 1, "the retry gap holds"
    _am_cycle(tmp_path, NOON_UTC + watchdog.ALERT_RETRY_SECONDS, alerts=[alert])
    assert len(SENT) == 1


def test_a_process_killed_mid_send_does_not_send_again(tmp_path):
    def killed(dest, text):
        raise SystemExit("killed by systemd while sending")

    alert = _alert("PadyarHigh5xxRate", "fp1")
    with pytest.raises(SystemExit):
        _am_cycle(tmp_path, NOON_UTC, alerts=[alert], sender=killed)
    _am_cycle(tmp_path, NOON_UTC + watchdog.ALERT_RETRY_SECONDS, alerts=[alert])
    assert SENT == [], "at most once: the saved attempt counts as sent"


# ── Bounded input and bounded state ──

def _sized_alertmanager(monkeypatch, body):
    """Fake opener serving `body` (bytes); records the size each read asked for."""
    asked = []

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self, amt=None):
            asked.append(amt)
            return body if amt is None else body[:amt]

    class Opener:
        def open(self, request, timeout):
            return Response()

    monkeypatch.setattr(urllib.request, "build_opener", lambda *handlers: Opener())
    return asked


def _body_of_size(size):
    """A JSON alert list of about `size` bytes with one real alert in it."""
    filler = {"labels": {"alertname": "Filler", "pad": ""}, "fingerprint": "pad",
              "status": {"state": "active"}}
    alerts = [_alert("PadyarHigh5xxRate", "fp1"), HEARTBEAT, filler]
    base = len(json.dumps(alerts).encode("utf-8"))
    filler["labels"]["pad"] = "x" * max(0, size - base)
    return json.dumps(alerts).encode("utf-8")


def _password(tmp_path, monkeypatch):
    password_file = tmp_path / "alertmanager-watchdog.pass"
    password_file.write_text("s3cret", encoding="utf-8")
    monkeypatch.setattr(watchdog, "ALERTMANAGER_PASS_FILE", str(password_file))


def test_an_alertmanager_body_over_the_limit_is_an_api_failure(tmp_path, monkeypatch, capsys):
    _password(tmp_path, monkeypatch)
    limit = watchdog.ALERTMANAGER_MAX_BODY_BYTES
    asked = _sized_alertmanager(monkeypatch, _body_of_size(limit + 1))
    _cycle(tmp_path, now=NOON_UTC, probe=lambda port: True, alerts_reader=None,
           owner_reader=lambda: OTHER)
    assert SENT == []
    assert f"[watchdog] {INSTALL}: alertmanager unreadable" in capsys.readouterr().out
    assert asked == [limit + 1], "the reader never asks for more than the limit plus one byte"


def test_control_an_alertmanager_body_at_the_limit_is_read(tmp_path, monkeypatch):
    _password(tmp_path, monkeypatch)
    body = _body_of_size(watchdog.ALERTMANAGER_MAX_BODY_BYTES)
    assert len(body) == watchdog.ALERTMANAGER_MAX_BODY_BYTES
    _sized_alertmanager(monkeypatch, body)
    _cycle(tmp_path, now=NOON_UTC, probe=lambda port: True, alerts_reader=None,
           owner_reader=lambda: OTHER)
    assert _texts() == ["پادیار | هشدار MYEVENT: خطای سرور زیاد. جزئیات در صفحهٔ هشدار."]


def test_forged_alerts_cannot_grow_the_state_without_bound(tmp_path):
    many = watchdog.ALERT_SENT_MAX + 50
    _am_cycle(tmp_path, NOON_UTC, alerts=[_alert("PadyarHigh5xxRate", f"f{i}") for i in range(many)])
    assert len(SENT) == 1
    assert len(_disk_state(tmp_path)["alert_sent"]) == watchdog.ALERT_SENT_MAX


# ── Every send attempt counts against the daily cap ──
#
# A gateway can accept an SMS and then fail the call (a timeout after the
# accept, a reply that does not parse). Counting only successful sends let
# such a gateway deliver one SMS every ALERT_RETRY_SECONDS: 288 a day. Now
# the attempt counts, and only the "sent" marks are undone, so the alert is
# still retried and the daily cap bounds spend in every failure mode.

def test_a_gateway_that_delivers_then_raises_is_bounded_by_the_daily_cap(tmp_path):
    def delivered_then_raised(dest, text):
        SENT.append((dest, text))
        raise TimeoutError("timed out after the gateway accepted the SMS")

    alert = _alert("PadyarHigh5xxRate", "fp1")
    midnight = NOON_UTC - 12 * 3600
    for minute in range(24 * 60):
        _am_cycle(tmp_path, midnight + 60 * minute, alerts=[alert],
                  sender=delivered_then_raised)
    assert len(SENT) == watchdog.ALERT_SMS_DAILY_CAP
    assert SENT[-1][1] == watchdog.cap_message("MYEVENT")


def test_control_a_failing_gateway_is_retried_every_300_s_until_the_cap(tmp_path):
    clock, calls = {}, []

    def failing(dest, text):
        calls.append((clock["now"], text))
        raise SmsError("gateway down")

    alert = _alert("PadyarHigh5xxRate", "fp1")
    for minute in range(120):
        clock["now"] = NOON_UTC + 60 * minute
        _am_cycle(tmp_path, clock["now"], alerts=[alert], sender=failing)
    assert len(calls) == watchdog.ALERT_SMS_DAILY_CAP
    assert {b - a for (a, _), (b, _) in zip(calls, calls[1:])} == {watchdog.ALERT_RETRY_SECONDS}
    assert calls[-1][1] == watchdog.cap_message("MYEVENT")
    assert _disk_state(tmp_path)["alert_sent"] == {}
    _am_cycle(tmp_path, NOON_UTC + 86400, alerts=[alert])
    assert _texts() == ["پادیار | هشدار MYEVENT: خطای سرور زیاد. جزئیات در صفحهٔ هشدار."], \
        "the unmarked alert is sent once the gateway works again on a new day"


# ── A wall-clock deadline for each Alertmanager GET ──
#
# urlopen's timeout bounds each socket read, not the whole GET. A listener on
# 127.0.0.1:9093 that sends one byte at a time held a GET for many seconds,
# and the oneshot unit had no start timeout, so one stuck cycle stopped the
# watchdog for that install, the down-SMS included.

import http.server
import re
import threading
import time


class _Dripping(http.server.BaseHTTPRequestHandler):
    """Sends a little, then one byte every 0.2 s for at most 6 s."""
    stop = None
    where = "body"

    def do_GET(self):
        if self.where == "headers":
            self.wfile.write(b"HTTP/1.0 200 OK\r\nX-Drip: ")
        else:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", "100000")
            self.end_headers()
        self.wfile.flush()
        for _ in range(30):
            if self.stop.wait(0.2):
                return
            try:
                self.wfile.write(b" ")
                self.wfile.flush()
            except OSError:
                return

    def log_message(self, *args):
        pass


def _local_server(handler):
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@pytest.mark.parametrize("where", ["headers", "body"])
def test_a_dripping_alertmanager_is_cut_off_at_the_deadline(tmp_path, monkeypatch, capsys, where):
    _password(tmp_path, monkeypatch)
    monkeypatch.setattr(watchdog, "ALERTMANAGER_DEADLINE_SECONDS", 1.0, raising=False)
    stop = threading.Event()
    server = _local_server(type("Drip", (_Dripping,), {"stop": stop, "where": where}))
    monkeypatch.setattr(watchdog, "ALERTMANAGER_URL", f"http://127.0.0.1:{server.server_address[1]}")
    try:
        started = time.monotonic()
        _cycle(tmp_path, now=NOON_UTC, probe=lambda port: True, alerts_reader=None,
               owner_reader=lambda: INSTALL)
        elapsed = time.monotonic() - started
    finally:
        stop.set()
        server.shutdown()
        server.server_close()
    assert elapsed < 2.0, f"the cycle waited {elapsed:.1f} s for a 1 s deadline"
    assert f"[watchdog] {INSTALL}: alertmanager unreadable (TimeoutError)" in capsys.readouterr().out
    assert _disk_state(tmp_path)["am_fail_count"] == 1
    assert SENT == []


def test_control_a_prompt_alertmanager_is_read_as_before(tmp_path, monkeypatch):
    _password(tmp_path, monkeypatch)
    body = json.dumps([_alert("PadyarHigh5xxRate", "fp1"), HEARTBEAT]).encode("utf-8")

    class Prompt(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = _local_server(Prompt)
    monkeypatch.setattr(watchdog, "ALERTMANAGER_URL", f"http://127.0.0.1:{server.server_address[1]}")
    try:
        _cycle(tmp_path, now=NOON_UTC, probe=lambda port: True, alerts_reader=None,
               owner_reader=lambda: OTHER)
    finally:
        server.shutdown()
        server.server_close()
    assert _texts() == ["پادیار | هشدار MYEVENT: خطای سرور زیاد. جزئیات در صفحهٔ هشدار."]


def test_the_unit_ends_only_a_truly_stuck_cycle():
    """The down-SMS and the low-credit SMS save state only at the end of a
    cycle, so a timeout that can kill a normal cycle after one of those sends
    makes the next cycle send it again. 300 s ends only a truly stuck run;
    the 8 s GET deadline already bounds an Alertmanager stall."""
    unit = (WATCHDOG.parents[1] / "systemd" / "padyar-watchdog@.service").read_text(
        encoding="utf-8")
    timeout = re.search(r"^TimeoutStartSec=(\d+)s$", unit, re.M)
    assert timeout, "a oneshot unit has no start timeout unless it sets one"
    assert int(timeout.group(1)) == 300
    assert int(timeout.group(1)) > 2 * watchdog.ALERTMANAGER_DEADLINE_SECONDS + 5, \
        "room for the 5 s probe and two Alertmanager GETs"


def test_an_app_that_cannot_be_imported_is_one_clear_line_and_no_send(tmp_path, monkeypatch, capsys):
    """When the unit's PYTHONPATH does not reach the app, every app-backed
    reader and the SMS sender fail. That used to read as "settings unreadable"
    and "send failed" each cycle, and no SMS ever went out. Now one line names
    the real cause and no send is tried, so the line "SMS disabled" is true.
    The probe verdict, the down streak and the cached phone keep working, and
    no alert is marked as sent, so it still goes out once the unit is fixed."""
    import sys

    def gateway_needs_the_app(destination, text):
        raise ModuleNotFoundError("No module named 'app'")

    monkeypatch.setitem(sys.modules, "app", None)
    monkeypatch.setattr(watchdog, "_send", gateway_needs_the_app)
    state_path = tmp_path / f"{INSTALL}.json"
    state_path.write_text(json.dumps({"cached_phone": PHONE}), encoding="utf-8")
    alert = _alert("PadyarHigh5xxRate", "fp1")

    for now in (1000, 1060, 1120):
        _cycle(tmp_path, now=now, sender=None, settings_reader=None, credit_reader=None,
               alerts_reader=lambda: [alert, HEARTBEAT], owner_reader=lambda: INSTALL,
               silences_reader=lambda: [])
        out = capsys.readouterr().out
        assert out.count(f"[watchdog] {INSTALL}: cannot import the app (ModuleNotFoundError): "
                         "SMS disabled; check PYTHONPATH in the unit") == 1, out
        assert "send failed" not in out and "unreadable" not in out, out
    disk = _disk_state(tmp_path)
    assert disk["fail_count"] == 3 and disk["down_since"] == 1000
    assert disk["alert_sent"] == {} and disk["alert_sms_today"] == 0

    _cycle(tmp_path, now=1180, probe=lambda port: True, sender=None, settings_reader=None,
           credit_reader=None)
    out = capsys.readouterr().out
    assert "cannot import the app" in out and "unreadable" not in out, out
    assert _disk_state(tmp_path)["fail_count"] == 0
    assert _disk_state(tmp_path)["cached_phone"] == PHONE
