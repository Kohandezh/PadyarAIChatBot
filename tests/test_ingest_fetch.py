"""Safe fetch of one admin-given web page (knowledge ingestion, slice S2).

An admin pastes a URL and this server connects to it from inside the
customer's network. Unless every hop is checked, that is a server-side request
forgery door: loopback, the private network, the cloud metadata service. The
contract is docs/features/knowledge-ingestion/SPEC.md, REQ-017..REQ-023,
SEC-012..SEC-017 and SC-004. Each hostile fixture U1..U9 of that spec is here,
next to the allowed case it must not break.

No test touches the network. `socket.getaddrinfo` is replaced by a table that
records every lookup, and `ingest_fetch._client` returns an httpx client on a
`MockTransport` that records every request. "No connection to a refused
address" is asserted as "the transport never saw a request for it".

The 20 second budget is tested with a fake clock put in place of the `time`
module inside `ingest_fetch`, so no test sleeps.

A MockTransport has no socket, so it cannot show a server that sends its
headers one byte at a time: httpx's read timeout is per recv, and each byte
restarts it. That review finding is covered by four tests against a REAL TLS
server on 127.0.0.1 in a thread, with the budget cut to 1 second. They are
the only tests here that open a socket, and only to loopback: the real
resolver is put back for them, `endpoint_policy.pin` runs with the internal
trust class for 127.0.0.1 only, port 443 is mapped to the server's port at
the httpcore socket layer, and `certifi.where` points at a CA made for the
test so certificate checking stays on.

Three choices below are team decisions on points the spec leaves open
(recorded in the S2 handoff): numeric IPv4 spellings such as 2130706433 and
every IPv4-mapped IPv6 address are `blocked_address`; a name that does not
resolve is `blocked_address` because REQ-019 maps every `EndpointRejected`
there; a compressed answer is `fetch_failed`, never inflated.
"""
import datetime
import gzip
import ipaddress
import socket
import ssl
import threading
import time
from urllib.parse import urlsplit

import certifi
import httpcore
import httpx
import pytest

from app.services import ingest_fetch
from app.services.ai import endpoint_policy

REAL_CLIENT = ingest_fetch._client
REAL_GETADDRINFO = socket.getaddrinfo

PUBLIC_V4 = "93.184.216.34"
PUBLIC_V4_B = "93.184.216.35"
PUBLIC_V6 = "2606:4700:4700::1111"
PAGE = "<html><body><h1>درباره ما</h1><p>نمایشگاه از ساعت ۹ باز است.</p></body></html>".encode()
MIB = 1024 * 1024

SENTENCES = {
    "too_large": "این صفحه بیش از اندازه بزرگ است.",
    "timeout": "این صفحه دیر جواب داد. دوباره امتحان کنید.",
    "bad_url": "این نشانی درست نیست.",
    "http_only": "فقط نشانی‌هایی که با https شروع می‌شوند پذیرفته می‌شوند.",
    "bad_port": "این نشانی پذیرفته نمی‌شود.",
    "blocked_address": "این نشانی به یک شبکهٔ داخلی اشاره می‌کند و پذیرفته نمی‌شود.",
    "too_many_redirects": "این صفحه بیش از حد به جای دیگری می‌فرستد.",
    "bad_content_type": "این نشانی یک صفحهٔ متنی یا PDF نیست.",
    "fetch_failed": "این صفحه باز نشد.",
}


class FakeDNS:
    def __init__(self):
        self.answers = {}
        self.calls = []

    def answer(self, host, *ips):
        self.answers[host] = [list(ips)]

    def answer_in_turn(self, host, *rounds):
        self.answers[host] = [list(r) for r in rounds]

    def __call__(self, host, port, family=0, type=0, proto=0, flags=0):
        self.calls.append(host)
        if host not in self.answers:
            raise socket.gaierror(socket.EAI_NONAME, "name not in the test table")
        rounds = self.answers[host]
        ips = rounds.pop(0) if len(rounds) > 1 else rounds[0]
        return [
            (socket.AF_INET6, socket.SOCK_STREAM, 6, "", (ip, 0, 0, 0)) if ":" in ip
            else (socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0))
            for ip in ips
        ]


