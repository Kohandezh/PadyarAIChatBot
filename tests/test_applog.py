"""Central logging service — the guarantees that must never regress.

The three that matter most, and why each is a test rather than a comment:

  * A broken log sink must NOT break the caller. Logging sits on the OTP path
    and the chat path. If a full disk could raise out of applog.record(), a
    logging bug would take the chatbot down — the opposite of the point.
  * A secret must NEVER reach a row. Redaction lives in one place so a call
    site added later cannot leak by forgetting; these tests are what keep that
    single place honest.
  * Lowering operational retention must NOT erase audit or security evidence.
    Otherwise an administrator can hide their own destructive actions by
    editing a number in a settings form.
"""
import json
from datetime import datetime, timedelta, timezone

import pytest


@pytest.fixture
def L(tmp_path, monkeypatch):
    """The service, pointed at throwaway databases."""
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "chat.db"))
    monkeypatch.setattr(config, "LOGS_DB_PATH", str(tmp_path / "logs.db"))
    from app.db.connection import init_db
    init_db()
    from app.services import applog
    applog.ensure_tables()
    # Storm suppression keeps state between calls; a leftover window from an
    # earlier test would silently drop this test's rows.
    applog._recent.clear()
    return applog


def _all(L):
    rows, total = L.query(tables=list(L.TABLES), limit=500)
    return rows, total


# ── Rule 1: never fatal ─────────────────────────────────────────────────

def test_record_returns_none_instead_of_raising_when_the_store_is_unusable(tmp_path, monkeypatch):
    """The single most important property. A directory is not a database."""
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "chat.db"))
    monkeypatch.setattr(config, "LOGS_DB_PATH", str(tmp_path))  # a directory
    from app.services import applog
    assert applog.record("system", "boom") is None       # no exception


def test_query_degrades_to_empty_instead_of_raising(tmp_path, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "LOGS_DB_PATH", str(tmp_path))
    from app.services import applog
    assert applog.query() == ([], 0)


# ── Table routing ───────────────────────────────────────────────────────

@pytest.mark.parametrize("category,expected", [
    ("audit", "audit_logs"), ("security", "security_events"),
    ("service", "service_events"), ("llm", "app_logs"),
    ("sms", "app_logs"), ("auth", "app_logs"), ("system", "app_logs"),
])
def test_category_routes_to_its_table(L, category, expected):
    assert L.table_for(category) == expected
    L.record(category, "routing.check")
    rows, _ = _all(L)
    assert any(r["source"] == expected for r in rows)


def test_unknown_category_and_level_fall_back(L):
    L.record("not-a-category", "e", level="not-a-level")
    rows, _ = _all(L)
    assert rows[0]["category"] == "system"
    assert rows[0]["level"] == "info"


# ── Redaction: no secret may ever be persisted ──────────────────────────

SECRETS = {
    "password": "hunter2-plaintext",
    "api_key": "sk-livedeadbeefcafe1234",
    "access_token": "at-should-never-persist",
    "refresh_token": "rt-should-never-persist",
    "client_secret": "cs-should-never-persist",
    "authorization": "Bearer eyJhbGciOiJIUzI1NiJ9.payload.sig",
    "cookie": "admin_session=abcdef123456",
    "session_token": "st-should-never-persist",
    "otp_code": "482913",
    "verification_code": "913482",
    "private_key": "-----BEGIN PRIVATE KEY-----abcdef",
    "security_answer": "my-mothers-maiden-name",
}


def test_no_secret_value_reaches_a_row(L):
    L.record("auth", "auth.login.failed", metadata=dict(SECRETS))
    blob = json.dumps(_all(L)[0], ensure_ascii=False)
    for key, value in SECRETS.items():
        assert value not in blob, f"LEAKED {key}: {value!r} was persisted"


def test_secrets_nested_in_dicts_and_lists_are_redacted(L):
    L.record("auth", "nested", metadata={
        "outer": {"inner": {"password": "deep-secret-1"}},
        "list": [{"api_key": "deep-secret-2"}],
    })
    blob = json.dumps(_all(L)[0], ensure_ascii=False)
    assert "deep-secret-1" not in blob
    assert "deep-secret-2" not in blob


def test_credential_shapes_in_free_text_are_stripped(L):
    """A secret pasted into a message, not passed as a keyed field."""
    L.record("system", "freetext",
             message="key sk-livedeadbeefcafe9999 and Bearer abc123DEFghi456jkl789 "
                     "and enc:gAAAAABqggNjIrM43cDMQUqbkLAiz")
    msg = _all(L)[0][0]["message"]
    assert "sk-live" not in msg
    assert "abc123DEFghi456jkl789" not in msg
    assert "enc:gAAAAAB" not in msg


def test_phone_and_email_are_masked_in_text_and_metadata(L):
    L.record("otp", "pii", message="visitor 09122723024 signed up",
             metadata={"contact": "+989122723024", "mail": "someone@example.com"})
    row = _all(L)[0][0]
    assert "09122723024" not in row["message"]
    assert "0912***3024" in row["message"]
    assert "989122723024" not in row["metadata"]
    assert "someone@example.com" not in row["metadata"]


