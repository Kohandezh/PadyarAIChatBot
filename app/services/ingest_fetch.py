"""Safe fetch of one web page an admin names (knowledge ingestion, slice S2).

An admin pastes a URL and this server connects to it, from inside the
customer's network. Without care that is a server-side request forgery door:
loopback, the private network, the cloud metadata service. The contract is
docs/features/knowledge-ingestion/SPEC.md, REQ-017..REQ-023 and
SEC-012..SEC-017.

Every hop, the typed URL and each redirect target, passes the same gate:

1. `_check_url`: https only, no userinfo, port empty or 443 (REQ-018).
   `endpoint_policy.validate` accepts any port, so that check lives here.
   It also refuses numeric IPv4 spellings and turns a Persian domain into
   its ASCII form, all before any lookup.
2. `_pin`: `endpoint_policy.pin(url, PUBLIC)` resolves the name ONCE and
   refuses every non-public answer (REQ-019). The request then goes to that
   validated IP, with `Host` and TLS SNI carrying the original name, the way
   `BaseAdapter.http` does it. The HTTP client never resolves, so a second
   DNS answer (rebinding) never gets a say.
3. Redirects are followed by hand, at most three, and each target goes
   through 1 and 2 again (REQ-020).

The answer is streamed under one budget for the whole fetch, all hops
together: 5 MiB and 20 seconds (REQ-021). The 20 seconds run from the first
byte sent to the last byte read, connect, TLS and headers included:
`_client()` connects through `_DeadlineBackend`, which gives every blocking
socket call only the time left (see there). The one wait it cannot bound is
the DNS lookup inside `pin` (getaddrinfo is a blocking C call). The budget is
checked before each lookup, so a spent budget never starts one, but a lookup
that has started runs for as long as the system resolver lets it.

Only HTML, XHTML, plain text and PDF come back (REQ-022). Text extraction is
not done here; this returns the raw bytes and the declared charset.
"""
import contextvars
import ipaddress
import socket
import time
from dataclasses import dataclass
from typing import NoReturn
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpcore
import httpx

from app.services.ai import endpoint_policy

MAX_BYTES = 5 * 1024 * 1024
TOTAL_TIMEOUT_S = 20.0
CONNECT_TIMEOUT_S = 10.0
MAX_REDIRECTS = 3
USER_AGENT = "PadyarIngest/1.0"
ALLOWED_CONTENT_TYPES = frozenset({
    "text/html", "application/xhtml+xml", "text/plain", "application/pdf",
})
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})

# The admin-facing sentences of SPEC section 8. They are contract text and the
# tests compare them character for character.
MESSAGES_FA = {
    "bad_url": "این نشانی درست نیست.",
    "http_only": "فقط نشانی‌هایی که با https شروع می‌شوند پذیرفته می‌شوند.",
    "bad_port": "این نشانی پذیرفته نمی‌شود.",
    "blocked_address": "این نشانی به یک شبکهٔ داخلی اشاره می‌کند و پذیرفته نمی‌شود.",
    "too_many_redirects": "این صفحه بیش از حد به جای دیگری می‌فرستد.",
    "too_large": "این صفحه بیش از اندازه بزرگ است.",
    "timeout": "این صفحه دیر جواب داد. دوباره امتحان کنید.",
    "bad_content_type": "این نشانی یک صفحهٔ متنی یا PDF نیست.",
    "fetch_failed": "این صفحه باز نشد.",
}


class FetchRejected(Exception):
    """The page was not fetched. `.message_fa` is safe to show the admin."""

    def __init__(self, code: str, message_fa: str):
        super().__init__(code)
        self.code = code
        self.message_fa = message_fa


def _reject(code: str) -> NoReturn:
    raise FetchRejected(code, MESSAGES_FA[code])


@dataclass(frozen=True)
class Fetched:
    final_url: str
    content_type: str
    charset: str
    data: bytes


# The monotonic deadline of the fetch_url call running in this context, read
# by every socket call below. None outside a fetch.
_DEADLINE: contextvars.ContextVar = contextvars.ContextVar("ingest_fetch_deadline",
                                                           default=None)


def _within_budget(timeout, error):
    """`timeout`, cut to the time left in the fetch. Raises `error` once none is left."""
    deadline = _DEADLINE.get()
    if deadline is None:
        return timeout
    left = deadline - time.monotonic()
    if left <= 0:
        raise error("the fetch budget is spent")
    return left if timeout is None else min(timeout, left)


class _DeadlineStream(httpcore.NetworkStream):
    # httpx's timeouts are per call: a read timeout restarts on every recv. A
    # server that sends one header byte every few seconds never trips it and
    # holds the fetch for as long as it likes (measured in review: 12 s
    # against a 2 s budget). Here every call gets only the time left, so the
    # whole fetch ends at the deadline whatever the server does. A TLS
    # handshake is one call: CPython bounds the whole handshake by the socket
    # timeout set before it.

    def __init__(self, stream: httpcore.NetworkStream):
        self._stream = stream

    def read(self, max_bytes, timeout=None):
        return self._stream.read(max_bytes, _within_budget(timeout, httpcore.ReadTimeout))

    def write(self, buffer, timeout=None):
        self._stream.write(buffer, _within_budget(timeout, httpcore.WriteTimeout))

    def close(self):
        self._stream.close()

    def start_tls(self, ssl_context, server_hostname=None, timeout=None):
        return _DeadlineStream(self._stream.start_tls(
            ssl_context, server_hostname, _within_budget(timeout, httpcore.ConnectTimeout)))

    def get_extra_info(self, info):
        return self._stream.get_extra_info(info)