class FakeWeb:
    def __init__(self):
        self.routes = {}
        self.requests = []

    def route(self, url, answer):
        self.routes[url] = answer

    def handler(self, request):
        self.requests.append(request)
        answer = self.routes.get(str(request.url))
        if answer is None:
            raise AssertionError(f"unexpected request to {request.url}")
        if isinstance(answer, Exception):
            raise answer
        if callable(answer):
            return answer(request)
        return answer

    def client(self, **kwargs):
        return httpx.Client(transport=httpx.MockTransport(self.handler), **kwargs)

    @property
    def hosts(self):
        return [r.url.host for r in self.requests]


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def monotonic(self):
        return self.now


@pytest.fixture(autouse=True)
def dns(monkeypatch):
    fake = FakeDNS()
    monkeypatch.setattr(socket, "getaddrinfo", fake)
    return fake


@pytest.fixture(autouse=True)
def web(monkeypatch):
    fake = FakeWeb()
    monkeypatch.setattr(ingest_fetch, "_client", fake.client)
    return fake


@pytest.fixture
def clock(monkeypatch):
    fake = FakeClock()
    monkeypatch.setattr(ingest_fetch, "time", fake)
    return fake


def page(body=PAGE, content_type="text/html; charset=utf-8", **headers):
    return httpx.Response(200, headers={"Content-Type": content_type, **headers}, content=body)


def redirect(location, status=302, **headers):
    return httpx.Response(status, headers={"Location": location, **headers})


def counted_body(chunks, size=64 * 1024):
    served = {"chunks": 0}

    def body():
        for _ in range(chunks):
            served["chunks"] += 1
            yield b"x" * size

    return body(), served


def rejected(url):
    with pytest.raises(ingest_fetch.FetchRejected) as excinfo:
        ingest_fetch.fetch_url(url)
    exc = excinfo.value
    assert exc.message_fa == SENTENCES[exc.code], "section 8 sentences are part of the contract"
    return exc


# ── The allowed path ────────────────────────────────────────────────────

def test_a_public_https_page_is_fetched_from_the_address_that_passed_the_check(dns, web):
    dns.answer("example.com", PUBLIC_V4)
    web.route(f"https://{PUBLIC_V4}/about", page())

    got = ingest_fetch.fetch_url("https://example.com/about")

    assert got == ingest_fetch.Fetched(final_url="https://example.com/about",
                                       content_type="text/html", charset="utf-8", data=PAGE)
    [request] = web.requests
    assert request.url.host == PUBLIC_V4
    assert request.headers["host"] == "example.com"
    assert request.extensions["sni_hostname"] == "example.com", "TLS must still verify the real name"


def test_port_443_written_out_is_accepted(dns, web):
    dns.answer("example.com", PUBLIC_V4)
    web.route(f"https://{PUBLIC_V4}/", page())

    got = ingest_fetch.fetch_url("https://example.com:443/")

    assert got.data == PAGE
    assert web.requests[0].headers["host"] == "example.com:443"


@pytest.mark.parametrize("url, connect_url", [
    (f"https://{PUBLIC_V4}/", f"https://{PUBLIC_V4}/"),
    (f"https://[{PUBLIC_V6}]/", f"https://[{PUBLIC_V6}]/"),
])
def test_a_public_ip_literal_is_accepted_without_a_lookup(dns, web, url, connect_url):
    web.route(connect_url, page())

    assert ingest_fetch.fetch_url(url).data == PAGE
    assert dns.calls == []


def test_a_name_with_a_public_ipv6_answer_is_accepted(dns, web):
    dns.answer("v6.example", PUBLIC_V6)
    web.route(f"https://[{PUBLIC_V6}]/", page())

    assert ingest_fetch.fetch_url("https://v6.example/").data == PAGE
    assert web.requests[0].extensions["sni_hostname"] == "v6.example"


def test_the_path_and_query_reach_the_server_unchanged(dns, web):
    """`endpoint_policy.pin` drops a trailing slash (right for an AI base URL).
    A page path must keep it, or /docs/ is asked for as /docs."""
    dns.answer("example.com", PUBLIC_V4)
    web.route(f"https://{PUBLIC_V4}/docs/?lang=fa", page())

    got = ingest_fetch.fetch_url("https://example.com/docs/?lang=fa")

    assert web.requests[0].url.raw_path == b"/docs/?lang=fa"
    assert got.final_url == "https://example.com/docs/?lang=fa"


