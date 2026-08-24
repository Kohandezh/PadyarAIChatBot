"""Registering a company that is not in the knowledge base (SPEC group M).

The thing these tests exist to hold down is one sentence: a company created at
a booth must not become an answer the chatbot gives the public before a human
has read it. Everything else here (the OTP, the invite, the duplicate refusal)
is the normal flow, and is asserted because this path is the one place where a
company name is typed rather than chosen.
"""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def paths(tmp_path, monkeypatch):
    """A throwaway install with an empty knowledge base."""
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "leads.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)

    from app.db.connection import init_db
    init_db()
    from app.services import applog
    applog.ensure_tables()
    from app.services import leads as leads_service
    leads_service.ensure_tables()
    return tmp_path


@pytest.fixture(autouse=True)
def _no_rate_limit(monkeypatch):
    """The suite fires a whole booth's worth of requests in a second."""
    import app.routers.leads as leads_router
    monkeypatch.setattr(leads_router, "check_rate_limit",
                        lambda request, key=None, limit=None: None)


@pytest.fixture
def outbox(monkeypatch):
    """Capture the delivered code instead of texting anyone."""
    from app.services import otp as otp_service
    sent = []
    monkeypatch.setattr(otp_service, "_deliver",
                        lambda dest, code: sent.append((dest, code)))
    return sent


@pytest.fixture
def client(paths):
    from app.main import app
    with TestClient(app) as c:
        yield c


@pytest.fixture
def visitor(client):
    """A field visitor with the session cookie their personal link sets."""
    from app.services import leads as leads_service
    made = leads_service.create_visitor("همکار تست")
    assert client.get(f"/v/{made['code']}", follow_redirects=False).status_code == 303
    return made


def _admin(client):
    """A real admin session row, plus the CSRF header its mutations need."""
    import datetime
    import secrets
    from app.config import ADMIN_COOKIE_NAME
    from app.auth.csrf import token_for_session
    from app.db.connection import get_db_connection
    token = secrets.token_hex(16)
    expiry = datetime.datetime.now() + datetime.timedelta(hours=1)
    conn = get_db_connection()
    conn.execute("INSERT INTO admin_sessions (token, username, expiry) VALUES (?, ?, ?)",
                 (token, "tester", expiry.isoformat()))
    conn.commit()
    conn.close()
    client.cookies.set(ADMIN_COOKIE_NAME, token)
    client.headers.update({"X-CSRF-Token": token_for_session(token)})
    return token


def _row(dataset_id):
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    try:
        row = conn.execute("SELECT * FROM dataset WHERE id = ?", (dataset_id,)).fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


def _edits(dataset_id):
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM dataset_edits WHERE dataset_id = ? ORDER BY created_at",
            (dataset_id,)).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def _add_company(dataset_id, title):
    """A company that was already in the knowledge base before the exhibition."""
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    conn.execute(
        "INSERT INTO dataset (id, title, text, video_url, title_en, text_en, position)"
        " VALUES (?, ?, 'متن قدیمی', '', '', '', 10)", (dataset_id, title))
    conn.commit()
    conn.close()


PHONE = "09121110022"

BODY = {
    "title": "پارس فناوران آریا",
    "title_en": "Pars Fanavaran Aria",
    "text": "پارس فناوران آریا سازندهٔ تجهیزات آزمایشگاهی است.",
    "text_en": "Pars Fanavaran Aria builds laboratory equipment.",
    "first_name": "مینا",
    "last_name": "رضایی",
    "position": "مدیر فروش",
    "phone": PHONE,
}


def _create(client, **overrides):
    body = dict(BODY)
    body.update(overrides)
    return client.post("/api/leads/new-company", json=body)


# ── The row that reaches the public ─────────────────────────────────────

def test_the_typed_answer_never_lands_in_the_dataset(client, visitor, outbox):
    """The whole point of group M: `dataset.text` stays empty until approval."""
    created = _create(client)
    assert created.status_code == 200, created.text
    dataset_id = created.json()["dataset_id"]

    row = _row(dataset_id)
    assert row is not None
    assert row["text"] == ""
    # Titles are written straight in. A company name claims nothing.
    assert row["title"] == BODY["title"]
    assert row["title_en"] == BODY["title_en"]
    # The English text has no review path in this design, and goes in directly.
    assert row["text_en"] == BODY["text_en"]

    pending = _edits(dataset_id)
    assert len(pending) == 1
    assert pending[0]["status"] == "pending"
    assert pending[0]["new_text"] == BODY["text"]
    assert pending[0]["old_text"] == ""


