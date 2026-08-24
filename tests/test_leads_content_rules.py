"""What may enter `dataset.text`, what the reviewer is warned about, and who
is recorded as having read the lists.

Three rules, one column. `dataset.text` is the sentence the chatbot says to
every visitor of the exhibition, and two doors write into it: the contact's
invite and the visitor's new-company form. So the tests come in pairs, because
one door checking is the same as neither.

- SEC-024, the plain-text rule. Markup is refused at BOTH doors. A `<` in
  ordinary prose is not markup and is not refused: a company writing
  "دمای کاری < ۵۰ درجه" must not be turned away by a security rule.
- F15, the risky tokens. Nothing is blocked. The point is that a reviewer
  approving the fortieth diff of the afternoon sees the bank account.
- SEC-031, the export rows. Reading the lead list in a browser tab is an
  export, and it leaves the same audit row a CSV download does.

One more test sits at the end, on a different subject: what
`set_visitor_active` binds to a BOOLEAN column. It is here because it is the
same file's worth of work, and its docstring says what it does and does not
prove.
"""
import datetime
import secrets

import pytest
from fastapi.testclient import TestClient


# ── Fixtures ────────────────────────────────────────────────────────────

@pytest.fixture
def paths(tmp_path, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "content_rules.db"))
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
    import app.routers.leads as leads_router
    monkeypatch.setattr(leads_router, "check_rate_limit",
                        lambda *args, **kwargs: None)


@pytest.fixture
def outbox(monkeypatch):
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


LIVE_TEXT = "متن قدیمی که تیم پادیار نوشته است."


def _admin(client, username="tester"):
    from app.config import ADMIN_COOKIE_NAME
    from app.auth.csrf import token_for_session
    from app.db.connection import get_db_connection
    token = secrets.token_hex(16)
    expiry = datetime.datetime.now() + datetime.timedelta(hours=1)
    conn = get_db_connection()
    conn.execute("INSERT INTO admin_sessions (token, username, expiry) VALUES (?, ?, ?)",
                 (token, username, expiry.isoformat()))
    conn.commit()
    conn.close()
    client.cookies.set(ADMIN_COOKIE_NAME, token)
    client.headers.update({"X-CSRF-Token": token_for_session(token)})
    return token


def _add_company(dataset_id, title, text=LIVE_TEXT):
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    conn.execute(
        "INSERT INTO dataset (id, title, text, video_url, title_en, text_en, position)"
        " VALUES (?, ?, ?, '', '', '', 10)", (dataset_id, title, text))
    conn.commit()
    conn.close()


def _visitor(client, name="همکار غرفه"):
    from app.services import leads as leads_service
    made = leads_service.create_visitor(name)
    assert client.get(f"/v/{made['code']}", follow_redirects=False).status_code == 303
    return made


def _booth(client, outbox, dataset_id="co-a", title="پارس فناوران آریا",
           phone="09121110022"):
    """A capture carried to `verified`, with the contact's invite in hand."""
    from app.services import leads as leads_service
    _add_company(dataset_id, title)
    _visitor(client, "همکار " + dataset_id)
    registered = client.post("/api/leads/register", json={
        "dataset_id": dataset_id, "first_name": "مینا", "last_name": "رضایی",
        "position": "مدیر فروش", "phone": phone})
    assert registered.status_code == 200, registered.text
    lead_id = registered.json()["lead_id"]
    verified = client.post("/api/leads/verify",
                           json={"lead_id": lead_id, "code": outbox[-1][1]})
    assert verified.status_code == 200, verified.text
    url = leads_service.create_invite(lead_id, dataset_id, "http://testserver")["invite_url"]
    client.cookies.delete(leads_service.VISITOR_COOKIE)      # now the contact
    return {"token": url.rsplit("/", 1)[1], "lead_id": lead_id,
            "dataset_id": dataset_id}


# ── SEC-024: the plain-text rule at both doors ──────────────────────────