class _DeadlineBackend(httpcore.NetworkBackend):
    def __init__(self):
        self._backend = httpcore.SyncBackend()

    def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        return _DeadlineStream(self._backend.connect_tcp(
            host, port, _within_budget(timeout, httpcore.ConnectTimeout),
            local_address, socket_options))

    def connect_unix_socket(self, path, timeout=None, socket_options=None):
        return _DeadlineStream(self._backend.connect_unix_socket(
            path, _within_budget(timeout, httpcore.ConnectTimeout), socket_options))

    def sleep(self, seconds):
        self._backend.sleep(seconds)


class _DeadlineTransport(httpx.HTTPTransport):
    """httpx's own transport on a connection pool built on `_DeadlineBackend`."""

    def __init__(self):
        ssl_context = httpx.create_ssl_context(trust_env=False)
        super().__init__(verify=ssl_context, trust_env=False)
        # HTTPTransport takes no network backend, so the pool it built is
        # replaced by one that has ours. `_pool` is what its handle_request
        # uses (httpx 0.28). The loopback tests fail if that ever changes.
        self._pool = httpcore.ConnectionPool(
            ssl_context=ssl_context,
            network_backend=_DeadlineBackend(),
        )


def _client() -> httpx.Client:
    # trust_env=False: with it on, HTTPS_PROXY and friends route the request
    # through a proxy that resolves the name itself, past the pin, and
    # connects from wherever the proxy sits.
    return httpx.Client(transport=_DeadlineTransport(), trust_env=False,
                        follow_redirects=False)


def _check_url(url: str) -> str:
    """Every check that needs no lookup: REQ-018, then the host spelling.
    Returns the URL to request, without its fragment."""
    text = (url or "").strip()
    try:
        parts = urlsplit(text)
    except ValueError:
        _reject("bad_url")
    scheme = parts.scheme.lower()
    if scheme == "http":
        _reject("http_only")
    if scheme != "https" or not parts.hostname:
        _reject("bad_url")
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        _reject("bad_url")
    try:
        port = parts.port
    except ValueError:
        _reject("bad_port")
    if port not in (None, 443):
        _reject("bad_port")

    # 2130706433, 0177.0.0.1, 0x7f000001 and 127.1 all mean 127.0.0.1 to the C
    # resolver but not to `ipaddress`, so `pin` would hand them to
    # getaddrinfo and the verdict would depend on that resolver. No site is
    # addressed this way; they are refused here, before any lookup.
    try:
        ipaddress.ip_address(parts.hostname)
    except ValueError:
        try:
            socket.inet_aton(parts.hostname)
        except (OSError, ValueError):
            pass
        else:
            _reject("blocked_address")

    # httpx refuses some URLs urlsplit accepts (control characters, over 65536
    # characters, a broken IDNA label). Asking it now keeps that refusal before
    # any lookup too. Its raw_host is the ASCII (punycode) form of a Persian
    # domain; the Host header and TLS SNI must carry that form, never the
    # Unicode one.
    path = parts.path or "/"
    try:
        host = httpx.URL(urlunsplit((scheme, parts.netloc, path, parts.query, ""))).raw_host
        host = host.decode("ascii")
    except (httpx.InvalidURL, UnicodeError):
        _reject("bad_url")
    if ":" in host:
        host = f"[{host}]"
    netloc = f"{host}:{port}" if port else host
    return urlunsplit((scheme, netloc, path, parts.query, ""))


def _pin(url: str) -> dict:
    """REQ-019, plus the IPv4-mapped refusal `pin` does not make on its own."""
    try:
        pinned = endpoint_policy.pin(url, endpoint_policy.PUBLIC)
    except endpoint_policy.EndpointRejected:
        _reject("blocked_address")
    except ValueError:
        # getaddrinfo refuses a name it cannot encode (an IDNA label over 63
        # characters) with UnicodeError, not gaierror, so `pin` lets it through.
        _reject("bad_url")

    # `pin` judges ::ffff:10.0.0.5 by the IPv4 inside it but lets
    # ::ffff:<public> through. No website is published under a mapped
    # address, so every one is refused. Checked on the pinned answers: no
    # second lookup.
    for ip in pinned["all_ips"]:
        address = ipaddress.ip_address(ip)
        if address.version == 6 and address.ipv4_mapped is not None:
            _reject("blocked_address")
    return pinned


def _time_left(deadline: float) -> float:
    left = deadline - time.monotonic()
    if left <= 0:
        _reject("timeout")
    return left


