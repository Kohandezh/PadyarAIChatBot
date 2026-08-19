"""Which URLs an AI provider instance is allowed to talk to.

THE PROBLEM
-----------
Letting an administrator type a Base URL turns the admin panel into a
server-side request forge. Whatever they type, this server will connect to,
with this server's network position and this server's credentials. On a cloud
host that reaches the instance metadata service; in a corporate network it
reaches every internal admin interface behind the firewall.

THE COMPLICATION
----------------
The naive fix — ban all private addresses — breaks a real Padyar use case. This
product is sold for on-prem and enterprise deployment, where the whole point is
that the model runs on `10.0.x.x`, or on `127.0.0.1:11434` as a local Ollama /
vLLM / LiteLLM gateway. A blanket RFC1918 ban would make the on-prem product
impossible.

So the policy is not "block private addresses". It is:

    A provider instance declares its TRUST CLASS.
    The trust class decides which address space is reachable.
    Widening the trust class is a privileged, audited configuration act.

TRUST CLASSES
-------------
`public` (the default, and what every hosted vendor uses)
    https only. Must resolve to a globally-routable address. This is the safe
    default: an operator who does nothing special cannot reach the internal
    network, no matter what they paste.

`internal` (must be chosen deliberately)
    Permits RFC1918, loopback and other private space, and permits plain http
    for endpoints that legitimately have no TLS inside a trusted perimeter.
    Intended for on-prem model servers.

NEVER REACHABLE, IN EITHER CLASS
--------------------------------
Cloud instance metadata (169.254.169.254 and friends), link-local, multicast,
reserved and unspecified space, and any scheme that is not http/https. There is
no legitimate LLM endpoint in that space, and it is exactly what an attacker
wants. `internal` widens the private-network door; it does not open this one.

WHAT THIS DOES NOT SOLVE — stated, not hidden
---------------------------------------------
`validate()` resolves DNS to judge the destination, and the HTTP client then
resolves it again when it connects. Between those two moments a hostile DNS
server can change the answer — classic DNS rebinding. Fully closing that means
pinning the validated IP into the transport for every request.

`resolved_ips()` exists so a caller CAN pin, and adapters are expected to use
it. But the guarantee is only as strong as the caller, so this is documented as
a residual risk rather than claimed as solved.

Redirects are the same story: a permitted host can 302 to a forbidden one.
Adapters must therefore disable automatic redirect following, and
`assert_safe_redirect()` is provided for the cases where a redirect must be
honoured.
"""
import ipaddress
import socket
from urllib.parse import urlsplit

PUBLIC = "public"
INTERNAL = "internal"
TRUST_CLASSES = (PUBLIC, INTERNAL)

ALLOWED_SCHEMES = ("https", "http")

# Hostnames that name a metadata service directly, so we reject them before
# DNS is ever consulted. The IP checks below are the real defence; this just
# produces a clearer error and costs nothing.
BLOCKED_HOSTNAMES = frozenset({
    "metadata.google.internal",
    "metadata.goog",
    "instance-data",
    "instance-data.ec2.internal",
})


class EndpointRejected(ValueError):
    """The URL is not permitted. `.reason_fa` is safe to show an operator."""

    def __init__(self, reason: str, reason_fa: str):
        self.reason = reason
        self.reason_fa = reason_fa
        super().__init__(reason)


def _reject(reason: str, reason_fa: str):
    raise EndpointRejected(reason, reason_fa)


def _is_forbidden_everywhere(ip: ipaddress._BaseAddress) -> str:
    """Return a reason string if this address is off-limits in EVERY trust class."""
    if ip.is_loopback:
        # Loopback is gated by the trust class further down, NOT forbidden
        # outright — `internal` must be able to reach a local Ollama/vLLM.
        # This early return matters for IPv6: `::1` also reports is_reserved
        # (it falls inside ::/8), so without this it would be rejected even for
        # `internal`, and `localhost` resolves to `::1` on a normal machine.
        return ""
    if ip.is_link_local:
        # Covers 169.254.0.0/16 — and therefore 169.254.169.254, the AWS/GCP/
        # Azure/DigitalOcean metadata address — plus IPv6 fe80::/10.
        return "link-local address (cloud metadata range)"
    if ip.is_multicast:
        return "multicast address"
    if ip.is_unspecified:
        return "unspecified address"
    if ip.is_reserved:
        return "reserved address"
    if isinstance(ip, ipaddress.IPv6Address):
        # An attacker can express a forbidden v4 address in v6 clothing.
        mapped = getattr(ip, "ipv4_mapped", None)
        if mapped is not None:
            return _is_forbidden_everywhere(mapped)
        sixtofour = getattr(ip, "sixtofour", None)
        if sixtofour is not None:
            return _is_forbidden_everywhere(sixtofour)
    return ""


