"""/metrics stays correct when uvicorn runs several workers.

THE DEFECT THIS FILE PINS. Production runs `uvicorn --workers ${WEB_CONCURRENCY}`
(3 by default). Every worker is its own process with its own in-memory
registry. A scrape lands on whichever worker the kernel picks, so counters
jumped between three unrelated values (Prometheus read that as resets) and
gauges showed one worker only. The fix is prometheus_client multiprocess
mode: every process writes into a shared directory, and /metrics merges it.

WHY EVERY MULTIPROCESS CASE RUNS IN A REAL SUBPROCESS. The library picks its
mode ONCE, at import time, from the environment. The pytest process has
already imported the metrics module in single-process mode, so it can never
switch. A fresh `python -c` process per "worker" also matches production:
each writer, and each scraper, is a separate process with its own pid.
"""
import json
import logging
import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from prometheus_client import generate_latest
from prometheus_client.multiprocess import MultiProcessCollector
from prometheus_client.parser import text_string_to_metric_families

REPO_ROOT = Path(__file__).resolve().parent.parent
SERVICE_TEMPLATE = REPO_ROOT / "deploy" / "systemd" / "padyar-app.service.template"

# The `# TYPE` names of the fifteen families /metrics may expose. A counter's
# TYPE line carries the `_total` suffix.
TYPE_NAMES = {
    "http_requests_total",
    "http_request_duration_seconds",
    "http_inflight",
    "chat_tier_served_total",
    "ai_calls_total",
    "ai_circuit_state",
    "backup_outcome_total",
    "backup_last_success_timestamp_seconds",
    "backup_schedule_interval_seconds",
    "health_score",
    "intent_holdout_accuracy",
    "intent_model_version",
    "backup_drill_last_success_timestamp_seconds",
    "backup_drill_last_duration_seconds",
    "backup_drill_last_ok",
}

# A writer that touches every one of the twelve families at least once.
WRITE_EVERYTHING = """
from app.services import metrics
metrics.http_requests_total.labels("GET", "/chat", "200").inc()
metrics.http_request_duration_seconds.labels("GET", "/chat").observe(0.1)
metrics.http_inflight.inc()
metrics.chat_tier_served_total.labels("local").inc()
metrics.ai_calls_total.labels("openai", "success").inc()
metrics.ai_circuit_state.labels(instance="i-all").set(1)
metrics.backup_outcome_total.labels("success").inc()
metrics.backup_last_success_timestamp_seconds.set(1790000000)
metrics.backup_schedule_interval_seconds.set(86400)
metrics.health_score.set(80)
metrics.intent_holdout_accuracy.set(0.8)
metrics.intent_model_version.set(3)
"""

SCRAPE = """
import sys
from app.services import metrics
sys.stdout.write(metrics.exposition().decode())
"""


# ── helpers ────────────────────────────────────────────────────────────


def _env(mp_dir=None, key="PROMETHEUS_MULTIPROC_DIR", **extra):
    """The environment for one child process.

    Both spellings of the variable are removed first, so a developer shell
    that already exports one of them cannot decide the result of a test.
    """
    env = {k: v for k, v in os.environ.items()
           if k.lower() != "prometheus_multiproc_dir"}
    if mp_dir is not None:
        env[key] = str(mp_dir)
    env.update(extra)
    return env


def _python(code, mp_dir=None, key="PROMETHEUS_MULTIPROC_DIR", cwd=None, **extra):
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(code)],
        cwd=cwd or REPO_ROOT,
        env=_env(mp_dir, key, PYTHONPATH=str(REPO_ROOT), **extra),
        capture_output=True, text=True, timeout=180)


def _ok(proc):
    assert proc.returncode == 0, (
        f"child process failed (exit {proc.returncode}).\n"
        f"--- stderr ---\n{proc.stderr}\n--- stdout ---\n{proc.stdout}")
    return proc.stdout


def _run(code, mp_dir, **kw):
    return _ok(_python(code, mp_dir, **kw))


