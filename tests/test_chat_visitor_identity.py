"""POST /chat takes identity from the session cookie, never from the request.

WHAT WAS BROKEN
---------------
`ChatRequest` had a `visitor` field. A caller posted their own job, position
and interests in the BODY and the targeted-visit planner personalised the
answer from it. Four extra fields made anybody a registered visitor. The
signup wall in front of the chat was a frontend function
(`ChatConfig.sendGateFn` in static/companion/registration.js), so a direct POST
with a valid chat token skipped it entirely. And `padyar_conv`, an unsigned
conversation id in a cookie, let anyone who pasted somebody else's id append to
that person's transcript and be answered from their history.

WHAT THIS FILE PINS DOWN
------------------------
1. A profile in the body is IGNORED. The same profile in a real session is
   used. That pair is the whole fix, so both halves are asserted against the
   one entry in the knowledge base whose answer is personalised.
2. The registration gate is enforced by the SERVER, with a machine-readable
   401 the frontend can branch on.
3. An install that does not load the registration module is never gated. The
   elecomp deployment is exactly that install and it must keep working with
   zero change, so it gets its own test.
4. A conversation that belongs to one visitor cannot be continued by another,
   while an ANONYMOUS conversation is still claimed by the person who
   registers halfway through — that claim is what keeps the questions somebody
   asked before they had a name.

Covers app/models.py (ChatRequest), the guards and the 12 rewritten call sites
in app/routers/chat.py, and the ownership rules in app/services/conversations.py.
"""
import pytest
from fastapi.testclient import TestClient


# The one entry the chatbot personalises. Everything else answers the same for
# everybody, which is what makes the difference below meaningful.
TARGETED_QUESTION = "بازدید هدفمند چیست"
TARGETED_TEXT = "بازدید هدفمند یعنی غرفه‌های مرتبط با کار شما را زودتر ببینید."
PLAIN_QUESTION = "ساعت کاری نمایشگاه چیست؟"

DATASET = [
    ("inotex-targeted-visit", "بازدید هدفمند", TARGETED_TEXT, ""),
    ("faq-hours", "ساعت کاری", "نمایشگاه هر روز از ۹ صبح تا ۱۸ باز است.", ""),
]

# A line the planner only writes for someone whose interests mention AI. Its
# presence in an answer is the proof that a profile reached the planner; its
# absence is the proof that one did not. See tests/test_visit_plan.py, which
# pins the same marker at the unit level.
AI_INTEREST = "هوش مصنوعی"
PLAN_MARKER = "همایش ملی هوش مصنوعی"

# The old hole, in the exact shape the frontend used to post it.
BODY_PROFILE = {"job": "پژوهشگر", "position": "مدیر", "interests": AI_INTEREST}


def _seed():
    import app.db.connection as dbc

    conn = dbc.get_db_connection()
    conn.execute("DELETE FROM dataset")
    conn.execute("DELETE FROM questions")
    conn.execute("DELETE FROM synonyms")
    for entry_id, title, text, video in DATASET:
        conn.execute("INSERT INTO dataset (id, title, text, video_url)"
                     " VALUES (?, ?, ?, ?)", (entry_id, title, text, video))
    for question, entry_id in ((TARGETED_QUESTION, "inotex-targeted-visit"),
                               (PLAIN_QUESTION, "faq-hours")):
        conn.execute("INSERT INTO questions (question, dataset_id, video_url)"
                     " VALUES (?, ?, '')", (question, entry_id))
    conn.commit()
    conn.close()

    from app.services import search
    search.load_dataset_internal()


def _client(app):
    """A browser. Its own cookie jar, its own chat token.

    A factory and not one shared client, because half this file is about two
    DIFFERENT people at the same booth and a shared jar cannot express that.
    """
    from app.auth.security import generate_chat_token
    c = TestClient(app)
    c.headers.update({"Origin": "http://localhost",
                      "X-Chat-Token": generate_chat_token(),
                      "User-Agent": "KioskBrowser/1.0"})
    return c


@pytest.fixture
def app(tmp_path, monkeypatch):
    """The REAL app on a throwaway database.

    The real one on purpose: the profile this file is about is put on
    `request.state` by the `resolve_visitor` middleware, and a hand-built app
    holding only the chat router would prove nothing about whether that
    middleware runs.
    """
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "chat-identity.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    from app.main import app as fastapi_app

    with TestClient(fastapi_app):          # lifespan: init_db, index load
        from app.db.queries import set_setting
        # No network in this file. Every tier exercised here is a local one,
        # and a provider call would make the test flaky about something it is
        # not testing.
        set_setting("openai_enabled", "false")
        _seed()
        yield fastapi_app


