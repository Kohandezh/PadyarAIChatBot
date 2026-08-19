"""Service control, health scoring and session management.

The constraint that matters most here is the customer's explicit one: no
arbitrary shell access from the admin panel. That is enforced by an allowlist
dict, and guarded by a source-inspection test — because an allowlist is only
worth anything until someone adds a convenient escape hatch beside it.
"""
import pathlib

import pytest


@pytest.fixture
def svc(tmp_path, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "chat.db"))
    monkeypatch.setattr(config, "LOGS_DB_PATH", str(tmp_path / "logs.db"))
    from app.db.connection import init_db
    init_db()
    from app.services import applog
    applog.ensure_tables()
    applog._recent.clear()
    from app.services import service_control
    return service_control


def _audit_rows(category="audit"):
    from app.services import applog
    rows, _ = applog.query(category=category, tables=list(applog.TABLES), limit=100)
    return rows


# ── The no-shell constraint ─────────────────────────────────────────────

def test_service_control_contains_no_execution_escape_hatch():
    """A regression guard for an explicit customer constraint.

    If someone later adds subprocess "just for a restart button", this fails.
    """
    source = pathlib.Path("app/services/service_control.py").read_text()
    for forbidden in ("subprocess", "os.system", "shell=True", "eval(",
                      "exec(", "__import__", "os.popen", "pty."):
        assert forbidden not in source, f"forbidden construct {forbidden!r} appeared"


def test_every_action_is_a_python_callable_not_a_string(svc):
    for name, entry in svc.ACTIONS.items():
        fn = entry[0]
        assert callable(fn), f"{name} is not a callable"


def test_an_unknown_action_executes_nothing(svc):
    with pytest.raises(svc.ActionRefused):
        svc.run("rm -rf /", actor="attacker")
    with pytest.raises(svc.ActionRefused):
        svc.run("__class__", actor="attacker")
    with pytest.raises(svc.ActionRefused):
        svc.run("_reindex_search", actor="attacker")   # private name, not a key


def test_a_rejected_action_is_still_recorded_as_a_security_event(svc):
    with pytest.raises(svc.ActionRefused):
        svc.run("definitely-not-allowed", actor="attacker", ip="1.2.3.4")
    events = _audit_rows("security")
    assert any(e["event_name"] == "admin.service.action.rejected" for e in events), \
        "an attempt to run an unknown action left no trace"


# ── Successful actions ──────────────────────────────────────────────────

def test_a_known_action_runs_and_is_audited(svc):
    result = svc.run("health_check", actor="inotex@admin", ip="10.0.0.1")
    assert result["ok"] is True
    assert result["duration_ms"] >= 0
    events = [e["event_name"] for e in _audit_rows("audit")]
    assert "admin.service.action.requested" in events
    assert "admin.service.action.completed" in events


def test_a_failing_action_is_audited_as_failed_and_never_leaks_a_trace(svc):
    def boom():
        raise RuntimeError("internal detail that must not reach the operator")
    svc.ACTIONS["explode"] = (boom, "آزمایش خطا", False, "search")
    try:
        with pytest.raises(svc.ActionRefused) as caught:
            svc.run("explode", actor="admin")
        assert "internal detail" not in caught.value.message_fa
        events = [e["event_name"] for e in _audit_rows("audit")]
        assert "admin.service.action.failed" in events
    finally:
        del svc.ACTIONS["explode"]


def test_concurrent_actions_are_refused_not_queued(svc):
    """Two simultaneous reindexes are pointless; two simultaneous destructive
    operations are dangerous. The second caller is refused immediately."""
    import threading
    barrier = threading.Event()
    released = threading.Event()

    def slow():
        barrier.set()
        released.wait(timeout=5)
        return "done"

    svc.ACTIONS["slow"] = (slow, "کند", False, "search")
    refused = {}
    try:
        worker = threading.Thread(target=lambda: svc.run("slow", actor="a"))
        worker.start()
        barrier.wait(timeout=5)
        with pytest.raises(svc.ActionRefused) as caught:
            svc.run("health_check", actor="b")
        refused["msg"] = caught.value.message_fa
        released.set()
        worker.join(timeout=5)
    finally:
        svc.ACTIONS.pop("slow", None)
        released.set()
    assert "در حال اجراست" in refused["msg"]