def _scrape(mp_dir):
    """What GET /metrics would return, asked from a brand-new process."""
    return _run(SCRAPE, mp_dir)


def _value(text, sample_name, **labels):
    """One sample value from Prometheus text, or None when it is absent."""
    for family in text_string_to_metric_families(text):
        for s in family.samples:
            if s.name == sample_name and s.labels == labels:
                return s.value
    return None


def _type_names(text):
    return set(re.findall(r"^# TYPE (\S+) ", text, flags=re.M))


def _result(stdout):
    """The JSON a child printed on its `RESULT:` line (logs may surround it)."""
    lines = [ln for ln in stdout.splitlines() if ln.startswith("RESULT:")]
    assert lines, f"child printed no RESULT line. stdout was:\n{stdout}"
    return json.loads(lines[-1][len("RESULT:"):])


@pytest.fixture
def mp_dir(tmp_path):
    """A fresh, empty metrics directory. Never shared between tests."""
    d = tmp_path / "mp"
    d.mkdir()
    return d


def _app_env(tmp_path, **extra):
    """Extra environment for a child that imports the whole app."""
    return dict(DB_PATH=str(tmp_path / "child.db"),
                LOGS_DB_PATH=str(tmp_path / "child-logs.db"),
                SEED_DEFAULT_CONTENT="false", **extra)


# ── AC1: counters and histograms are summed over every process ────────


def test_counters_are_summed_over_every_process(mp_dir):
    _run("""
        from app.services import metrics
        metrics.http_requests_total.labels("GET", "/chat", "200").inc(2)
        metrics.chat_tier_served_total.labels("local").inc(1)
        metrics.ai_calls_total.labels("openai", "success").inc(4)
        metrics.backup_outcome_total.labels("failed").inc(1)
    """, mp_dir)
    own_scrape = _run("""
        import sys
        from app.services import metrics
        metrics.http_requests_total.labels("GET", "/chat", "200").inc(3)
        metrics.chat_tier_served_total.labels("local").inc(1)
        metrics.ai_calls_total.labels("openai", "success").inc(6)
        metrics.backup_outcome_total.labels("failed").inc(0)
        sys.stdout.write(metrics.exposition().decode())
    """, mp_dir)
    third = _scrape(mp_dir)

    for who, text in (("the third process", third),
                      ("the process that wrote last", own_scrape)):
        assert _value(text, "http_requests_total", method="GET",
                      route="/chat", status="200") == 5, who
        assert _value(text, "chat_tier_served_total", tier="local") == 2, who
        assert _value(text, "ai_calls_total", provider="openai",
                      outcome="success") == 10, who
        # Worker A counted 1, worker B counted 0. Every scrape must still
        # show the 1. This is the series an alert is built on.
        assert _value(text, "backup_outcome_total", result="failed") == 1, who


def test_histogram_counts_sums_and_buckets_are_summed(mp_dir):
    _run("""
        from app.services import metrics
        h = metrics.http_request_duration_seconds.labels("GET", "/chat")
        h.observe(0.1)
        h.observe(0.2)
    """, mp_dir)
    _run("""
        from app.services import metrics
        h = metrics.http_request_duration_seconds.labels("GET", "/chat")
        h.observe(0.3)
        h.observe(0.4)
        h.observe(2.0)
    """, mp_dir)
    text = _scrape(mp_dir)
    labels = dict(method="GET", route="/chat")
    assert _value(text, "http_request_duration_seconds_count", **labels) == 5
    assert _value(text, "http_request_duration_seconds_sum",
                  **labels) == pytest.approx(3.0)
    # Buckets are cumulative. 0.1 and 0.2 are under 0.25; 0.3 and 0.4 join
    # them under 0.5; the 2.0 joins under 2.5.
    assert _value(text, "http_request_duration_seconds_bucket",
                  le="0.25", **labels) == 2
    assert _value(text, "http_request_duration_seconds_bucket",
                  le="0.5", **labels) == 4
    assert _value(text, "http_request_duration_seconds_bucket",
                  le="2.5", **labels) == 5
    assert _value(text, "http_request_duration_seconds_bucket",
                  le="+Inf", **labels) == 5


