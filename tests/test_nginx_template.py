"""The install vhost must never proxy /metrics to the app.

`deploy/nginx/instance.conf.template` used to send every path, `/metrics`
included, to uvicorn through the catch-all `location /`. So on a deployed
install the Prometheus endpoint was reachable from the internet, and the
app's own 403 was the only thing in front of it. Prometheus scrapes the app's
loopback port directly, so nginx never needs to serve `/metrics`.

These tests read the template text. nginx is not installed on a dev machine
or on CI, so `nginx -t` cannot run here, and the test needs no network and no
root. The template is rendered the same way `deploy/15-nginx-and-ssl.sh` and
`deploy/17-watchdog.sh` render it (three sed substitutions), then split into
server and location blocks by brace matching.

What nginx itself guarantees, and is therefore not re-tested here: an exact
`location = /metrics` is matched against the decoded, slash-merged URI
without the query string. So `/metrics?x=1`, `//metrics` and `/%6Detrics`
also get the 404. `/metrics/`, `/metricsx` and `/METRICS` are different URIs
and keep today's behaviour (proxied to the app).
"""
import hashlib
import pathlib
import re

TEMPLATE = (
    pathlib.Path(__file__).resolve().parents[1]
    / "deploy" / "nginx" / "instance.conf.template"
)

LOCATION_RE = re.compile(r"location\s+(?:(=|\^~|~\*|~)\s+)?(\S+)\s*\{")

# Every location of the HTTPS server block, in order, with a sha256 prefix of
# its whitespace-normalised, comment-free body. All but `/metrics` are the
# digests of base commit 3a4a415, so the PR that added `/metrics` provably left
# every other location as it was.
EXPECTED_LOCATIONS = [
    ("=", "/__maintenance.html", "b975c3ec3a9ec2d4"),
    ("", "/media/", "dc28ca9d4cd3a57f"),
    ("=", "/api/dataset", "886ad557f3988fca"),
    ("=", "/api/questions", "886ad557f3988fca"),
    ("", "/api/", "f39bcc6684922e3d"),
    ("=", "/chat", "f39bcc6684922e3d"),
    ("", "/admin/", "1e32450836b107f1"),
    ("", "/secure-panel-admin", "1e32450836b107f1"),
    ("=", "/metrics", "87b39e14adfffeab"),
    ("", "/", "7dfe9d604b889552"),
]


def _render():
    text = TEMPLATE.read_text(encoding="utf-8")
    text = (text.replace("{{DOMAIN}}", "chat.example.com")
                .replace("{{SLUG}}", "myevent")
                .replace("{{PORT}}", "8010"))
    return "\n".join(line.split("#", 1)[0] for line in text.splitlines())


def _block_end(text, open_brace):
    depth = 0
    for i in range(open_brace, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return i
    raise AssertionError("unbalanced braces in the rendered template")


def _server_blocks():
    text = _render()
    blocks = []
    for match in re.finditer(r"(?m)^server\s*\{", text):
        open_brace = match.end() - 1
        blocks.append(text[open_brace + 1:_block_end(text, open_brace)])
    return blocks


def _locations(server):
    """(modifier, path, body, offset) for each location directly in a server."""
    found = []
    pos = 0
    while True:
        match = LOCATION_RE.search(server, pos)
        if not match:
            return found
        open_brace = match.end() - 1
        close = _block_end(server, open_brace)
        found.append((match.group(1) or "", match.group(2),
                      server[open_brace + 1:close], match.start()))
        pos = close + 1


def _proxying_servers():
    return [s for s in _server_blocks() if "proxy_pass" in s]


def _metrics_location(server):
    hits = [loc for loc in _locations(server) if loc[:2] == ("=", "/metrics")]
    assert len(hits) == 1, "exactly one `location = /metrics` per proxying server"
    return hits[0]


def test_the_rendered_template_leaves_no_placeholder():
    assert "{{" not in _render()


def test_only_the_https_server_block_proxies_to_the_app():
    servers = _server_blocks()
    assert len(servers) == 2
    proxying = _proxying_servers()
    assert len(proxying) == 1
    assert re.search(r"listen\s+443\b", proxying[0])


def test_the_plain_http_block_only_redirects_to_https():
    plain = [s for s in _server_blocks() if re.search(r"listen\s+80\b", s)]
    assert len(plain) == 1
    assert "proxy_pass" not in plain[0], "port 80 would be a second way to reach /metrics"
    assert "return 301 https://$host$request_uri;" in plain[0]


def test_every_proxying_server_answers_metrics_with_a_404_from_nginx():
    for server in _proxying_servers():
        _, _, body, _ = _metrics_location(server)
        assert body.split() == ["return", "404;"], (
            "the /metrics block must answer itself, never proxy_pass to the app")


def test_the_metrics_block_sits_before_the_catch_all():
    for server in _proxying_servers():
        _, _, _, metrics_at = _metrics_location(server)
        catch_all = [loc for loc in _locations(server) if loc[:2] == ("", "/")]
        assert len(catch_all) == 1
        assert metrics_at < catch_all[0][3]
        assert server.index("location = /metrics") < server.index("location / {")


def test_no_other_location_touches_a_metrics_path():
    for server in _proxying_servers():
        others = [loc[:2] for loc in _locations(server)
                  if "metrics" in loc[1] and loc[:2] != ("=", "/metrics")]
        assert others == [], (
            "/metrics/ and /metricsx must keep today's behaviour (the catch-all)")


def test_every_other_location_is_unchanged():
    server = _proxying_servers()[0]
    actual = [
        (mod, path, hashlib.sha256(" ".join(body.split()).encode()).hexdigest()[:16])
        for mod, path, body, _ in _locations(server)
    ]
    assert actual == EXPECTED_LOCATIONS, (
        "a location's name, order or body changed. If that change is intended, "
        "update EXPECTED_LOCATIONS with the digests this test prints")