@pytest.fixture
def client(app):
    return _client(app)


@pytest.fixture(autouse=True)
def _no_rate_limit_carryover():
    """Each test starts with empty buckets. Several tests send half a dozen
    messages from one address, and a leftover bucket would fail the NEXT test
    for a reason that has nothing to do with it."""
    from app.auth import security
    security._chat_rate_limits.clear()
    yield
    security._chat_rate_limits.clear()


# ── Helpers ──────────────────────────────────────────────────────────────

def _cookie_name():
    from app.auth.visitor import VISITOR_COOKIE_NAME
    return VISITOR_COOKIE_NAME


def _ask(client, message=PLAIN_QUESTION, **body):
    return client.post("/chat", json={"message": message, "lang": "fa", **body})


def _sign_in(client, *, phone="09120000001", job="", position="",
             interests=""):
    """Put a REAL, server-minted session in this browser. Returns the id.

    The same two steps POST /api/auth/otp/verify performs once the code checks
    out: a row in `visitors`, then `visitor_auth.mint()` and the cookie. Done
    at the seam rather than through the OTP endpoint so this file tests the
    chat side and does not fail when the registration flow changes shape.
    """
    from app.auth import visitor as visitor_auth
    from app.services.conversations import upsert_visitor
    visitor_id = upsert_visitor(first_name="آزمون", last_name="کاربر",
                                phone=phone, job=job, position=position,
                                interests=interests)
    token = visitor_auth.mint(visitor_id)
    assert token, "the session store must be able to mint"
    _set_cookie(client, _cookie_name(), token)
    return visitor_id


def _set_cookie(client, name, value):
    """Replace a cookie in this browser's jar.

    delete() first, and always: httpx files a hand-set cookie under an empty
    domain and a server-set one under the request host, and a jar holding two
    cookies of the same name refuses to read either.
    """
    client.cookies.delete(name)
    client.cookies.set(name, value)


def _conv(response):
    """The conversation id the server put on THIS response.

    Read from the response rather than the jar for the same reason as above,
    and it says the more useful thing anyway: which conversation the server
    decided this turn belongs to.
    """
    return response.cookies.get("padyar_conv", "")


def _rows(sql, args=()):
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    try:
        return [dict(r) for r in conn.execute(sql, args)]
    finally:
        conn.close()


def _messages(conversation_id):
    return _rows("SELECT * FROM messages WHERE conversation_id = ?"
                 " ORDER BY id ASC", (conversation_id,))


def _owner(conversation_id):
    rows = _rows("SELECT visitor_id FROM conversations WHERE id = ?",
                 (conversation_id,))
    return rows[0]["visitor_id"] if rows else None


def _gate_on():
    from app.db.queries import set_setting
    set_setting("registration_enabled", "true")


# ── The body is not identity ─────────────────────────────────────────────

class TestTheBodyIsNotIdentity:
    """The hole itself: a self-described visitor in the POST body.

    Both directions are asserted. Showing that the body is ignored proves
    nothing on its own — an answer that is never personalised for anybody
    would pass it. So the same profile is also delivered the legitimate way,
    and the two answers must differ.
    """

    def test_a_profile_in_the_body_does_not_personalise_the_answer(self, client):
        r = _ask(client, TARGETED_QUESTION, visitor=BODY_PROFILE)

        assert r.status_code == 200, r.text
        text = r.json()["text"]
        assert text.startswith(TARGETED_TEXT)
        # Nothing the caller wrote about themselves reached the planner.
        assert PLAN_MARKER not in text, text

    def test_the_same_profile_in_a_session_does_personalise_it(self, client):
        _sign_in(client, interests=AI_INTEREST)

        r = _ask(client, TARGETED_QUESTION)

        assert r.status_code == 200, r.text
        text = r.json()["text"]
        assert text.startswith(TARGETED_TEXT)
        assert PLAN_MARKER in text, text

    def test_the_body_cannot_overrule_the_session(self, client):
        """A signed-in visitor who posts a DIFFERENT profile is still answered
        from their own. The body is not a second opinion, it is nothing."""
        _sign_in(client, interests="غذا و نوشیدنی")

        text = _ask(client, TARGETED_QUESTION, visitor=BODY_PROFILE).json()["text"]

        assert PLAN_MARKER not in text, text

    def test_an_old_frontend_still_chats(self, client):
        """A browser with the previous JS cached keeps posting `visitor`. That
        must be a normal answered turn, not a 422 — the field is ignored, and
        ignoring is not the same as rejecting."""
        r = _ask(client, PLAIN_QUESTION, visitor=BODY_PROFILE)

        assert r.status_code == 200, r.text
        assert r.json()["source"] == "local_questions"

    def test_the_request_model_no_longer_has_the_field(self):
        """The field is GONE, not defaulted to None. A model that still
        declares it would start believing bodies again the moment somebody
        read `request.visitor`."""
        from app.models import ChatRequest
        assert "visitor" not in ChatRequest.model_fields

        # ...and the shape itself survives, because the server now builds it.
        from app.models import VisitorProfile
        assert VisitorProfile(job="ج", position="س", interests="ع").job == "ج"