MARKUP = [
    "<script>alert(1)</script>",
    "شرکت ما <b>بهترین</b> است.",
    "خط اول<br>خط دوم",
    "</p>تمام",
    "<!-- یک یادداشت -->",
    '<a href="http://example.com">اینجا</a>',
]

# Prose a real company writes. None of it is a tag, so none of it is refused.
# The last one is the boundary drawn on purpose: the letter has to TOUCH the
# bracket. `< img` with a space is not a tag in HTML and not inline HTML in
# CommonMark either, so it reaches the reader as the characters typed.
INNOCENT = [
    "دمای کاری دستگاه < ۵۰ درجه است.",
    "قیمت<۱۰۰ هزار تومان.",
    "اگر x < y باشد خروجی صفر است.",
    "بازده >۹۵٪ و مصرف <۳ کیلووات.",
    "علامت ← و > در متن ما آزاد است.",
    "< img src=x >",
]


@pytest.mark.parametrize("text", MARKUP)
def test_the_contact_cannot_submit_markup(client, outbox, text):
    booth = _booth(client, outbox)
    r = client.post(f"/api/leads/edit/{booth['token']}", json={"text": text})
    assert r.status_code == 400, r.text
    assert "کد صفحهٔ وب" in r.json()["detail"]


@pytest.mark.parametrize("text", INNOCENT)
def test_an_ordinary_less_than_sign_is_not_markup(client, outbox, text):
    """The rule is a tag-shaped token, not the character `<`. A company
    describing its own product must not be refused by a security rule."""
    booth = _booth(client, outbox)
    r = client.post(f"/api/leads/edit/{booth['token']}", json={"text": text})
    assert r.status_code == 200, r.text


@pytest.mark.parametrize("text", MARKUP)
def test_the_new_company_form_cannot_carry_markup(client, outbox, text):
    """The other door into the same column. Our own field staff type here."""
    _visitor(client)
    r = client.post("/api/leads/new-company", json={
        "title": "شرکت تازه " + secrets.token_hex(3), "phone": "09121110044",
        "first_name": "علی", "last_name": "کریمی", "position": "مدیر",
        "text": text})
    assert r.status_code == 400, r.text
    assert "کد صفحهٔ وب" in r.json()["detail"]


def test_the_new_company_form_accepts_ordinary_prose(client, outbox):
    _visitor(client)
    r = client.post("/api/leads/new-company", json={
        "title": "شرکت نمونهٔ اول", "phone": "09121110055",
        "first_name": "علی", "last_name": "کریمی", "position": "مدیر",
        "text": "دستگاه ما در دمای < ۵۰ درجه کار می‌کند."})
    assert r.status_code == 200, r.text


def test_a_refused_markup_edit_leaves_the_invite_alive(client, outbox):
    """The refusal happens before the burn, so the contact fixes their text
    and sends it again on the same link."""
    booth = _booth(client, outbox)
    bad = client.post(f"/api/leads/edit/{booth['token']}",
                      json={"text": "ما <b>اول</b> هستیم."})
    assert bad.status_code == 400
    good = client.post(f"/api/leads/edit/{booth['token']}",
                       json={"text": "ما اولین سازندهٔ این دستگاه در ایران هستیم."})
    assert good.status_code == 200, good.text


def test_a_refused_markup_edit_writes_nothing(client, outbox):
    from app.db.connection import get_db_connection
    booth = _booth(client, outbox)
    client.post(f"/api/leads/edit/{booth['token']}", json={"text": "<script>x</script>"})
    conn = get_db_connection()
    try:
        rows = conn.execute("SELECT COUNT(*) c FROM dataset_edits").fetchone()["c"]
    finally:
        conn.close()
    assert rows == 0


# ── F15: the risky tokens the reviewer has to read ──────────────────────

