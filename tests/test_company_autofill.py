"""Company autofill: the companies-page button's backend contract.

The rule under test, from app/services/company_autofill.py: the model only
SUGGESTS — the code validates every field's own shape, and every write lands
only in a column that is still empty (per-field COALESCE(field,'')='' guard),
so organizer data is never overwritten and the run is re-runnable. What the
intro text does not mention comes back empty and stays empty — absence is
not an error. The three English fields are TRANSLATED by the model, not
extracted.

The scenario the whole feature exists for: companies arrive with a real
intro text but half-empty profile columns (a blank workbook cell, an approved
booth proposal that wrote nothing but the text), and a non-technical
operator clears the backlog with one button.
"""
import datetime
import secrets
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient


# A row with every autofill target already full: the run must not touch it.
FULL = {
    "contact_name": "مسئول کامل", "contact_position": "مدیر",
    "contact_mobile": "09120000000", "email": "done@example.com",
    "website": "https://done.example.com", "company_phone": "02112345678",
    "fax": "02187654321", "address": "تهران، خیابان نمونه",
    "address_en": "Tehran", "province": "تهران", "booth_number": "12",
    "hall": "سالن نمونه", "company_type": "غرفه‌دار",
    "org_stage": "بالغ", "activity_field": "برق",
    "participation": "غرفه‌ای", "title_en": "Done Co",
    "text_en": "Done.",
}

# The model's answer for the مهرکالا-style intro: one value for every
# extraction field, translated English fields, labels as a list.
GOOD = {
    "contact_name": "رامین مهر انور", "contact_position": "مدیرعامل",
    "contact_mobile": "09121234567", "email": "info@mehrkala.example.com",
    "website": "https://mehrkala.example.com", "company_phone": "02188776655",
    "fax": "02188776656", "address": "تهران، خیابان ولیعصر",
    "address_en": "Valiasr St., Tehran", "province": "تهران",
    "booth_number": "31A", "hall": "میلاد پایین",
    "company_type": "غرفه‌دار", "org_stage": "بالغ",
    "activity_field": ["لوازم جانبی موبایل", "گجت هوشمند"],
    "participation": "غرفه‌ای",
    "title_en": "Arman Tejarat Mehrkala",
    "text_en": "Arman Tejarat Mehrkala sells mobile and computer accessories and smart gadgets.",
}


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "autofill.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    from app.main import app
    with TestClient(app) as c:
        from app.db.connection import get_db_connection
        conn = get_db_connection()
        # Two fillable rows (text but no fields), one dark row (no text to
        # read anything out of), one fully-full row the run must not touch,
        # and one row whose ONLY hole is the English name — the translated
        # half of the contract.
        conn.execute("INSERT INTO companies (id, title, text) VALUES"
                     " ('co-a', 'شرکت آ', 'متن معرفی آ')"
                     ", ('co-b', 'شرکت ب', 'متن معرفی ب')"
                     ", ('co-dark', 'شرکت تاریک', '')")
        conn.execute(
            "INSERT INTO companies (id, title, text, " + ", ".join(FULL) + ")"
            " VALUES ('co-done', 'شرکت کامل', 'متن کامل', "
            + ", ".join(["?"] * len(FULL)) + ")", tuple(FULL.values()))
        partial = dict(FULL, title_en="")
        conn.execute(
            "INSERT INTO companies (id, title, text, " + ", ".join(partial) + ")"
            " VALUES ('co-en', 'شرکت انگلیسی‌مانده', 'متن انگلیسی‌مانده', "
            + ", ".join(["?"] * len(partial)) + ")", tuple(partial.values()))
        token = secrets.token_hex(16)
        conn.execute("INSERT OR IGNORE INTO admins (username, password_hash, salt,"
                     " security_question, security_answer_hash)"
                     " VALUES ('padmin','x','y','q','z')")
        conn.execute("INSERT INTO admin_sessions (token, username, expiry)"
                     " VALUES (?,?,?)",
                     (token, "padmin",
                      (datetime.datetime.now(datetime.timezone.utc)
                       + datetime.timedelta(hours=1)).isoformat()))
        conn.commit()
        conn.close()
        c.cookies.set("admin_session", token)
        from app.auth.csrf import token_for_session
        c.headers["X-CSRF-Token"] = token_for_session(token)
        yield c


def _fake_classify(fields):
    """Patch _classify with a model that always returns `fields`."""
    async def fake(company, empty_fields, vocabulary):
        return dict(fields), SimpleNamespace(tokens_total=10, cost=0.001,
                                             finish_reason="stop")
    return fake


def _row(company_id):
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    try:
        row = conn.execute("SELECT * FROM companies WHERE id = ?",
                           (company_id,)).fetchone()
        return dict(row) if row else {}
    finally:
        conn.close()


