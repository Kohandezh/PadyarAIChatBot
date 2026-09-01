"""Thumbs up / down on one of the visitor's own assistant replies.

WHAT THIS ADDS: `migrations/0025_message_feedback.sql` gives `messages` a
`feedback` TEXT column ('up' / 'down' / '', mirrored for SQLite in
app/db/connection.py), a service function
(app/services/conversations.set_message_feedback) and a router endpoint
(POST /api/chat/messages/{id}/feedback in app/routers/chat.py) that sets it.

WHAT THIS FILE PINS DOWN
------------------------
1. A visitor can rate their own message up or down, and it round-trips
   through GET /api/chat/conversations/{id} (conversation_messages() already
   does `SELECT *`, so `feedback` needs no extra plumbing on that path).
2. `rating: null` clears it — a visitor tapping an already-active thumb
   again to take it back, not an error.
3. Rating a message in someone else's conversation is refused with the same
   404 a nonexistent message id gets — the caller cannot tell those apart,
   same privacy pattern as GET/DELETE .../conversations/{id}
   (see get_conversation_for_visitor's docstring).
4. Anonymous (no visitor session) gets the same 401 the other
   /api/chat/conversations... endpoints give — REGISTRATION_REQUIRED.
5. The endpoint is origin-checked, same as its siblings.

Service-level tests call app.services.conversations.set_message_feedback
directly, the same split test_conversations_store.py already uses for the
rest of that module. Endpoint tests go through the real app + TestClient, the
same pattern tests/test_menu_and_history_pagination.py uses for the sibling
`/api/chat/conversations` endpoints.
"""
import pytest
from fastapi.testclient import TestClient

