"""The bulk confirm campaign (migrations/0024): one SMS per company with a
mobile on file, each carrying its own one-time link.

Scenarios:
- the audience is exactly the companies that filed a mobile;
- a run texts every audience company (dev provider: the outbox file), skips
  companies whose draft is already pending review, and creates a campaign
  lead for companies nobody registered — so their link has a lead to hang
  from and the funnel stays honest;
- a link refusal (Asanak 1014) or an exhausted budget STOPS the campaign on
  the record instead of retrying silently;
- the panel endpoints launch, list with delivery counts, and show detail.
"""
import datetime
import secrets

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "campaigns.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    from app.main import app
    with TestClient(app) as c:
        from app.db.connection import get_db_connection
        from app.db.queries import set_setting
        from app.services import campaigns, leads as leads_svc, sms_outbox
        import app.services.sms as sms
        leads_svc.ensure_tables()
        campaigns.ensure_table()
        sms_outbox.ensure_table()
        set_setting("sms_provider", "dev")
        monkeypatch.setattr(sms, "_DEV_OUTBOX", str(tmp_path / "dev-outbox.log"))

        token = secrets.token_hex(16)
        conn = get_db_connection()
        conn.execute("INSERT OR IGNORE INTO admins (username, password_hash, salt,"
                     " security_question, security_answer_hash)"
                     " VALUES ('campadmin','x','y','q','z')")
        conn.execute("INSERT INTO admin_sessions (token, username, expiry)"
                     " VALUES (?,?,?)",
                     (token, "campadmin",
                      # 12h: verify_admin compares naive expiry against LOCAL
                      # now (CI is UTC; a +03:30 dev machine is not).
                      (datetime.datetime.utcnow() + datetime.timedelta(hours=12)).isoformat()))
        conn.commit()
        conn.close()
        c.cookies.set("admin_session", token)
        from app.auth.csrf import token_for_session
        c.headers["X-CSRF-Token"] = token_for_session(token)
        c._campaigns = campaigns
        yield c


def _company(dataset_id, title, mobile, text="متن"):
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    conn.execute("INSERT INTO companies (id, title, text, contact_mobile)"
                 " VALUES (?, ?, ?, ?)", (dataset_id, title, text, mobile))
    conn.commit()
    conn.close()


def _pending_draft(dataset_id):
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    conn.execute(
        "INSERT INTO dataset_edits (id, dataset_id, lead_id, old_text, new_text,"
        " status, created_at) VALUES (?, ?, '', 'a', 'b', 'pending', ?)",
        (secrets.token_urlsafe(6), dataset_id, datetime.datetime.utcnow().isoformat()),
    )
    conn.commit()
    conn.close()


TEXT = "لطفاً اطلاعات شرکت خود را بررسی و تأیید کنید:\n{magic_link}"


def test_the_audience_is_the_companies_with_a_mobile(admin_client):
    _company("co-1", "شرکت یک", "09121111111")
    _company("co-2", "شرکت دو", "")
    _company("co-3", "شرکت سه", "09123333333")

    rows = admin_client._campaigns.audience()

    assert {r["id"] for r in rows} == {"co-1", "co-3"}


def test_a_run_texts_everyone_and_creates_campaign_leads(admin_client, tmp_path):
    _company("co-1", "شرکت یک", "09121111111")
    _company("co-2", "شرکت دو", "09122222222")
    _pending_draft("co-2")

    campaigns = admin_client._campaigns
    launched = campaigns.launch(TEXT, "http://x", actor="op")
    campaigns.run(launched["id"], "http://x", pace_seconds=0)

    from app.db.connection import get_db_connection
    conn = get_db_connection()
    try:
        row = conn.execute("SELECT * FROM sms_campaigns WHERE id = ?",
                           (launched["id"],)).fetchone()
        leads = conn.execute(
            "SELECT dataset_id, origin, status FROM company_leads"
            " WHERE origin = 'campaign'").fetchall()
        invites = conn.execute("SELECT COUNT(*) c FROM edit_invites").fetchone()
    finally:
        conn.close()

    assert row["status"] == "done"
    assert row["sent"] == 1 and row["skipped"] == 1 and row["failed"] == 0
    assert row["audience"] == 2
    assert [l["dataset_id"] for l in leads] == ["co-1"], \
        "a company nobody registered must get a campaign lead for its link"
    assert leads[0]["status"] == "verified"
    assert invites["c"] == 1, "the skipped company must not spend an invite"

    # One link per company, in the dev outbox, with the company's own token.
    out = (tmp_path / "dev-outbox.log").read_text(encoding="utf-8")
    assert out.count("campaign") == 1
    assert "/edit/" in out

    detail = campaigns.campaign_detail(launched["id"])
    by_status = {m["status"] for m in detail["messages"]}
    assert "skipped" in by_status and "unknown" in by_status, \
        "the skipped company and the dev-outbox send are both on the report"


