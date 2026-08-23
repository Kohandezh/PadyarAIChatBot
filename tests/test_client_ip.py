"""Which address the app believes a request came from.

This decides every rate-limit bucket and the ip field on every audit row, so
getting it wrong fails in both directions at once. Reading X-Forwarded-For left
to right reads the entry the CLIENT wrote: an abuser varies it per request and
never hits a limit, while honest traffic behind one NAT'd exhibition hall all
lands in a single bucket. The tests below pin the resolution order.
"""
import pytest
from fastapi import Request

import app.config as config
from app.auth.security import client_ip, check_rate_limit, _chat_rate_limits


def make_request(headers=None, host="10.0.0.9") -> Request:
    """A bare ASGI scope is enough. client_ip reads only client and headers."""
    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    return Request({"type": "http", "method": "GET", "path": "/",
                    "headers": raw, "client": (host, 51000)})


# ── Nothing configured: the headers are not evidence ─────────────────────

@pytest.fixture(autouse=True)
def _untrusted(monkeypatch):
    """Default posture for every test unless it opts in."""
    monkeypatch.setattr(config, "TRUST_CLOUDFLARE", False)
    monkeypatch.setattr(config, "TRUSTED_PROXY_HOPS", 0)


def test_spoofed_forwarded_for_is_ignored():
    a = make_request({"X-Forwarded-For": "1.1.1.1"})
    b = make_request({"X-Forwarded-For": "2.2.2.2"})
    assert client_ip(a) == client_ip(b) == "10.0.0.9"


def test_cloudflare_header_is_ignored_when_cloudflare_is_not_trusted():
    r = make_request({"CF-Connecting-IP": "1.1.1.1"})
    assert client_ip(r) == "10.0.0.9"


def test_plain_request_resolves_to_the_socket_address():
    assert client_ip(make_request()) == "10.0.0.9"


# ── Cloudflare ───────────────────────────────────────────────────────────

def test_cloudflare_header_is_used_when_trusted(monkeypatch):
    monkeypatch.setattr(config, "TRUST_CLOUDFLARE", True)
    r = make_request({"CF-Connecting-IP": "203.0.113.7",
                      "X-Forwarded-For": "9.9.9.9"})
    assert client_ip(r) == "203.0.113.7"


def test_cloudflare_trust_falls_back_when_the_header_is_absent(monkeypatch):
    monkeypatch.setattr(config, "TRUST_CLOUDFLARE", True)
    assert client_ip(make_request()) == "10.0.0.9"


def test_empty_cloudflare_header_falls_back(monkeypatch):
    monkeypatch.setattr(config, "TRUST_CLOUDFLARE", True)
    assert client_ip(make_request({"CF-Connecting-IP": "   "})) == "10.0.0.9"


# ── Hop counting from the right ──────────────────────────────────────────

def test_one_hop_takes_the_rightmost_entry(monkeypatch):
    monkeypatch.setattr(config, "TRUSTED_PROXY_HOPS", 1)
    r = make_request({"X-Forwarded-For": "203.0.113.7, 172.16.0.1"})
    assert client_ip(r) == "172.16.0.1"


def test_two_hops_take_the_second_from_the_right(monkeypatch):
    monkeypatch.setattr(config, "TRUSTED_PROXY_HOPS", 2)
    r = make_request({"X-Forwarded-For": "203.0.113.7, 172.16.0.1, 172.16.0.2"})
    assert client_ip(r) == "172.16.0.1"


def test_prepended_hops_cannot_shift_the_result(monkeypatch):
    """The whole point. A client prepending junk only grows the left side."""
    monkeypatch.setattr(config, "TRUSTED_PROXY_HOPS", 1)
    honest = make_request({"X-Forwarded-For": "203.0.113.7, 172.16.0.1"})
    spoofed = make_request({"X-Forwarded-For": "evil, 8.8.8.8, 203.0.113.7, 172.16.0.1"})
    assert client_ip(honest) == client_ip(spoofed) == "172.16.0.1"


def test_too_few_hops_falls_back_to_the_socket(monkeypatch):
    """Fewer entries than expected means the chain did not pass our proxies."""
    monkeypatch.setattr(config, "TRUSTED_PROXY_HOPS", 2)
    assert client_ip(make_request({"X-Forwarded-For": "1.1.1.1"})) == "10.0.0.9"


@pytest.mark.parametrize("value", ["", "   ", ",", " , , "])
def test_malformed_forwarded_for_falls_back(monkeypatch, value):
    monkeypatch.setattr(config, "TRUSTED_PROXY_HOPS", 1)
    assert client_ip(make_request({"X-Forwarded-For": value})) == "10.0.0.9"


def test_no_socket_and_no_trusted_header_yields_empty_string():
    r = Request({"type": "http", "method": "GET", "path": "/",
                 "headers": [], "client": None})
    assert client_ip(r) == ""


# ── Rate limiting ────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _clear_buckets():
    _chat_rate_limits.clear()
    yield
    _chat_rate_limits.clear()


def test_plain_request_still_hits_the_limit():
    """Unchanged behaviour for the ordinary case: no proxy, no headers."""
    from app.config import CHAT_RATE_LIMIT
    for _ in range(CHAT_RATE_LIMIT):
        check_rate_limit(make_request())
    with pytest.raises(Exception) as exc:
        check_rate_limit(make_request())
    assert exc.value.status_code == 429


def test_varying_forwarded_for_no_longer_buys_a_fresh_bucket():
    """The bug: one caller used to get an unlimited quota per header value."""
    from app.config import CHAT_RATE_LIMIT
    for i in range(CHAT_RATE_LIMIT):
        check_rate_limit(make_request({"X-Forwarded-For": f"1.2.3.{i}"}))
    with pytest.raises(Exception) as exc:
        check_rate_limit(make_request({"X-Forwarded-For": "1.2.3.250"}))
    assert exc.value.status_code == 429


def test_two_socket_addresses_get_separate_buckets():
    from app.config import CHAT_RATE_LIMIT
    for _ in range(CHAT_RATE_LIMIT):
        check_rate_limit(make_request(host="10.0.0.1"))
    check_rate_limit(make_request(host="10.0.0.2"))  # must not raise


def test_explicit_key_replaces_the_ip_bucket():
    """A route can limit per identity instead of per address."""
    from app.config import CHAT_RATE_LIMIT
    for _ in range(CHAT_RATE_LIMIT):
        check_rate_limit(make_request(host="10.0.0.1"), key="user:42")
    # Same key from a different address is still exhausted.
    with pytest.raises(Exception) as exc:
        check_rate_limit(make_request(host="10.0.0.2"), key="user:42")
    assert exc.value.status_code == 429
    # A different key is untouched.
    check_rate_limit(make_request(host="10.0.0.1"), key="user:43")