# ── Log injection: a visitor must not forge a log line ──────────────────

def test_newlines_control_chars_and_ansi_are_neutralised(L):
    hostile = "real\nFAKE critical entry\r\n\x1b[31mred\x00nul\x07bell"
    L.record("api", "injection", message=hostile,
             metadata={"ua": hostile})
    row = _all(L)[0][0]
    for bad in ("\n", "\r", "\x1b", "\x00", "\x07"):
        assert bad not in row["message"], f"{bad!r} survived into the message"
        assert bad not in row["metadata"], f"{bad!r} survived into metadata"


# ── Content policy ──────────────────────────────────────────────────────

@pytest.mark.parametrize("policy,expect_empty", [
    ("metadata", True), ("redacted", False), ("full", False)])
def test_content_policy_governs_persisted_conversation_text(L, policy, expect_empty):
    from app.db.queries import set_setting
    set_setting("log_content_policy", policy)
    out = L.apply_content_policy("a visitor question " * 40)
    assert (out == "") is expect_empty


def test_full_policy_still_strips_credentials(L):
    """"full" means full CONTENT. It never means full SECRETS."""
    from app.db.queries import set_setting
    set_setting("log_content_policy", "full")
    assert "sk-live" not in L.apply_content_policy("my key is sk-livedeadbeefcafe1234")


# ── Storm protection ────────────────────────────────────────────────────

def test_identical_operational_rows_are_suppressed(L):
    for _ in range(200):
        L.record("llm", "llm.request.failed", level="error")
    _, total = _all(L)
    assert total <= L._SUPPRESS_THRESHOLD + 1, f"no suppression: {total} rows written"


def test_security_and_audit_are_never_suppressed(L):
    """Evidence is not sampled away, however loud the attack is."""
    for _ in range(60):
        L.security("security.rate_limit.triggered")
        L.audit("admin.action")
    rows, _ = _all(L)
    assert sum(r["source"] == "security_events" for r in rows) == 60
    assert sum(r["source"] == "audit_logs" for r in rows) == 60


# ── Level gating ────────────────────────────────────────────────────────

def test_debug_rows_are_dropped_unless_explicitly_enabled(L):
    from app.db.queries import set_setting
    L.debug("system", "quiet")
    assert _all(L)[1] == 0
    set_setting("log_debug_enabled", "true")
    L.debug("system", "now.kept")
    assert _all(L)[1] == 1


def test_min_level_suppresses_lower_severities(L):
    from app.db.queries import set_setting
    set_setting("log_min_level", "error")
    L.info("system", "dropped")
    L.error("system", "kept")
    rows, total = _all(L)
    assert total == 1 and rows[0]["event_name"] == "kept"


# ── Correlation ─────────────────────────────────────────────────────────

def test_correlation_id_is_inherited_from_the_request_context(L):
    L.set_request_context(request_id="req-1", correlation_id="corr-1", ip="10.0.0.9")
    L.record("chat", "chat.message.received")
    row = _all(L)[0][0]
    assert row["request_id"] == "req-1"
    assert row["correlation_id"] == "corr-1"
    assert row["ip"] == "10.0.0.9"


def test_related_reconstructs_the_operation_chain(L):
    L.set_request_context(correlation_id="chain-42")
    L.record("chat", "chat.message.received")
    L.record("retrieval", "retrieval.search.completed")
    L.record("llm", "llm.request.completed")
    rows, _ = _all(L)
    chain, key = L.related(rows[0])
    assert key == "correlation_id"
    assert len(chain) == 3


# ── Retention: three independent windows ────────────────────────────────

def _backdate(L, table, event, days):
    conn = L.get_logs_connection()
    old = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    conn.execute(f"UPDATE {table} SET created_at = ? WHERE event_name = ?", (old, event))
    conn.commit()
    conn.close()


def test_defaults_are_90_operational_and_365_for_evidence(L):
    assert L.retention_days() == 90
    assert L.audit_retention_days() == 365
    assert L.security_retention_days() == 365


def test_lowering_operational_retention_cannot_erase_evidence(L):
    """The anti-tamper guarantee, stated as a test."""
    from app.db.queries import set_setting
    L.record("system", "ops.old")
    L.audit("audit.old")
    L.security("sec.old")
    for table, event in (("app_logs", "ops.old"), ("audit_logs", "audit.old"),
                         ("security_events", "sec.old")):
        _backdate(L, table, event, 200)

    set_setting("log_retention_days", "1")     # an administrator covering tracks
    L.purge_expired()

    rows, _ = _all(L)
    events = {r["event_name"] for r in rows}
    assert "ops.old" not in events, "operational row should have expired"
    assert "audit.old" in events, "AUDIT evidence was destroyed by the ops setting"
    assert "sec.old" in events, "SECURITY evidence was destroyed by the ops setting"


def test_retention_zero_keeps_everything(L):
    from app.db.queries import set_setting
    set_setting("log_retention_days", "0")
    L.record("system", "ancient")
    _backdate(L, "app_logs", "ancient", 5000)
    L.purge_expired()
    assert _all(L)[1] == 1