def test_the_text_stays_out_of_the_chatbot_through_verification(client, visitor, outbox):
    """Verifying the number claims the company. It publishes nothing."""
    dataset_id = _create(client).json()["dataset_id"]
    lead_id = _last_lead()["id"]

    code = outbox[-1][1]
    verified = client.post("/api/leads/verify", json={"lead_id": lead_id, "code": code})
    assert verified.status_code == 200, verified.text
    assert verified.json()["qr"]
    assert _row(dataset_id)["text"] == ""


def test_an_admin_approval_is_what_publishes_it(client, visitor, outbox):
    dataset_id = _create(client).json()["dataset_id"]
    _admin(client)
    edits = client.get("/admin/api/leads/edits").json()["edits"]
    assert len(edits) == 1
    approved = client.post(f"/admin/api/leads/edits/{edits[0]['id']}", json={"approve": True})
    assert approved.status_code == 200, approved.text
    assert _row(dataset_id)["text"] == BODY["text"]


def test_a_blank_answer_queues_nothing(client, visitor, outbox):
    """Only the name is required. The company writes its own text later."""
    created = _create(client, text="", text_en="", title_en="", title="شرکت بی‌متن")
    assert created.status_code == 200, created.text
    dataset_id = created.json()["dataset_id"]
    assert _row(dataset_id)["text"] == ""
    assert _edits(dataset_id) == []


# ── The refusals ────────────────────────────────────────────────────────

def test_a_company_already_in_the_list_is_refused(client, visitor, outbox):
    """Compared on the normalised name, so a half-space is not a new company."""
    _add_company("pars-existing", "پارس فناوران آریا")
    refused = _create(client, title="پارس‌فناوران آريا")
    assert refused.status_code == 409, refused.text
    assert refused.json()["code"] == "company_exists"
    assert "جستجو" in refused.json()["detail"]
    # Nothing was created and no code was sent.
    assert outbox == []
    assert _count_dataset() == 1


def test_an_owned_company_is_refused_too(client, visitor, outbox):
    """The company search hides a company a colleague already registered, so
    this refusal is the only thing standing between "I cannot find it" and a
    second copy of the same company."""
    _add_company("pars-owned", "پارس فناوران آریا")
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    conn.execute(
        "INSERT INTO company_leads (id, dataset_id, company_name, visitor_id, status,"
        " created_at) VALUES ('other', 'pars-owned', 'پارس فناوران آریا', 'someone',"
        " 'verified', '2026-08-23T09:00:00')")
    conn.commit()
    conn.close()
    assert client.get("/api/leads/companies?q=پارس").json()["companies"] == []
    assert _create(client).status_code == 409


def test_a_nameless_company_is_refused(client, visitor, outbox):
    refused = _create(client, title="؟؟؟")
    assert refused.status_code == 400
    assert refused.json()["code"] == "missing_title"
    assert _count_dataset() == 0


def test_a_refused_registration_leaves_no_company_behind(client, visitor, outbox):
    """A bad number must not litter the knowledge base with a company nobody
    registered and nobody can explain."""
    refused = _create(client, phone="12345678")
    assert refused.status_code == 400, refused.text
    assert _count_dataset() == 0
    assert outbox == []


def test_the_duplicate_number_warning_and_its_override(client, visitor, outbox):
    """Same warning, same override, same audit trail as the normal path."""
    _add_company("other-co", "شرکت دیگر")
    _register_and_verify(client, "other-co", outbox)

    warned = _create(client)
    assert warned.status_code == 409
    assert warned.json()["duplicate"] is True
    # The warning creates nothing, so the retry is a clean first attempt.
    assert _count_dataset() == 1

    again = _create(client, override_duplicate=True)
    assert again.status_code == 200, again.text
    assert _count_dataset() == 2
    lead = _last_lead()
    assert lead["duplicate_override_of"] == "lead-other"
    assert lead["duplicate_override_at"]


# ── What the contact does next ──────────────────────────────────────────

