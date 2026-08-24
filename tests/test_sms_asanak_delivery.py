"""What the Asanak gateway actually accepts, and what "success" is allowed to claim.

Both behaviours here were found the expensive way — by sending real messages
and watching them not arrive:

  * The app stores a destination as E.164 (`+989121234567`) because that is the
    visitor's identity in `otp_challenges`. Posting that form to Asanak is
    refused outright: HTTP 406, meta.status 1010, `Invalids: ["0Invalid"]`.
    Every OTP for a visitor who typed their number with +98 would have been
    silently lost. The `+` has to go at the gateway edge, and nowhere else.

  * A 200 from Asanak means QUEUED, not delivered. Seven real messages were
    accepted with ids and none was ever charged or delivered. The response
    must therefore hand back the message id (the only handle for a later
    `msgstatus` enquiry) and must not tell the operator the message was sent.
"""
import pytest


# ── The destination format the gateway will accept ───────────────────────

@pytest.mark.parametrize("stored,expected", [
    ("+989121234567", "09121234567"),   # what the app stores — the bug
    ("989121234567", "09121234567"),    # country code, no plus
    ("09121234567", "09121234567"),     # already local — untouched
    ("+989351234567", "09351234567"),   # a different operator prefix
])
def test_plus_prefix_is_stripped_for_asanak(stored, expected):
    from app.services.sms import asanak_destination
    assert asanak_destination(stored) == expected


def test_result_always_starts_09_never_plus():
    """Asanak's documented rule: a mobile destination begins 091/093 (or
    9891/9893). A leading + is what earns error 1010."""
    from app.services.sms import asanak_destination
    for raw in ("+989121234567", "989121234567", "09121234567"):
        out = asanak_destination(raw)
        assert not out.startswith("+")
        assert out.startswith("09")


def test_short_or_odd_input_is_passed_through_untouched():
    """Only the +98/98 country-code case is rewritten. A short code or a
    landline is the gateway's business to reject, not this function's to
    mangle into something that looks valid."""
    from app.services.sms import asanak_destination
    assert asanak_destination("30001234") == "30001234"
    assert asanak_destination("02164063188") == "02164063188"


def test_the_canonical_store_form_is_left_alone():
    """normalize_destination keeps the +, because that is the visitor's
    identity. Weakening it there would change what OTP rows are keyed on."""
    from app.services.otp import normalize_destination
    assert normalize_destination("+989121234567") == "+989121234567"


def test_send_asanak_posts_the_stripped_number(monkeypatch):
    from app.services import sms as sms_service

    captured = {}

    def fake_post(url, payload):
        captured.update(payload)
        return 200, '{"meta":{"status":200,"message":"success"},"data":[987654321]}'

    monkeypatch.setattr(sms_service, "_http_post", fake_post)
    monkeypatch.setattr(sms_service, "setting", lambda key: {
        "sms_asanak_username": "u",
        "sms_asanak_password": "p",
        "sms_asanak_source": "9821000",
        "sms_asanak_url": "https://example.invalid/sendsms",
        "sms_asanak_trim": "true",
        "sms_asanak_send_to_blacklist": "1",
    }.get(key, ""))

    sms_service.send_asanak("+989121234567", "code 12345")
    assert captured["destination"] == "09121234567"


# ── "Accepted" is not "delivered" ────────────────────────────────────────

def test_send_returns_the_message_id(monkeypatch):
    """Without the id there is no way to ask msgstatus what became of a
    message — which is exactly the hole that made a silent non-delivery
    impossible to investigate."""
    from app.services import sms as sms_service

    monkeypatch.setattr(sms_service, "_http_post", lambda url, payload: (
        200, '{"meta":{"status":200,"message":"success"},"data":[5271501387]}'))
    monkeypatch.setattr(sms_service, "setting", lambda key: {
        "sms_asanak_username": "u",
        "sms_asanak_password": "p",
        "sms_asanak_source": "9821000",
        "sms_asanak_url": "https://example.invalid/sendsms",
        "sms_asanak_trim": "true",
        "sms_asanak_send_to_blacklist": "1",
    }.get(key, ""))

    assert sms_service.send_asanak("09121234567", "hello") == 5271501387


def test_provider_seam_passes_the_id_through(monkeypatch):
    from app.services import sms as sms_service
    monkeypatch.setitem(sms_service.PROVIDERS["asanak"], "send",
                        lambda destination, message, code=None: 424242)
    assert sms_service.send("asanak", "09121234567", "hi") == 424242


# ── Template routing: the only path that reaches a service line ──────────

