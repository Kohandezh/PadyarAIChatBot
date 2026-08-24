"""A 200 from Asanak means QUEUED. This is what stops it reading as DELIVERED.

Measured on the live account 2026-08-17: eleven real free-text messages were
accepted with message ids, every one sat at msgstatus Status 20 with
`DeliverTime: 0000-00-00`, and the credit never moved. Nothing in the app could
tell those apart from delivered messages, so nobody noticed for weeks.

`last_freetext_delivery()` closes that hole with the smallest thing that works:
every free-text send leaves its message id in a settings row, the admin SMS
settings page asks msgstatus about it ONCE after a grace period, and the
verdict is written back. No poller, no background task, and no visitor or
booth ever waits on a gateway roundtrip for a diagnostic.
"""
import pytest


@pytest.fixture
def db(tmp_path, monkeypatch):
    """A throwaway SQLite database with the settings table in it."""
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "verdict.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    from app.db.connection import init_db
    init_db()


def _stub_gateway(monkeypatch, sms_service, status_body):
    """Answer sendsms with a queued id and msgstatus with `status_body`."""
    calls = {"send": 0, "status": 0}

    def fake_post(url, payload):
        if "msgid" in payload:
            calls["status"] += 1
            return 200, status_body
        calls["send"] += 1
        return 200, '{"meta":{"status":200,"message":"success"},"data":[5271599999]}'

    monkeypatch.setattr(sms_service, "_http_post", fake_post)
    monkeypatch.setattr(sms_service, "setting", lambda key: {
        "sms_asanak_username": "u",
        "sms_asanak_password": "p",
        "sms_asanak_source": "9821000",
        "sms_asanak_url": "https://example.invalid/sendsms",
        "sms_asanak_status_url": "https://example.invalid/msgstatus",
        "sms_asanak_trim": "true",
        "sms_asanak_send_to_blacklist": "1",
    }.get(key, ""))
    return calls


def _age_the_record(sms_service, seconds):
    """Backdate the stored send so the grace period has passed."""
    from datetime import timedelta
    from app.db.queries import get_setting, set_setting
    msgid, _, rest = (get_setting(sms_service._LAST_FREETEXT_KEY, "")).partition("|")
    sent_at, _, tail = rest.partition("|")
    from datetime import datetime
    older = (datetime.fromisoformat(sent_at) - timedelta(seconds=seconds)).isoformat()
    set_setting(sms_service._LAST_FREETEXT_KEY, "%s|%s|%s" % (msgid, older, tail))


def test_nothing_sent_means_nothing_to_report(db):
    from app.services import sms as sms_service
    assert sms_service.last_freetext_delivery()["state"] == "none"


def test_a_fresh_send_is_not_judged_yet(db, monkeypatch):
    """A message queued seconds ago is SUPPOSED to be pending. Calling it lost
    would train the operator to ignore the line."""
    from app.services import sms as sms_service
    calls = _stub_gateway(monkeypatch, sms_service, "")
    sms_service.send_asanak("09122723024", "hello")

    verdict = sms_service.last_freetext_delivery()
    assert verdict["state"] == "waiting"
    assert calls["status"] == 0     # the gateway was not even asked


def test_status_20_is_reported_as_never_delivered(db, monkeypatch):
    """The exact failure that lost eleven messages in August."""
    from app.services import sms as sms_service
    _stub_gateway(monkeypatch, sms_service,
                  '{"meta":{"status":200},"data":[{"Status":20,"DeliverTime":"0000-00-00"}]}')
    sms_service.send_asanak("09122723024", "hello")
    _age_the_record(sms_service, sms_service._DELIVERY_GRACE_SECONDS + 60)

    verdict = sms_service.last_freetext_delivery()
    assert verdict["state"] == "held"
    assert str(verdict["msgid"]) == "5271599999"
    assert "نرساند" in verdict["message"]


def test_status_6_is_reported_as_delivered(db, monkeypatch):
    from app.services import sms as sms_service
    _stub_gateway(monkeypatch, sms_service,
                  '{"meta":{"status":200},"data":[{"Status":6}]}')
    sms_service.send_asanak("09122723024", "hello")
    _age_the_record(sms_service, sms_service._DELIVERY_GRACE_SECONDS + 60)
    assert sms_service.last_freetext_delivery()["state"] == "delivered"


def test_an_unknown_status_is_reported_as_unknown_not_as_success(db, monkeypatch):
    """Only 6 and 20 were measured on this account. Everything else is handed
    to the operator as a raw number rather than guessed at."""
    from app.services import sms as sms_service
    _stub_gateway(monkeypatch, sms_service,
                  '{"meta":{"status":200},"data":[{"Status":13}]}')
    sms_service.send_asanak("09122723024", "hello")
    _age_the_record(sms_service, sms_service._DELIVERY_GRACE_SECONDS + 60)

    verdict = sms_service.last_freetext_delivery()
    assert verdict["state"] == "code"
    assert "13" in verdict["message"]


def test_the_gateway_is_asked_only_once(db, monkeypatch):
    """The verdict is written back, so opening the page twice is one call, not
    a poller nobody asked for."""
    from app.services import sms as sms_service
    calls = _stub_gateway(monkeypatch, sms_service,
                          '{"meta":{"status":200},"data":[{"Status":6}]}')
    sms_service.send_asanak("09122723024", "hello")
    _age_the_record(sms_service, sms_service._DELIVERY_GRACE_SECONDS + 60)

    sms_service.last_freetext_delivery()
    sms_service.last_freetext_delivery()
    assert calls["status"] == 1


def test_a_gateway_that_cannot_be_asked_leaves_the_question_open(db, monkeypatch):
    """An unanswered question is not an answer, so the next page load asks
    again rather than recording a verdict nobody got."""
    from app.services import sms as sms_service
    _stub_gateway(monkeypatch, sms_service, "not json at all")
    sms_service.send_asanak("09122723024", "hello")
    _age_the_record(sms_service, sms_service._DELIVERY_GRACE_SECONDS + 60)

    assert sms_service.last_freetext_delivery()["state"] == "unknown"
    from app.db.queries import get_setting
    assert get_setting(sms_service._LAST_FREETEXT_KEY, "").endswith("|")


def test_a_template_send_is_not_tracked_here(db, monkeypatch):
    """Templates were MEASURED to deliver on this line. This check is about
    the free-text hold, and claiming more than was measured would be a guess."""
    from app.services import sms as sms_service
    monkeypatch.setattr(sms_service, "_http_post_json", lambda url, doc: (
        200, '{"meta":{"status":200,"message":"success"},"data":[777]}'))
    monkeypatch.setattr(sms_service, "setting", lambda key: {
        "sms_asanak_username": "u",
        "sms_asanak_password": "p",
        "sms_asanak_source": "9821000",
        "sms_asanak_template_id": "1654",
        "sms_asanak_template_url": "https://example.invalid/template",
        "sms_asanak_send_to_blacklist": "1",
    }.get(key, ""))

    sms_service.send_asanak("09122723024", "ignored", code="123456")
    assert sms_service.last_freetext_delivery()["state"] == "none"


def test_the_send_still_succeeds_when_the_record_cannot_be_written(db, monkeypatch):
    """Diagnostics never break a send that the gateway accepted."""
    from app.services import sms as sms_service
    _stub_gateway(monkeypatch, sms_service, "")

    def broken(*a, **k):
        raise RuntimeError("settings table is unavailable")

    monkeypatch.setattr("app.db.queries.set_setting", broken)
    assert sms_service.send_asanak("09122723024", "hello") == 5271599999