@pytest.mark.parametrize("kind,text,value", [
    ("url",    "سایت ما www.rakib.com است.",                    "www.rakib.com"),
    ("url",    "جزئیات در https://rakib.ir/products آمده.",      "https://rakib.ir/products"),
    ("phone",  "با ۰۹۱۲۳۴۵۶۷۸۹ تماس بگیرید.",                   "۰۹۱۲۳۴۵۶۷۸۹"),
    ("phone",  "دفتر ما 02188776655 است.",                       "02188776655"),
    ("iban",   "شبا IR820540102680020817909002 است.",            "IR820540102680020817909002"),
    ("card",   "کارت 6037 9975 1234 5678 به نام شرکت.",          "6037 9975 1234 5678"),
    ("handle", "در تلگرام @rakibsales هستیم.",                   "@rakibsales"),
])
def test_each_risky_kind_is_extracted(paths, kind, text, value):
    from app.services import leads as leads_service
    found = leads_service.find_risky(text)
    assert [(f["kind"], f["value"]) for f in found if f["kind"] == kind] == [(kind, value)]
    assert all(f["label"] for f in found)


def test_a_card_number_is_not_reported_a_second_time_as_a_phone(paths):
    """The digits of a card contain a phone-shaped run. Reporting both makes
    the warning list longer and less readable, which is how it gets ignored."""
    from app.services import leads as leads_service
    found = leads_service.find_risky("کارت 6037997512345678 به نام شرکت.")
    assert [f["kind"] for f in found] == ["card"]


def test_plain_prose_produces_no_warning(paths):
    from app.services import leads as leads_service
    assert leads_service.find_risky("ما سازندهٔ دستگاه‌های بسته‌بندی هستیم.") == []


def test_the_allowlist_marks_the_exhibition_own_domains(client, paths):
    """An operator permits their own site without a deploy. Everything else
    still shows up, just without the green badge."""
    from app.services import leads as leads_service
    _admin(client)
    saved = client.post("/admin/api/leads/settings",
                        json={"allowed_link_domains": "inotex.ir, padyar.ai"})
    assert saved.status_code == 200, saved.text
    assert saved.json()["allowed_link_domains"] == "inotex.ir, padyar.ai"

    found = leads_service.find_risky(
        "ما در https://www.inotex.ir/hall5 هستیم و رقیب ما rakib.com است.")
    by_value = {f["value"]: f["allowed"] for f in found}
    assert by_value["https://www.inotex.ir/hall5"] is True
    assert by_value["rakib.com"] is False


def test_the_allowlist_never_excuses_a_bank_account(client, paths):
    """There is no domain list that makes someone else's IBAN fine to publish,
    so `allowed` stays false for everything that is not a link."""
    from app.services import leads as leads_service
    _admin(client)
    client.post("/admin/api/leads/settings", json={"allowed_link_domains": "inotex.ir"})
    found = leads_service.find_risky("شبا IR820540102680020817909002 و @inotexir")
    assert [f["allowed"] for f in found] == [False, False]


def test_a_subdomain_of_an_allowed_domain_is_allowed(client, paths):
    from app.services import leads as leads_service
    _admin(client)
    client.post("/admin/api/leads/settings", json={"allowed_link_domains": "inotex.ir"})
    found = leads_service.find_risky("https://booth.inotex.ir/a و https://notinotex.ir/b")
    assert [f["allowed"] for f in found] == [True, False]


def test_the_review_queue_carries_the_risky_list(client, outbox):
    """The reviewer's card reads this, not the client-side fallback."""
    booth = _booth(client, outbox)
    text = "برای خرید به rakib.com بروید یا با 09129998877 تماس بگیرید."
    assert client.post(f"/api/leads/edit/{booth['token']}",
                       json={"text": text}).status_code == 200

    _admin(client)
    r = client.get("/admin/api/leads/edits")
    assert r.status_code == 200, r.text
    risky = r.json()["edits"][0]["risky"]
    assert {f["kind"] for f in risky} == {"url", "phone"}


def test_risky_content_is_never_a_reason_to_refuse(client, outbox):
    """Information for a human, not a gate. A company giving its own phone
    number is the normal case and must go through."""
    booth = _booth(client, outbox)
    r = client.post(f"/api/leads/edit/{booth['token']}",
                    json={"text": "تماس با فروش: 02188776655"})
    assert r.status_code == 200, r.text