BROWSER = {"Origin": "http://localhost", "User-Agent": "pytest-agent/1.0"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "message_feedback.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    from app.main import app
    with TestClient(app) as c:
        c.headers.update(BROWSER)
        yield c


def _sign_in(client, phone):
    from app.auth import visitor as visitor_auth
    from app.config import VISITOR_COOKIE_NAME
    from app.services import conversations
    visitor_id = conversations.upsert_visitor(first_name="تست", phone=phone)
    token = visitor_auth.mint(visitor_id)
    assert token, "mint() failed — session was never created"
    client.cookies.set(VISITOR_COOKIE_NAME, token)
    return visitor_id


def _seed_conversation_with_reply(visitor_id: str, conv_id: str) -> int:
    """One conversation, one visitor turn, one assistant reply owned by
    `visitor_id`. Returns the assistant message's id — the thing under test."""
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    conn.execute(
        "INSERT INTO conversations (id, visitor_id, message_count)"
        " VALUES (?, ?, 2)", (conv_id, visitor_id))
    conn.execute(
        "INSERT INTO messages (conversation_id, role, text) VALUES (?, 'visitor', ?)",
        (conv_id, "سوال آزمایشی"))
    cur = conn.execute(
        "INSERT INTO messages (conversation_id, role, text, source)"
        " VALUES (?, 'assistant', ?, 'local')",
        (conv_id, "پاسخ آزمایشی"))
    message_id = cur.lastrowid
    conn.commit()
    conn.close()
    return message_id


# ── Service layer ────────────────────────────────────────────────────────

class TestSetMessageFeedbackService:

    def test_rating_your_own_message_up_succeeds_and_sticks(self, client):
        from app.services import conversations
        visitor_id = _sign_in(client, "+989120000401")
        message_id = _seed_conversation_with_reply(visitor_id, "conv-svc-1")

        assert conversations.set_message_feedback(message_id, visitor_id, "up")

        from app.db.connection import get_db_connection
        conn = get_db_connection()
        row = conn.execute("SELECT feedback FROM messages WHERE id = ?",
                           (message_id,)).fetchone()
        conn.close()
        assert row["feedback"] == "up"

    def test_rating_down_overwrites_a_previous_up(self, client):
        from app.services import conversations
        visitor_id = _sign_in(client, "+989120000402")
        message_id = _seed_conversation_with_reply(visitor_id, "conv-svc-2")

        conversations.set_message_feedback(message_id, visitor_id, "up")
        conversations.set_message_feedback(message_id, visitor_id, "down")

        from app.db.connection import get_db_connection
        conn = get_db_connection()
        row = conn.execute("SELECT feedback FROM messages WHERE id = ?",
                           (message_id,)).fetchone()
        conn.close()
        assert row["feedback"] == "down"

    def test_a_null_rating_clears_existing_feedback(self, client):
        from app.services import conversations
        visitor_id = _sign_in(client, "+989120000403")
        message_id = _seed_conversation_with_reply(visitor_id, "conv-svc-3")
        conversations.set_message_feedback(message_id, visitor_id, "up")

        assert conversations.set_message_feedback(message_id, visitor_id, None)

        from app.db.connection import get_db_connection
        conn = get_db_connection()
        row = conn.execute("SELECT feedback FROM messages WHERE id = ?",
                           (message_id,)).fetchone()
        conn.close()
        assert row["feedback"] == ""

    def test_rating_a_message_in_someone_elses_conversation_is_refused(self, client):
        from app.services import conversations
        owner_id = _sign_in(client, "+989120000404")
        message_id = _seed_conversation_with_reply(owner_id, "conv-svc-4")
        stranger_id = conversations.upsert_visitor(
            first_name="غریبه", phone="+989120000405")

        assert not conversations.set_message_feedback(
            message_id, stranger_id, "up")

        from app.db.connection import get_db_connection
        conn = get_db_connection()
        row = conn.execute("SELECT feedback FROM messages WHERE id = ?",
                           (message_id,)).fetchone()
        conn.close()
        assert row["feedback"] == "", "a refused write must not touch the row"

    def test_an_unknown_message_id_is_refused_not_an_error(self, client):
        from app.services import conversations
        visitor_id = _sign_in(client, "+989120000406")

        assert not conversations.set_message_feedback(999_999, visitor_id, "up")

    def test_an_out_of_vocabulary_rating_is_treated_as_a_clear(self, client):
        """The router validates the Pydantic field's type; anything that
        reaches the service which is not 'up'/'down' is a clear, not a 500 —
        same fail-safe shape the rest of this module uses."""
        from app.services import conversations
        visitor_id = _sign_in(client, "+989120000407")
        message_id = _seed_conversation_with_reply(visitor_id, "conv-svc-7")
        conversations.set_message_feedback(message_id, visitor_id, "up")

        assert conversations.set_message_feedback(message_id, visitor_id, "sideways")

        from app.db.connection import get_db_connection
        conn = get_db_connection()
        row = conn.execute("SELECT feedback FROM messages WHERE id = ?",
                           (message_id,)).fetchone()
        conn.close()
        assert row["feedback"] == ""


# ── Endpoint ─────────────────────────────────────────────────────────────

class TestFeedbackEndpoint:

    def test_requires_a_signed_in_visitor(self, client):
        r = client.post("/api/chat/messages/1/feedback", json={"rating": "up"})
        assert r.status_code == 401

    def test_up_then_reflected_in_conversation_history(self, client):
        visitor_id = _sign_in(client, "+989120000411")
        message_id = _seed_conversation_with_reply(visitor_id, "conv-ep-1")

        r = client.post(f"/api/chat/messages/{message_id}/feedback",
                        json={"rating": "up"})
        assert r.status_code == 200, r.text
        assert r.json() == {"ok": True}

        history = client.get("/api/chat/conversations/conv-ep-1")
        assert history.status_code == 200, history.text
        assistant_rows = [m for m in history.json()["messages"]
                          if m["role"] == "assistant"]
        assert len(assistant_rows) == 1
        assert assistant_rows[0]["feedback"] == "up"

    def test_down_then_reflected_too(self, client):
        visitor_id = _sign_in(client, "+989120000412")
        message_id = _seed_conversation_with_reply(visitor_id, "conv-ep-2")

        r = client.post(f"/api/chat/messages/{message_id}/feedback",
                        json={"rating": "down"})
        assert r.status_code == 200, r.text

        history = client.get("/api/chat/conversations/conv-ep-2").json()
        assert history["messages"][-1]["feedback"] == "down"

    def test_null_rating_clears_it_through_the_endpoint(self, client):
        visitor_id = _sign_in(client, "+989120000413")
        message_id = _seed_conversation_with_reply(visitor_id, "conv-ep-3")
        client.post(f"/api/chat/messages/{message_id}/feedback",
                   json={"rating": "up"})

        r = client.post(f"/api/chat/messages/{message_id}/feedback",
                        json={"rating": None})
        assert r.status_code == 200, r.text

        history = client.get("/api/chat/conversations/conv-ep-3").json()
        assert history["messages"][-1]["feedback"] == ""

    def test_an_omitted_rating_field_also_clears_it(self, client):
        """MessageFeedbackRequest.rating defaults to None — an empty body is
        not rejected as malformed, it behaves exactly like `{"rating": null}`."""
        visitor_id = _sign_in(client, "+989120000414")
        message_id = _seed_conversation_with_reply(visitor_id, "conv-ep-4")
        client.post(f"/api/chat/messages/{message_id}/feedback",
                   json={"rating": "up"})

        r = client.post(f"/api/chat/messages/{message_id}/feedback", json={})
        assert r.status_code == 200, r.text

        history = client.get("/api/chat/conversations/conv-ep-4").json()
        assert history["messages"][-1]["feedback"] == ""

    def test_a_message_in_someone_elses_conversation_is_404(self, client):
        owner_id = _sign_in(client, "+989120000415")
        message_id = _seed_conversation_with_reply(owner_id, "conv-ep-5")

        from app.main import app
        with TestClient(app) as stranger:
            stranger.headers.update(BROWSER)
            _sign_in(stranger, "+989120000416")
            r = stranger.post(f"/api/chat/messages/{message_id}/feedback",
                             json={"rating": "up"})

        assert r.status_code == 404, r.text

        # And the real owner's row is untouched by the refused attempt.
        history = client.get("/api/chat/conversations/conv-ep-5").json()
        assert history["messages"][-1]["feedback"] == ""

    def test_an_unknown_message_id_is_404(self, client):
        _sign_in(client, "+989120000417")
        r = client.post("/api/chat/messages/999999/feedback",
                        json={"rating": "up"})
        assert r.status_code == 404, r.text

    def test_requires_the_allowed_origin(self, client):
        visitor_id = _sign_in(client, "+989120000418")
        message_id = _seed_conversation_with_reply(visitor_id, "conv-ep-6")

        r = client.post(f"/api/chat/messages/{message_id}/feedback",
                        json={"rating": "up"},
                        headers={"Origin": "https://evil.example"})
        assert r.status_code == 403, r.text


# ── The header-driven live turn, end to end ─────────────────────────────

def test_the_x_message_id_header_from_a_live_turn_can_be_rated_immediately(
        client, monkeypatch):
    """The whole point of X-Message-Id: rate a reply from THIS SAME session,
    with no reopen-from-history round trip in between."""
    import app.config as config
    from app.db.queries import set_setting
    from app.auth.security import generate_chat_token

    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    conn.execute("DELETE FROM dataset")
    conn.execute("DELETE FROM questions")
    conn.execute("INSERT INTO dataset (id, title, text, video_url)"
                 " VALUES ('faq-x', 'ساعت کاری', 'هر روز از ۹ تا ۱۸.', '')")
    conn.execute("INSERT INTO questions (question, dataset_id, video_url)"
                 " VALUES ('ساعت کاری نمایشگاه چیست؟', 'faq-x', '')")
    conn.commit()
    conn.close()
    from app.services import search
    search.load_dataset_internal()
    set_setting("openai_enabled", "false")

    visitor_id = _sign_in(client, "+989120000419")
    client.headers.update({"X-Chat-Token": generate_chat_token()})

    chat_response = client.post(
        "/chat", json={"message": "ساعت کاری نمایشگاه چیست؟", "lang": "fa"})
    assert chat_response.status_code == 200, chat_response.text
    message_id = chat_response.headers.get("X-Message-Id")
    assert message_id, "X-Message-Id missing on a normal answered turn"

    r = client.post(f"/api/chat/messages/{message_id}/feedback",
                    json={"rating": "up"})
    assert r.status_code == 200, r.text