def test_preview_counts_the_backlog(admin_client):
    r = admin_client.get("/admin/api/company-profiles/autofill")
    assert r.status_code == 200
    assert r.json() == {"fillable": 3, "no_text": 1}


def test_preview_is_not_swallowed_by_the_wildcard_route(admin_client):
    """The literal /autofill path must be declared before /{dataset_id}:
    matched after it, the same URL returns a profile dict and the button's
    count dies silently. This test fails on exactly that wiring bug."""
    r = admin_client.get("/admin/api/company-profiles/autofill")
    assert "fillable" in r.json()


def test_run_fills_only_empty_fields_and_reports(admin_client, monkeypatch):
    from app.services import company_autofill
    monkeypatch.setattr(company_autofill, "_classify", _fake_classify(GOOD))

    r = admin_client.post("/admin/api/company-profiles/autofill")
    assert r.status_code == 200
    data = r.json()
    assert {f["id"] for f in data["filled"]} == {"co-a", "co-b", "co-en"}
    assert data["failed"] == []
    assert data["no_text"] == 1

    row = _row("co-a")
    assert row["contact_name"] == "رامین مهر انور"
    assert row["contact_position"] == "مدیرعامل"
    assert row["email"] == "info@mehrkala.example.com"
    assert row["activity_field"] == "لوازم جانبی موبایل | گجت هوشمند"
    assert row["title_en"] == "Arman Tejarat Mehrkala"
    assert row["text_en"] == GOOD["text_en"]

    # The report names the columns it wrote, so the log is reviewable.
    entry = next(f for f in data["filled"] if f["id"] == "co-a")
    assert set(entry["fields"]) >= {"contact_name", "email", "activity_field",
                                    "title_en", "text_en"}

    # A company whose only hole was the English name gets exactly that one.
    en = _row("co-en")
    assert en["title_en"] == "Arman Tejarat Mehrkala"
    assert en["email"] == FULL["email"]          # the rest was already full
    en_entry = next(f for f in data["filled"] if f["id"] == "co-en")
    assert en_entry["fields"] == ["title_en"]

    # Organizer data survives the run untouched.
    done = _row("co-done")
    for field, value in FULL.items():
        assert done[field] == value
    assert _row("co-dark")["activity_field"] == ""


def test_run_hard_validates_model_fields(admin_client, monkeypatch):
    from app.services import company_autofill
    monkeypatch.setattr(company_autofill, "_classify", _fake_classify({
        "email": "not-an-email",              # no @ — cannot be mailed
        "contact_mobile": "سلام",             # letters, not a phone
        "website": "not a website",           # a space, not a URL
        "company_phone": "09121234567890123456",  # 20 digits — junk
        "activity_field": ["این برچسب بسیار طولانی است و قطعاً از حد هشت کلمه"
                           " مجاز فراتر رفته است", "باتری", 42,
                           "سایر", "برق", "اپتیک", "خوبه"],
        "contact_name": "رامین مهر انور",      # valid — must survive
        "title_en": "Arman Tejarat Mehrkala",  # valid — must survive
    }))

    r = admin_client.post("/admin/api/company-profiles/autofill")
    assert r.status_code == 200
    row = _row("co-a")
    assert row["email"] == ""
    assert row["contact_mobile"] == ""
    assert row["website"] == ""
    assert row["company_phone"] == ""
    assert row["activity_field"] == "باتری | سایر | برق"
    assert row["contact_name"] == "رامین مهر انور"
    assert row["title_en"] == "Arman Tejarat Mehrkala"


def test_run_reports_company_the_model_failed_on(admin_client, monkeypatch):
    from app.services import company_autofill
    monkeypatch.setattr(company_autofill, "_classify", _fake_classify({}))

    r = admin_client.post("/admin/api/company-profiles/autofill")
    assert r.status_code == 200
    data = r.json()
    assert data["filled"] == []
    assert {f["id"] for f in data["failed"]} == {"co-a", "co-b", "co-en"}
    assert _row("co-a")["contact_name"] == ""


def test_ai_unavailable_is_503_and_writes_nothing(admin_client, monkeypatch):
    from app.services import company_autofill

    async def dead(company, empty_fields, vocabulary):
        raise company_autofill.AutofillUnavailable("هوش مصنوعی در دسترس نیست (x).")
    monkeypatch.setattr(company_autofill, "_classify", dead)

    r = admin_client.post("/admin/api/company-profiles/autofill")
    assert r.status_code == 503
    assert _row("co-a")["contact_name"] == ""