# ── The gate ─────────────────────────────────────────────────────────────

class TestTheRegistrationGate:

    def test_no_session_is_401_with_a_machine_readable_marker(self, client):
        """The frontend has to tell "sign up first" apart from every other
        401, and matching on Persian prose is not a contract."""
        from app.auth.visitor import REGISTRATION_REQUIRED
        _gate_on()

        r = _ask(client)

        assert r.status_code == 401, r.text
        detail = r.json()["detail"]
        assert detail["code"] == REGISTRATION_REQUIRED
        assert detail["message"], "a human-readable line has to be there too"

    def test_a_forged_cookie_does_not_open_the_gate(self, client):
        """The cookie must hold a token this server minted. A visitor id, or
        anything else pasted in, is anonymous."""
        _gate_on()
        visitor_id = _sign_in(client)
        _set_cookie(client, _cookie_name(), visitor_id)  # the id, not the token

        assert _ask(client).status_code == 401

    def test_a_signed_in_visitor_is_answered(self, client):
        # Signed in AND signed up: /chat now refuses an incomplete profile
        # with 403 signup_incomplete, so the pass-case needs a complete row
        # (every value a real taxonomy label).
        _gate_on()
        _sign_in(client, job="خبرنگار / رسانه", position="کارشناس",
                 interests="هوش مصنوعی")

        r = _ask(client)

        assert r.status_code == 200, r.text
        assert r.json()["source"] == "local_questions"

    def test_the_gate_is_shut_until_the_operator_opens_it(self, client):
        """`registration_enabled` defaults to false. An install that never
        turned registration on answers everybody, exactly as it does today."""
        from app.db.queries import get_setting
        assert get_setting("registration_enabled", "false") == "false"

        assert _ask(client).status_code == 200

    def test_an_install_without_the_registration_module_is_never_gated(
            self, client, monkeypatch):
        """THE ELECOMP CASE. Read this before changing the gate.

        The second live install does not load the registration module: its
        /api/auth/registration-status is a 404, there is no /verify page and
        no OTP endpoint, so no visitor there can ever hold a session. If the
        gate asked only about the setting, that whole install would answer
        401 to every message and the chatbot would be dead.

        The gate's first condition is `is_module_enabled("registration")`,
        read from the module registry, so this test pins the registry to the
        elecomp shape and leaves the operator setting switched ON — the
        strictest possible version of the case.
        """
        import app.config as config
        from app.db.queries import get_setting
        _gate_on()
        without_registration = [m for m in config.ENABLED_MODULES
                                if m != "registration"]
        monkeypatch.setattr(config, "ENABLED_MODULES", without_registration)

        # Not vacuous: the setting really is on, and the module really is off.
        assert get_setting("registration_enabled", "false") == "true"
        assert not config.is_module_enabled("registration")

        r = _ask(client)

        assert r.status_code == 200, r.text
        assert r.json()["source"] == "local_questions"

    def test_the_gate_never_reads_a_header_or_a_body_field(self, client):
        """The credential is the cookie. Handing the server a real visitor id
        through a channel the caller writes must change nothing."""
        _gate_on()
        visitor_id = _sign_in(client)
        client.cookies.delete(_cookie_name())

        r = client.post("/chat",
                        json={"message": PLAIN_QUESTION,
                              "visitor_id": visitor_id,
                              "challenge_id": visitor_id,
                              "visitor": BODY_PROFILE},
                        headers={"X-Visitor-Id": visitor_id,
                                 "Authorization": f"Bearer {visitor_id}"})

        assert r.status_code == 401, r.text


# ── Whose conversation is it ─────────────────────────────────────────────