def test_legacy_lowercase_variable_name_also_switches_the_mode_on(mp_dir):
    """The library treats `prometheus_multiproc_dir` the same as the upper
    case name. The app must decide the mode the same way, or the two would
    disagree about where the files are."""
    code = """
        from app.services import metrics
        metrics.http_requests_total.labels("GET", "/legacy", "200").inc(2)
    """
    _run(code, mp_dir, key="prometheus_multiproc_dir")
    _run(code, mp_dir, key="prometheus_multiproc_dir")
    text = _run(SCRAPE, mp_dir, key="prometheus_multiproc_dir")
    assert _value(text, "http_requests_total", method="GET",
                  route="/legacy", status="200") == 4


# ── AC2: gauges ────────────────────────────────────────────────────────


def test_inflight_is_summed_over_live_processes_only(mp_dir):
    """http_inflight is `livesum`. A process that exited without marking
    itself dead is still counted. Marking it dead removes it."""
    pid_a = int(_run("""
        import os
        from app.services import metrics
        metrics.http_inflight.inc()
        metrics.http_inflight.inc()
        print(os.getpid())
    """, mp_dir).strip().splitlines()[-1])
    _run("""
        from app.services import metrics
        metrics.http_inflight.inc()
    """, mp_dir)

    assert _value(_scrape(mp_dir), "http_inflight") == 3

    from prometheus_client import multiprocess
    multiprocess.mark_process_dead(pid_a, str(mp_dir))
    assert _value(_scrape(mp_dir), "http_inflight") == 1


def test_circuit_state_is_the_most_recently_set_value_per_instance(mp_dir):
    """`mostrecent`, not `max`. Worker A sets open (2), worker B later sets
    closed (0), A never writes again. Under max the scrape would show open
    forever. Runs A and B one after the other so B's timestamp is later."""
    _run("""
        from app.services import metrics
        metrics.ai_circuit_state.labels(instance="closes-later").set(2)
        metrics.ai_circuit_state.labels(instance="opens-later").set(0)
    """, mp_dir)
    _run("""
        from app.services import metrics
        metrics.ai_circuit_state.labels(instance="closes-later").set(0)
        metrics.ai_circuit_state.labels(instance="opens-later").set(2)
    """, mp_dir)
    text = _scrape(mp_dir)
    assert _value(text, "ai_circuit_state", instance="closes-later") == 0, (
        "A set 2, B set 0 later: the later value must win (max would say 2)")
    assert _value(text, "ai_circuit_state", instance="opens-later") == 2, (
        "A set 0, B set 2 later: the later value must win")


def test_circuit_state_set_by_a_worker_that_then_exited_still_counts(mp_dir):
    """A transition published by a worker that has since exited is still the
    truth: the state lives in the database. So the gauge must not be `live`."""
    _run("""
        from app.services import metrics
        metrics.ai_circuit_state.labels(instance="gone").set(2)
        metrics.mark_process_dead()
    """, mp_dir)
    assert _value(_scrape(mp_dir), "ai_circuit_state", instance="gone") == 2


@pytest.mark.parametrize("first,second", [(40, 95), (95, 40)])
def test_health_score_is_the_most_recently_set_value(mp_dir, first, second):
    _run(f"""
        from app.services import metrics
        metrics.health_score.set({first})
    """, mp_dir)
    _run(f"""
        from app.services import metrics
        metrics.health_score.set({second})
    """, mp_dir)
    assert _value(_scrape(mp_dir), "health_score") == second


# ── The last verified backup time is the maximum over processes ──────


