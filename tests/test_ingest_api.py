"""The admin API of the ingest module (docs/features/knowledge-ingestion/SPEC.md,
section 6; REQ-052..REQ-062, REQ-065..REQ-068, REQ-081; SEC-001..SEC-004,
SEC-018, SEC-019).

What this file protects:

- every route is admin-only on its own (401 without a session), every
  mutation needs the CSRF token (403 without it), and with the module off no
  route exists (404);
- the upload refuses a bad type, an oversized body and a nameless file inside
  the request, and the same bytes while a job is active return that job;
- nothing goes live without a human: approve writes dataset, questions and
  synonyms in one transaction and schedules exactly ONE publish after the
  commit; approve-seen approves only proposals the admin actually saw;
- a duplicate is decided by text at approve time (409 with duplicate_of), a
  shared title never blocks;
- lists are paginated with a hard cap of 50.

Proposals are seeded straight into the throwaway SQLite file where the test is
about review, not extraction; the upload tests run the real S1 child process on
a small DOCX. The publish is replaced by a recorder on app.services.search
(decision D3); the AI is off, so no test reaches the network.
"""
import datetime
import importlib
import io
import json
import os
import secrets
import threading
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

FIXTURES = Path(__file__).parent / "fixtures" / "ingest"
API = "/admin/api/ingest"


def _rows(query, params=()):
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    try:
        return [dict(r) for r in conn.execute(query, params).fetchall()]
    finally:
        conn.close()


def _exec(query, params=()):
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    try:
        conn.execute(query, params)
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def app_env(tmp_path, monkeypatch):
    import tempfile
    import app.config as config
    from app.services import embeddings
    from app.services.ai.wrapper import padyar_ai
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "ingest-api.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    monkeypatch.setattr(embeddings, "available", lambda: False)
    monkeypatch.setattr(padyar_ai, "external_ai_enabled", lambda: False)
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(uploads))
    yield


@pytest.fixture
def publishes(monkeypatch):
    """The SC-028 recorder: each entry is the dataset ids visible at call time."""
    from app.services import search
    calls = []

    def recorder(timeout_s=120.0):
        calls.append(sorted(r["id"] for r in _rows("SELECT id FROM dataset")))
        return True
    monkeypatch.setattr(search, "reindex_and_publish_until_done", recorder)
    return calls


def _session(username="ingestadmin"):
    from app.db.connection import get_db_connection
    token = secrets.token_hex(16)
    conn = get_db_connection()
    conn.execute("INSERT OR IGNORE INTO admins (username, password_hash, salt, security_question,"
                 " security_answer_hash) VALUES (?, 'x', 'y', 'q', 'z')", (username,))
    conn.execute("INSERT INTO admin_sessions (token, username, expiry) VALUES (?, ?, ?)",
                 (token, username, (datetime.datetime.now(datetime.timezone.utc)
                                    + datetime.timedelta(hours=2)).isoformat()))
    conn.commit()
    conn.close()
    return token


@pytest.fixture
def client(app_env, publishes):
    from app.main import app
    from app.auth.csrf import token_for_session
    with TestClient(app) as c:
        token = _session()
        c.cookies.set("admin_session", token)
        c.headers["X-CSRF-Token"] = token_for_session(token)
        yield c


@pytest.fixture
def anon(app_env, publishes):
    from app.main import app
    with TestClient(app) as c:
        yield c


@pytest.fixture
def no_csrf(app_env, publishes):
    from app.main import app
    with TestClient(app) as c:
        c.cookies.set("admin_session", _session())
        yield c


# ── Seeding ──────────────────────────────────────────────────────────────

def _seed_job(status="ready", job_id=None, created="datetime('now')", **columns):
    job_id = job_id or secrets.token_hex(12)
    _exec("INSERT INTO ingest_jobs (id, source_kind, source_name, content_hash, byte_size, format,"
          f" status, created_by, created_at, finished_at) VALUES (?, 'file', 'doc.docx', ?, 10, 'docx', ?,"
          f" 'ingestadmin', {created}, ?)",
          (job_id, secrets.token_hex(8), status, columns.pop("finished_at", None)))
    for name, value in columns.items():
        _exec(f"UPDATE ingest_jobs SET {name} = ? WHERE id = ?", (value, job_id))
    return job_id


def _seed_proposal(job_id, seq, text, title=None, ai_state="local", seen=False, status="pending",
                   similar_kind="", similar_to="", questions=(), synonyms=(), title_source="heading"):
    proposal_id = secrets.token_hex(12)
    seen_at = "datetime('now')" if seen else "NULL"
    _exec("INSERT INTO ingest_proposals (id, job_id, seq, source_text, heading, title, text, title_source,"
          " questions, synonyms, ai_state, similar_kind, similar_to, status, seen_at)"
          f" VALUES (?, ?, ?, ?, '', ?, ?, ?, ?, ?, ?, ?, ?, ?, {seen_at})",
          (proposal_id, job_id, seq, text, title or f"عنوان {seq}", text, title_source,
           json.dumps(list(questions), ensure_ascii=False),
           json.dumps([{"word": w, "suggestion": s} for w, s in synonyms], ensure_ascii=False),
           ai_state, similar_kind, similar_to, status))
    return proposal_id