def test_the_fragment_is_not_sent_and_an_empty_path_becomes_root(dns, web):
    dns.answer("example.com", PUBLIC_V4)
    web.route(f"https://{PUBLIC_V4}/", page())

    got = ingest_fetch.fetch_url("https://example.com#team")

    assert got.final_url == "https://example.com/"
    assert web.requests[0].url.raw_path == b"/"


def test_a_persian_domain_is_fetched_under_its_ascii_name(dns, web):
    """An IDN host in the Host header crashed httpx with UnicodeEncodeError,
    which is not a FetchRejected. The lookup, Host and SNI use punycode."""
    dns.answer("xn--mgbaxj59aup.ir", PUBLIC_V4)
    web.route(f"https://{PUBLIC_V4}/about", page())

    got = ingest_fetch.fetch_url("https://پادیار.ir/about")

    assert dns.calls == ["xn--mgbaxj59aup.ir"]
    assert web.requests[0].headers["host"] == "xn--mgbaxj59aup.ir"
    assert web.requests[0].extensions["sni_hostname"] == "xn--mgbaxj59aup.ir"
    assert got.final_url == "https://xn--mgbaxj59aup.ir/about"


@pytest.mark.parametrize("header, content_type, charset", [
    ("text/html", "text/html", ""),
    ('Text/HTML; Charset="ISO-8859-1"', "text/html", "iso-8859-1"),
    ("application/xhtml+xml; charset=utf-8", "application/xhtml+xml", "utf-8"),
    ("text/plain; charset=windows-1256", "text/plain", "windows-1256"),
    ("application/pdf", "application/pdf", ""),
])
def test_each_allowed_content_type_comes_back_bare_and_lowercase(dns, web, header, content_type,
                                                                   charset):
    dns.answer("example.com", PUBLIC_V4)
    web.route(f"https://{PUBLIC_V4}/doc", page(content_type=header))

    got = ingest_fetch.fetch_url("https://example.com/doc")

    assert (got.content_type, got.charset) == (content_type, charset)


# ── REQ-018: scheme, userinfo, port. Checked before any lookup ──────────

def test_plain_http_is_refused_before_any_lookup(dns, web):
    """U5."""
    assert rejected("http://example.com/").code == "http_only"
    assert rejected("HTTP://example.com/").code == "http_only"
    assert dns.calls == [] and web.requests == []


@pytest.mark.parametrize("url", [
    "ftp://example.com/",
    "file:///etc/passwd",
    "javascript:alert(1)",
    "example.com/about",
    "",
    "https://",
    "https:///path",
    "https://[::1/",
    "https://example.com/pa\x01th",
    "https://example.com/" + "a" * 70000,
])
def test_an_unreadable_url_or_another_scheme_is_a_bad_url(dns, web, url):
    assert rejected(url).code == "bad_url"
    assert dns.calls == [] and web.requests == []


@pytest.mark.parametrize("url", [
    "https://user@example.com/",
    "https://user:secret@example.com/",
    "https://example.com@evil.example/",
])
def test_credentials_in_the_url_are_refused_before_pin(monkeypatch, dns, web, url):
    """U9."""
    pinned = []
    monkeypatch.setattr(endpoint_policy, "pin", lambda *a, **k: pinned.append(a))

    assert rejected(url).code == "bad_url"
    assert pinned == [] and dns.calls == [] and web.requests == []


@pytest.mark.parametrize("url", [
    "https://example.com:8443/",
    "https://example.com:80/",
    "https://example.com:8080/",
    "https://example.com:99999/",
    "https://example.com:abc/",
])
def test_every_port_other_than_443_is_refused(dns, web, url):
    """U4."""
    assert rejected(url).code == "bad_port"
    assert dns.calls == [] and web.requests == []


# ── REQ-019: every address is checked, and the connection is pinned ─────