def test_last_backup_time_is_the_newest_any_worker_set(mp_dir):
    """`max`, not `mostrecent` and not `live`. Worker A ran the backup and set
    its time. Worker B never saw a backup (it holds 0) and C seeded an older
    time at startup, after A. Neither may hide or lower A's value, and A
    exiting does not remove it: the backup is still on disk."""
    _run("""
        from app.services import metrics
        metrics.backup_last_success_timestamp_seconds.set(1790000000)
        metrics.mark_process_dead()
    """, mp_dir)
    _run("""
        from app.services import metrics
        metrics.backup_last_success_timestamp_seconds.set(0)
    """, mp_dir)
    _run("""
        from app.services import metrics
        metrics.backup_last_success_timestamp_seconds.set(1780000000)
    """, mp_dir)
    assert _value(_scrape(mp_dir),
                  "backup_last_success_timestamp_seconds") == 1790000000


def test_last_backup_time_is_zero_when_no_worker_knows_one(mp_dir):
    """0 is "no verified backup known", the value the stale alert fires on."""
    assert _value(_scrape(mp_dir), "backup_last_success_timestamp_seconds") == 0


# ── The backup schedule is the most recently published value ─────────


@pytest.mark.parametrize("first,second", [(86400, 0), (0, 172800)])
def test_backup_schedule_is_the_most_recently_set_value(mp_dir, first, second):
    """`mostrecent`, not `max`. Every worker reads the same settings row, so
    the newest write is the current schedule. Under `max`, switching automatic
    backups off (0) would never show while an older 86400 sat in another
    worker's file, and the stale alert would keep waiting for a backup."""
    _run(f"""
        from app.services import metrics
        metrics.backup_schedule_interval_seconds.set({first})
    """, mp_dir)
    _run(f"""
        from app.services import metrics
        metrics.backup_schedule_interval_seconds.set({second})
    """, mp_dir)
    assert _value(_scrape(mp_dir), "backup_schedule_interval_seconds") == second


def test_backup_schedule_set_by_a_worker_that_then_exited_still_counts(mp_dir):
    """The schedule lives in the database, so it stays true after the worker
    that published it exits. The gauge must not be `live`."""
    _run("""
        from app.services import metrics
        metrics.backup_schedule_interval_seconds.set(172800)
        metrics.mark_process_dead()
    """, mp_dir)
    assert _value(_scrape(mp_dir), "backup_schedule_interval_seconds") == 172800


# ── The served intent model is the most recently published value ─────


@pytest.mark.parametrize("first,second", [((0.6, 1), (0.8, 2)), ((0.8, 2), (0.6, 1))])
def test_intent_gauges_are_the_most_recently_set_values(mp_dir, first, second):
    """Every worker publishes the model it serves at boot and on reindex, so
    the newest write is the newest model. A fresh process that only imports
    the module (the scrape below) must not overwrite it with the import-time
    NaN that single-process mode uses."""
    for accuracy, version in (first, second):
        _run(f"""
            from app.services import metrics
            metrics.intent_holdout_accuracy.set({accuracy})
            metrics.intent_model_version.set({version})
        """, mp_dir)
    text = _scrape(mp_dir)
    assert _value(text, "intent_holdout_accuracy") == second[0]
    assert _value(text, "intent_model_version") == second[1]


def test_intent_gauges_set_by_a_worker_that_then_exited_still_count(mp_dir):
    """The model stays on disk and is still served after the worker that
    published it exits. The gauges must not be `live`."""
    _run("""
        from app.services import metrics
        metrics.intent_holdout_accuracy.set(0.75)
        metrics.intent_model_version.set(4)
        metrics.mark_process_dead()
    """, mp_dir)
    text = _scrape(mp_dir)
    assert _value(text, "intent_holdout_accuracy") == 0.75
    assert _value(text, "intent_model_version") == 4


# ── AC3: exactly fifteen families, in both modes ──────────────────────


