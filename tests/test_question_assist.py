"""Question-variant assistant for dataset entries — the backend contract.

The scenario: an admin is editing a knowledge entry and knows visitors phrase
the same need ten different ways, but typing paraphrases by hand is slow and
never happens. One button inside the entry edit modal — «پیشنهاد سوال» —
asks the model (through the Padyar AI wrapper, the only AI path) for
paraphrase questions grounded in that entry's own title/text/existing
questions. The model SUGGESTS; the code decides (company_autofill's
contract): every suggestion is validated before the admin ever sees it —
normalized-unique against the entry's existing questions, 5..120 chars,
Persian unless the entry already carries English questions, deduped, capped.

Nothing is saved by the suggest call. The admin picks with checkboxes and
«افزودن انتخاب‌شده‌ها» posts to apply-questions, which inserts through the
SAME insert statement the single question-create endpoint uses, with ONE
reindex for the whole batch (the bulk-delete precedent).
"""
import datetime
import secrets
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "qassist.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    from app.main import app
    with TestClient(app) as c:
        from app.db.connection import get_db_connection
        conn = get_db_connection()
        # One Persian entry with one existing curated question, and one entry
        # that already carries an English question (the only case where an
        # English suggestion may survive).
        conn.execute(
            "INSERT INTO dataset (id, title, text) VALUES"
            " ('qa1', 'هزینه غرفه', 'متن درباره هزینه شرکت در نمایشگاه'),"
            " ('qa-en', 'Booth cost', 'About booth pricing')")
        conn.execute(
            "INSERT INTO questions (question, dataset_id) VALUES"
            " ('هزینه غرفه چقدر است؟', 'qa1'),"
            " ('How much is the booth?', 'qa-en')")
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


def _fake_model(raw_questions):
    """Patch question_assist._ask_model to return these raw strings."""
    from app.services import question_assist

    async def fake(entry, existing, count):
        return list(raw_questions), SimpleNamespace(
            tokens_total=10, cost=0.001, finish_reason="stop")
    return fake


LONG = "این سوال عمداً بسیار طولانی است و بیشتر از صد و بیست نویسه مجاز است " \
       "تا بی‌درنگ رد شود و هرگز به فهرست پیشنهادها راه پیدا نکند اصلاً و هرگز"


# --- suggest: the validation contract ----------------------------------------

def test_suggest_filters_duplicates_lengths_and_english(admin_client, monkeypatch):
    from app.services import question_assist
    monkeypatch.setattr(question_assist, "_ask_model", _fake_model([
        "هزینه غرفه چقدر است؟",          # exact duplicate of an existing one
        "هزینه غرفه چقدر است",           # same after normalization (؟ stripped)
        "سلام",                          # 4 chars — under the 5-char floor
        LONG,                            # over the 120-char ceiling
        "How much is a booth?",          # English; entry qa1 has none
        "12345",                         # neither Persian nor English
        "قیمت غرفه چقدر می‌شود؟",         # valid
        "قیمت غرفه چقدر می شود؟",        # same after ZWNJ normalization — deduped
        "شرایط پرداخت چیست؟",            # valid
    ]))

    r = admin_client.post("/admin/api/dataset/qa1/suggest-questions")
    assert r.status_code == 200
    suggestions = r.json()["suggestions"]
    assert [s["question"] for s in suggestions] == \
        ["قیمت غرفه چقدر می‌شود؟", "شرایط پرداخت چیست؟"]
    assert all(s["is_new"] for s in suggestions)


def test_suggest_caps_at_count(admin_client, monkeypatch):
    from app.services import question_assist
    monkeypatch.setattr(question_assist, "_ask_model", _fake_model(
        [f"سوال پیشنهادی شماره {i} چیست؟" for i in range(15)]))

    r = admin_client.post("/admin/api/dataset/qa1/suggest-questions")
    assert r.status_code == 200
    assert len(r.json()["suggestions"]) == 10


def test_suggest_allows_english_when_entry_already_has_english(admin_client,
                                                               monkeypatch):
    from app.services import question_assist
    monkeypatch.setattr(question_assist, "_ask_model", _fake_model([
        "What does a booth cost?",        # English entry — allowed
        "قیمت غرفه چقدر است؟",            # Persian — always allowed
    ]))

    r = admin_client.post("/admin/api/dataset/qa-en/suggest-questions")
    assert r.status_code == 200
    assert [s["question"] for s in r.json()["suggestions"]] == \
        ["What does a booth cost?", "قیمت غرفه چقدر است؟"]