@pytest.mark.parametrize("host", [
    "127.0.0.1",
    "10.0.0.5",
    "172.16.0.1",
    "192.168.1.1",
    "169.254.169.254",
    "169.254.10.10",
    "0.0.0.0",
    "[::1]",
    "[fc00::1]",
    "[fd12:3456::1]",
    "[fe80::1]",
    "[::ffff:127.0.0.1]",
    "[::ffff:10.0.0.5]",
    "[::ffff:93.184.216.34]",
    "2130706433",
    "0177.0.0.1",
    "0x7f000001",
    "127.1",
    "0",
])
def test_an_internal_address_is_refused_and_never_connected(dns, web, host):
    """U3 (169.254.169.254) and the address classes of SEC-013.

    The numeric spellings mean 127.0.0.1 or 0.0.0.0 to a C resolver. They are
    refused before any lookup, so the verdict never depends on the resolver.
    """
    assert rejected(f"https://{host}/").code == "blocked_address"
    assert web.requests == [] and dns.calls == []


@pytest.mark.parametrize("answers", [
    ["10.0.0.5"],
    ["127.0.0.1"],
    ["169.254.169.254"],
    [PUBLIC_V4, "127.0.0.1"],
    ["::ffff:93.184.216.34"],
])
def test_a_name_that_resolves_to_an_internal_address_is_refused(dns, web, answers):
    dns.answer("inside.example", *answers)

    assert rejected("https://inside.example/").code == "blocked_address"
    assert web.requests == []


def test_a_metadata_hostname_is_refused_by_name(dns, web):
    assert rejected("https://metadata.google.internal/").code == "blocked_address"
    assert dns.calls == [] and web.requests == []


def test_a_name_that_does_not_resolve_is_refused_as_req_019_says(dns, web):
    assert rejected("https://no-such-name.example/").code == "blocked_address"
    assert web.requests == []


def test_the_connection_goes_to_the_address_that_passed_the_check(dns, web):
    """U2: DNS rebinding. The second answer would be 10.0.0.5."""
    dns.answer_in_turn("rebind.example", [PUBLIC_V4], ["10.0.0.5"])
    web.route(f"https://{PUBLIC_V4}/", page())

    ingest_fetch.fetch_url("https://rebind.example/")

    assert dns.calls == ["rebind.example"], "one lookup per hop, none by the HTTP client"
    assert web.hosts == [PUBLIC_V4]


def test_a_redirect_back_to_a_rebinding_name_is_looked_up_and_checked_again(dns, web):
    dns.answer_in_turn("rebind.example", [PUBLIC_V4], ["10.0.0.5"])
    web.route(f"https://{PUBLIC_V4}/", redirect("/next"))

    assert rejected("https://rebind.example/").code == "blocked_address"
    assert dns.calls == ["rebind.example", "rebind.example"]
    assert web.hosts == [PUBLIC_V4]


def test_a_refused_connection_moves_to_the_next_validated_address(dns, web):
    dns.answer("example.com", PUBLIC_V4, PUBLIC_V4_B)
    web.route(f"https://{PUBLIC_V4}/", httpx.ConnectError("refused"))
    web.route(f"https://{PUBLIC_V4_B}/", page())

    assert ingest_fetch.fetch_url("https://example.com/").data == PAGE
    assert web.hosts == [PUBLIC_V4, PUBLIC_V4_B]


def test_a_refused_connection_on_every_address_is_fetch_failed(dns, web):
    dns.answer("example.com", PUBLIC_V4, PUBLIC_V4_B)
    web.route(f"https://{PUBLIC_V4}/", httpx.ConnectError("refused"))
    web.route(f"https://{PUBLIC_V4_B}/", httpx.ConnectError("refused"))

    assert rejected("https://example.com/").code == "fetch_failed"


# ── REQ-020: redirects by hand, every hop checked again ─────────────────

def test_a_redirect_to_loopback_is_refused_at_that_hop(dns, web):
    """U1."""
    dns.answer("example.com", PUBLIC_V4)
    web.route(f"https://{PUBLIC_V4}/", redirect("https://127.0.0.1/"))

    assert rejected("https://example.com/").code == "blocked_address"
    assert web.hosts == [PUBLIC_V4]