# ── SEC-031: an export row for every admin listing ──────────────────────

HEADERS = {"User-Agent": "ExportProbe/1.0", "X-Forwarded-For": "203.0.113.9"}


def _exports():
    from app.services import applog
    rows, _ = applog.query(category="audit", limit=500, sort="id", direction="asc")
    return [r for r in rows if r["event_name"] == "data.export"]


@pytest.mark.parametrize("path,target", [
    ("/admin/api/leads",          "company_leads"),
    ("/admin/api/leads/visitors", "lead_visitors"),
    ("/admin/api/leads/edits",    "dataset_edits"),
    ("/admin/api/leads/stuck",    "stuck_leads"),
    ("/admin/api/leads/funnel",   "lead_funnel"),
])
def test_every_admin_listing_writes_one_export_row(client, paths, path, target):
    _admin(client, "alice")
    before = len(_exports())

    r = client.get(path, headers=HEADERS)
    assert r.status_code == 200, r.text

    rows = _exports()
    assert len(rows) == before + 1
    row = rows[-1]
    assert row["actor"] == "alice"
    assert row["target"] == target
    assert row["user_agent"] == "ExportProbe/1.0"
    assert row["ip"]
    assert row["route"] == path
    assert row["http_method"] == "GET"


def test_an_unauthenticated_read_writes_no_export_row(client, paths):
    """The row must mean data actually left. A 401 took nothing."""
    before = len(_exports())
    assert client.get("/admin/api/leads", headers=HEADERS).status_code == 401
    assert len(_exports()) == before


def test_the_exported_numbers_never_reach_the_audit_row(client, outbox):
    """audit_logs has a long retention. A row quoting the contact list would
    be a second copy of the thing it exists to police."""
    _booth(client, outbox, title="شرکت رازدار پارسیان")
    _admin(client)
    r = client.get("/admin/api/leads", headers=HEADERS)
    assert "شرکت رازدار پارسیان" in r.text         # it really was handed out
    assert "شرکت رازدار پارسیان" not in str(_exports()[-1])


# ── The revoke button on a real PostgreSQL ──────────────────────────────

def test_deactivating_a_visitor_binds_a_boolean_not_an_integer(client, paths,
                                                               monkeypatch):
    """`lead_visitors.active` is a real BOOLEAN in PostgreSQL, and psycopg
    adapts a Python `int` to `integer`, so binding `1` made the admin's revoke
    button fail with "column active is of type boolean". SQLite accepts either.

    So this asserts on the VALUE BOUND, not on the outcome. Running against
    SQLite it can never fail for the reason the bug existed, and it proves
    nothing about how PostgreSQL behaves. What it does prove is that this call
    site still passes a `bool`, which is the one thing that regressed. A test
    that could fail for the real reason belongs in `tests/postgres/`, against
    a live server.

    `type(...) is bool`, not `isinstance`: `isinstance(1, int)` is true of
    `True` as well, so `isinstance` would pass against the value under test.
    """
    from app.services import leads as leads_service

    calls = []

    class Spy:
        def __init__(self, real):
            self._real = real

        def execute(self, sql, params=()):
            calls.append((sql, params))
            return self._real.execute(sql, params)

        def __getattr__(self, name):
            return getattr(self._real, name)

    real_connect = leads_service.get_db_connection
    monkeypatch.setattr(leads_service, "get_db_connection",
                        lambda *a, **kw: Spy(real_connect(*a, **kw)))

    made = leads_service.create_visitor("همکار غرفه")
    for wanted in (False, True):
        calls.clear()
        assert leads_service.set_visitor_active(made["id"], wanted) is True
        bound = [p for sql, p in calls if "SET active" in sql]
        assert len(bound) == 1
        assert type(bound[0][0]) is bool
        assert bound[0][0] is wanted