def test_the_contact_supersedes_the_visitor_text(client, visitor, outbox):
    """One pending edit per company, whoever wrote the previous one."""
    dataset_id = _create(client).json()["dataset_id"]
    lead_id = _last_lead()["id"]
    client.post("/api/leads/verify", json={"lead_id": lead_id, "code": outbox[-1][1]})

    from app.services import leads as leads_service
    invite = leads_service.create_invite(lead_id, dataset_id, "http://test")
    token = invite["invite_url"].rsplit("/", 1)[1]

    # What the contact opens shows the visitor's draft, not an empty box.
    state = client.get(f"/api/leads/edit/{token}")
    assert state.status_code == 200, state.text
    assert state.json()["text"] == BODY["text"]

    sent = client.post(f"/api/leads/edit/{token}",
                       json={"text": "متن درست شرکت را خودمان نوشتیم."})
    assert sent.status_code == 200, sent.text

    rows = _edits(dataset_id)
    assert len(rows) == 2
    pending = [r for r in rows if r["status"] == "pending"]
    superseded = [r for r in rows if r["status"] == "superseded"]
    assert len(pending) == 1 and len(superseded) == 1
    assert pending[0]["new_text"] == "متن درست شرکت را خودمان نوشتیم."
    assert superseded[0]["new_text"] == BODY["text"]
    # Still nothing on the chatbot.
    assert _row(dataset_id)["text"] == ""


# ── What the admin sees ─────────────────────────────────────────────────

def test_the_review_queue_says_who_typed_the_text(client, visitor, outbox):
    dataset_id = _create(client).json()["dataset_id"]
    lead_id = _last_lead()["id"]
    _admin(client)

    edit = client.get("/admin/api/leads/edits").json()["edits"][0]
    assert edit["new_company"] is True
    assert edit["typed_by_visitor"] is True

    # After the contact sends their own text, only the company flag survives.
    client.post("/api/leads/verify", json={"lead_id": lead_id, "code": outbox[-1][1]})
    from app.services import leads as leads_service
    token = leads_service.create_invite(
        lead_id, dataset_id, "http://test")["invite_url"].rsplit("/", 1)[1]
    client.post(f"/api/leads/edit/{token}", json={"text": "متن خود شرکت."})

    edit = client.get("/admin/api/leads/edits").json()["edits"][0]
    assert edit["new_company"] is True
    assert edit["typed_by_visitor"] is False


def test_the_lead_list_marks_a_company_the_visitor_created(client, visitor, outbox):
    _add_company("known-co", "شرکت از قبل موجود")
    _register_and_verify(client, "known-co", outbox, phone="09121110033")
    _create(client)
    _admin(client)

    leads = client.get("/admin/api/leads").json()["leads"]
    by_company = {row["company_name"]: row["new_company"] for row in leads}
    assert by_company[BODY["title"]] is True
    assert by_company["شرکت از قبل موجود"] is False


# ── The door itself ─────────────────────────────────────────────────────

def test_the_endpoint_needs_a_visitor_session(client):
    assert client.post("/api/leads/new-company", json=BODY).status_code == 401


# ── Helpers that need the database ──────────────────────────────────────

def _count_dataset():
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    try:
        return conn.execute("SELECT COUNT(*) AS n FROM dataset").fetchone()["n"]
    finally:
        conn.close()


def _last_lead():
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    try:
        row = conn.execute(
            "SELECT * FROM company_leads ORDER BY created_at DESC, id DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    return dict(row)


def _register_and_verify(client, dataset_id, outbox, phone=PHONE):
    """The normal path, used here only to set up a state this path must meet."""
    from app.db.connection import get_db_connection
    res = client.post("/api/leads/register", json={
        "dataset_id": dataset_id, "first_name": "علی", "last_name": "کریمی",
        "position": "مدیر", "phone": phone})
    assert res.status_code == 200, res.text
    lead_id = res.json()["lead_id"]
    client.post("/api/leads/verify", json={"lead_id": lead_id, "code": outbox[-1][1]})
    # A stable id for the assertion on `duplicate_override_of`.
    conn = get_db_connection()
    conn.execute("UPDATE company_leads SET id = 'lead-other' WHERE id = ?", (lead_id,))
    conn.commit()
    conn.close()
    return "lead-other"