def test_process_control_is_reported_unavailable_rather_than_faked(svc):
    """This deployment has no supervisor. A Start/Stop button that cannot work
    would be worse than none, so the honest flag must stay False."""
    assert svc.PROCESS_CONTROL_AVAILABLE is False
    assert svc.PROCESS_CONTROL_REASON.strip()


# ── Health ──────────────────────────────────────────────────────────────

@pytest.fixture
def hz(tmp_path, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "chat.db"))
    monkeypatch.setattr(config, "LOGS_DB_PATH", str(tmp_path / "logs.db"))
    from app.db.connection import init_db
    init_db()
    from app.services import health
    health._probe_cache.clear()
    return health


def test_a_probe_never_raises_even_when_its_dependency_explodes(hz):
    def explode():
        raise RuntimeError("dependency is on fire")
    result = hz._probe("boom", "آزمایش", explode)
    assert result["status"] == hz.UNKNOWN
    assert "RuntimeError" in result["detail_fa"]


def test_every_registered_probe_returns_a_valid_status(hz):
    valid = {hz.OK, hz.DEGRADED, hz.DOWN, hz.DISABLED, hz.UNKNOWN}
    for probe in hz.probe_all(force=True):
        assert probe["status"] in valid, f"{probe['name']} returned {probe['status']}"
        assert probe["label_fa"] and probe["status_fa"]


def test_health_check_never_sends_an_sms(hz, monkeypatch):
    """A health check must not cost money. The SMS probe uses the credit
    lookup, never the send path."""
    from app.services import sms
    def forbidden(*a, **k):
        raise AssertionError("a health check attempted to SEND an SMS")
    monkeypatch.setattr(sms, "send", forbidden)
    monkeypatch.setattr(sms, "send_asanak", forbidden, raising=False)
    hz.probe_all(force=True)


def test_disabled_services_do_not_lower_the_score(hz):
    base = [{"name": "a", "status": hz.OK, "critical": True}]
    with_disabled = base + [{"name": "b", "status": hz.DISABLED, "critical": True}]
    assert hz.health_score(base)["score"] == hz.health_score(with_disabled)["score"] == 100


def test_a_down_critical_service_cannot_read_as_healthy(hz):
    services = [{"name": f"ok{i}", "status": hz.OK, "critical": False} for i in range(20)]
    services.append({"name": "db", "status": hz.DOWN, "critical": True})
    score = hz.health_score(services)
    assert score["score"] <= 40, "one dead critical service still scored as healthy"
    assert score["label_fa"] == "بحرانی"


def test_degraded_costs_less_than_down(hz):
    degraded = hz.health_score([{"name": "x", "status": hz.DEGRADED, "critical": False}])
    down = hz.health_score([{"name": "x", "status": hz.DOWN, "critical": False}])
    assert degraded["score"] > down["score"]


def test_probe_results_are_cached_so_a_dashboard_cannot_hammer_the_gateway(hz):
    calls = {"n": 0}

    def counting():
        calls["n"] += 1
        return hz.OK, "ok"

    hz._probe_cache.clear()
    original = hz.REGISTRY
    hz.REGISTRY = (("counted", "شمارش", counting, False, ()),)
    try:
        hz.probe_all(force=True)
        hz.probe_all()
        hz.probe_all()
        assert calls["n"] == 1, f"probe ran {calls['n']} times despite the cache"
    finally:
        hz.REGISTRY = original
        hz._probe_cache.clear()


def test_process_info_leaks_no_secrets(hz):
    info = hz.process_info()
    blob = str(info).lower()
    for secret_word in ("password", "api_key", "secret", "token", "enc:"):
        assert secret_word not in blob, f"process_info leaked {secret_word}"
