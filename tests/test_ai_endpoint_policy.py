"""SSRF policy for administrator-supplied AI provider Base URLs.

An admin who can type a Base URL can aim this server at anything the server can
reach. These tests hold the line in both directions: the cloud metadata service
must be unreachable no matter how the URL is dressed up, and a legitimate
on-prem model server must stay reachable — because banning private addresses
outright would make the on-prem product impossible, which is the failure mode
a naive SSRF fix produces.

No network is required and no public DNS is consulted. Hostnames that need to
resolve are stubbed via the `dns` fixture; everything else is an IP literal or
is rejected before resolution is ever attempted. A security test that fails
because someone's DNS was slow teaches nobody anything.
"""
import pytest

from app.services.ai import endpoint_policy as ep


@pytest.fixture
def dns(monkeypatch):
    """Point a hostname at chosen addresses without touching the network."""
    table = {}

    def fake_resolve(host):
        import ipaddress
        try:
            return [ipaddress.ip_address(host.strip("[]"))]
        except ValueError:
            pass
        if host not in table:
            ep._reject("hostname does not resolve: stubbed",
                       "نام میزبان قابل ترجمه به آدرس نیست.")
        return [ipaddress.ip_address(a) for a in table[host]]

    monkeypatch.setattr(ep, "_resolve", fake_resolve)
    return table


def rejected(url, trust_class=ep.PUBLIC):
    """Assert the URL is refused, and hand back the reason for inspection."""
    with pytest.raises(ep.EndpointRejected) as excinfo:
        ep.validate(url, trust_class)
    return excinfo.value


# ── Schemes ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("url", [
    "file:///etc/passwd",
    "unix:///var/run/docker.sock",
    "ftp://example.com/x",
    "gopher://example.com:70/_x",
    "data:text/plain;base64,aGk=",
    "javascript:alert(1)",
])
def test_only_http_schemes_are_accepted(url):
    assert "scheme" in rejected(url, ep.INTERNAL).reason


def test_plain_http_is_refused_for_a_public_endpoint():
    assert "http" in rejected("http://api.example.com/v1").reason


def test_plain_http_is_allowed_for_an_internal_endpoint():
    """On-prem model servers routinely have no TLS inside the perimeter."""
    assert ep.validate("http://10.0.0.5:8000/v1", ep.INTERNAL)["scheme"] == "http"


# ── Cloud metadata: forbidden in EVERY trust class ──────────────────────

@pytest.mark.parametrize("url", [
    "http://169.254.169.254/latest/meta-data/",
    "https://169.254.169.254/computeMetadata/v1/",
    "http://169.254.170.2/v2/credentials",
])
def test_metadata_addresses_are_refused_even_when_internal_is_trusted(url):
    """`internal` widens the private-network door. It must not open this one."""
    reason = rejected(url, ep.INTERNAL).reason
    assert "link-local" in reason


@pytest.mark.parametrize("host", [
    "metadata.google.internal",
    "metadata.goog",
    "instance-data",
])
def test_metadata_hostnames_are_refused_by_name(host):
    assert "metadata" in rejected(f"https://{host}/x", ep.INTERNAL).reason


def test_ipv6_mapped_metadata_address_is_refused():
    """An IPv4 metadata address wearing IPv6 clothing is the same address."""
    reason = rejected("https://[::ffff:169.254.169.254]/v1", ep.INTERNAL).reason
    assert "link-local" in reason


# ── Private space: gated on the trust class, not banned ─────────────────

@pytest.mark.parametrize("url", [
    "https://10.0.0.5/v1",
    "https://192.168.1.9:8443/v1",
    "https://172.16.4.4/v1",
])
def test_private_addresses_are_refused_for_a_public_endpoint(url):
    assert "private" in rejected(url).reason


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:11434/v1",      # Ollama
    "http://10.0.0.5:8000/v1",        # vLLM / LiteLLM on a private subnet
    "https://172.16.4.4/v1",
])
def test_private_addresses_are_permitted_for_an_internal_endpoint(url):
    """This is the on-prem product working. If these start failing, it broke."""
    assert ep.validate(url, ep.INTERNAL)["private"] is True


