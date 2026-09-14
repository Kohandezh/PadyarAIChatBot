"""Synonym suggestion engine: the model proposes, the operator approves.

The scenario: synonyms were managed purely by hand, and the person who knows
which words visitors actually type is not the person editing the table. One
button on the synonyms page asks the model ONCE — fed with two signals this
install already owns (the questions corpus's frequent terms, and the real
visitor queries the bot answered weakly or not at all) — and shows a checkbox
list. Nothing is saved until the operator picks rows; the apply route then
goes through the SAME write path as the manual add form, so normalization
reload and the index-version bump stay single-source (ADR-017).

These tests pin the backend contract:
  * strict JSON parse of the model answer, validated pair by pair
    (duplicate / already-in-table / self-normalizing / over-length dropped),
  * suggest NEVER writes; apply inserts rows AND bumps the index version,
  * both endpoints refuse an unauthenticated caller,
  * the paid-model cooldown blocks an immediate second request,
  * AI-down degrades to a 503 with a Persian message, nothing written.
"""
import datetime
import json
import secrets

import pytest
from fastapi.testclient import TestClient

SUGGEST_URL = "/admin/api/synonyms/suggest"
APPLY_URL = "/admin/api/synonyms/suggest/apply"


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    """A logged-in operator on a throwaway install with a small corpus."""
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "test_suggest.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)

    from app.main import app
    with TestClient(app) as c:
        from app.db.connection import get_db_connection
        conn = get_db_connection()
        conn.execute("INSERT INTO questions (question, dataset_id)"
                     " VALUES ('هزینه غرفه چقدر است؟', 'd1')"
                     ", ('غرفه‌های نمایشگاهی کجا هستند؟', 'd1')")
        conn.execute("INSERT INTO chat_logs (query, response, source, confidence)"
                     " VALUES ('قیمت اشانتیون چند دره؟', '…', 'no_answer', NULL)")
        conn.execute("INSERT INTO chat_logs (query, response, source, confidence)"
                     " VALUES ('تخفیف ورودی دارید؟', '…', 'local', 0.05)")
        conn.execute("INSERT INTO synonyms (source, target)"
                     " VALUES ('بلیط', 'بلیت')")
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

    import app.utils.normalizer as normalizer
    normalizer.active_synonyms = []


@pytest.fixture
def anon_client(tmp_path, monkeypatch):
    """No session at all — for the auth check on both endpoints."""
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "test_suggest_anon.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)

    from app.main import app
    with TestClient(app) as c:
        yield c

    import app.utils.normalizer as normalizer
    normalizer.active_synonyms = []


def _mock_ai(monkeypatch, suggestions=None, error=None, capture=None):
    """Patch the ONE runtime AI interface (padyar_ai.generate) for one test."""
    from app.services.ai.wrapper import padyar_ai
    from app.services.ai.request import AIResponse

    async def fake_generate(messages, **kwargs):
        if capture is not None:
            capture.append({"messages": messages, "kwargs": kwargs})
        if error is not None:
            raise error
        return AIResponse(content=json.dumps({"suggestions": suggestions},
                                             ensure_ascii=False))

    monkeypatch.setattr(padyar_ai, "generate", fake_generate)


def _synonym_rows(client):
    return client.get("/api/synonyms").json()["synonyms"]


# ── Suggest: parse, validate, never save ──────────────────────────────────

def test_suggest_parses_and_returns_without_saving(admin_client, monkeypatch):
    _mock_ai(monkeypatch, suggestions=[
        {"word": "اشانتیون", "suggestion": "غرفه",
         "reason": "کاربران «اشانتیون» می‌نویسند"},
    ])
    res = admin_client.post(SUGGEST_URL)
    assert res.status_code == 200
    body = res.json()
    assert body == {"suggestions": [
        {"word": "اشانتیون", "suggestion": "غرفه",
         "reason": "کاربران «اشانتیون» می‌نویسند"}]}
    # Suggest is read-only: the pre-seeded pair is still the only row.
    assert _synonym_rows(admin_client) == [
        {"source": "بلیط", "target": "بلیت"}]


def test_suggest_filters_invalid_suggestions(admin_client, monkeypatch):
    _mock_ai(monkeypatch, suggestions=[
        {"word": "اشانتیون", "suggestion": "غرفه", "reason": "معتبر"},
        # duplicate of the first, word-for-word
        {"word": "اشانتیون", "suggestion": "غرفه", "reason": "تکراری"},
        # pair already in the synonyms table
        {"word": "بلیط", "suggestion": "بلیت", "reason": "از قبل هست"},
        # the two sides normalize to the same string (Arabic ي)
        {"word": "هزینه", "suggestion": "هزينه", "reason": "شکل نوشتار"},
        # a side longer than the 40-char cap
        {"word": "ا" * 41, "suggestion": "غرفه", "reason": "بلند"},
        {"word": "غرفه", "suggestion": "ب" * 45, "reason": "بلند"},
        # empty side
        {"word": "", "suggestion": "غرفه", "reason": "خالی"},
    ])
    res = admin_client.post(SUGGEST_URL)
    assert res.status_code == 200
    assert res.json()["suggestions"] == [
        {"word": "اشانتیون", "suggestion": "غرفه", "reason": "معتبر"}]


