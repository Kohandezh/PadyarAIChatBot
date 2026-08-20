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
    """`internal` widens the private-network door. It must not open this one.

    The reason now says "cloud instance-metadata" rather than "link-local":
    metadata addresses are matched by an explicit deny list checked BEFORE the
    trust-class branch, because Alibaba's sits in CGNAT rather than link-local
    space and was slipping through as an ordinary private address.
    """
    reason = rejected(url, ep.INTERNAL).reason
    assert "metadata" in reason or "link-local" in reason


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


# ── Cloud metadata: an explicit deny, checked before any trust class ─────

@pytest.mark.parametrize("url", [
    "http://100.100.100.200/latest/meta-data/",     # Alibaba Cloud
    "http://169.254.169.254/latest/meta-data/",     # AWS / Azure / GCP
    "http://169.254.170.2/v2/credentials",          # AWS ECS task role
])
def test_every_known_metadata_endpoint_is_refused_under_internal(url):
    """Alibaba's endpoint is the one that motivated the explicit list: it lives
    in CGNAT (100.64.0.0/10), which is neither link-local nor RFC1918, so the
    address-class rules classified it as an ordinary private address and the
    `internal` trust class let it through."""
    assert "metadata" in rejected(url, ep.INTERNAL).reason


def test_ordinary_cgnat_is_still_reachable_for_on_prem():
    """The fix must not ban all of 100.64/10. A customer may legitimately run
    an on-prem model server on CGNAT, and blocking the whole range to stop one
    address would break the on-prem product to fix a single endpoint.

    Note `100.64.0.0/10` is NOT reported private by Python's `ipaddress`, so it
    is treated as ordinary routable space rather than as internal — only the
    single Alibaba metadata address inside it is denied.
    """
    out = ep.validate("http://100.64.1.5:8000/v1", ep.INTERNAL)
    assert out["host"] == "100.64.1.5"


# ── Pinning: the connection goes to the address that was validated ───────

def test_pin_returns_a_connect_url_using_the_validated_ip(dns):
    dns["api.example.com"] = ["93.184.216.34"]
    out = ep.pin("https://api.example.com/v1/chat", ep.PUBLIC)
    assert out["ip"] == "93.184.216.34"
    assert "93.184.216.34" in out["connect_url"]
    assert out["host"] == "api.example.com"      # for Host header + TLS SNI


def test_pin_preserves_the_port_and_path():
    out = ep.pin("http://10.0.0.5:8000/v1/chat", ep.INTERNAL)
    assert out["connect_url"] == "http://10.0.0.5:8000/v1/chat"


def test_pin_brackets_an_ipv6_literal():
    out = ep.pin("http://[::1]:11434/v1", ep.INTERNAL)
    assert "[::1]" in out["connect_url"]


def test_pin_refuses_what_validate_refuses(dns):
    """Pinning must never be a way around the policy."""
    with pytest.raises(ep.EndpointRejected):
        ep.pin("http://169.254.169.254/", ep.INTERNAL)


def test_pin_refuses_a_name_with_one_good_and_one_forbidden_address(dns):
    """A hostname answering with a public AND a metadata address is the shape
    of a rebinding attack; every answer must pass or none does."""
    dns["mixed.example"] = ["93.184.216.34", "169.254.169.254"]
    with pytest.raises(ep.EndpointRejected):
        ep.pin("https://mixed.example/v1", ep.PUBLIC)


# ── Dual-stack: pinning must not remove the address fallback ─────────────

def test_pin_returns_every_validated_address_not_just_the_first(dns):
    """The regression that pinning introduced, and the reason `connect_urls`
    exists.

    Handing httpx a hostname let IT fall through the address list. Handing it a
    single IP literal took that fallback away: `localhost` resolves to
    ['::1', '127.0.0.1'] on a normal machine, so an Ollama server bound to
    127.0.0.1 — its default — became unreachable through the pinned path while
    looking perfectly healthy to `curl`.
    """
    dns["ollama.local"] = ["::1", "127.0.0.1"]
    out = ep.pin("http://ollama.local:11434/v1", ep.INTERNAL)
    assert out["connect_urls"] == ["http://[::1]:11434/v1",
                                   "http://127.0.0.1:11434/v1"]
    assert out["connect_url"] == out["connect_urls"][0]   # first is the default


def test_every_pinned_candidate_is_a_validated_address(dns):
    """Falling back is only safe because every candidate already passed policy.
    A name with one good and one forbidden answer is rejected outright — it
    never reaches the fallback loop with a usable candidate."""
    dns["ok.example"] = ["93.184.216.34", "93.184.216.35"]
    out = ep.pin("https://ok.example/v1", ep.PUBLIC)
    assert len(out["connect_urls"]) == 2
    for url in out["connect_urls"]:
        ep.validate(url, ep.PUBLIC)          # each stands on its own


# ── The Host header must carry the original authority ────────────────────

def test_pin_exposes_the_original_authority_including_the_port(dns):
    """RFC 9110 requires the port in `Host` when it is not the scheme default.
    Sending the bare hostname breaks name-based virtual-host routing and any
    server that builds absolute URLs from `Host`."""
    dns["vllm.internal"] = ["10.0.0.5"]
    out = ep.pin("http://vllm.internal:8000/v1", ep.INTERNAL)
    assert out["authority"] == "vllm.internal:8000"
    assert out["host"] == "vllm.internal"          # SNI gets the bare name


def test_the_authority_omits_a_default_port(dns):
    dns["api.example.com"] = ["93.184.216.34"]
    assert ep.pin("https://api.example.com/v1", ep.PUBLIC)["authority"] == \
        "api.example.com"


def test_the_authority_brackets_an_ipv6_literal_host():
    out = ep.pin("http://[::1]:11434/v1", ep.INTERNAL)
    assert out["authority"] == "[::1]:11434"


# ── Metadata endpoints outside link-local space ──────────────────────────

@pytest.mark.parametrize("addr, cloud", [
    ("192.0.0.192", "Oracle OCI"),
    ("168.63.129.16", "Azure WireServer"),
    ("100.100.100.200", "Alibaba"),
])
@pytest.mark.parametrize("trust", [ep.INTERNAL, ep.PUBLIC])
def test_metadata_outside_link_local_is_denied_in_every_trust_class(addr, cloud,
                                                                    trust):
    """Each of these escaped by a DIFFERENT route, which is why the deny list is
    enumerated rather than derived from address class:

      Alibaba 100.100.100.200 — CGNAT; Python does not report it private, so it
        was reachable from BOTH classes.
      Oracle 192.0.0.192 — reports is_private, so `public` refused it, but
        `internal` (the class an on-prem customer runs) let it through.
      Azure 168.63.129.16 — reports is_global, so it looked like ordinary
        internet to both classes.
    """
    assert "metadata" in rejected(f"https://{addr}/x", trust).reason, cloud