@pytest.mark.parametrize("location, code", [
    ("http://example.com/next", "http_only"),
    ("https://example.com:8443/next", "bad_port"),
    ("https://user@example.com/next", "bad_url"),
    ("https://169.254.169.254/latest/meta-data/", "blocked_address"),
    ("//10.0.0.5/admin", "blocked_address"),
    ("https://[::ffff:10.0.0.5]/", "blocked_address"),
])
def test_a_redirect_target_gets_no_more_trust_than_a_typed_url(dns, web, location, code):
    dns.answer("example.com", PUBLIC_V4)
    web.route(f"https://{PUBLIC_V4}/", redirect(location))

    assert rejected("https://example.com/").code == code
    assert web.hosts == [PUBLIC_V4]


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_a_redirect_to_a_public_https_url_is_followed(dns, web, status):
    dns.answer("example.com", PUBLIC_V4)
    dns.answer("other.example", PUBLIC_V4_B)
    web.route(f"https://{PUBLIC_V4}/old", redirect("https://other.example/new", status))
    web.route(f"https://{PUBLIC_V4_B}/new", page())

    got = ingest_fetch.fetch_url("https://example.com/old")

    assert got.final_url == "https://other.example/new"
    second = web.requests[1]
    assert second.method == "GET"
    assert second.headers["host"] == "other.example"
    assert second.extensions["sni_hostname"] == "other.example"


def test_a_relative_redirect_is_resolved_against_the_url_of_that_hop(dns, web):
    dns.answer("example.com", PUBLIC_V4)
    web.route(f"https://{PUBLIC_V4}/a/b", redirect("../c"))
    web.route(f"https://{PUBLIC_V4}/c", page())

    assert ingest_fetch.fetch_url("https://example.com/a/b").final_url == "https://example.com/c"


def test_three_redirects_are_followed_and_a_fourth_is_refused(dns, web):
    """U6."""
    dns.answer("example.com", PUBLIC_V4)
    for n in range(4):
        web.route(f"https://{PUBLIC_V4}/{n}", redirect(f"/{n + 1}"))

    assert rejected("https://example.com/0").code == "too_many_redirects"
    assert len(web.requests) == 4

    web.requests.clear()
    web.route(f"https://{PUBLIC_V4}/3", page())
    assert ingest_fetch.fetch_url("https://example.com/0").final_url == "https://example.com/3"
    assert len(web.requests) == 4


def test_a_redirect_without_a_location_is_fetch_failed(dns, web):
    dns.answer("example.com", PUBLIC_V4)
    web.route(f"https://{PUBLIC_V4}/", httpx.Response(302))

    assert rejected("https://example.com/").code == "fetch_failed"


def test_a_client_set_to_follow_redirects_still_has_each_hop_checked(monkeypatch, dns, web):
    monkeypatch.setattr(ingest_fetch, "_client", lambda: web.client(follow_redirects=True))
    dns.answer("example.com", PUBLIC_V4)
    web.route(f"https://{PUBLIC_V4}/", redirect("https://127.0.0.1/"))

    assert rejected("https://example.com/").code == "blocked_address"
    assert web.hosts == [PUBLIC_V4]


# ── REQ-021: 5 MiB, 20 seconds, status 200 ──────────────────────────────

def test_a_streamed_body_over_5_mib_is_refused_and_reading_stops_early(dns, web):
    """U7. No Content-Length: only the running count can stop it."""
    dns.answer("example.com", PUBLIC_V4)
    body, served = counted_body(chunks=200)
    web.route(f"https://{PUBLIC_V4}/", httpx.Response(
        200, headers={"Content-Type": "text/html"}, content=body))

    assert rejected("https://example.com/").code == "too_large"
    assert served["chunks"] <= 5 * MIB // (64 * 1024) + 1, "the read must stop right after the cap"


def test_a_declared_length_over_5_mib_is_refused_before_reading(dns, web):
    dns.answer("example.com", PUBLIC_V4)
    body, served = counted_body(chunks=200)
    web.route(f"https://{PUBLIC_V4}/", httpx.Response(
        200, headers={"Content-Type": "text/html", "Content-Length": str(6 * MIB)}, content=body))

    assert rejected("https://example.com/").code == "too_large"
    assert served["chunks"] == 0