def test_write_yields_to_a_mid_run_organizer_edit(admin_client, monkeypatch):
    """The organizer edits a field while the model is thinking: THEIR value
    stays, the model's other fields still land, and the per-company report
    does not claim the field it yielded. A whole-row guard here would also
    leave the UI loop forever pending on the same company."""
    from app.db.connection import get_db_connection

    async def editing(company, empty_fields, vocabulary):
        conn = get_db_connection()
        conn.execute("UPDATE companies SET email = 'hand@example.com'"
                     " WHERE id = 'co-a'")
        conn.commit()
        conn.close()
        return dict(GOOD), SimpleNamespace(tokens_total=10, cost=0.001,
                                           finish_reason="stop")

    from app.services import company_autofill
    monkeypatch.setattr(company_autofill, "_classify", editing)

    r = admin_client.post("/admin/api/company-profiles/autofill")
    assert r.status_code == 200
    row = _row("co-a")
    assert row["email"] == "hand@example.com"             # organizer wins
    assert row["contact_name"] == GOOD["contact_name"]    # the rest still lands
    entry = next(f for f in r.json()["filled"] if f["id"] == "co-a")
    assert "email" not in entry["fields"]


def test_full_field_echoes_are_dropped_not_written(admin_client, monkeypatch):
    """The elecomp failure, 2026-08-31: for a company whose ONLY hole is
    title_en, the model echoed already-full columns (email etc.) instead —
    nothing intersected the hole and every write came back empty while the
    pending count never moved. A value for a column that was not asked
    about must be dropped unread and reported as nothing-in-text, never
    written over organizer data."""
    from app.services import company_autofill
    monkeypatch.setattr(company_autofill, "_classify",
                        _fake_classify({"email": "info@x.example.com"}))

    r = admin_client.post("/admin/api/company-profiles/autofill")
    assert r.status_code == 200
    data = r.json()
    # co-en's only hole (title_en) was never answered: honest-empty, failed.
    entry = next(f for f in data["failed"] if f["id"] == "co-en")
    assert entry["reason"] == "در متن معرفی چیزی برای فیلدهای خالی نبود"
    assert _row("co-en")["title_en"] == ""
    # co-a's email IS one of its holes: that one lands.
    a = next(f for f in data["filled"] if f["id"] == "co-a")
    assert a["fields"] == ["email"]
    assert _row("co-a")["email"] == "info@x.example.com"


def test_scan_skips_past_no_yield_companies(admin_client, monkeypatch):
    """A company whose text yields nothing must not strand the queue behind
    it: the batch counts FILLS, not scans, so the run continues past it and
    the next companies still get their turn."""
    from app.services import company_autofill

    async def sometimes(company, empty_fields, vocabulary):
        fields = ({} if company["id"] == "co-a"
                  else {"email": "info@y.example.com",
                        "title_en": "Y Co"})
        return fields, SimpleNamespace(tokens_total=10, cost=0.001,
                                       finish_reason="stop")
    monkeypatch.setattr(company_autofill, "_classify", sometimes)

    r = admin_client.post("/admin/api/company-profiles/autofill")
    assert r.status_code == 200
    data = r.json()
    assert [f["id"] for f in data["failed"]] == ["co-a"]
    assert {f["id"] for f in data["filled"]} == {"co-en", "co-b"}
    assert data["remaining"] == 0
    assert _row("co-a")["email"] == ""


def test_cursor_resumes_after_the_last_examined_company(admin_client, monkeypatch):
    """The pass cursor is what keeps a no-yield stretch from being re-asked
    every batch (elecomp, 2026-08-31: 37 companies re-asked per batch while
    ~700 behind them were never reached). The second POST must resume AFTER
    the first one's cursor, not at the queue head."""
    from app.services import company_autofill

    asked = []

    async def once(company, empty_fields, vocabulary):
        asked.append(company["id"])
        return ({} if company["id"] == "co-a"
                else {"title_en": "Cursor Co"}), \
            SimpleNamespace(tokens_total=10, cost=0.001, finish_reason="stop")
    monkeypatch.setattr(company_autofill, "_classify", once)

    first = admin_client.post("/admin/api/company-profiles/autofill").json()
    assert first["cursor"] and first["pass_complete"] is True
    # co-a yielded nothing; co-en and co-b filled. Pass reached the end.
    assert asked == ["co-a", "co-en", "co-b"]

    # Re-ask with the same cursor shape a UI would forward: whatever is
    # still pending AFTER that cursor (nothing here) — never the head again.
    second = admin_client.post(
        "/admin/api/company-profiles/autofill",
        json={"cursor": first["cursor"]}).json()
    assert second["filled"] == [] and second["failed"] == []
    assert asked == ["co-a", "co-en", "co-b"]      # nothing was re-asked


def test_companies_page_carries_the_button(admin_client):
    r = admin_client.get("/secure-panel-inotex/companies")
    assert r.status_code == 200
    assert 'id="autofill-btn"' in r.text
    assert 'id="autofill-count"' in r.text
    # The button promises what it does: every empty field, not just one.
    assert "پر کردن خودکار اطلاعات" in r.text