def test_only_the_twelve_families_are_exposed_and_foreign_metrics_are_not(mp_dir):
    foreign = textwrap.dedent("""
        import prometheus_client
        leak = prometheus_client.Counter("foreign_leak_total", "x")
        leak.inc()
    """)
    _run(foreign + WRITE_EVERYTHING, mp_dir)

    # Control: without the filter the foreign metric IS in the directory, so
    # an empty result below comes from the filter and not from a broken writer.
    raw = {f.name for f in MultiProcessCollector(None, path=str(mp_dir)).collect()}
    assert "foreign_leak" in raw, f"the writer never wrote the foreign metric: {raw}"

    text = _scrape(mp_dir)
    assert _type_names(text) == TYPE_NAMES
    assert "foreign_leak" not in text


def test_all_twelve_families_are_listed_even_before_anything_was_written(mp_dir):
    """Single-process mode always lists all fifteen (labelled families just have
    no samples yet). Multiprocess mode must not hide a family only because no
    worker has written to it yet."""
    text = _scrape(mp_dir)
    assert _type_names(text) == TYPE_NAMES


def test_single_process_output_is_unchanged_and_lists_the_twelve_families():
    """AC3 and AC4 without the variable: exposition() is exactly
    generate_latest(registry), in this very process."""
    from app.services import metrics
    assert metrics.MULTIPROC_DIR is None, (
        "this test process must run in single-process mode; "
        "unset PROMETHEUS_MULTIPROC_DIR in the shell that runs pytest")
    metrics.http_requests_total.labels("GET", "/single", "200").inc()
    metrics.http_request_duration_seconds.labels("GET", "/single").observe(0.1)

    assert metrics.exposition() == generate_latest(metrics.registry)
    assert len(metrics.FAMILY_NAMES) == 15
    assert metrics.FAMILY_NAMES == {
        "http_requests", "http_request_duration_seconds", "http_inflight",
        "chat_tier_served", "ai_calls", "ai_circuit_state",
        "backup_outcome", "backup_last_success_timestamp_seconds",
        "backup_schedule_interval_seconds", "health_score",
        "intent_holdout_accuracy", "intent_model_version",
        "backup_drill_last_success_timestamp_seconds",
        "backup_drill_last_duration_seconds", "backup_drill_last_ok"}

    names = _type_names(metrics.exposition().decode())
    # generate_latest also prints a `<family>_created` gauge for every counter
    # and histogram child. That is today's output and AC4 keeps it. Nothing
    # else may appear next to the fifteen.
    extra = {n for n in names if n not in TYPE_NAMES}
    assert all(n.endswith("_created") for n in extra), extra
    assert TYPE_NAMES <= names


def test_marking_a_process_dead_is_a_no_op_in_single_process_mode():
    from app.services import metrics
    assert metrics.MULTIPROC_DIR is None
    assert metrics.mark_process_dead() is None


# ── AC5: auth is unchanged and runs before any collection ──────────────


def test_metrics_endpoint_auth_in_multiprocess_mode(tmp_path, mp_dir):
    _run("""
        from app.services import metrics
        metrics.http_requests_total.labels("GET", "/writer-only", "200").inc(7)
    """, mp_dir)

    out = _run("""
        import json
        import app.config as config
        config.METRICS_TOKEN = "token-for-this-test"
        from app.main import app
        from app.services import metrics
        from fastapi.testclient import TestClient

        collections = []
        real_exposition = metrics.exposition

        def counting_exposition():
            collections.append(1)
            return real_exposition()

        metrics.exposition = counting_exposition
        metrics.http_requests_total.labels("GET", "/writer-only", "200").inc(3)

        with TestClient(app) as c:
            none = c.get("/metrics")
            wrong = c.get("/metrics", headers={"Authorization": "Bearer nope"})
            collected_after_denied = len(collections)
            good = c.get("/metrics", headers={
                "Authorization": "Bearer token-for-this-test"})

        print("RESULT:" + json.dumps({
            "none": none.status_code, "none_body": none.text,
            "wrong": wrong.status_code, "wrong_body": wrong.text,
            "collected_after_denied": collected_after_denied,
            "good": good.status_code, "good_body": good.text,
            "collected_after_good": len(collections)}))
    """, mp_dir, **_app_env(tmp_path))
    r = _result(out)

    assert r["none"] == 403, "no credentials must be refused"
    assert r["wrong"] == 403, "a wrong bearer token must be refused"
    assert "http_requests_total" not in r["none_body"] + r["wrong_body"]
    assert r["collected_after_denied"] == 0, (
        "auth must run BEFORE any collection; a denied request read the files")

    assert r["good"] == 200
    assert r["collected_after_good"] == 1
    body = r["good_body"]
    # 7 from the writer process plus 3 from this process.
    assert _value(body, "http_requests_total", method="GET",
                  route="/writer-only", status="200") == 10
    # The two denied requests above went through the middleware and were
    # counted into the shared directory.
    assert _value(body, "http_requests_total", method="GET",
                  route="/metrics", status="403") == 2
    assert _type_names(body) == TYPE_NAMES