def test_a_body_of_exactly_5_mib_is_accepted(dns, web):
    dns.answer("example.com", PUBLIC_V4)
    web.route(f"https://{PUBLIC_V4}/", page(body=b"a" * (5 * MIB), content_type="text/plain"))

    assert len(ingest_fetch.fetch_url("https://example.com/").data) == 5 * MIB


@pytest.mark.parametrize("error", [httpx.ConnectTimeout, httpx.ReadTimeout, httpx.PoolTimeout])
def test_a_transport_timeout_is_timeout(dns, web, error):
    dns.answer("example.com", PUBLIC_V4)
    web.route(f"https://{PUBLIC_V4}/", error("slow"))

    assert rejected("https://example.com/").code == "timeout"


def test_the_20_second_budget_is_shared_by_every_hop(dns, web, clock):
    dns.answer("example.com", PUBLIC_V4)

    def slow_redirect(request):
        clock.now += 15
        return redirect("/next")

    web.route(f"https://{PUBLIC_V4}/start", slow_redirect)
    web.route(f"https://{PUBLIC_V4}/next", page())

    ingest_fetch.fetch_url("https://example.com/start")

    first, second = (r.extensions["timeout"] for r in web.requests)
    assert first == {"connect": 10.0, "read": 20.0, "write": 20.0, "pool": 20.0}
    assert second["read"] == pytest.approx(5.0)
    assert second["connect"] == pytest.approx(5.0)


def test_a_hop_after_the_budget_is_spent_is_never_looked_up_or_sent(dns, web, clock):
    """The lookup inside `pin` is a blocking C call nothing can cut short, so a
    hop that starts late must not reach the resolver at all."""
    dns.answer("example.com", PUBLIC_V4)
    dns.answer("other.example", PUBLIC_V4_B)

    def very_slow_redirect(request):
        clock.now += 21
        return redirect("https://other.example/next")

    web.route(f"https://{PUBLIC_V4}/start", very_slow_redirect)

    assert rejected("https://example.com/start").code == "timeout"
    assert len(web.requests) == 1
    assert dns.calls == ["example.com"]


def test_a_body_that_trickles_past_the_budget_is_timeout(dns, web, clock):
    dns.answer("example.com", PUBLIC_V4)

    def trickle():
        for _ in range(5):
            clock.now += 8
            yield b"x" * 100

    web.route(f"https://{PUBLIC_V4}/", httpx.Response(
        200, headers={"Content-Type": "text/html"}, content=trickle()))

    assert rejected("https://example.com/").code == "timeout"


@pytest.mark.parametrize("status", [204, 300, 304, 404, 500, 503])
def test_an_answer_other_than_200_is_fetch_failed(dns, web, status):
    dns.answer("example.com", PUBLIC_V4)
    web.route(f"https://{PUBLIC_V4}/", httpx.Response(status, headers={"Content-Type": "text/html"}))

    assert rejected("https://example.com/").code == "fetch_failed"


# ── REQ-021 on a real socket: the budget binds every blocking read ──────

BUDGET_S = 1.0
MARGIN_S = 0.5


def _write_test_ca(folder):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    now = datetime.datetime.now(datetime.timezone.utc)

    def certificate(subject, issuer, public_key, signing_key, extensions):
        builder = (x509.CertificateBuilder().subject_name(subject).issuer_name(issuer)
                   .public_key(public_key).serial_number(x509.random_serial_number())
                   .not_valid_before(now - datetime.timedelta(minutes=5))
                   .not_valid_after(now + datetime.timedelta(hours=1)))
        for extension, critical in extensions:
            builder = builder.add_extension(extension, critical=critical)
        return builder.sign(signing_key, hashes.SHA256())

    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "ingest fetch test CA")])
    ca = certificate(ca_name, ca_name, ca_key.public_key(), ca_key, [
        (x509.BasicConstraints(ca=True, path_length=None), True),
        (x509.KeyUsage(digital_signature=True, content_commitment=False, key_encipherment=False,
                       data_encipherment=False, key_agreement=False, key_cert_sign=True,
                       crl_sign=True, encipher_only=False, decipher_only=False), True),
        (x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), False),
    ])
    key = ec.generate_private_key(ec.SECP256R1())
    leaf = certificate(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")]),
                       ca_name, key.public_key(), ca_key, [
        (x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), False),
        (x509.BasicConstraints(ca=False, path_length=None), True),
        (x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), False),
        (x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), False),
    ])
    paths = {name: folder / f"{name}.pem" for name in ("ca", "cert", "key")}
    paths["ca"].write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    paths["cert"].write_bytes(leaf.public_bytes(serialization.Encoding.PEM))
    paths["key"].write_bytes(key.private_bytes(serialization.Encoding.PEM,
                                               serialization.PrivateFormat.PKCS8,
                                               serialization.NoEncryption()))
    return {name: str(path) for name, path in paths.items()}