def test_a_reissued_company_gets_a_fresh_link(admin_client):
    from app.services import leads as leads_svc
    _company("co-1", "شرکت یک", "09121111111")
    lead_id = secrets.token_urlsafe(8)
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    conn.execute(
        "INSERT INTO company_leads (id, dataset_id, company_name, phone, phone_hash,"
        " status, created_at, verified_at, challenge_id)"
        " VALUES (?, 'co-1', 'شرکت یک', '09121111111', 'h', 'verified', ?, ?, '')",
        (lead_id, datetime.datetime.utcnow().isoformat(),
         datetime.datetime.utcnow().isoformat()),
    )
    conn.commit()
    conn.close()
    first = leads_svc.create_invite(lead_id, "co-1", "http://x")

    campaigns = admin_client._campaigns
    launched = campaigns.launch(TEXT, "http://x")
    campaigns.run(launched["id"], "http://x", pace_seconds=0)

    conn = get_db_connection()
    try:
        rows = conn.execute("SELECT token_hash FROM edit_invites").fetchall()
    finally:
        conn.close()
    assert len(rows) == 1, "the campaign's invite replaced the old one, not stacked"
    from app.services.leads import _digest
    assert rows[0]["token_hash"] != _digest(first["invite_url"].rsplit("/", 1)[1])


def test_a_link_refusal_stops_the_campaign_on_the_record(admin_client, monkeypatch):
    _company("co-1", "شرکت یک", "09121111111")
    _company("co-2", "شرکت دو", "09122222222")
    from app.services import sms as sms_service

    def refused(destination, link, campaign_id="", reference=""):
        raise sms_service.SmsError("ارسال نشد", detail="خط اجازهٔ لینک", code=1014)

    monkeypatch.setattr(sms_service, "send_campaign_link", refused)

    campaigns = admin_client._campaigns
    launched = campaigns.launch(TEXT, "http://x")
    campaigns.run(launched["id"], "http://x", pace_seconds=0)

    from app.db.connection import get_db_connection
    conn = get_db_connection()
    try:
        row = conn.execute("SELECT status, stop_reason, failed FROM sms_campaigns"
                           " WHERE id = ?", (launched["id"],)).fetchone()
    finally:
        conn.close()
    assert row["status"] == "stopped"
    assert row["stop_reason"], "the operator must be told why it stopped"


def test_launch_refuses_a_text_without_the_link_placeholder(admin_client):
    campaigns = admin_client._campaigns
    with pytest.raises(campaigns.CampaignError):
        campaigns.launch("متنی بدون لینک", "http://x")


def test_the_panel_launches_lists_and_shows_detail(admin_client):
    _company("co-1", "شرکت یک", "09121111111")

    r = admin_client.post("/admin/api/leads/campaigns", json={"text": TEXT})
    assert r.status_code == 200, r.text
    campaign_id = r.json()["id"]
    # BackgroundTasks ran with the response; the campaign exists either way.

    r = admin_client.get("/admin/api/leads/campaigns")
    assert r.status_code == 200
    body = r.json()
    assert body["capability"]["available"] is True, \
        "a dev install must be able to exercise the campaign flow"
    assert any(c["id"] == campaign_id for c in body["campaigns"])
    assert "delivery" in body["campaigns"][0]

    r = admin_client.get(f"/admin/api/leads/campaigns/{campaign_id}")
    assert r.status_code == 200 and "messages" in r.json()

    r = admin_client.get("/admin/api/leads/campaigns/no-such")
    assert r.status_code == 404


def test_the_campaign_endpoints_refuse_an_anonymous_caller(tmp_path, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "campaigns_anon.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    from app.main import app
    with TestClient(app) as anon:
        assert anon.get("/admin/api/leads/campaigns").status_code in (401, 403)
        assert anon.post("/admin/api/leads/campaigns",
                         json={"text": TEXT}).status_code in (401, 403)
