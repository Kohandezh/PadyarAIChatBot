"""Provider credentials must never survive into a log row or an admin page.

The AI Provider phase adds vendors whose key formats the original scrubber was
never written against. Providers echo request material back inside error
bodies — an invalid-key error frequently quotes the key it rejected — and that
text is exactly what gets stored for diagnosis and rendered to an operator.

Each case below is a shape that leaked before this phase. They are kept as
tests so a future regex tidy-up cannot quietly re-open the hole.
"""
import pytest

from app.services import applog
from app.services.ai import errors as E

REDACTED = "[redacted]"


@pytest.mark.parametrize("secret, text", [
    # Anthropic. The original pattern was `sk-[A-Za-z0-9]{12,}`, which stops at
    # the first hyphen — so it matched nothing here and passed the live key
    # through untouched.
    ("sk-ant-api03-SENTINELAbCdEfGhIjKlMnOpQrSt",
     "authentication_error: invalid x-api-key sk-ant-api03-SENTINELAbCdEfGhIjKlMnOpQrSt"),
    # Google. Not `sk-` prefixed at all, so nothing matched it.
    ("AIzaSySENTINELAb3dEfGhIjKlMnOpQrStUvWxYz1234",
     "x-goog-api-key: AIzaSySENTINELAb3dEfGhIjKlMnOpQrStUvWxYz1234 is not valid"),
    # A credential in a query string, as it appears when an error quotes the URL.
    ("AIzaSySENTINELAb3dEfGhIjKlMnOpQrStUvWxYz1234",
     "GET /v1beta/models?key=AIzaSySENTINELAb3dEfGhIjKlMnOpQrStUvWxYz1234 -> 400"),
    # OpenAI-style project key inside a bearer header.
    ("sk-proj-SENTINELAbCdEfGhIjKlMnOpQrStUv",
     "Authorization: Bearer sk-proj-SENTINELAbCdEfGhIjKlMnOpQrStUv"),
    # A JSON body echoing the field back.
    ("abcdef1234567890abcdef",
     '{"error":{"message":"bad credentials","api_key":"abcdef1234567890abcdef"}}'),
    # Our own encrypted-at-rest marker must not be logged either.
    ("enc:AAAABBBBCCCCDDDDEEEE", "stored value enc:AAAABBBBCCCCDDDDEEEE"),
])
def test_provider_credential_shapes_are_scrubbed(secret, text):
    out = applog.scrub_text(text)
    assert secret not in out
    assert REDACTED in out


def test_scrubbing_leaves_ordinary_persian_diagnostics_readable():
    """Redaction must not be so greedy that error messages stop being useful."""
    text = "سرویس‌دهنده پاسخ نداد — مهلت ۴۵ ثانیه تمام شد"
    assert applog.scrub_text(text) == text


def test_a_normalized_error_redacts_provider_text_before_it_is_exposed():
    """`provider_detail` holds the vendor's own words. Everything that reads it
    for storage or display must go through the redacting accessor."""
    err = E.AIError(
        code=E.AUTHENTICATION_FAILED,
        provider_type="anthropic",
        provider_detail="invalid x-api-key sk-ant-api03-SENTINELAbCdEfGhIjKlMnOpQrSt",
    )
    assert "sk-ant-api03" not in err.redacted_detail()
    assert REDACTED in err.redacted_detail()


def test_error_log_fields_never_carry_raw_provider_text():
    err = E.AIError(
        code=E.AUTHENTICATION_FAILED,
        provider_type="openai",
        provider_detail="Bearer sk-proj-SENTINELAbCdEfGhIjKlMnOpQrStUv rejected",
    )
    blob = repr(err.as_log_fields())
    assert "sk-proj-" not in blob
    assert REDACTED in blob


def test_error_log_fields_still_carry_what_an_operator_needs():
    """Redaction that removes the diagnosis along with the secret is a
    different bug, not a fix."""
    err = E.AIError(code=E.RATE_LIMITED, provider_type="deepseek",
                    model="deepseek-v4-flash", status_code=429,
                    provider_instance_id="inst-7", attempts=2)
    fields = err.as_log_fields()
    assert fields["error_code"] == "rate_limited"
    assert fields["provider"] == "deepseek"
    assert fields["model"] == "deepseek-v4-flash"
    assert fields["metadata"]["status_code"] == 429
    assert fields["metadata"]["attempts"] == 2
    assert fields["metadata"]["retryable"] is True
    assert fields["metadata"]["failover_eligible"] is True
