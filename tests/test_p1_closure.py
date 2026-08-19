"""The three P1 defects, each held down by a test that would fail without the fix.

1. A duplicate dataset id returned 500 on PostgreSQL, because the router caught
   `sqlite3.IntegrityError` and psycopg raises `UniqueViolation`.
2. Speech-to-text read the legacy `ai_api_key` while chat read the encrypted
   control-plane secret — so rotating the key in Admin → AI fixed chat and left
   voice returning 401 against the stale value.
3. Settings → AI wrote `ai_model_chat` / `ai_model_classify`, which the runtime
   stopped reading when routing moved to the Control Plane. The page reported
   success and changed nothing.

These run on SQLite like the rest of the suite. The PostgreSQL half of the
proof — where defects 1 and 2 actually bit — lives in `tests/postgres/`.
"""
import datetime
import secrets
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.db import dberrors


@pytest.fixture
def client(tmp_path, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "p1.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    monkeypatch.setattr("app.services.openai.OPENAI_API_BASE", "")
    monkeypatch.setattr("app.services.openai.OPENAI_API_KEY", "")
    from app.main import app
    with TestClient(app) as c:
        from app.db.connection import get_db_connection
        conn = get_db_connection()
        token = secrets.token_hex(16)
        conn.execute("INSERT OR IGNORE INTO admins (username, password_hash, salt,"
                     " security_question, security_answer_hash)"
                     " VALUES ('p1admin','x','y','q','z')")
        conn.execute("INSERT INTO admin_sessions (token, username, expiry) VALUES (?,?,?)",
                     (token, "p1admin",
                      (datetime.datetime.now() + datetime.timedelta(hours=1)).isoformat()))
        conn.commit()
        conn.close()
        c.cookies.set("admin_session", token)
        yield c


def _csrf(client):
    return client.get("/admin/csrf").json()["csrf_token"]


def _post(client, url, body):
    return client.post(url, json=body, headers={"X-CSRF-Token": _csrf(client)})


# ── P1 #1 — duplicate dataset id ────────────────────────────────────────

def test_a_new_dataset_id_is_created(client):
    r = _post(client, "/admin/api/dataset", {"id": "alpha", "title": "T", "text": "X"})
    assert r.status_code == 200, r.text


def test_a_duplicate_dataset_id_is_refused_with_a_conflict(client):
    _post(client, "/admin/api/dataset", {"id": "alpha", "title": "First", "text": "X"})
    r = _post(client, "/admin/api/dataset", {"id": "alpha", "title": "Second", "text": "Y"})
    assert r.status_code == 409, f"expected 409, got {r.status_code}: {r.text}"
    assert r.status_code != 500


def test_a_duplicate_does_not_overwrite_the_existing_row(client):
    """No upsert. The first writer's content must survive the second attempt."""
    _post(client, "/admin/api/dataset", {"id": "alpha", "title": "First", "text": "ORIGINAL"})
    _post(client, "/admin/api/dataset", {"id": "alpha", "title": "Second", "text": "OVERWRITTEN"})
    rows = {d["id"]: d for d in client.get("/api/dataset").json()}
    assert rows["alpha"]["text"] == "ORIGINAL"
    assert rows["alpha"]["title"] == "First"


def test_a_persian_duplicate_id_behaves_the_same(client):
    """ids are TEXT and the product is Persian-first, so a non-ASCII id must
    take exactly the same path — not trip some encoding-specific branch."""
    pid = "نمایشگاه-اینوتکس"
    assert _post(client, "/admin/api/dataset",
                 {"id": pid, "title": "T", "text": "X"}).status_code == 200
    assert _post(client, "/admin/api/dataset",
                 {"id": pid, "title": "T2", "text": "Y"}).status_code == 409


def test_the_connection_still_works_after_a_duplicate_was_refused(client):
    """The failure must not leave the session unusable. On PostgreSQL an
    unhandled constraint error aborts the transaction, so everything after it
    fails too — this asserts the request path recovers."""
    _post(client, "/admin/api/dataset", {"id": "alpha", "title": "T", "text": "X"})
    _post(client, "/admin/api/dataset", {"id": "alpha", "title": "T", "text": "X"})  # 409
    assert _post(client, "/admin/api/dataset",
                 {"id": "beta", "title": "T", "text": "X"}).status_code == 200
    assert client.get("/api/dataset").status_code == 200


# ── the helper itself ───────────────────────────────────────────────────

def test_a_sqlite_unique_violation_is_recognised():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE t (id TEXT PRIMARY KEY)")
    conn.execute("INSERT INTO t VALUES ('a')")
    with pytest.raises(sqlite3.IntegrityError) as e:
        conn.execute("INSERT INTO t VALUES ('a')")
    assert dberrors.is_unique_violation(e.value) is True


def test_a_non_unique_integrity_error_is_not_called_a_duplicate():
    """`sqlite3.IntegrityError` also covers NOT NULL and FOREIGN KEY. Reporting
    those as 'ID already exists' would send an operator hunting for a duplicate
    that does not exist."""
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE t (id TEXT PRIMARY KEY, name TEXT NOT NULL)")
    with pytest.raises(sqlite3.IntegrityError) as e:
        conn.execute("INSERT INTO t (id, name) VALUES ('a', NULL)")
    assert dberrors.is_unique_violation(e.value) is False
    assert dberrors.is_not_null_violation(e.value) is True


def test_an_unrelated_exception_is_not_a_unique_violation():
    assert dberrors.is_unique_violation(ValueError("boom")) is False
    assert dberrors.is_unique_violation(RuntimeError("UNIQUE constraint failed")) is False


# ── P1 #2 — STT credential binding ──────────────────────────────────────

def _make_instance(secret, display="GW", enabled=True):
    from app.services.ai import store
    iid = store.create_instance("openai_compatible", display,
                                {"base_url": "https://93.184.216.34/v1"}, secret)
    if enabled:
        store.set_enabled(iid, True, actor="test")
    return iid


def test_stt_uses_the_control_plane_secret_not_the_legacy_key(client):
    """The core of the defect: the control-plane secret must win."""
    from app.db.queries import set_setting
    from app.services.ai import stt, store
    set_setting("ai_api_key", "legacy-key-must-not-be-used")
    set_setting("ai_api_base", "https://legacy.example/v1")
    _make_instance("control-plane-secret-aaa")
    store._invalidate_runtime()

    base, key, _model, source = stt.resolve()
    assert key == "control-plane-secret-aaa"
    assert key != "legacy-key-must-not-be-used"
    assert source in ("explicit", "implicit")


def test_rotating_the_provider_secret_changes_stt_without_touching_ai_api_key(client):
    """Quality Gate 2 stated as a test: an operator rotates the key in
    Admin → AI and voice follows, with `ai_api_key` never edited."""
    from app.db.queries import set_setting, get_setting
    from app.services.ai import stt, store
    set_setting("ai_api_key", "legacy-untouched")
    iid = _make_instance("secret-before")
    store._invalidate_runtime()
    assert stt.resolve()[1] == "secret-before"

    store.update_instance(iid, secret="secret-after", actor="test")
    store._invalidate_runtime()
    assert stt.resolve()[1] == "secret-after"
    assert get_setting("ai_api_key", "") == "legacy-untouched"


def test_stt_falls_back_to_legacy_only_when_no_instance_can_serve_it(client):
    """Backward compatibility for an install that never migrated."""
    from app.db.queries import set_setting
    from app.services.ai import stt, store
    set_setting("ai_api_base", "https://legacy.example/v1")
    set_setting("ai_api_key", "legacy-key")
    store._invalidate_runtime()
    base, key, _m, source = stt.resolve()
    assert source == "legacy"
    assert key == "legacy-key"


def test_missing_credentials_raise_a_normalized_error(client):
    from app.db.queries import set_setting
    from app.services.ai import stt, store
    set_setting("ai_api_base", "")
    set_setting("ai_api_key", "")
    store._invalidate_runtime()
    with pytest.raises(stt.STTNotConfigured) as e:
        stt.resolve()
    assert e.value.message_fa                      # operator-facing Persian


def test_a_stale_explicit_binding_fails_loudly_instead_of_silently(client):
    """A binding pointing at a deleted instance is a configuration error the
    operator must see — not something to quietly paper over with the legacy key."""
    from app.db.queries import set_setting
    from app.services.ai import stt, store
    set_setting("ai_api_key", "legacy-key")
    set_setting(stt.SETTING_INSTANCE, "no-such-instance-id")
    store._invalidate_runtime()
    with pytest.raises(stt.STTNotConfigured):
        stt.resolve()


def test_stt_never_binds_to_a_provider_that_cannot_transcribe(client):
    """Anthropic and Gemini do not serve /audio/transcriptions. Binding to one
    would turn a config mistake into a runtime failure that looks like a broken
    microphone."""
    from app.services.ai import stt
    assert "anthropic" not in stt.STT_CAPABLE_TYPES
    assert "gemini" not in stt.STT_CAPABLE_TYPES
    assert set(stt.STT_CAPABLE_TYPES) <= {"openai", "openai_compatible"}


def test_stt_status_never_exposes_the_secret(client):
    from app.services.ai import stt, store
    _make_instance("SENTINEL-stt-secret-zzz")
    store._invalidate_runtime()
    blob = repr(stt.status())
    assert "SENTINEL" not in blob


def test_two_eligible_instances_are_ambiguous_so_it_does_not_guess(client):
    """With more than one candidate, picking one silently would bill an
    account the operator did not choose."""
    from app.db.queries import set_setting
    from app.services.ai import stt, store
    set_setting("ai_api_key", "legacy-key")
    set_setting("ai_api_base", "https://legacy.example/v1")
    _make_instance("secret-one", display="GW One")
    _make_instance("secret-two", display="GW Two")
    store._invalidate_runtime()
    assert stt.resolve()[3] == "legacy"            # refused to choose


# ── P1 #3 — dead Settings → AI model controls ───────────────────────────

def test_the_dead_model_inputs_are_gone_from_the_rendered_page(client):
    html = client.get("/secure-panel-inotex/settings/ai").text
    assert 'id="ai-conn-model-chat"' not in html
    assert 'id="ai-conn-model-classify"' not in html


def test_the_page_points_the_operator_at_ai_routing(client):
    html = client.get("/secure-panel-inotex/settings/ai").text
    assert "/secure-panel-inotex/ai/routing" in html


def test_the_still_meaningful_stt_model_field_remains(client):
    """`ai_model_stt` IS read at runtime, so it must not be removed with the
    two that were not."""
    html = client.get("/secure-panel-inotex/settings/ai").text
    assert 'id="ai-conn-model-stt"' in html


def test_posting_legacy_model_values_no_longer_writes_them(client):
    """The defect was a form that said 'saved' and changed nothing. Now the
    values are simply not persisted, so nothing can read a stale one back."""
    from app.db.queries import get_setting
    r = _post(client, "/admin/api/ai-connection", {
        "api_base": "https://example.test/v1", "api_key": "",
        "model_chat": "should-not-persist", "model_classify": "should-not-persist",
        "model_stt": "whisper-1", "feature_tts": True, "feature_stt": True,
        "search_backend": "tfidf", "default_lang": "fa"})
    assert r.status_code == 200
    assert get_setting("ai_model_chat", "") != "should-not-persist"
    assert get_setting("ai_model_classify", "") != "should-not-persist"
    assert get_setting("ai_model_stt", "") == "whisper-1"      # this one still works


def test_the_legacy_endpoint_still_accepts_an_old_client_payload(client):
    """Backward compatibility: an older cached admin page must not 422."""
    r = _post(client, "/admin/api/ai-connection", {
        "api_base": "https://example.test/v1", "api_key": "",
        "model_chat": "x", "model_classify": "y", "model_stt": "whisper-1",
        "feature_tts": True, "feature_stt": True,
        "search_backend": "tfidf", "default_lang": "fa"})
    assert r.status_code == 200


def test_the_api_reports_the_legacy_model_fields_as_deprecated(client):
    d = client.get("/admin/api/ai-connection").json()
    assert d["model_chat_deprecated"] is True
    assert d["model_classify_deprecated"] is True
    assert d["routing_url"] == "/secure-panel-inotex/ai/routing"


def test_the_ai_kill_switch_still_works(client):
    """Unrelated Settings → AI behaviour must survive the cleanup."""
    r = _post(client, "/admin/api/toggle_openai", {"enabled": False})
    assert r.status_code == 200
    assert client.get("/admin/api/settings").json()["openai_enabled"] is False


def test_the_settings_endpoint_still_requires_csrf(client):
    r = client.post("/admin/api/ai-connection", json={
        "api_base": "", "api_key": "", "model_chat": "", "model_classify": "",
        "model_stt": "", "feature_tts": True, "feature_stt": True,
        "search_backend": "tfidf", "default_lang": "fa"})
    assert r.status_code == 403


def test_a_provider_error_echoing_the_stt_key_cannot_reach_a_log(client):
    """Rejected-credential responses commonly quote the key they rejected.

    The transcription path wrote raw vendor output to the stdlib logger,
    bypassing the redaction every other provider path gets. `resolve()` now
    registers the live key, so it is stripped by exact value regardless of
    whether any regex recognises that vendor's key shape.
    """
    from app.db.queries import set_setting
    from app.services import applog
    from app.services.ai import stt, store

    applog.forget_secrets()
    set_setting("ai_api_base", "https://legacy.example/v1")
    set_setting("ai_api_key", "zzUNGUESSABLESHAPE9911")   # matches no key regex
    store._invalidate_runtime()
    _base, key, _m, source = stt.resolve()
    assert source == "legacy"

    echoed = f'{{"error":{{"message":"Invalid key: {key}"}}}}'
    assert key not in applog.scrub_text(echoed)
    assert "[redacted]" in applog.scrub_text(echoed)
    applog.forget_secrets()