def _live_entry(item_id, title, text, position=10):
    _exec("INSERT INTO dataset (id, title, text, position) VALUES (?, ?, ?, ?)", (item_id, title, text, position))


def _docx(paragraphs):
    """A DOCX built from the committed fixture with a new document body.
    `paragraphs` is a list of (style or None, text)."""
    source = zipfile.ZipFile(FIXTURES / "fa-sample.docx")
    parts = []
    for style, text in paragraphs:
        props = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
        parts.append(f'<w:p>{props}<w:r><w:t xml:space="preserve">{text}</w:t></w:r></w:p>')
    document = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                f'<w:body>{"".join(parts)}</w:body></w:document>')
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as target:
        for info in source.infolist():
            if info.filename != "word/document.xml":
                target.writestr(info.filename, source.read(info.filename))
        target.writestr("word/document.xml", document)
    return out.getvalue()


def _upload(client, name, data):
    return client.post(f"{API}/jobs/upload", files={"file": (name, data, "application/octet-stream")})


def _refusal(response, status, code):
    assert response.status_code == status, response.text
    body = response.json()
    assert body["code"] == code, body
    assert isinstance(body["detail"], str) and body["detail"]
    return body


# ── SEC-001..SEC-003: the doors ──────────────────────────────────────────

ROUTES = [
    ("POST", f"{API}/jobs/upload"),
    ("POST", f"{API}/jobs/url"),
    ("GET", f"{API}/jobs"),
    ("GET", f"{API}/jobs/j1"),
    ("GET", f"{API}/jobs/j1/proposals"),
    ("POST", f"{API}/jobs/j1/cancel"),
    ("POST", f"{API}/jobs/j1/approve-seen"),
    ("POST", f"{API}/proposals/seen"),
    ("PUT", f"{API}/proposals/p1"),
    ("POST", f"{API}/proposals/p1/approve"),
    ("POST", f"{API}/proposals/p1/reject"),
    ("GET", f"{API}/index-status"),
]
MUTATIONS = [(m, p) for m, p in ROUTES if m != "GET"]


@pytest.mark.parametrize("method,path", ROUTES)
def test_every_route_refuses_a_caller_without_an_admin_session(anon, method, path):
    assert anon.request(method, path, json={}).status_code == 401, (method, path)


@pytest.mark.parametrize("method,path", MUTATIONS)
def test_every_mutation_without_the_csrf_token_is_refused(no_csrf, method, path):
    assert no_csrf.request(method, path, json={}).status_code == 403, (method, path)


def test_the_same_mutation_with_the_csrf_token_reaches_the_route(client):
    _refusal(client.post(f"{API}/jobs/unknown/cancel"), 404, "not_found")
    _refusal(client.post(f"{API}/proposals/unknown/approve"), 404, "not_found")


def test_with_the_module_off_no_ingest_route_exists(app_env, monkeypatch):
    import app.config as config
    import app.main
    from app.modules.registry import CORE_MODULE_NAMES
    previous = config.ENABLED_MODULES
    monkeypatch.setattr(config, "ENABLED_MODULES", CORE_MODULE_NAMES + ["voice"])
    try:
        rebuilt = importlib.reload(app.main).app
        with TestClient(rebuilt) as c:
            from app.auth.csrf import token_for_session
            token = _session()
            c.cookies.set("admin_session", token)
            c.headers["X-CSRF-Token"] = token_for_session(token)
            for method, path in ROUTES:
                assert c.request(method, path, json={}).status_code == 404, (method, path)
    finally:
        monkeypatch.setattr(config, "ENABLED_MODULES", previous)
        importlib.reload(app.main)


def test_the_module_is_registered_optional_with_its_router():
    from app.modules.registry import MODULES
    module = MODULES["ingest"]
    assert module.is_core is False
    assert module.router_module == "app.routers.ingest"
    assert module.description == ("Semi-automatic knowledge ingestion from documents and web pages,"
                                  " with human approval")


# ── REQ-025, REQ-027, SEC-004, SEC-018: upload ───────────────────────────

def test_a_small_docx_is_accepted_and_read_into_proposals(client):
    response = _upload(client, "fa-sample.docx", (FIXTURES / "fa-sample.docx").read_bytes())
    assert response.status_code == 202, response.text
    job = response.json()["job"]
    assert set(job) == {"id", "source_kind", "source_name", "format", "status", "error_code",
                        "error_message", "encoding_note", "chunk_count", "ai_stopped_at",
                        "created_by", "created_at", "finished_at"}
    assert (job["source_kind"], job["source_name"], job["format"], job["status"]) == (
        "file", "fa-sample.docx", "docx", "queued")
    assert job["created_by"] == "ingestadmin"
    detail = client.get(f"{API}/jobs/{job['id']}").json()
    assert detail["job"]["status"] == "ready"
    listed = client.get(f"{API}/jobs/{job['id']}/proposals").json()
    assert [p["title"] for p in listed["items"]] == ["ساعت‌های بازدید", "ثبت‌نام غرفه‌ها"]


