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

PHONE = "09121234567"
SETTINGS = lambda: (PHONE, "300000")  # noqa: E731
RICH_CREDIT = lambda: 10_000_000  # noqa: E731 — far above any floor
SENT = []


@pytest.fixture(autouse=True)
def _isolate_sent():
    SENT.clear()
    yield
    SENT.clear()


def _send(dest, text):
    SENT.append((dest, text))


def _cycle(tmp_path, **overrides):
    """One watchdog cycle with every outside dependency replaced."""
    defaults = dict(
        probe=lambda port: False,
        sender=_send,
        settings_reader=SETTINGS,
        credit_reader=RICH_CREDIT,
        state_path=tmp_path / "inotex.json",
    )
    defaults.update(overrides)
    return watchdog.run_cycle("inotex", **defaults)


def _disk_state(tmp_path):
    return json.loads((tmp_path / "inotex.json").read_text(encoding="utf-8"))


def test_three_bad_probes_send_exactly_one_alert_sms(tmp_path):
    for now in (1000, 1060, 1120):
        _cycle(tmp_path, now=now)
    assert len(SENT) == 1
    dest, text = SENT[0]
    assert dest == PHONE
    assert "INOTEX" in text and "پاسخ نمی‌دهد" in text
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
    state_path = tmp_path / "inotex.json"
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
    (tmp_path / "inotex.json").write_text("not json", encoding="utf-8")
    result = _cycle(tmp_path, now=1000)
    assert result["fail_count"] == 1  # ran as a fresh cycle, not a crash
    assert _disk_state(tmp_path)["fail_count"] == 1


def test_unknown_install_returns_none_without_raising(tmp_path):
    result = watchdog.run_cycle(
        "nosuch", probe=lambda port: False, sender=_send,
        settings_reader=SETTINGS, credit_reader=RICH_CREDIT,
        state_path=tmp_path / "nosuch.json",
    )
    assert result is None


def test_default_state_lives_in_per_install_directory(tmp_path, monkeypatch):
    # Production runs as a per-install service user; the state must land in
    # STATE_DIR/<install>/state.json (a dir that user owns), not flat in the
    # root-owned parent where every persist would fail silently.
    monkeypatch.setattr(watchdog, "STATE_DIR", str(tmp_path))
    result = watchdog.run_cycle(
        "inotex", now=1000, probe=lambda port: True, sender=_send,
        settings_reader=SETTINGS, credit_reader=RICH_CREDIT,
    )
    state_file = tmp_path / "inotex" / "state.json"
    assert state_file.is_file()
    assert result["fail_count"] == 0
    assert json.loads(state_file.read_text(encoding="utf-8"))["fail_count"] == 0