# ── AC6: a worker that exits normally removes its live gauge files ─────


def test_lifespan_shutdown_removes_the_live_gauge_file_of_this_worker(tmp_path, mp_dir):
    out = _run("""
        import glob, json, os
        import app.config as config
        from app.main import app
        from fastapi.testclient import TestClient

        pattern = os.path.join(os.environ["PROMETHEUS_MULTIPROC_DIR"],
                               "gauge_livesum_%d.db" % os.getpid())
        with TestClient(app) as c:
            c.get("/api/health")
            during = glob.glob(pattern)
        print("RESULT:" + json.dumps({"during": during,
                                      "after": glob.glob(pattern)}))
    """, mp_dir, **_app_env(tmp_path))
    r = _result(out)
    assert r["during"], (
        "this worker has no gauge_livesum file; http_inflight must use "
        "multiprocess_mode='livesum'")
    assert r["after"] == [], (
        "shutdown left this worker's live gauge file behind; "
        "a dead worker's in-flight requests would be counted forever")


def test_a_worker_that_marks_itself_dead_is_not_counted_in_flight(mp_dir):
    pid = int(_run("""
        import os
        from app.services import metrics
        metrics.http_inflight.inc(2)
        metrics.mark_process_dead()
        print(os.getpid())
    """, mp_dir).strip().splitlines()[-1])

    assert not list(mp_dir.glob(f"gauge_livesum_{pid}.db")), (
        "mark_process_dead() left the live gauge file")
    assert (_value(_scrape(mp_dir), "http_inflight") or 0) == 0


# ── Stale files ────────────────────────────────────────────────────────


def test_a_fresh_directory_does_not_carry_the_counts_of_an_old_run(tmp_path):
    """Why the directory must be empty at every start: files are summed, so
    anything left there is counted again. A new directory has nothing."""
    old = tmp_path / "old-run"
    fresh = tmp_path / "fresh-run"
    old.mkdir()
    fresh.mkdir()
    count = """
        from app.services import metrics
        metrics.http_requests_total.labels("GET", "/chat", "200").inc({n})
    """
    _run(count.format(n=7), old)
    _run(count.format(n=1), fresh)

    labels = dict(method="GET", route="/chat", status="200")
    assert _value(_scrape(fresh), "http_requests_total", **labels) == 1

    # Control: reusing the old directory DOES sum the old 7 into the new 1.
    _run(count.format(n=1), old)
    assert _value(_scrape(old), "http_requests_total", **labels) == 8


# ── AC10: a bad directory stops the app, loudly ────────────────────────


def _bad_dir_cases(tmp_path):
    missing = tmp_path / "does-not-exist"
    a_file = tmp_path / "a-regular-file"
    a_file.write_text("not a directory")
    return missing, a_file


def test_a_valid_directory_lets_the_metrics_module_import(mp_dir):
    _run("import app.services.metrics", mp_dir)


@pytest.mark.parametrize("key", ["PROMETHEUS_MULTIPROC_DIR",
                                 "prometheus_multiproc_dir"])
