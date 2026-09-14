"""Prometheus metrics for this install, on a dedicated registry.

WHY A DEDICATED REGISTRY: the default prometheus_client.REGISTRY collects
anything any dependency registered. /metrics must expose exactly this
application's series — nothing more, nothing unreviewed — so every metric
below is bound to this module's own CollectorRegistry.

CARDINALITY IS THE WHOLE DESIGN. Prometheus server memory grows with the
number of distinct label COMBINATIONS, and a hostile or merely unlucky
visitor can mint unlimited combinations if a raw path, id or query string
ever becomes a label value. So every label here has a bounded value set:

  * `route` — always the ROUTE TEMPLATE (`/api/things/{thing_id}`), never
    the raw path. route_template() below is the only thing that produces
    this label, and it collapses everything unmatched to the fixed string
    "unmatched" and every asset mount to its fixed prefix.
  * `status` — str of an HTTP status code (finite).
  * `tier` — the `source` value a chat turn was served from; the set is the
    closed list of tiers in app/routers/chat.py.
  * `provider` — the provider TYPE from the AI control plane (11 values).
  * `outcome` — "success" | "failed" (ai_calls), "success" | "failed"
    (backups).
  * `instance` — provider instance id; bounded by the control-plane rows an
    operator creates by hand.

Every update is an in-memory operation — counter increments and gauge sets
only, no I/O — so instrumentation never adds latency to the chat path.

The gauge value encodes circuit state numerically (0 closed, 1 half_open,
2 open) rather than as a label: one series per provider instance, and
"alert when > 0" is a single PromQL comparison.
"""
from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

registry = CollectorRegistry()

http_requests_total = Counter(
    "http_requests_total",
    "HTTP requests by method, route template and status code.",
    ["method", "route", "status"], registry=registry)

http_request_duration_seconds = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency in seconds, by method and route template.",
    ["method", "route"], registry=registry,
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0))

http_inflight = Gauge(
    "http_inflight",
    "Requests currently being served.", registry=registry)

chat_tier_served_total = Counter(
    "chat_tier_served_total",
    "Chat turns recorded, by the tier/source that served them.",
    ["tier"], registry=registry)

ai_calls_total = Counter(
    "ai_calls_total",
    "Routed AI requests by provider type and outcome.",
    ["provider", "outcome"], registry=registry)

ai_circuit_state = Gauge(
    "ai_circuit_state",
    "Circuit-breaker state per provider instance: 0 closed, 1 half_open, 2 open.",
    ["instance"], registry=registry)

backup_outcome_total = Counter(
    "backup_outcome_total",
    "PostgreSQL backup attempts by outcome.",
    ["result"], registry=registry)

health_score = Gauge(
    "health_score",
    "The system health score computed by app/services/health.py (0-100).",
    registry=registry)

# Same set as _NO_VISITOR_PREFIXES in app/main.py: every path served by a
# static MOUNT. A mount never puts a route template in the scope, and the
# file part of the path must not become a label, so the prefix is the label.
MOUNTED_PREFIXES = ("/static", "/themes", "/media", "/LOGO")

UNMATCHED_ROUTE = "unmatched"

CIRCUIT_CLOSED, CIRCUIT_HALF_OPEN, CIRCUIT_OPEN = 0, 1, 2


def route_template(request) -> str:
    """The bounded route label for a request.

    FastAPI puts the matched APIRoute in request.scope["route"], and its
    `.path` is the template (`/chat`, `/api/things/{thing_id}`) even when
    the request 404s/422s inside that route. Mounted static files and
    unmatched paths have no route object: mounts collapse to their fixed
    prefix, everything else to "unmatched". No branch of this function can
    return an unbounded value.
    """
    template = getattr(request.scope.get("route"), "path", "") or ""
    if template:
        return template
    path = request.url.path
    for prefix in MOUNTED_PREFIXES:
        if path.startswith(prefix):
            return prefix
    return UNMATCHED_ROUTE