def test_a_garbage_retention_setting_falls_back_to_the_default(L):
    from app.db.queries import set_setting
    set_setting("log_retention_days", "not-a-number")
    assert L.retention_days() == 90


def test_a_row_inside_the_window_survives_the_purge(L):
    from app.db.queries import set_setting
    set_setting("log_retention_days", "30")
    L.record("system", "fresh")
    L.record("system", "stale")
    _backdate(L, "app_logs", "stale", 200)
    L.purge_expired()
    rows, _ = _all(L)
    assert {r["event_name"] for r in rows} == {"fresh"}


# ── Query: filters, paging, and injection ───────────────────────────────

def test_filters_narrow_the_result_set(L):
    L.record("llm", "a", provider="openai", model="gpt-4.1", actor="x",
             ip="1.1.1.1", http_status=200, duration_ms=50, outcome="ok")
    L.record("sms", "b", provider="asanak", actor="y", ip="2.2.2.2",
             http_status=500, duration_ms=5000, outcome="failed")
    assert L.query(category="llm")[1] == 1
    assert L.query(provider="asanak")[1] == 1
    assert L.query(model="gpt-4.1")[1] == 1
    assert L.query(actor="y")[1] == 1
    assert L.query(ip="1.1.1.1")[1] == 1
    assert L.query(http_status="500")[1] == 1
    assert L.query(outcome="failed")[1] == 1
    assert L.query(min_duration="1000")[1] == 1


def test_free_text_search_covers_message_event_and_metadata(L):
    L.record("system", "needle.event", message="haystack body",
             metadata={"note": "buried treasure"})
    assert L.query(q="needle")[1] == 1
    assert L.query(q="haystack")[1] == 1
    assert L.query(q="treasure")[1] == 1
    assert L.query(q="absent-string")[1] == 0


def test_pagination_pages_do_not_overlap(L):
    for i in range(12):
        L.record("system", f"row-{i:02d}")
    first, total = L.query(limit=5, offset=0)
    second, _ = L.query(limit=5, offset=5)
    assert total == 12
    assert not ({r["id"] for r in first} & {r["id"] for r in second})


def test_limit_is_clamped(L):
    for i in range(3):
        L.record("system", f"r{i}")
    rows, _ = L.query(limit=99999)
    assert len(rows) <= 500


@pytest.mark.parametrize("evil", [
    "'; DROP TABLE app_logs; --",
    "1 OR 1=1",
    "\" UNION SELECT * FROM sqlite_master --",
])
def test_sql_injection_through_every_string_filter_is_inert(L, evil):
    L.record("system", "survivor")
    L.query(q=evil)
    L.query(category=evil)
    L.query(actor=evil)
    L.query(sort=evil)
    L.query(direction=evil)
    # The table and its row must both still be there.
    assert L.query(q="survivor")[1] == 1


def test_an_unapproved_sort_column_falls_back(L):
    L.record("system", "x")
    rows, _ = L.query(sort="metadata; DROP TABLE app_logs")
    assert len(rows) == 1


def test_summary_is_safe_on_an_empty_store(L):
    out = L.summary(7)
    assert out["by_category"] == {} and out["retention"]["operational_days"] == 90


# ── Truncate ────────────────────────────────────────────────────────────

def test_truncate_by_category_leaves_other_categories(L):
    L.record("llm", "keep-me")
    L.record("sms", "delete-me")
    assert L.truncate(category="sms") == 1
    rows, _ = _all(L)
    assert {r["event_name"] for r in rows} == {"keep-me"}


def test_count_matching_predicts_the_deletion(L):
    for i in range(4):
        L.record("sms", f"s{i}")
    predicted = L.count_matching(category="sms")
    assert predicted == 4
    assert L.truncate(category="sms") == predicted


# ── Export ──────────────────────────────────────────────────────────────

def test_csv_export_starts_with_a_bom_and_a_header(L):
    L.record("system", "exported")
    out = "".join(L.export_csv())
    assert out.startswith("﻿")
    assert "event_name" in out.splitlines()[0]
    assert "exported" in out


def test_csv_export_neutralises_spreadsheet_formulas(L):
    """A visitor's user-agent must not execute when Excel opens the export."""
    L.record("api", "formula", message="=cmd|'/c calc'!A1")
    cells = "".join(L.export_csv())
    assert "=cmd" not in cells or "'=cmd" in cells or '"\'=cmd' in cells
    assert not any(line.startswith("=") for line in cells.splitlines())


def test_json_export_parses_and_honours_filters(L):
    L.record("llm", "in-scope")
    L.record("sms", "out-of-scope")
    parsed = json.loads("".join(L.export_json(category="llm")))
    assert [r["event_name"] for r in parsed] == ["in-scope"]


def test_export_contains_no_secret(L):
    L.record("auth", "leaky", metadata={"password": "never-export-me"})
    assert "never-export-me" not in "".join(L.export_csv())
    assert "never-export-me" not in "".join(L.export_json())