def test_a_missing_directory_stops_the_import(tmp_path, key):
    missing, _ = _bad_dir_cases(tmp_path)
    proc = _python("import app.services.metrics", missing, key=key)
    assert proc.returncode != 0, "a missing directory must not be accepted"
    assert "PROMETHEUS_MULTIPROC_DIR" in proc.stderr
    assert repr(str(missing)) in proc.stderr


def test_an_empty_value_stops_the_import(tmp_path):
    """prometheus_client turns multiprocess mode on when the KEY exists, even
    with an empty value. So an empty value must not be read as 'unset'."""
    # Run from an empty directory: a library that took "" as a path would
    # write its files into the working directory, and that must never be the
    # repository.
    proc = _python("import app.services.metrics", "", cwd=tmp_path)
    assert not list(tmp_path.glob("*.db")), "files were written into the cwd"
    assert proc.returncode != 0, "an empty value must not be accepted"
    assert "PROMETHEUS_MULTIPROC_DIR" in proc.stderr
    assert "''" in proc.stderr


def test_a_regular_file_stops_the_import(tmp_path):
    _, a_file = _bad_dir_cases(tmp_path)
    proc = _python("import app.services.metrics", a_file)
    assert proc.returncode != 0, "a file is not a directory"
    assert "PROMETHEUS_MULTIPROC_DIR" in proc.stderr
    assert repr(str(a_file)) in proc.stderr


@pytest.mark.skipif(os.geteuid() == 0,
                    reason="root can write to any directory, so mode 0500 proves nothing")
def test_a_directory_without_write_access_stops_the_import(tmp_path):
    read_only = tmp_path / "read-only"
    read_only.mkdir()
    read_only.chmod(0o500)
    try:
        proc = _python("import app.services.metrics", read_only)
    finally:
        read_only.chmod(0o700)
    assert proc.returncode != 0, "a directory the process cannot write to was accepted"
    assert "PROMETHEUS_MULTIPROC_DIR" in proc.stderr
    assert repr(str(read_only)) in proc.stderr


@pytest.mark.skipif(os.geteuid() == 0,
                    reason="root can read any directory, so mode 0300 proves nothing")
def test_a_directory_without_read_access_stops_the_import(tmp_path):
    """Writing works but the scrape cannot list the files. Without this guard
    the app boots and /metrics silently shows no samples."""
    write_only = tmp_path / "write-only"
    write_only.mkdir()
    write_only.chmod(0o300)
    try:
        proc = _python("import app.services.metrics", write_only)
    finally:
        write_only.chmod(0o700)
    assert proc.returncode != 0, "a directory the process cannot read was accepted"
    assert "PROMETHEUS_MULTIPROC_DIR" in proc.stderr
    assert repr(str(write_only)) in proc.stderr


def test_a_readable_writable_directory_lets_the_metrics_module_import(tmp_path):
    """Control for the two access tests above: a directory with read, write and
    search access (mode 0700) must still be accepted."""
    usable = tmp_path / "usable"
    usable.mkdir()
    usable.chmod(0o700)
    _run("import app.services.metrics", usable)


def test_the_app_itself_refuses_to_boot_with_a_missing_directory(tmp_path):
    missing, _ = _bad_dir_cases(tmp_path)
    proc = _python("import app.main", missing, **_app_env(tmp_path))
    assert proc.returncode != 0, "the app booted with a bad PROMETHEUS_MULTIPROC_DIR"
    assert "PROMETHEUS_MULTIPROC_DIR" in proc.stderr


# ── AC8: one warning when several workers share no directory ───────────