class TestConversationOwnership:

    def test_a_visitor_cannot_continue_another_visitors_conversation(self, app):
        """`padyar_conv` is unsigned, so knowing an id must not be owning it.

        Continuing somebody else's conversation is two harms at once: your
        messages land in their transcript, and their history is what the
        model is shown when it answers you.
        """
        alice = _client(app)
        alice_id = _sign_in(alice, phone="09120000001")
        conv_a = _conv(_ask(alice, PLAIN_QUESTION))
        assert conv_a and _owner(conv_a) == alice_id
        assert len(_messages(conv_a)) == 2

        bob = _client(app)
        _sign_in(bob, phone="09120000002")
        _set_cookie(bob, "padyar_conv", conv_a)     # the pasted cookie
        answer = _ask(bob, PLAIN_QUESTION)
        assert answer.status_code == 200

        # Bob was given a conversation of his own...
        conv_b = _conv(answer)
        assert conv_b and conv_b != conv_a
        # ...Alice's is untouched, and still hers...
        assert len(_messages(conv_a)) == 2
        assert _owner(conv_a) == alice_id
        # ...and Bob's holds only Bob's turn.
        assert len(_messages(conv_b)) == 2

    def test_an_anonymous_visitor_cannot_pick_up_an_owned_conversation(self, app):
        """The kiosk case. One browser, one cookie, many people: the next
        person at the screen has no session, and must not inherit the last
        person's chat."""
        alice = _client(app)
        alice_id = _sign_in(alice, phone="09120000001")
        conv_a = _conv(_ask(alice, PLAIN_QUESTION))

        stranger = _client(app)
        _set_cookie(stranger, "padyar_conv", conv_a)   # no session cookie
        answer = _ask(stranger, PLAIN_QUESTION)
        assert answer.status_code == 200

        assert _conv(answer) != conv_a
        assert len(_messages(conv_a)) == 2
        assert _owner(conv_a) == alice_id

    def test_your_own_conversation_stays_yours(self, app):
        """The refusal must not fire on the ordinary case, or every message
        would start a new conversation and the pick tier would forget its
        list between turns."""
        alice = _client(app)
        _sign_in(alice)
        conv = _conv(_ask(alice, PLAIN_QUESTION))

        again = _ask(alice, TARGETED_QUESTION)

        assert _conv(again) == conv
        assert len(_messages(conv)) == 4

    def test_a_conversation_started_anonymously_is_claimed_on_signup(self, app):
        """Somebody asks four questions, THEN registers. Those questions are
        theirs and stay where they are.

        This is what `_promote_to_visitor` (app/routers/otp.py) does on a
        verified code, performed here at the seam it calls: register_visitor()
        claims the conversation named by `padyar_conv`, and the session is
        minted for the same person. The claim only works because an UNOWNED
        conversation is still handed over — if ownership were required to
        continue one, the pre-registration half of every signup would be lost.
        """
        from app.auth import visitor as visitor_auth
        from app.services import conversations

        walk_up = _client(app)
        _ask(walk_up, PLAIN_QUESTION)
        conv = _conv(_ask(walk_up, TARGETED_QUESTION))
        assert _owner(conv) == ""            # nobody's yet
        assert len(_messages(conv)) == 4

        visitor_id = conversations.register_visitor(conv, {
            "first_name": "سینا", "last_name": "آزمون",
            "phone": "09121110000", "interests": AI_INTEREST})
        assert visitor_id
        _set_cookie(walk_up, _cookie_name(), visitor_auth.mint(visitor_id))

        r = _ask(walk_up, TARGETED_QUESTION)

        assert r.status_code == 200, r.text
        # Same conversation, and the four earlier messages are still in it.
        assert _conv(r) == conv
        assert _owner(conv) == visitor_id
        rows = _messages(conv)
        assert len(rows) == 6
        assert rows[0]["text"] == PLAIN_QUESTION
        # And the profile that arrived with the signup is now answering.
        assert PLAN_MARKER in r.json()["text"]

    def test_a_claim_cannot_steal_an_owned_conversation(self, app):
        """The same cookie, the other way round: register while `padyar_conv`
        points at somebody else's conversation. Without the guard in
        attach_visitor, that would move their whole transcript onto your
        name."""
        from app.services import conversations

        alice = _client(app)
        alice_id = _sign_in(alice, phone="09120000001")
        conv_a = _conv(_ask(alice, PLAIN_QUESTION))

        thief_id = conversations.register_visitor(conv_a, {
            "first_name": "دزد", "phone": "09129999999"})

        assert thief_id and thief_id != alice_id
        assert _owner(conv_a) == alice_id

    def test_reclaiming_your_own_conversation_is_allowed(self, app):
        """One person verifying twice is one person: upsert_visitor gives them
        the same id, so the second claim must not be read as a theft."""
        from app.services import conversations

        walk_up = _client(app)
        conv = _conv(_ask(walk_up, PLAIN_QUESTION))

        profile = {"first_name": "سینا", "phone": "09121110000"}
        first = conversations.register_visitor(conv, profile)
        second = conversations.register_visitor(conv, profile)

        assert first == second
        assert _owner(conv) == first