def test_a_file_whose_bytes_do_not_match_its_name_is_415(client):
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
    from app.services.ingest_extract import MESSAGES
    body = _refusal(_upload(client, "photo.pdf", png), 415, "bad_type")
    assert body["detail"] == MESSAGES["bad_type"]
    assert _rows("SELECT id FROM ingest_jobs") == []


def test_a_body_over_20_mib_is_413_before_any_job(client):
    from app.services.ingest_extract import MAX_FILE_BYTES, MESSAGES
    body = _refusal(_upload(client, "big.txt", b"a" * (MAX_FILE_BYTES + 1)), 413, "too_large")
    assert body["detail"] == MESSAGES["too_large"]
    assert _rows("SELECT id FROM ingest_jobs") == []


def test_a_zip_with_too_many_members_is_413(client):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr("[Content_Types].xml", "<x/>")
        archive.writestr("word/document.xml", "<x/>")
        for i in range(2001):
            archive.writestr(f"m/{i}", "")
    _refusal(_upload(client, "many.docx", out.getvalue()), 413, "zip_too_big")


def test_a_file_without_a_name_is_422(client):
    response = client.post(f"{API}/jobs/upload", files={"file": ("", b"abc", "text/plain")})
    assert response.status_code == 422
    assert client.post(f"{API}/jobs/upload").status_code == 422


def test_the_same_file_while_its_job_is_active_returns_that_job(client):
    data = (FIXTURES / "fa-sample.docx").read_bytes()
    first = _upload(client, "a.docx", data)
    second = _upload(client, "renamed.docx", data)
    assert first.status_code == 202
    assert second.status_code == 200
    assert second.json()["existing"] is True
    assert second.json()["job"]["id"] == first.json()["job"]["id"]
    assert len(_rows("SELECT id FROM ingest_jobs")) == 1