def _connect_urls(url: str, pinned: dict) -> list:
    # Not pinned["connect_urls"]: `pin` builds those from `validate`, which
    # drops a trailing slash. Right for an AI base URL, wrong for a page:
    # /docs/ would be asked for as /docs, and a site that redirects /docs to
    # /docs/ would loop until too_many_redirects. So the addresses come from
    # `pin` and the path and query from our own URL.
    parts = urlsplit(url)
    out = []
    for ip in pinned["all_ips"]:
        literal = f"[{ip}]" if ":" in ip else ip
        netloc = f"{literal}:{parts.port}" if parts.port else literal
        out.append(urlunsplit((parts.scheme, netloc, parts.path, parts.query, "")))
    return out


def _send(client: httpx.Client, url: str, pinned: dict, deadline: float) -> httpx.Response:
    # The request is built by hand, not with client.build_request, so nothing
    # the client holds is merged in: no cookie a previous hop set, no default
    # header. auth=None drops any client-level credentials too (REQ-023).
    headers = {
        "Host": pinned["authority"],
        "User-Agent": USER_AGENT,
        # So the 5 MiB count is a count of the real bytes on the wire.
        "Accept-Encoding": "identity",
    }
    candidates = _connect_urls(url, pinned)
    for i, candidate in enumerate(candidates):
        left = _time_left(deadline)
        request = httpx.Request("GET", candidate, headers=headers, extensions={
            "sni_hostname": pinned["host"],
            "timeout": httpx.Timeout(left, connect=min(CONNECT_TIMEOUT_S, left)).as_dict(),
        })
        try:
            return client.send(request, stream=True, auth=None, follow_redirects=False)
        except httpx.ConnectError:
            # Every candidate already passed policy. Moving to the next one is
            # the fallback httpx would have done had it been given the name.
            if i == len(candidates) - 1:
                _reject("fetch_failed")
        except httpx.TimeoutException:
            _reject("timeout")
        except httpx.HTTPError:
            _reject("fetch_failed")


def _redirect_target(url: str, response: httpx.Response) -> str:
    location = response.headers.get("location", "").strip()
    if not location:
        _reject("fetch_failed")
    try:
        target = urljoin(url, location)
    except ValueError:
        _reject("bad_url")
    return _check_url(target)


def _media_type(header: str) -> tuple:
    media, _, params = header.partition(";")
    charset = ""
    for param in params.split(";"):
        name, _, value = param.partition("=")
        if name.strip().lower() == "charset":
            charset = value.strip().strip("\"'").lower()
            break
    return media.strip().lower(), charset


def _read(response: httpx.Response, url: str, deadline: float) -> Fetched:
    if response.status_code != 200:
        _reject("fetch_failed")
    content_type, charset = _media_type(response.headers.get("content-type", ""))
    if content_type not in ALLOWED_CONTENT_TYPES:
        _reject("bad_content_type")
    # We asked for identity. A server that compresses anyway would turn the
    # 5 MiB cap into a cap on compressed bytes, and inflating is how a small
    # answer becomes a huge one, so it is refused rather than decoded.
    if response.headers.get("content-encoding", "").strip().lower() not in ("", "identity"):
        _reject("fetch_failed")
    declared = response.headers.get("content-length", "").strip()
    if declared.isdigit() and int(declared) > MAX_BYTES:
        _reject("too_large")

    # Only identity is left at this point, so iter_bytes decodes nothing and
    # counts the bytes as they arrived.
    chunks, size = [], 0
    try:
        for chunk in response.iter_bytes():
            size += len(chunk)
            if size > MAX_BYTES:
                _reject("too_large")
            _time_left(deadline)
            chunks.append(chunk)
    except httpx.TimeoutException:
        _reject("timeout")
    except httpx.HTTPError:
        _reject("fetch_failed")
    return Fetched(final_url=url, content_type=content_type, charset=charset,
                   data=b"".join(chunks))


def fetch_url(url: str) -> Fetched:
    """Fetch one page for an ingestion job. Blocking: the caller runs it in a
    thread. Every refusal is a FetchRejected with a code from SPEC section 8."""
    deadline = time.monotonic() + TOTAL_TIMEOUT_S
    token = _DEADLINE.set(deadline)
    try:
        return _fetch(url, deadline)
    finally:
        _DEADLINE.reset(token)


def _fetch(url: str, deadline: float) -> Fetched:
    current = _check_url(url)
    redirects = 0
    with _client() as client:
        while True:
            # Before the lookup: a hop that starts after the budget is spent
            # must not even reach the resolver.
            _time_left(deadline)
            response = _send(client, current, _pin(current), deadline)
            try:
                if response.status_code not in REDIRECT_STATUSES:
                    return _read(response, current, deadline)
                if redirects == MAX_REDIRECTS:
                    _reject("too_many_redirects")
                redirects += 1
                current = _redirect_target(current, response)
            finally:
                # Closing drops the connection, so a body past the cap, or a
                # redirect body nobody reads, is not downloaded any further.
                response.close()