def _stub_settings(monkeypatch, sms_service, **overrides):
    base = {
        "sms_asanak_username": "u",
        "sms_asanak_password": "p",
        "sms_asanak_source": "9821000",
        "sms_asanak_url": "https://example.invalid/sendsms",
        "sms_asanak_template_url": "https://example.invalid/template",
        "sms_asanak_trim": "true",
        "sms_asanak_send_to_blacklist": "1",
    }
    base.update(overrides)
    monkeypatch.setattr(sms_service, "setting", lambda key: base.get(key, ""))


def test_a_configured_template_takes_over_from_free_text(monkeypatch):
    from app.services import sms as sms_service
    _stub_settings(monkeypatch, sms_service, sms_asanak_template_id="1654")

    seen = {}

    def fake_json(url, document):
        seen["url"] = url
        seen["doc"] = document
        return 200, '{"meta":{"status":200,"message":"success"},"data":[5271599580]}'

    monkeypatch.setattr(sms_service, "_http_post_json", fake_json)
    monkeypatch.setattr(sms_service, "_http_post", lambda *a: pytest.fail(
        "free text must not be used once a template is configured"))

    assert sms_service.send_asanak("+989122723024", "ignored body", code="45231") == 5271599580
    assert seen["url"].endswith("/template")
    assert seen["doc"]["template_id"] == 1654
    assert seen["doc"]["destination"] == "09122723024"
    assert seen["doc"]["parameters"] == {"code": "45231"}
    # The body text is NOT sent — an approved template supplies its own wording.
    assert "ignored body" not in str(seen["doc"])


def test_without_a_template_id_free_text_is_still_used(monkeypatch):
    """An install on a promotional line, or one with no approved template yet,
    must keep working exactly as before."""
    from app.services import sms as sms_service
    _stub_settings(monkeypatch, sms_service, sms_asanak_template_id="")

    monkeypatch.setattr(sms_service, "_http_post", lambda url, payload: (
        200, '{"meta":{"status":200,"message":"success"},"data":[111]}'))
    monkeypatch.setattr(sms_service, "_http_post_json", lambda *a: pytest.fail(
        "template endpoint used with no template configured"))

    assert sms_service.send_asanak("09122723024", "hello", code="45231") == 111


def test_a_message_with_no_code_never_uses_the_template(monkeypatch):
    """The template's only parameter IS the code. A generic message without one
    would render an empty slot, so it has to go as free text."""
    from app.services import sms as sms_service
    _stub_settings(monkeypatch, sms_service, sms_asanak_template_id="1654")

    monkeypatch.setattr(sms_service, "_http_post", lambda url, payload: (
        200, '{"meta":{"status":200,"message":"success"},"data":[222]}'))
    monkeypatch.setattr(sms_service, "_http_post_json", lambda *a: pytest.fail(
        "template used without a code"))

    assert sms_service.send_asanak("09122723024", "plain notice") == 222


def test_otp_hands_the_bare_code_to_the_gateway(monkeypatch):
    """The seam that makes template delivery possible: otp passes the code on
    its own, not only baked into the body text."""
    from app.services import sms as sms_service
    captured = {}
    monkeypatch.setitem(sms_service.PROVIDERS["asanak"], "send",
                        lambda destination, message, code=None: captured.update(
                            destination=destination, message=message, code=code))
    sms_service.send("asanak", "09122723024", "کد تأیید شما: 45231", code="45231")
    assert captured["code"] == "45231"


# ── The invite and the rejection notice: free text, operator's words ─────
# Settled with Asanak support 2026-08-24: `sendsms` free text is a normal
# path and carries links; the verification code is the only message that
# needs an approved template. Both link messages are composed from the
# admin-editable `sms_invite_text` / `sms_reject_text` with {{magic_link}}
# marking where the one-time link goes.

def _asanak(monkeypatch, sms_service, **overrides):
    _stub_settings(monkeypatch, sms_service, **overrides)
    monkeypatch.setattr(sms_service, "_active_provider", lambda: "asanak")


def test_invite_goes_as_free_text_with_the_link_in_the_body(monkeypatch):
    from app.services import sms as sms_service
    _asanak(monkeypatch, sms_service)
    captured = {}

    def fake_post(url, payload):
        captured.update(payload)
        return 200, '{"meta":{"status":200,"message":"success"},"data":[901]}'

    monkeypatch.setattr(sms_service, "_http_post", fake_post)
    monkeypatch.setattr(sms_service, "_http_post_json", lambda *a: pytest.fail(
        "free text needs no template"))

    link = "https://example.invalid/e/abc"
    assert sms_service.send_invite_link("+989122723024", link, "lead-1") == 901
    assert captured["destination"] == "09122723024"
    # A real message, not a parameter slot: the token became the actual link.
    assert link in captured["message"]
    assert "{{magic_link}}" not in captured["message"]
    # The default says what the link does and how long it lasts.
    assert "۲۴ ساعت" in captured["message"]