def test_two_uploads_of_one_file_at_the_same_moment_make_one_job(app_env):
    from app.db.connection import init_db
    from app.services import ingest
    init_db()
    barrier = threading.Barrier(2)
    results = []

    def create():
        barrier.wait()
        results.append(ingest.create_job("file", "a.txt", b"same bytes", "txt", "admin"))
    threads = [threading.Thread(target=create) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(existing for _, existing in results) == [False, True]
    assert results[0][0]["id"] == results[1][0]["id"]
    assert len(_rows("SELECT id FROM ingest_jobs")) == 1


def test_the_21st_job_of_an_admin_within_an_hour_is_429(client, monkeypatch):
    from app.services import ingest

    async def not_run(job_id, charset=""):
        return None
    monkeypatch.setattr(ingest, "run_job", not_run)
    for n in range(20):
        assert _upload(client, f"n{n}.txt", f"متن شماره {n}".encode()).status_code == 202
    body = _refusal(_upload(client, "n21.txt", "متن بیست و یکم".encode()), 429, "rate_limited")
    assert body["detail"] == "در یک ساعت گذشته فایل‌های زیادی فرستاده‌اید. کمی بعد دوباره امتحان کنید."
    _refusal(client.post(f"{API}/jobs/url", json={"url": "https://example.com/"}), 429, "rate_limited")


# ── REQ-026: a web page ──────────────────────────────────────────────────

@pytest.fixture
def fetched(monkeypatch):
    from app.services import ingest, ingest_fetch
    state = {"result": None, "runs": []}

    def fake_fetch(url):
        if isinstance(state["result"], BaseException):
            raise state["result"]
        return state["result"]

    async def recording_run(job_id, charset=""):
        state["runs"].append((job_id, charset))
    monkeypatch.setattr(ingest_fetch, "fetch_url", fake_fetch)
    monkeypatch.setattr(ingest, "run_job", recording_run)
    return state


def test_a_web_page_becomes_a_job_named_by_its_readable_address(client, fetched):
    from app.services.ingest_fetch import Fetched
    host = "نمونه.ایران".encode("idna").decode()
    fetched["result"] = Fetched(final_url=f"https://{host}/about", content_type="text/html",
                                charset="windows-1256", data=b"<p>page</p>")
    response = client.post(f"{API}/jobs/url", json={"url": f"https://{host}/"})
    assert response.status_code == 202, response.text
    job = response.json()["job"]
    assert (job["source_kind"], job["source_name"], job["format"]) == ("url", "https://نمونه.ایران/about", "html")
    assert fetched["runs"] == [(job["id"], "windows-1256")]


@pytest.mark.parametrize("content_type,fmt", [("application/pdf", "pdf"), ("text/plain", "txt"),
                                              ("application/xhtml+xml", "html")])
def test_a_page_keeps_the_format_its_server_declared(client, fetched, content_type, fmt):
    from app.services.ingest_fetch import Fetched
    fetched["result"] = Fetched("https://example.com/f", content_type, "", b"%PDF-1.4 " + fmt.encode())
    assert client.post(f"{API}/jobs/url", json={"url": "https://example.com/f"}).json()["job"]["format"] == fmt


def test_a_refused_page_is_422_with_its_sentence_and_a_slow_one_is_504(client, fetched):
    from app.services.ingest_fetch import FetchRejected, MESSAGES_FA
    fetched["result"] = FetchRejected("blocked_address", MESSAGES_FA["blocked_address"])
    body = _refusal(client.post(f"{API}/jobs/url", json={"url": "https://10.0.0.1/"}), 422, "blocked_address")
    assert body["detail"] == MESSAGES_FA["blocked_address"]
    fetched["result"] = FetchRejected("timeout", MESSAGES_FA["timeout"])
    body = _refusal(client.post(f"{API}/jobs/url", json={"url": "https://slow.example/"}), 504, "timeout")
    assert body["detail"] == MESSAGES_FA["timeout"]
    assert _rows("SELECT id FROM ingest_jobs") == []


def test_a_missing_address_is_422(client, fetched):
    for payload in ({}, {"url": ""}, {"url": 42}):
        _refusal(client.post(f"{API}/jobs/url", json=payload), 422, "bad_url")


def test_a_broken_fetch_install_is_not_turned_into_a_refusal(app_env, publishes, fetched):
    from app.main import app
    from app.auth.csrf import token_for_session
    fetched["result"] = RuntimeError("the deadline pool is missing")
    with TestClient(app, raise_server_exceptions=False) as c:
        token = _session()
        c.cookies.set("admin_session", token)
        c.headers["X-CSRF-Token"] = token_for_session(token)
        assert c.post(f"{API}/jobs/url", json={"url": "https://example.com/"}).status_code == 500


# ── REQ-052..REQ-054, REQ-068: reading ───────────────────────────────────

def test_a_list_over_the_cap_is_cut_to_50_and_the_next_page_is_reachable(client):
    for n in range(55):
        _seed_job("done", job_id=f"job-{n:02d}", created=f"datetime('now', '-{n} minutes')")
    first = client.get(f"{API}/jobs", params={"limit": 500}).json()
    assert first["total"] == 55
    assert [j["id"] for j in first["items"]] == [f"job-{n:02d}" for n in range(50)]
    second = client.get(f"{API}/jobs", params={"limit": 50, "offset": 50}).json()
    assert [j["id"] for j in second["items"]] == [f"job-{n:02d}" for n in range(50, 55)]
    assert len(client.get(f"{API}/jobs").json()["items"]) == 20
    assert len(client.get(f"{API}/jobs", params={"limit": 0, "offset": -3}).json()["items"]) == 1


def test_a_job_shows_its_counts(client):
    job_id = _seed_job("ready", chunk_count=4)
    _seed_proposal(job_id, 1, "متن یک", seen=True)
    _seed_proposal(job_id, 2, "متن دو", ai_state="waiting")
    _seed_proposal(job_id, 3, "متن سه", status="approved", ai_state="done")
    _seed_proposal(job_id, 4, "متن چهار", status="rejected", ai_state="failed")
    body = client.get(f"{API}/jobs/{job_id}").json()
    assert body["job"]["id"] == job_id
    assert {k: body["counts"][k] for k in ("chunk_count", "ai_done", "pending", "approved", "rejected",
                                            "seen_pending")} == {
        "chunk_count": 4, "ai_done": 3, "pending": 2, "approved": 1, "rejected": 1, "seen_pending": 1}
    _refusal(client.get(f"{API}/jobs/nope"), 404, "not_found")


def test_reading_the_list_recovers_a_job_whose_worker_died(client):
    job_id = _seed_job("proposing")
    _exec("UPDATE ingest_jobs SET heartbeat_at = datetime('now', '-6 minutes') WHERE id = ?", (job_id,))
    _seed_proposal(job_id, 1, "متن", ai_state="waiting")
    client.get(f"{API}/jobs")
    job = client.get(f"{API}/jobs/{job_id}").json()["job"]
    assert (job["status"], job["error_code"]) == ("failed", "interrupted")
    assert job["error_message"].startswith("کار این فایل نیمه‌کاره ماند.")
    assert _rows("SELECT ai_state FROM ingest_proposals")[0]["ai_state"] == "local"


def test_proposals_are_listed_in_document_order_by_status(client):
    job_id = _seed_job("ready")
    _live_entry("d-7", "عنوان زنده", "متن زنده")
    ids = [_seed_proposal(job_id, seq, f"متن {seq}", status=status, similar_kind=kind, similar_to=to)
           for seq, status, kind, to in ((3, "pending", "dataset", "d-7"), (1, "pending", "", ""),
                                         (2, "approved", "", ""))]
    pending = client.get(f"{API}/jobs/{job_id}/proposals").json()
    assert pending["total"] == 2
    assert [p["seq"] for p in pending["items"]] == [1, 3]
    assert pending["items"][1]["similar_title"] == "عنوان زنده"
    everything = client.get(f"{API}/jobs/{job_id}/proposals", params={"status": "all", "limit": 2})
    assert [p["seq"] for p in everything.json()["items"]] == [1, 2]
    assert everything.json()["total"] == 3
    proposal = pending["items"][0]
    assert set(proposal) >= {"id", "job_id", "seq", "source_text", "heading", "title", "title_source", "text",
                             "questions", "synonyms", "ai_state", "similar_kind", "similar_to",
                             "similar_title", "same_title_as", "status", "edited", "seen", "reviewed_by",
                             "reviewed_at", "dataset_id"}
    assert proposal["seen"] is False and proposal["questions"] == []
    assert ids[1] == proposal["id"]
    _refusal(client.get(f"{API}/jobs/{job_id}/proposals", params={"status": "maybe"}), 422, "bad_status")
    _refusal(client.get(f"{API}/jobs/nope/proposals"), 404, "not_found")


def test_the_index_status_is_the_published_version(client):
    from app.db.queries import set_setting
    from app.services import search
    set_setting(search.INDEX_VERSION_KEY, "41")
    assert client.get(f"{API}/index-status").json() == {"version": search.published_index_version()} == {
        "version": 41}


# ── REQ-055, REQ-056: seen and edit ──────────────────────────────────────

def test_seen_marks_only_pending_unseen_proposals(client):
    job_id = _seed_job("ready")
    fresh = _seed_proposal(job_id, 1, "یک")
    seen = _seed_proposal(job_id, 2, "دو", seen=True)
    done = _seed_proposal(job_id, 3, "سه", status="approved")
    response = client.post(f"{API}/proposals/seen", json={"ids": [fresh, seen, done, "unknown", fresh]})
    assert response.json() == {"marked": 1}
    row = _rows("SELECT seen_by, seen_at FROM ingest_proposals WHERE id = ?", (fresh,))[0]
    assert row["seen_by"] == "ingestadmin" and row["seen_at"]
    assert _rows("SELECT seen_at FROM ingest_proposals WHERE id = ?", (done,))[0]["seen_at"] is None
    _refusal(client.post(f"{API}/proposals/seen", json={"ids": [f"x{i}" for i in range(51)]}), 422, "too_many_ids")
    _refusal(client.post(f"{API}/proposals/seen", json={"ids": "all"}), 422, "too_many_ids")


def test_an_edit_is_checked_field_by_field(client):
    job_id = _seed_job("ready")
    proposal_id = _seed_proposal(job_id, 1, "متن درباره ساعت کاری نمایشگاه.")
    for payload, field in (({"title": ""}, "title"), ({"title": "ع" * 101}, "title"),
                           ({"text": ""}, "text"), ({"text": "م" * 5001}, "text"),
                           ({"questions": ["What?"]}, "questions"), ({"questions": ["سؤال کوتاه؟"] * 2}, "questions"),
                           ({"questions": [f"پرسش شماره {n} این است؟" for n in "ابپتثج"]}, "questions"),
                           ({"synonyms": [{"word": "غرفه", "suggestion": "غرفه"}]}, "synonyms"),
                           ({"synonyms": "غرفه"}, "synonyms")):
        body = _refusal(client.put(f"{API}/proposals/{proposal_id}", json=payload), 422, "invalid_field")
        assert body["field"] == field, payload
    assert _rows("SELECT edited FROM ingest_proposals")[0]["edited"] == 0


def test_an_accepted_edit_is_saved_marked_and_seen(client):
    job_id = _seed_job("ready")
    proposal_id = _seed_proposal(job_id, 1, "متن درباره ساعت کاری نمایشگاه.")
    response = client.put(f"{API}/proposals/{proposal_id}", json={
        "title": "ساعت کاری", "text": "نمایشگاه از ۹ تا ۱۸ باز است.",
        "questions": ["نمایشگاه تا ساعت ۲۰ باز است؟"],
        "synonyms": [{"word": "نمایشگاه", "suggestion": "اکسپو"}]})
    assert response.status_code == 200, response.text
    proposal = response.json()["proposal"]
    assert (proposal["title"], proposal["title_source"], proposal["edited"], proposal["seen"]) == (
        "ساعت کاری", "admin", True, True)
    assert proposal["text"] == "نمایشگاه از ۹ تا ۱۸ باز است."
    assert proposal["questions"] == ["نمایشگاه تا ساعت ۲۰ باز است؟"]
    assert proposal["synonyms"] == [{"word": "نمایشگاه", "suggestion": "اکسپو"}]
    kept = client.put(f"{API}/proposals/{proposal_id}", json={"text": "متن تازه"}).json()["proposal"]
    assert (kept["title"], kept["title_source"]) == ("ساعت کاری", "admin")


def test_a_reviewed_proposal_cannot_be_edited(client):
    job_id = _seed_job("ready")
    proposal_id = _seed_proposal(job_id, 1, "متن", status="approved")
    body = _refusal(client.put(f"{API}/proposals/{proposal_id}", json={"title": "تازه"}), 409, "already_reviewed")
    assert body["detail"] == "این پیشنهاد قبلاً بررسی شده است."
    _refusal(client.put(f"{API}/proposals/nope", json={"title": "تازه"}), 404, "not_found")


# ── REQ-057..REQ-059, REQ-065: approve one ───────────────────────────────

def test_approve_writes_the_entry_its_questions_and_synonyms_then_publishes_once(client, publishes):
    _live_entry("old", "قدیمی", "متن قدیمی", position=70)
    job_id = _seed_job("ready")
    proposal_id = _seed_proposal(job_id, 1, "متن تازه درباره پارکینگ.", title="پارکینگ",
                                 questions=["پارکینگ کجاست؟", "جای پارک هست؟"],
                                 synonyms=[("پارکینگ", "توقفگاه")])
    other = _seed_proposal(job_id, 2, "متن دیگر")
    from app.services import search
    before = search.published_index_version()
    response = client.post(f"{API}/proposals/{proposal_id}/approve")
    assert response.status_code == 200, response.text
    body = response.json()
    dataset_id = body["dataset_id"]
    assert dataset_id.startswith("ing-") and len(dataset_id) == 14
    assert body["index_version_before"] == before
    assert (body["proposal"]["status"], body["proposal"]["dataset_id"], body["proposal"]["seen"]) == (
        "approved", dataset_id, True)
    assert body["proposal"]["reviewed_by"] == "ingestadmin"
    entry = _rows("SELECT * FROM dataset WHERE id = ?", (dataset_id,))[0]
    assert (entry["title"], entry["text"], entry["position"]) == ("پارکینگ", "متن تازه درباره پارکینگ.", 80)
    assert (entry["title_en"], entry["text_en"], entry["video_url"]) == ("", "", "")
    assert sorted(r["question"] for r in _rows("SELECT question FROM questions WHERE dataset_id = ?",
                                              (dataset_id,))) == ["جای پارک هست؟", "پارکینگ کجاست؟"]
    assert _rows("SELECT source, target FROM synonyms") == [{"source": "پارکینگ", "target": "توقفگاه"}]
    assert publishes == [sorted(["old", dataset_id])]
    assert _rows("SELECT status FROM ingest_jobs")[0]["status"] == "ready"
    client.post(f"{API}/proposals/{other}/reject")
    assert _rows("SELECT status FROM ingest_jobs")[0]["status"] == "done"


def test_a_second_approve_of_one_proposal_is_409_and_writes_nothing(client, publishes):
    job_id = _seed_job("ready")
    proposal_id = _seed_proposal(job_id, 1, "متن یکتا")
    assert client.post(f"{API}/proposals/{proposal_id}/approve").status_code == 200
    body = _refusal(client.post(f"{API}/proposals/{proposal_id}/approve"), 409, "already_reviewed")
    assert body["detail"] == "این پیشنهاد قبلاً بررسی شده است."
    assert len(_rows("SELECT id FROM dataset")) == 1
    assert len(publishes) == 1


def test_the_same_text_from_two_jobs_goes_live_once(client):
    first = _seed_proposal(_seed_job("ready"), 1, "متن مشترک دو سند.")
    second = _seed_proposal(_seed_job("ready"), 1, "متن مشترك دو سند.")
    dataset_id = client.post(f"{API}/proposals/{first}/approve").json()["dataset_id"]
    body = _refusal(client.post(f"{API}/proposals/{second}/approve"), 409, "duplicate")
    assert body["duplicate_of"] == dataset_id
    assert body["detail"] == "همین پاسخ همین حالا اضافه شده است."
    row = _rows("SELECT status, similar_kind, similar_to FROM ingest_proposals WHERE id = ?", (second,))[0]
    assert row == {"status": "pending", "similar_kind": "duplicate", "similar_to": dataset_id}
    assert len(_rows("SELECT id FROM dataset")) == 1


def test_a_shared_title_does_not_block_but_a_shared_text_does(client):
    _live_entry("d-1", "تماس با ما", "شماره تلفن دفتر.")
    job_id = _seed_job("ready")
    same_title = _seed_proposal(job_id, 1, "نشانی ایمیل پشتیبانی.", title="تماس با ما", seen=True)
    same_text = _seed_proposal(job_id, 2, "شماره تلفن دفتر.", title="تلفن", seen=True,
                               similar_kind="duplicate", similar_to="d-1")
    bulk = client.post(f"{API}/jobs/{job_id}/approve-seen").json()
    assert bulk["approved"] == 1
    assert _rows("SELECT status FROM ingest_proposals WHERE id = ?", (same_title,))[0]["status"] == "approved"
    body = _refusal(client.post(f"{API}/proposals/{same_text}/approve"), 409, "duplicate")
    assert body["duplicate_of"] == "d-1"


def test_a_proposal_still_being_prepared_or_of_a_cancelled_job_is_409(client):
    waiting = _seed_proposal(_seed_job("proposing"), 1, "متن", ai_state="waiting")
    _refusal(client.post(f"{API}/proposals/{waiting}/approve"), 409, "not_ready")
    cancelled_job = _seed_job("cancelled")
    gone = _seed_proposal(cancelled_job, 1, "متن دیگر", status="rejected")
    body = _refusal(client.post(f"{API}/proposals/{gone}/approve"), 409, "cancelled")
    assert body["detail"] == "کار این فایل لغو شده است."
    assert _rows("SELECT id FROM dataset") == []


def test_a_failed_job_keeps_its_proposals_approvable(client):
    proposal_id = _seed_proposal(_seed_job("failed", error_code="interrupted"), 1, "متن آماده")
    assert client.post(f"{API}/proposals/{proposal_id}/approve").status_code == 200


def test_a_failing_write_leaves_no_partial_rows(app_env, publishes, monkeypatch):
    from app.db import queries
    from app.db.connection import init_db
    from app.services import ingest
    init_db()
    proposal_id = _seed_proposal(_seed_job("ready"), 1, "متن", questions=["این پرسش است؟"],
                                 synonyms=[("الف", "ب")])

    def broken(conn, pairs):
        raise RuntimeError("synonym insert failed")
    monkeypatch.setattr(queries, "insert_synonym_pairs", broken)
    with pytest.raises(RuntimeError):
        ingest.approve(proposal_id, "admin")
    assert _rows("SELECT id FROM dataset") == [] and _rows("SELECT id FROM questions") == []
    row = _rows("SELECT status, dataset_id FROM ingest_proposals")[0]
    assert row == {"status": "pending", "dataset_id": ""}
    assert publishes == []


def test_a_dataset_id_collision_is_retried_once_with_a_new_id(app_env, monkeypatch):
    from app.db.connection import init_db
    from app.services import ingest
    init_db()
    _live_entry("ing-0000000000", "قبلی", "متن قبلی")
    proposal_id = _seed_proposal(_seed_job("ready"), 1, "متن تازه")
    real = secrets.token_hex
    tokens = iter(["0000000000", "1111111111"])
    monkeypatch.setattr(ingest.secrets, "token_hex", lambda n=None: next(tokens) if n == 5 else real(n))
    assert ingest.approve(proposal_id, "admin")["dataset_id"] == "ing-1111111111"
    assert len(_rows("SELECT id FROM dataset")) == 2


# ── REQ-061, REQ-062: approve what was seen ──────────────────────────────

def test_approve_seen_approves_only_the_seen_unlabeled_ready_proposals(client, publishes):
    job_id = _seed_job("ready")
    seen = [_seed_proposal(job_id, n, f"متن دیده‌شده {n}", seen=True) for n in (1, 2, 3)]
    company = _seed_proposal(job_id, 4, "متن شرکت", seen=True, similar_kind="company", similar_to="c-1")
    unseen = [_seed_proposal(job_id, n, f"متن ندیده {n}") for n in range(5, 11)]
    body = client.post(f"{API}/jobs/{job_id}/approve-seen").json()
    assert (body["approved"], body["skipped"], body["remaining"]) == (3, [], 0)
    assert len(_rows("SELECT id FROM dataset")) == 3
    status = {r["id"]: r["status"] for r in _rows("SELECT id, status FROM ingest_proposals")}
    assert [status[p] for p in seen] == ["approved"] * 3
    assert status[company] == "pending"
    assert {status[p] for p in unseen} == {"pending"}
    assert len(publishes) == 1 and len(publishes[0]) == 3
    assert "index_version_before" in body


def test_approve_seen_skips_a_late_duplicate_and_keeps_going(client, publishes):
    job_id = _seed_job("ready")
    _live_entry("d-1", "زنده", "متن تکراری")
    first = _seed_proposal(job_id, 1, "متن تکراری", seen=True)
    second = _seed_proposal(job_id, 2, "متن تازه", seen=True)
    body = client.post(f"{API}/jobs/{job_id}/approve-seen").json()
    assert body["approved"] == 1
    assert body["skipped"] == [{"id": first, "reason": "duplicate"}]
    assert body["remaining"] == 0
    assert _rows("SELECT status FROM ingest_proposals WHERE id = ?", (second,))[0]["status"] == "approved"


def test_approve_seen_takes_50_at_a_time_and_reports_the_rest(client, publishes):
    job_id = _seed_job("ready")
    for n in range(53):
        _seed_proposal(job_id, n + 1, f"متن شماره {n} برای تأیید", seen=True)
    body = client.post(f"{API}/jobs/{job_id}/approve-seen").json()
    assert (body["approved"], body["remaining"]) == (50, 3)
    body = client.post(f"{API}/jobs/{job_id}/approve-seen").json()
    assert (body["approved"], body["remaining"]) == (3, 0)
    assert len(publishes) == 2
    assert _rows("SELECT status FROM ingest_jobs")[0]["status"] == "done"


def test_approve_seen_with_nothing_seen_publishes_nothing(client, publishes):
    job_id = _seed_job("ready")
    _seed_proposal(job_id, 1, "متن ندیده")
    assert client.post(f"{API}/jobs/{job_id}/approve-seen").json()["approved"] == 0
    assert publishes == []
    cancelled = _seed_job("cancelled")
    _refusal(client.post(f"{API}/jobs/{cancelled}/approve-seen"), 409, "cancelled")
    _refusal(client.post(f"{API}/jobs/nope/approve-seen"), 404, "not_found")


def test_a_long_section_uploaded_as_docx_goes_live_as_three_entries(client, publishes):
    sentences = " ".join((f"جمله شماره {i} از این بخش بلند است و " + "ت" * 100)[:99] + "." for i in range(20))
    data = _docx([("Heading1", "قوانین غرفه"), (None, sentences)])
    job_id = _upload(client, "rules.docx", data).json()["job"]["id"]
    items = client.get(f"{API}/jobs/{job_id}/proposals").json()["items"]
    assert [p["title"] for p in items] == ["قوانین غرفه (بخش ۱ از ۳)", "قوانین غرفه (بخش ۲ از ۳)",
                                           "قوانین غرفه (بخش ۳ از ۳)"]
    assert {p["similar_kind"] for p in items} == {""}
    assert client.post(f"{API}/proposals/{items[0]['id']}/approve").status_code == 200
    client.post(f"{API}/proposals/seen", json={"ids": [p["id"] for p in items[1:]]})
    assert client.post(f"{API}/jobs/{job_id}/approve-seen").json()["approved"] == 2
    titles = sorted(r["title"] for r in _rows("SELECT title FROM dataset"))
    assert titles == sorted(p["title"] for p in items)


# ── REQ-060, REQ-039, REQ-040: reject and cancel ─────────────────────────

def test_reject_records_the_reason_and_the_last_review_finishes_the_job(client):
    job_id = _seed_job("ready")
    proposal_id = _seed_proposal(job_id, 1, "متن")
    _refusal(client.post(f"{API}/proposals/{proposal_id}/reject", json={"reason": "ر" * 201}), 422,
             "reason_too_long")
    response = client.post(f"{API}/proposals/{proposal_id}/reject", json={"reason": "اشتباه است"})
    assert response.status_code == 200
    assert response.json()["proposal"]["status"] == "rejected"
    row = _rows("SELECT reject_reason, reviewed_by FROM ingest_proposals")[0]
    assert row == {"reject_reason": "اشتباه است", "reviewed_by": "ingestadmin"}
    job = _rows("SELECT status, finished_at FROM ingest_jobs")[0]
    assert job["status"] == "done" and job["finished_at"]
    _refusal(client.post(f"{API}/proposals/{proposal_id}/reject"), 409, "already_reviewed")
    _refusal(client.post(f"{API}/proposals/nope/reject"), 404, "not_found")


@pytest.mark.parametrize("before,after", [("queued", "cancelled"), ("extracting", "cancelled"),
                                          ("extracted", "cancelled"), ("proposing", "cancelling")])
def test_cancel_stops_a_running_job_and_rejects_what_is_pending(client, before, after):
    job_id = _seed_job(before)
    pending = _seed_proposal(job_id, 1, "متن", ai_state="waiting")
    response = client.post(f"{API}/jobs/{job_id}/cancel")
    assert response.status_code == 200, response.text
    assert response.json()["job"]["status"] == after
    row = _rows("SELECT status, reject_reason FROM ingest_proposals WHERE id = ?", (pending,))[0]
    assert row == {"status": "rejected", "reject_reason": "cancelled"}


@pytest.mark.parametrize("state,code", [("ready", "not_cancellable"), ("done", "not_cancellable"),
                                        ("failed", "not_cancellable"), ("cancelled", "cancelled"),
                                        ("cancelling", "cancelled")])
def test_cancel_of_a_finished_or_cancelled_job_is_409(client, state, code):
    _refusal(client.post(f"{API}/jobs/{_seed_job(state)}/cancel"), 409, code)


# ── REQ-066, SEC-023: audit ──────────────────────────────────────────────

def test_every_review_action_is_audited_without_the_document_text(client, monkeypatch):
    from app.services import applog
    events = []
    monkeypatch.setattr(applog, "audit", lambda event, message="", actor="", target="", outcome="ok", **f:
                        events.append((event, message, actor, target, f)))
    secret = "متن محرمانه سند"
    job_id = _seed_job("ready")
    a = _seed_proposal(job_id, 1, secret + " یک", seen=True)
    b = _seed_proposal(job_id, 2, secret + " دو")
    c = _seed_proposal(job_id, 3, secret + " سه", seen=True)
    client.put(f"{API}/proposals/{b}", json={"title": "عنوان تازه"})
    client.post(f"{API}/proposals/{b}/approve")
    client.post(f"{API}/proposals/{c}/reject")
    client.post(f"{API}/jobs/{job_id}/approve-seen")
    _upload(client, "x.txt", "متن یک فایل کوچک برای آزمون ساخت.".encode())
    client.post(f"{API}/jobs/{_seed_job('queued')}/cancel")
    by_name = {}
    for event in events:
        by_name.setdefault(event[0], []).append(event)
    expected = ("ingest.proposal.edited", "ingest.proposal.approved", "ingest.proposal.rejected",
                "ingest.bulk_approved", "ingest.job.created", "ingest.job.cancelled")
    for name in expected:
        assert name in by_name, sorted(by_name)
        assert {e[2] for e in by_name[name]} == {"ingestadmin"}, name
    approved_targets = [e[3] for e in by_name["ingest.proposal.approved"]]
    assert any(b in t and "ing-" in t for t in approved_targets)
    assert any(a in t for t in approved_targets)
    assert secret not in json.dumps(events, ensure_ascii=False, default=str)
