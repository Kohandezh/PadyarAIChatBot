"""Activity-field autofill: the companies-page button's backend contract.

The rule under test, from app/services/company_autofill.py: the model only
SUGGESTS — the code validates every label against the facet reader's own
limits (≤ 8 tokens, ≤ 70 chars, ≤ 3 labels), and the UPDATE carries
`AND COALESCE(activity_field,'')=''` so organizer data is never overwritten.

The scenario the whole feature exists for: a company arrives with a real
intro text but no حوزهٔ فعالیت, is invisible in every field-filtered chat
list, and a non-technical operator clears the backlog with one button.
"""
import datetime
import secrets
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "autofill.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    from app.main import app
    with TestClient(app) as c:
        from app.db.connection import get_db_connection
        conn = get_db_connection()
        # Two fillable rows (text but no field), one dark row (neither), one
        # already-labelled row that the run must not touch.
        conn.execute("INSERT INTO companies (id, title, text) VALUES"
                     " ('co-a', 'شرکت آ', 'متن معرفی آ')"
                     ", ('co-b', 'شرکت ب', 'متن معرفی ب')"
                     ", ('co-dark', 'شرکت تاریک', '')")
        conn.execute("INSERT INTO companies (id, title, text, activity_field)"
                     " VALUES ('co-done', 'شرکت کامل', 'متن کامل', 'هوش مصنوعی')")
        token = secrets.token_hex(16)
        conn.execute("INSERT OR IGNORE INTO admins (username, password_hash, salt,"
                     " security_question, security_answer_hash)"
                     " VALUES ('padmin','x','y','q','z')")
        conn.execute("INSERT INTO admin_sessions (token, username, expiry)"
                     " VALUES (?,?,?)",
                     (token, "padmin",
                      (datetime.datetime.utcnow() + datetime.timedelta(hours=1)).isoformat()))
        conn.commit()
        conn.close()
        c.cookies.set("admin_session", token)
        from app.auth.csrf import token_for_session
        c.headers["X-CSRF-Token"] = token_for_session(token)
        yield c


def _fake_classify(labels):
    """Patch _classify with a model that always returns `labels`."""
    async def fake(company, vocabulary):
        return list(labels), SimpleNamespace(tokens_total=10, cost=0.001,
                                             finish_reason="stop")
    return fake


def _field(admin_client, company_id):
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    try:
        row = conn.execute("SELECT activity_field FROM companies WHERE id = ?",
                           (company_id,)).fetchone()
        return row["activity_field"] if row else None
    finally:
        conn.close()


def test_preview_counts_the_backlog(admin_client):
    r = admin_client.get("/admin/api/company-profiles/autofill")
    assert r.status_code == 200
    assert r.json() == {"fillable": 2, "no_text": 1}


def test_preview_is_not_swallowed_by_the_wildcard_route(admin_client):
    """The literal /autofill path must be declared before /{dataset_id}:
    matched after it, the same URL returns a profile dict and the button's
    count dies silently. This test fails on exactly that wiring bug."""
    r = admin_client.get("/admin/api/company-profiles/autofill")
    assert "fillable" in r.json()


def test_run_fills_only_empty_fields_and_reports(admin_client, monkeypatch):
    from app.services import company_autofill
    monkeypatch.setattr(company_autofill, "_classify",
                        _fake_classify(["هوش مصنوعی"]))

    r = admin_client.post("/admin/api/company-profiles/autofill")
    assert r.status_code == 200
    data = r.json()
    assert {f["id"] for f in data["filled"]} == {"co-a", "co-b"}
    assert data["failed"] == []
    assert data["no_text"] == 1

    assert _field(admin_client, "co-a") == "هوش مصنوعی"
    assert _field(admin_client, "co-b") == "هوش مصنوعی"
    # Organizer data survives the run untouched.
    assert _field(admin_client, "co-done") == "هوش مصنوعی"
    assert _field(admin_client, "co-dark") == ""


def test_run_hard_validates_model_labels(admin_client, monkeypatch):
    from app.services import company_autofill
    monkeypatch.setattr(company_autofill, "_classify", _fake_classify([
        "این برچسب بسیار طولانی است و قطعاً از حد هشت کلمه مجاز فراتر رفته است",
        "باتری",
        42,
        "سایر", "برق", "اپتیک", "خوبه",
    ]))

    r = admin_client.post("/admin/api/company-profiles/autofill")
    assert r.status_code == 200
    assert all(f["id"] != "co-a" or f["labels"] == ["باتری", "سایر", "برق"]
               for f in r.json()["filled"])
    assert _field(admin_client, "co-a") == "باتری | سایر | برق"


def test_run_reports_company_the_model_failed_on(admin_client, monkeypatch):
    from app.services import company_autofill
    monkeypatch.setattr(company_autofill, "_classify", _fake_classify([]))

    r = admin_client.post("/admin/api/company-profiles/autofill")
    assert r.status_code == 200
    data = r.json()
    assert data["filled"] == []
    assert {f["id"] for f in data["failed"]} == {"co-a", "co-b"}
    assert _field(admin_client, "co-a") == ""


def test_ai_unavailable_is_503_and_writes_nothing(admin_client, monkeypatch):
    from app.services import company_autofill

    async def dead(company, vocabulary):
        raise company_autofill.AutofillUnavailable("هوش مصنوعی در دسترس نیست (x).")
    monkeypatch.setattr(company_autofill, "_classify", dead)

    r = admin_client.post("/admin/api/company-profiles/autofill")
    assert r.status_code == 503
    assert _field(admin_client, "co-a") == ""


def test_companies_page_carries_the_button(admin_client):
    r = admin_client.get("/secure-panel-inotex/companies")
    assert r.status_code == 200
    assert 'id="autofill-btn"' in r.text
    assert 'id="autofill-count"' in r.text