def test_suggest_unknown_entry_is_404(admin_client, monkeypatch):
    from app.services import question_assist
    monkeypatch.setattr(question_assist, "_ask_model", _fake_model(["چی؟"]))
    assert admin_client.post(
        "/admin/api/dataset/nope/suggest-questions").status_code == 404


def test_suggest_ai_unavailable_is_503_persian_nothing_written(admin_client,
                                                               monkeypatch):
    from app.services import question_assist

    async def dead(entry, existing, count):
        raise question_assist.QuestionAssistUnavailable(
            "هوش مصنوعی در دسترس نیست (connection_failed).")
    monkeypatch.setattr(question_assist, "_ask_model", dead)

    r = admin_client.post("/admin/api/dataset/qa1/suggest-questions")
    assert r.status_code == 503
    assert "هوش مصنوعی" in r.json()["detail"]
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    n = conn.execute(
        "SELECT COUNT(*) AS n FROM questions WHERE dataset_id = 'qa1'"
    ).fetchone()["n"]
    conn.close()
    assert n == 1  # only the pre-existing curated question


# --- apply: insert through the question-create path --------------------------

def test_apply_inserts_rows_mapped_to_entry_and_reindexes_once(admin_client,
                                                               monkeypatch):
    calls = []
    import app.routers.dataset as dataset_router
    monkeypatch.setattr(dataset_router, "_trigger_reindex",
                        lambda: calls.append(1))

    r = admin_client.post("/admin/api/dataset/qa1/apply-questions", json={
        "questions": ["سوال جدید یک؟", "سوال جدید دو؟"]})
    assert r.status_code == 200
    body = r.json()
    assert body["created"] == 2 and len(body["ids"]) == 2

    from app.db.connection import get_db_connection
    conn = get_db_connection()
    rows = conn.execute(
        "SELECT question FROM questions WHERE dataset_id = 'qa1'"
        " ORDER BY id").fetchall()
    conn.close()
    assert [r_["question"] for r_ in rows] == \
        ["هزینه غرفه چقدر است؟", "سوال جدید یک؟", "سوال جدید دو؟"]
    assert len(calls) == 1  # one reindex for the batch, not per row


def test_apply_rejects_bad_payloads(admin_client):
    for payload in ({}, {"questions": []}, {"questions": "x"},
                    {"questions": [123]}, {"questions": ["  "]}):
        assert admin_client.post(
            "/admin/api/dataset/qa1/apply-questions",
            json=payload).status_code == 400, payload


def test_apply_unknown_entry_is_404(admin_client):
    assert admin_client.post(
        "/admin/api/dataset/nope/apply-questions",
        json={"questions": ["چی؟"]}).status_code == 404


# --- auth / CSRF on both endpoints -------------------------------------------

def test_both_endpoints_reject_anonymous(tmp_path, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "anon.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    from app.main import app
    with TestClient(app) as anon:
        assert anon.post(
            "/admin/api/dataset/qa1/suggest-questions").status_code == 401
        assert anon.post(
            "/admin/api/dataset/qa1/apply-questions",
            json={"questions": ["چی؟"]}).status_code == 401


def test_both_endpoints_reject_missing_csrf_token(admin_client):
    # A valid session WITHOUT the X-CSRF-Token header must be refused by the
    # CSRF middleware (403) before the route runs — same as every other
    # admin mutation.
    admin_client.headers.pop("X-CSRF-Token", None)
    assert admin_client.post(
        "/admin/api/dataset/qa1/suggest-questions").status_code == 403
    assert admin_client.post(
        "/admin/api/dataset/qa1/apply-questions",
        json={"questions": ["چی؟"]}).status_code == 403


# --- the UI wiring (ADR-017: the feature is the scenario, not the endpoint) ---

def test_dataset_edit_modal_carries_the_assist_controls(admin_client):
    r = admin_client.get("/secure-panel-inotex/manage-datasets")
    assert r.status_code == 200
    assert "پیشنهاد سوال" in r.text
    assert "افزودن انتخاب‌شده‌ها" in r.text