def test_ipv6_loopback_is_permitted_for_an_internal_endpoint():
    """`localhost` resolves to ::1 on a normal machine, and ::1 also reports
    is_reserved because it sits inside ::/8. Reserved space is refused in every
    trust class, so without an explicit loopback carve-out a local model server
    addressed as `localhost` would be unreachable — the on-prem case breaking on
    a technicality of IPv6 classification."""
    assert ep.validate("http://[::1]:11434/v1", ep.INTERNAL)["private"] is True


def test_a_hostname_pointing_at_loopback_is_permitted_for_internal(dns):
    dns["ollama.internal.example"] = ["127.0.0.1"]
    assert ep.validate("http://ollama.internal.example:11434/v1",
                       ep.INTERNAL)["private"] is True


def test_every_resolved_address_is_checked_not_just_the_first(dns):
    """A host with one public and one loopback record must not pass on the
    strength of whichever record DNS happened to return first."""
    dns["split.example"] = ["93.184.216.34", "127.0.0.1"]
    assert "private" in rejected("https://split.example/v1").reason


def test_loopback_is_refused_for_a_public_endpoint():
    assert "private" in rejected("https://127.0.0.1:11434/v1").reason


# ── URL shape ───────────────────────────────────────────────────────────

def test_credentials_in_the_url_are_refused():
    """Userinfo leaks into logs and confuses parsers about where the host ends."""
    assert "credentials" in rejected("https://user:pw@api.example.com/v1").reason


def test_credentials_are_refused_even_when_they_disguise_the_real_host():
    """`https://api.openai.com@169.254.169.254/` connects to the metadata IP."""
    assert "credentials" in rejected(
        "https://api.openai.com@169.254.169.254/", ep.INTERNAL).reason


@pytest.mark.parametrize("url", ["", "   ", "https://", "https:///v1"])
def test_empty_or_hostless_urls_are_refused(url):
    rejected(url, ep.INTERNAL)


def test_unknown_trust_class_is_refused():
    """Fail closed: an unrecognised class must never fall back to permissive."""
    assert "trust class" in rejected("https://api.example.com", "wide-open").reason


def test_a_valid_public_endpoint_is_accepted_and_normalized(dns):
    dns["api.example.com"] = ["93.184.216.34"]
    out = ep.validate("https://api.example.com/v1/", ep.PUBLIC)
    assert out["url"] == "https://api.example.com/v1"     # trailing slash dropped
    assert out["scheme"] == "https"
    assert out["host"] == "api.example.com"
    assert out["private"] is False
    assert out["resolved_ips"] == ["93.184.216.34"]


def test_an_unresolvable_hostname_is_refused(dns):
    assert "resolve" in rejected("https://nope.example/v1", ep.INTERNAL).reason


def test_resolution_result_is_exposed_so_callers_can_pin_the_connection():
    """DNS rebinding is only closable if the caller can pin what we validated."""
    ips = ep.resolved_ips("https://10.0.0.5/v1", ep.INTERNAL)
    assert ips == ["10.0.0.5"]


# ── Redirects get no more trust than a typed URL ────────────────────────

def test_a_redirect_to_metadata_is_refused():
    """A permitted host answering 302 to the metadata IP is the classic bypass."""
    with pytest.raises(ep.EndpointRejected):
        ep.assert_safe_redirect("http://169.254.169.254/latest/", ep.INTERNAL)


def test_a_redirect_to_a_public_endpoint_is_allowed(dns):
    dns["api.example.com"] = ["93.184.216.34"]
    assert ep.assert_safe_redirect("https://api.example.com/v2", ep.PUBLIC)


# ── Operator-facing text ────────────────────────────────────────────────

def test_rejection_carries_a_persian_reason_for_the_operator():
    err = rejected("https://10.0.0.5/v1")
    assert err.reason_fa
    assert err.reason_fa != err.reason        # not the English string reused


def test_each_trust_class_is_described_for_the_operator():
    assert ep.describe(ep.INTERNAL) != ep.describe(ep.PUBLIC)
    assert all(ep.describe(tc) for tc in ep.TRUST_CLASSES)