@pytest.fixture(scope="module")
def test_ca(tmp_path_factory):
    return _write_test_ca(tmp_path_factory.mktemp("ingest-fetch-ca"))


class LoopbackServer:
    """One connection, served in one of four ways. Every slow way gives up
    after 4 seconds, so a fetch that ignores the budget fails the timing
    assertion instead of hanging the suite."""

    def __init__(self, cert, key):
        self.tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.tls.load_cert_chain(cert, key)
        self.listener = socket.create_server(("127.0.0.1", 0))
        self.port = self.listener.getsockname()[1]
        self.stop = threading.Event()
        self.thread = None

    def serve(self, mode):
        self.thread = threading.Thread(target=self._run, args=(mode,), daemon=True)
        self.thread.start()

    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=5)
        self.listener.close()

    def _trickle(self, sock, byte, seconds=4.0):
        until = time.monotonic() + seconds
        while not self.stop.is_set() and time.monotonic() < until:
            sock.sendall(byte)
            time.sleep(0.05)

    def _run(self, mode):
        conn, _ = self.listener.accept()
        try:
            with conn:
                if mode == "handshake":
                    conn.sendall(b"\x16\x03\x03\x40\x00")
                    self._trickle(conn, b"\x00")
                    return
                tls = self.tls.wrap_socket(conn, server_side=True)
                tls.recv(65536)
                if mode == "page":
                    tls.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n"
                                b"Content-Length: 2\r\n\r\nok")
                elif mode == "headers":
                    tls.sendall(b"HTTP/1.1 200 OK\r\nX-Slow: ")
                    self._trickle(tls, b"a")
                elif mode == "body_then_stall":
                    tls.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n"
                                b"Content-Length: 1000\r\n\r\n")
                    self._trickle(tls, b"a", seconds=0.9 * BUDGET_S)
                    self.stop.wait(4)
        except OSError:
            pass


@pytest.fixture
def loopback(monkeypatch, test_ca):
    server = LoopbackServer(test_ca["cert"], test_ca["key"])
    real_pin = endpoint_policy.pin
    real_connect = httpcore.SyncBackend.connect_tcp

    def pin_loopback_only(url, trust_class):
        assert urlsplit(url).hostname == "127.0.0.1"
        return real_pin(url, endpoint_policy.INTERNAL)

    def connect_to_server(self, host, port, *args, **kwargs):
        assert (host, port) == ("127.0.0.1", 443)
        return real_connect(self, host, server.port, *args, **kwargs)

    monkeypatch.setattr(ingest_fetch, "_client", REAL_CLIENT)
    monkeypatch.setattr(ingest_fetch, "TOTAL_TIMEOUT_S", BUDGET_S)
    monkeypatch.setattr(socket, "getaddrinfo", REAL_GETADDRINFO)
    monkeypatch.setattr(endpoint_policy, "pin", pin_loopback_only)
    monkeypatch.setattr(httpcore.SyncBackend, "connect_tcp", connect_to_server)
    monkeypatch.setattr(certifi, "where", lambda: test_ca["ca"])
    yield server
    server.close()


def test_the_real_client_fetches_from_the_loopback_server(loopback):
    """Control: proves TLS, the port mapping and the policy patch work, so a
    timeout below is the budget and not a broken harness."""
    loopback.serve("page")

    got = ingest_fetch.fetch_url("https://127.0.0.1/")

    assert (got.content_type, got.data) == ("text/html", b"ok")


