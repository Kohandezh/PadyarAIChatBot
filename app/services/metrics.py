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
  * `outcome`: "success" | "failed" (ai_calls).
  * `result`: "success" | "failed" (backups).
  * `instance` — provider instance id; bounded by the control-plane rows an
    operator creates by hand.

Every update is a cheap local operation: counter increments and gauge sets
only. So instrumentation never adds latency to the chat path. With one
process it is a plain in-memory change. In multiprocess mode (below) it is a
write into a memory-mapped file in PROMETHEUS_MULTIPROC_DIR. Still no network,
no database and no fsync.

The gauge value encodes circuit state numerically (0 closed, 1 half_open,
2 open) rather than as a label: one series per provider instance, and
"alert when > 0" is a single PromQL comparison.

MULTIPROCESS MODE. Production runs several uvicorn workers, and each worker
is its own process. Without a shared place for the numbers, a scrape shows
only the one worker that got the connection: counters jump between three
unrelated values and gauges show one worker. So when PROMETHEUS_MULTIPROC_DIR
is set, every worker writes into that directory and exposition() merges the
files on each scrape. The mode is chosen at import time, by the library, for
EVERY metric in the process. That is also why the dedicated registry alone
is not enough: a metric from any dependency would land in the same
directory. exposition() therefore keeps only the nine families defined
here. See docs/engineering/MONITORING.md.
"""
import os
import threading

from prometheus_client import (CollectorRegistry, Counter, Gauge, Histogram,
                               generate_latest, multiprocess)
from prometheus_client.core import Metric

# Decide the mode exactly like the library does: by whether the KEY exists.
# prometheus_client turns file mode on when the key is present, even with an
# empty value. Testing the value for truth would call "" "single process"
# while the library already writes its files into the working directory.
# The lower case spelling is the old name; the library still accepts it.
MULTIPROC_DIR = os.environ.get("PROMETHEUS_MULTIPROC_DIR",
                               os.environ.get("prometheus_multiproc_dir"))

# A bad directory must stop the app at import, before any worker serves a
# request. Falling back to one process would give the exact bug this mode
# fixes (wrong numbers) and nobody would notice.
# The directory needs read, write and search access. Write is for the worker
# that records numbers. Read is for the scrape, which lists the files. A
# write-only directory would let the app boot and then /metrics would show no
# samples, with no error anywhere.
if MULTIPROC_DIR is not None and not (
        os.path.isdir(MULTIPROC_DIR)
        and os.access(MULTIPROC_DIR, os.R_OK | os.W_OK | os.X_OK)):
    raise RuntimeError(
        f"PROMETHEUS_MULTIPROC_DIR is set to {MULTIPROC_DIR!r}, but that is "
        "not an existing directory this process can read and write. Point it "
        "at an existing directory this process can read and write (on a "
        "server: the systemd unit's RuntimeDirectory, /run/padyar-<slug>), or "
        "remove the variable to run with one process. See "
        "docs/engineering/MONITORING.md.")

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

# livesum: the total over the workers that are alive. A worker's in-flight
# count means nothing once it is gone, so the file of a worker that exits is
# removed by mark_process_dead() below.
http_inflight = Gauge(
    "http_inflight",
    "Requests currently being served.", registry=registry,
    multiprocess_mode="livesum")

chat_tier_served_total = Counter(
    "chat_tier_served_total",
    "Chat turns recorded, by the tier/source that served them.",
    ["tier"], registry=registry)

ai_calls_total = Counter(
    "ai_calls_total",
    "Routed AI requests by provider type and outcome.",
    ["provider", "outcome"], registry=registry)

# mostrecent, not max and not live. The circuit state lives in the shared
# database, and each transition is published by exactly one worker: the one
# that won the conditional UPDATE (app/services/ai/circuit.py). Under max,
# worker A sets 2 (open), worker B later sets 0 (closed), A never writes
# again, and the scrape shows "open" forever. The newest write is the truth.
# Not live: a transition published by a worker that has since exited is still
# true. mostrecent forbids inc() and dec(); this gauge only ever uses set().
ai_circuit_state = Gauge(
    "ai_circuit_state",
    "Circuit-breaker state per provider instance: 0 closed, 1 half_open, 2 open.",
    ["instance"], registry=registry, multiprocess_mode="mostrecent")

# One outcome per backup attempt made by app/services/backup.py
# _run_backup_now: "success" only when the dump was created AND verified.
# Both series exist from the start (value 0), so increase() after a restart
# has a series to read before the first failure.
backup_outcome_total = Counter(
    "backup_outcome_total",
    "PostgreSQL backup attempts by outcome.",
    ["result"], registry=registry)
for _result in ("success", "failed"):
    backup_outcome_total.labels(result=_result)

# max: the newest verified backup ANY worker knows. The worker that ran the
# backup set it; the others still hold 0 or an older seeded value, and must
# not hide it. A backup that is still on disk stays true after its worker
# exits, so not live. Only set() is used, through
# set_backup_last_success() below, which never lowers the value.
# 0 means no verified backup is known (none since this start, none on disk).
backup_last_success_timestamp_seconds = Gauge(
    "backup_last_success_timestamp_seconds",
    "Unix time of the newest PostgreSQL backup that passed verification; "
    "0 when none is known.",
    registry=registry, multiprocess_mode="max")

# mostrecent: every worker computes the same score from the same checks, so
# the newest value is the current one. Only set() is used (never inc/dec).
health_score = Gauge(
    "health_score",
    "The system health score computed by app/services/health.py (0-100).",
    registry=registry, multiprocess_mode="mostrecent")

# The registry stays the single source of truth for "which families exist".
# FAMILY_NAMES is what the multiprocess scrape is allowed to show. The
# documentation and type are kept so a family nobody has written to yet can
# still be listed, like single-process mode lists it.
_FAMILY_META = {f.name: (f.documentation, f.type) for f in registry.collect()}
FAMILY_NAMES = frozenset(_FAMILY_META)

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


_backup_last_success_lock = threading.Lock()


def set_backup_last_success(timestamp: float) -> None:
    """Move backup_last_success_timestamp_seconds forward to `timestamp`.

    Never backward. Verifying an older backup (the admin panel's verify
    button, the restore pre-check) must not make the newest good backup look
    older. The current value is read from the gauge itself, so a reset of the
    gauge is a reset of this rule too. The lock keeps two threads of one
    worker from both reading the old value.
    """
    gauge = backup_last_success_timestamp_seconds
    with _backup_last_success_lock:
        current = next(iter(gauge.collect())).samples[0].value
        if timestamp > current:
            gauge.set(timestamp)


class _OurFamiliesFromFiles:
    """Collector for one multiprocess scrape: merge the files, keep our nine.

    The directory holds every metric any code in any worker created, so the
    merged result is filtered by name. This is the multiprocess twin of the
    dedicated registry.
    """

    def collect(self):
        seen = set()
        for family in multiprocess.MultiProcessCollector(
                None, path=MULTIPROC_DIR).collect():
            # The filter is by family NAME only. A metric with one of these
            # nine names, created by other code in the process, would be
            # merged in. No dependency does that today.
            if family.name in FAMILY_NAMES:
                seen.add(family.name)
                yield family
        # Nothing has written this family yet (for example ai_circuit_state
        # before the first circuit transition).
        # List it with no samples, as single-process mode does, so /metrics
        # always shows the same nine families.
        for name, (documentation, typ) in _FAMILY_META.items():
            if name not in seen:
                yield Metric(name, documentation, typ)


def exposition() -> bytes:
    """The body of GET /metrics: Prometheus text for this install.

    One process: exactly generate_latest(registry), as before. Several
    processes: a fresh registry per scrape reads the shared directory, so the
    files of every worker are added up (counters and histograms are summed).
    """
    if MULTIPROC_DIR is None:
        return generate_latest(registry)
    scrape_registry = CollectorRegistry()
    scrape_registry.register(_OurFamiliesFromFiles())
    return generate_latest(scrape_registry)


def mark_process_dead() -> None:
    """Drop this worker's live gauge files when it shuts down normally.

    Without this, the http_inflight file of an exited worker would keep
    adding its last in-flight count to the total. It removes only the
    gauge_live*_<pid>.db files. Counters and the mostrecent gauges stay,
    because what a worker counted or published is still true after it exits.
    A worker that is killed hard skips this (see docs/engineering/MONITORING.md).
    """
    if MULTIPROC_DIR is not None:
        multiprocess.mark_process_dead(os.getpid(), MULTIPROC_DIR)


def single_process_warning() -> str | None:
    """A one-line warning when several workers run without a shared directory.

    Read from the environment at call time. A WEB_CONCURRENCY that is not a
    number gives no warning: app/prodcheck.py already reports that.
    """
    if MULTIPROC_DIR is not None:
        return None
    try:
        workers = int(os.environ.get("WEB_CONCURRENCY", ""))
    except ValueError:
        return None
    if workers <= 1:
        return None
    return (f"WEB_CONCURRENCY={workers} but PROMETHEUS_MULTIPROC_DIR is not "
            "set, so /metrics will show the numbers of one worker only. Set "
            "PROMETHEUS_MULTIPROC_DIR to a writable directory that is empty at "
            "every start. See docs/engineering/MONITORING.md.")