@pytest.mark.parametrize("workers,dir_is_set,expect_warning", [
    ("3", False, True),
    ("2", False, True),
    (" 3 ", False, True),
    ("1", False, False),
    ("0", False, False),
    ("abc", False, False),
    ("", False, False),
    ("3", True, False),
    (None, False, False),
])
def test_single_process_warning_rules(monkeypatch, tmp_path,
                                      workers, dir_is_set, expect_warning):
    from app.services import metrics
    if workers is None:
        monkeypatch.delenv("WEB_CONCURRENCY", raising=False)
    else:
        monkeypatch.setenv("WEB_CONCURRENCY", workers)
    monkeypatch.setattr(metrics, "MULTIPROC_DIR",
                        str(tmp_path) if dir_is_set else None)

    message = metrics.single_process_warning()

    if not expect_warning:
        assert message is None, f"WEB_CONCURRENCY={workers!r} must not warn"
        return
    assert message, f"WEB_CONCURRENCY={workers!r} with no directory must warn"
    assert "one worker only" in message.lower()
    assert "PROMETHEUS_MULTIPROC_DIR" in message
    assert "MONITORING.md" in message
    assert "\n" not in message, "the warning is one line"


def _boot_and_collect_warnings(tmp_path, monkeypatch, caplog, workers):
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "warn.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    monkeypatch.setenv("WEB_CONCURRENCY", workers)
    from app.main import app
    with caplog.at_level(logging.WARNING):
        with TestClient(app):
            pass
    return [r for r in caplog.records
            if r.levelno == logging.WARNING
            and "one worker only" in r.getMessage().lower()]


def test_startup_logs_exactly_one_warning_for_several_workers(
        tmp_path, monkeypatch, caplog):
    from app.services import metrics
    assert metrics.MULTIPROC_DIR is None
    hits = _boot_and_collect_warnings(tmp_path, monkeypatch, caplog, "3")
    assert len(hits) == 1, [r.getMessage() for r in hits]


def test_startup_logs_no_warning_for_one_worker(tmp_path, monkeypatch, caplog):
    hits = _boot_and_collect_warnings(tmp_path, monkeypatch, caplog, "1")
    assert hits == []


# ── AC7: the systemd unit ──────────────────────────────────────────────


def _service_section():
    text = SERVICE_TEMPLATE.read_text(encoding="utf-8")
    body = text.split("[Service]", 1)[1].split("[Install]", 1)[0]
    return [ln.rstrip("\n") for ln in body.splitlines()]


def _service_settings():
    """Non-comment lines of [Service]."""
    return [ln for ln in _service_section()
            if ln.strip() and not ln.lstrip().startswith("#")]


def test_unit_gives_the_service_a_private_empty_runtime_directory():
    lines = _service_settings()
    dirs = [ln for ln in lines if ln.startswith("RuntimeDirectory=")]
    modes = [ln for ln in lines if ln.startswith("RuntimeDirectoryMode=")]
    envs = [ln for ln in lines
            if ln.startswith("Environment=PROMETHEUS_MULTIPROC_DIR=")]
    assert len(dirs) == 1 and len(modes) == 1 and len(envs) == 1, (dirs, modes, envs)

    name = dirs[0].split("=", 1)[1]
    assert name == "padyar-{{SLUG}}"
    assert modes[0] == "RuntimeDirectoryMode=0700"
    assert envs[0].split("=", 2)[2] == "/run/" + name


def test_unit_never_preserves_the_runtime_directory():
    """systemd deletes the directory on every stop only while Preserve is at
    its default. Stale files would be summed into the next run."""
    assert not [ln for ln in _service_settings()
                if ln.startswith("RuntimeDirectoryPreserve")]


def test_unit_keeps_every_hardening_line():
    lines = _service_settings()
    for expected in (
            "NoNewPrivileges=true",
            "PrivateTmp=true",
            "ProtectSystem=full",
            "ProtectHome=true",
            "ReadWritePaths=/var/lib/padyar/{{SLUG}} /var/log/padyar/{{SLUG}} /opt/padyar-{{SLUG}}"):
        assert expected in lines, f"hardening line changed or removed: {expected}"


def test_unit_renders_without_a_left_over_placeholder():
    rendered = SERVICE_TEMPLATE.read_text(encoding="utf-8").replace("{{SLUG}}", "demo")
    assert "{{" not in rendered
    assert "Environment=PROMETHEUS_MULTIPROC_DIR=/run/padyar-demo" in rendered