def _is_private(ip: ipaddress._BaseAddress) -> bool:
    """Private/loopback space — permitted only for the `internal` trust class."""
    if ip.is_loopback or ip.is_private:
        return True
    if isinstance(ip, ipaddress.IPv6Address):
        mapped = getattr(ip, "ipv4_mapped", None)
        if mapped is not None:
            return _is_private(mapped)
    return False


def _resolve(host: str) -> list:
    """Every address `host` resolves to. A literal IP resolves to itself.

    ALL results are checked, not just the first. A hostname with one public and
    one loopback A record must not pass because the public one was returned
    first.
    """
    try:
        return [ipaddress.ip_address(host.strip("[]"))]
    except ValueError:
        pass

    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        _reject(f"hostname does not resolve: {type(exc).__name__}",
                "نام میزبان قابل ترجمه به آدرس نیست.")

    out, seen = [], set()
    for info in infos:
        addr = info[4][0]
        if addr in seen:
            continue
        seen.add(addr)
        try:
            out.append(ipaddress.ip_address(addr))
        except ValueError:
            continue
    if not out:
        _reject("hostname resolved to no usable address",
                "نام میزبان به هیچ آدرس معتبری ترجمه نشد.")
    return out


def validate(url: str, trust_class: str = PUBLIC) -> dict:
    """Check `url` against `trust_class`. Raises EndpointRejected, or returns
    a dict with the normalized URL and the addresses it resolved to."""
    if trust_class not in TRUST_CLASSES:
        _reject(f"unknown trust class {trust_class!r}",
                "ردهٔ اعتماد نامعتبر است.")

    raw = (url or "").strip()
    if not raw:
        _reject("empty URL", "نشانی خالی است.")

    parts = urlsplit(raw)

    if parts.scheme.lower() not in ALLOWED_SCHEMES:
        _reject(f"scheme {parts.scheme!r} is not permitted",
                "فقط نشانی‌های https و http پذیرفته می‌شوند.")

    # Credentials in the URL. These leak into logs and error messages, and are
    # a classic way to confuse a URL parser about where the host actually ends.
    if parts.username or parts.password or "@" in parts.netloc:
        _reject("credentials in URL are not permitted",
                "نام کاربری یا رمز نباید داخل نشانی نوشته شود.")

    host = (parts.hostname or "").lower()
    if not host:
        _reject("URL has no host", "نشانی میزبان ندارد.")
    if host in BLOCKED_HOSTNAMES:
        _reject(f"{host} is a metadata service", "این میزبان سرویس متادیتای ابری است.")

    if parts.scheme.lower() == "http" and trust_class == PUBLIC:
        _reject("plain http is not permitted for a public endpoint",
                "برای نشانی عمومی باید از https استفاده شود.")

    try:
        port = parts.port
    except ValueError:
        _reject("invalid port", "شمارهٔ پورت نامعتبر است.")
    if port is not None and not (1 <= port <= 65535):
        _reject("invalid port", "شمارهٔ پورت نامعتبر است.")

    addresses = _resolve(host)
    for ip in addresses:
        forbidden = _is_forbidden_everywhere(ip)
        if forbidden:
            _reject(f"{host} resolves to a {forbidden}",
                    "این نشانی به فضای آدرس ممنوع (متادیتا/link-local) اشاره می‌کند.")
        if _is_private(ip) and trust_class != INTERNAL:
            _reject(
                f"{host} resolves to private address {ip}; a public endpoint "
                "may not reach the internal network",
                "این نشانی به شبکهٔ داخلی اشاره می‌کند. اگر عمداً یک سرویس "
                "درون‌سازمانی است، ردهٔ اعتماد را روی «داخلی» بگذارید.")

    return {
        "url": raw.rstrip("/"),
        "scheme": parts.scheme.lower(),
        "host": host,
        "port": port,
        "trust_class": trust_class,
        "resolved_ips": [str(ip) for ip in addresses],
        "private": any(_is_private(ip) for ip in addresses),
    }


def resolved_ips(url: str, trust_class: str = PUBLIC) -> list:
    """The validated addresses, for a caller that wants to pin the connection."""
    return validate(url, trust_class)["resolved_ips"]


def assert_safe_redirect(location: str, trust_class: str = PUBLIC) -> dict:
    """Validate a redirect target with the same rules as the original URL.

    A permitted host answering 302 with `Location: http://169.254.169.254/…`
    is the standard way to walk around an allowlist, so a redirect gets no more
    trust than a freshly typed URL.
    """
    return validate(location, trust_class)


def describe(trust_class: str) -> str:
    """Operator-facing Persian explanation of what a trust class allows."""
    if trust_class == INTERNAL:
        return ("نشانی درون‌سازمانی: دسترسی به شبکهٔ خصوصی و localhost مجاز است "
                "و http بدون TLS پذیرفته می‌شود. فقط برای سرویس‌های مدل که خودتان "
                "در شبکهٔ امن اجرا می‌کنید.")
    return ("نشانی عمومی: فقط https و فقط آدرس‌های عمومی اینترنت. دسترسی به "
            "شبکهٔ داخلی مسدود است.")