def test_a_custom_invite_text_is_used_and_the_token_replaces(monkeypatch):
    from app.services import sms as sms_service
    _asanak(monkeypatch, sms_service, sms_invite_text="بیا اینجا:\n{{magic_link}}")
    captured = {}

    def fake_post(url, payload):
        captured.update(payload)
        return 200, '{"meta":{"status":200,"message":"success"},"data":[905]}'

    monkeypatch.setattr(sms_service, "_http_post", fake_post)

    link = "https://example.invalid/e/own-words"
    sms_service.send_invite_link("09122723024", link)
    assert captured["message"] == "بیا اینجا:\n" + link


def test_a_custom_text_without_the_token_still_carries_the_link(monkeypatch):
    """The panel refuses to save such a body, but a setting can also be
    written by hand. The link is appended rather than lost — a contact who
    cannot reach their page is the one failure this must never produce."""
    from app.services import sms as sms_service
    _asanak(monkeypatch, sms_service, sms_invite_text="سلام، منتظر شما هستیم")
    captured = {}

    def fake_post(url, payload):
        captured.update(payload)
        return 200, '{"meta":{"status":200,"message":"success"},"data":[906]}'

    monkeypatch.setattr(sms_service, "_http_post", fake_post)

    link = "https://example.invalid/e/rescued"
    sms_service.send_invite_link("09122723024", link)
    assert captured["message"].endswith(link)
    assert "{{magic_link}}" not in captured["message"]


def test_rejection_goes_as_free_text_with_the_link_in_the_body(monkeypatch):
    """The reviewer's reason lives on the contact's own page, which is where
    the link goes and where there is room to read it."""
    from app.services import sms as sms_service
    _asanak(monkeypatch, sms_service)
    captured = {}

    def fake_post(url, payload):
        captured.update(payload)
        return 200, '{"meta":{"status":200,"message":"success"},"data":[903]}'

    monkeypatch.setattr(sms_service, "_http_post", fake_post)
    monkeypatch.setattr(sms_service, "_http_post_json", lambda *a: pytest.fail(
        "free text needs no template"))

    link = "https://example.invalid/e/xyz"
    assert sms_service.send_reject_notice("09122723024", link, "lead-2") == 903
    assert link in captured["message"]
    assert "تأیید نشد" in captured["message"]


def test_a_link_refusal_from_the_gateway_is_recognised(monkeypatch):
    """1014 — the line may not send links — is the one failure the caller
    answers with a QR instead of an error (SPEC REQ-057). It can still come
    back from a line without link permission, so it must stay recognisable."""
    from app.services import sms as sms_service
    _asanak(monkeypatch, sms_service)
    monkeypatch.setattr(sms_service, "_http_post", lambda url, payload: (
        200, '{"meta":{"status":1014,"message":"link not permitted"}}'))

    with pytest.raises(sms_service.SmsError) as caught:
        sms_service.send_invite_link("09122723024", "https://example.invalid/e/x")
    assert sms_service.is_link_refusal(caught.value) is True
    # The operator-facing sentence names the fix: get the permission from
    # Asanak, and until then hand the contact a QR.
    assert "۱۰۱۴" in caught.value.detail
    assert "QR" in caught.value.detail


def test_send_reject_notice_takes_no_reason(monkeypatch):
    """The reviewer's sentence cannot travel in the SMS — the contact reads
    it on their own page. This pins the contract the review path relies on."""
    import inspect
    from app.services import sms as sms_service
    names = list(inspect.signature(sms_service.send_reject_notice).parameters)
    assert names == ["destination", "link", "reference"]


def test_a_link_message_is_counted_against_the_daily_budget(monkeypatch):
    """Every path comes off the same Asanak credit, so every path is counted."""
    from app.services import sms as sms_service
    _asanak(monkeypatch, sms_service)
    monkeypatch.setattr(sms_service, "_http_post", lambda url, payload: (
        200, '{"meta":{"status":200,"message":"success"},"data":[904]}'))
    spent = []
    monkeypatch.setattr(sms_service, "_spend_budget", lambda kind: spent.append(kind))

    sms_service.send_invite_link("09122723024", "https://example.invalid/e/abc")
    assert spent == ["invite"]