@pytest.mark.parametrize("mode", ["headers", "handshake", "body_then_stall"])
def test_a_slow_server_is_cut_off_when_the_budget_runs_out(loopback, mode):
    """headers: one header byte every 50 ms. Each recv returns in time, so a
    per-read timeout never fires; this held a fetch for as long as the server
    liked. handshake: a TLS record that never completes. body_then_stall: a
    body that trickles, then stops while a recv is waiting."""
    loopback.serve(mode)
    started = time.monotonic()

    assert rejected("https://127.0.0.1/").code == "timeout"
    assert time.monotonic() - started < BUDGET_S + MARGIN_S


async def test_the_budget_holds_when_called_inside_a_running_event_loop(loopback):
    """The deadline needs no event loop of its own, so it works the same from
    a worker thread (how S3 calls it) or from inside a running loop."""
    loopback.serve("headers")
    started = time.monotonic()

    assert rejected("https://127.0.0.1/").code == "timeout"
    assert time.monotonic() - started < BUDGET_S + MARGIN_S


# ── REQ-022: content type, read before the body ─────────────────────────

@pytest.mark.parametrize("header", ["image/png", "application/json", "application/octet-stream",
                                    "text/css", None])
def test_a_content_type_outside_the_list_is_refused_unread(dns, web, header):
    """U8 (image/png). A missing header is refused too, not sniffed."""
    dns.answer("example.com", PUBLIC_V4)
    body, served = counted_body(chunks=3)
    headers = {"Content-Type": header} if header else {}
    web.route(f"https://{PUBLIC_V4}/", httpx.Response(200, headers=headers, content=body))

    assert rejected("https://example.com/").code == "bad_content_type"
    assert served["chunks"] == 0


def test_a_compressed_answer_is_refused_rather_than_inflated(dns, web):
    dns.answer("example.com", PUBLIC_V4)
    web.route(f"https://{PUBLIC_V4}/", page(body=gzip.compress(PAGE), **{"Content-Encoding": "gzip"}))

    assert rejected("https://example.com/").code == "fetch_failed"


def test_an_identity_content_encoding_is_accepted(dns, web):
    dns.answer("example.com", PUBLIC_V4)
    web.route(f"https://{PUBLIC_V4}/", page(**{"Content-Encoding": "identity"}))

    assert ingest_fetch.fetch_url("https://example.com/").data == PAGE


# ── REQ-023: what the request carries ───────────────────────────────────

def test_no_hop_carries_a_cookie_or_credentials_even_from_a_primed_client(monkeypatch, dns, web):
    monkeypatch.setattr(ingest_fetch, "_client", lambda: web.client(
        cookies={"session": "primed"}, auth=("admin", "secret"),
        headers={"Authorization": "Bearer primed", "Cookie": "session=primed"}))
    dns.answer("example.com", PUBLIC_V4)
    web.route(f"https://{PUBLIC_V4}/a", redirect("/b", **{"Set-Cookie": "sid=abc; Path=/"}))
    web.route(f"https://{PUBLIC_V4}/b", page())

    ingest_fetch.fetch_url("https://example.com/a")

    assert len(web.requests) == 2
    for request in web.requests:
        assert request.headers["user-agent"] == "PadyarIngest/1.0"
        assert request.headers["accept-encoding"] == "identity"
        assert "cookie" not in request.headers
        assert "authorization" not in request.headers


def test_the_real_client_ignores_proxy_settings_in_the_environment(monkeypatch):
    """With trust_env left on, httpx mounts a proxy transport per env variable
    (checked on httpx 0.28.1); that proxy would resolve the name itself, past
    the pin."""
    for name in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "https_proxy", "all_proxy"):
        monkeypatch.setenv(name, "http://10.9.9.9:3128")

    with REAL_CLIENT() as client:
        assert client.trust_env is False
        assert client.follow_redirects is False
        assert not client._mounts


# ── The error contract ──────────────────────────────────────────────────

def test_a_refusal_is_a_fetch_rejected_carrying_code_and_sentence():
    exc = ingest_fetch.FetchRejected("bad_url", SENTENCES["bad_url"])

    assert isinstance(exc, Exception)
    assert (exc.code, exc.message_fa) == ("bad_url", SENTENCES["bad_url"])
