"""read_settings_strict: the settings read that fails when the database does.

WHY THIS EXISTS
---------------
get_setting() swallows every database error and returns its default. For the
app that is right: a visitor must never see a 500 because a settings row was
unreadable. For the watchdog it was wrong. During a PostgreSQL outage it read
alert_critical_phone as "" (not configured) instead of "unreadable", so its
cached-phone fallback never ran and no SMS went out at all.

read_settings_strict() is the one read that raises. get_setting() runs on top
of the same query and reveal code, so the two cannot drift apart, and keeps its
swallow-and-default behaviour unchanged.
"""
import pytest


@pytest.fixture
def app_db(tmp_path, monkeypatch):
    import app.config as config
    from app.db.connection import init_db

    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "settings.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    init_db()
    yield


@pytest.fixture
def unreachable_db(tmp_path, monkeypatch):
    import app.config as config

    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "no-such-dir" / "settings.db"))
    yield


def test_several_keys_come_back_from_one_read(app_db):
    from app.db.queries import read_settings_strict, set_setting

    set_setting("alert_critical_phone", "+989121234567")
    set_setting("alert_credit_threshold_toman", "250000")

    values = read_settings_strict(
        ["alert_critical_phone", "alert_credit_threshold_toman", "never_written"])

    assert values == {"alert_critical_phone": "+989121234567",
                      "alert_credit_threshold_toman": "250000"}, \
        "a key with no row must be absent, not a guessed default"


def test_an_encrypted_row_comes_back_revealed(app_db):
    from app.db.queries import read_settings_strict, set_setting
    from app.services.secure_store import protect

    stored = protect("asanak-secret")
    assert stored.startswith("enc:")
    set_setting("sms_asanak_password", stored)

    assert read_settings_strict(["sms_asanak_password"]) == {
        "sms_asanak_password": "asanak-secret"}


def test_an_unreachable_database_raises_instead_of_returning_nothing(unreachable_db):
    from app.db.queries import read_settings_strict

    with pytest.raises(Exception):
        read_settings_strict(["alert_critical_phone"])


def test_get_setting_still_swallows_the_same_error(unreachable_db):
    from app.db.queries import get_setting

    assert get_setting("alert_critical_phone", "fallback", fresh=True) == "fallback"


def test_get_setting_reads_through_the_strict_read(app_db, monkeypatch):
    from app.db import queries

    queries.set_setting("whitelabel_app_name", "Padyar")
    calls = []
    real = queries.read_settings_strict

    def spy(keys):
        calls.append(list(keys))
        return real(keys)

    monkeypatch.setattr(queries, "read_settings_strict", spy)

    assert queries.get_setting("whitelabel_app_name", fresh=True) == "Padyar"
    assert calls == [["whitelabel_app_name"]], \
        "get_setting must share the strict read's query, not keep its own copy"