def test_prompt_carries_corpus_terms_and_weak_queries(admin_client, monkeypatch):
    captured = []
    _mock_ai(monkeypatch, suggestions=[], capture=captured)
    res = admin_client.post(SUGGEST_URL)
    assert res.status_code == 200
    assert len(captured) == 1
    prompt = captured[0]["kwargs"]["system_prompt"]
    # Frequent term of the curated questions corpus …
    assert "غرفه" in prompt
    # … and the real visitor queries the bot answered weakly / not at all.
    assert "اشانتیون" in prompt
    assert "تخفیف ورودی" in prompt


def test_ai_unavailable_is_503_with_persian_message(admin_client, monkeypatch):
    from app.services.ai.errors import AIError
    _mock_ai(monkeypatch, error=AIError(code="connection_failed"))
    res = admin_client.post(SUGGEST_URL)
    assert res.status_code == 503
    assert "هوش مصنوعی در دسترس نیست" in res.json()["detail"]


def test_unparseable_model_answer_means_no_suggestions(admin_client, monkeypatch):
    from app.services.ai.wrapper import padyar_ai
    from app.services.ai.request import AIResponse

    async def fake_generate(messages, **kwargs):
        return AIResponse(content="این متن JSON نیست")

    monkeypatch.setattr(padyar_ai, "generate", fake_generate)
    res = admin_client.post(SUGGEST_URL)
    assert res.status_code == 200
    assert res.json() == {"suggestions": []}


# ── Cooldown: one paid-model call per window ──────────────────────────────

def test_second_immediate_suggest_hits_cooldown(admin_client, monkeypatch):
    _mock_ai(monkeypatch, suggestions=[])
    assert admin_client.post(SUGGEST_URL).status_code == 200
    second = admin_client.post(SUGGEST_URL)
    assert second.status_code == 429
    assert "چند لحظه صبر کنید" in second.json()["detail"]


# ── Apply: through the existing add-synonym write path ────────────────────

def test_apply_inserts_pairs_and_bumps_index_once(admin_client, monkeypatch):
    import app.services.search as search
    bumps = []
    monkeypatch.setattr(search, "bump_index_version", lambda: bumps.append(1))

    res = admin_client.post(APPLY_URL, json={"pairs": [
        {"word": "اشانتیون", "suggestion": "غرفه"},
        {"word": "لیزیک", "suggestion": "فمتو"},
    ]})
    assert res.status_code == 200
    assert res.json() == {"status": "success", "added": 2}
    assert len(bumps) == 1

    rows = {(r["source"], r["target"]) for r in _synonym_rows(admin_client)}
    assert ("اشانتیون", "غرفه") in rows
    assert ("لیزیک", "فمتو") in rows
    assert ("بلیط", "بلیت") in rows


def test_apply_rejects_bad_payloads(admin_client):
    cases = [
        {},
        {"pairs": []},
        {"pairs": "not-a-list"},
        {"pairs": ["not-a-pair"]},
        {"pairs": [{"word": "", "suggestion": "غرفه"}]},
        {"pairs": [{"word": "غرفه"}]},
        # self-normalizing pair — the same rule the suggest filter applies
        {"pairs": [{"word": "هزینه", "suggestion": "هزينه"}]},
        # over the 40-char side cap
        {"pairs": [{"word": "ا" * 41, "suggestion": "غرفه"}]},
        # more pairs than one apply may carry
        {"pairs": [{"word": f"و{i}", "suggestion": f"ج{i}"} for i in range(51)]},
    ]
    for payload in cases:
        res = admin_client.post(APPLY_URL, json=payload)
        assert res.status_code == 400, payload
    assert len(_synonym_rows(admin_client)) == 1


def test_apply_is_idempotent_for_an_existing_pair(admin_client, monkeypatch):
    import app.services.search as search
    monkeypatch.setattr(search, "bump_index_version", lambda: None)
    res = admin_client.post(APPLY_URL, json={"pairs": [
        {"word": "بلیط", "suggestion": "بلیت"}]})
    assert res.status_code == 200
    assert res.json()["added"] == 0
    assert len(_synonym_rows(admin_client)) == 1


# ── Auth: every endpoint checks the admin session itself ──────────────────

def test_suggest_requires_admin(anon_client):
    assert anon_client.post(SUGGEST_URL).status_code in (401, 403)


def test_apply_requires_admin(anon_client):
    res = anon_client.post(APPLY_URL, json={"pairs": [
        {"word": "اشانتیون", "suggestion": "غرفه"}]})
    assert res.status_code in (401, 403)


# ── Wiring: the page actually carries the button ──────────────────────────

def test_synonyms_page_carries_the_button(admin_client):
    res = admin_client.get("/secure-panel-inotex/synonyms")
    assert res.status_code == 200
    assert 'id="suggest-synonyms-btn"' in res.text
    assert "پیشنهاد هوشمند مترادف" in res.text
    assert 'id="suggestModal"' in res.text
    assert "افزودن انتخاب‌شده‌ها" in res.text
