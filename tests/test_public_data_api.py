"""The public data API must hand out chip labels, and nothing else.

`/api/dataset` and `/api/questions` were served without authentication and
returned every row of the knowledge base. On the INOTEX install that is 222
dataset rows, 168 of them exhibitor company records with their full write-ups.
Anyone who typed the URL downloaded the customer's commercial content.

They are gone. `/api/suggestions` replaces both, and it returns only what the
chat page prints on its suggested-question chips: a title and its English
twin, for the first `SUGGESTION_LIMIT` rows of the curated order.

What each test here is really guarding:

  * the old URLs are gone, not merely undocumented — a 404, so a scraper with
    the URL in a bookmark gets nothing;
  * the replacement stays anonymous, because the chat page has no credentials
    to offer and never will;
  * the answer bodies do not travel. This is the leak itself, so it is asserted
    against the RAW response text, not against parsed keys: a renamed field, a
    row spliced into a title, or an answer smuggled anywhere in the payload all
    fail here;
  * the row COUNT is capped. Returning every title would still be a scrape of
    the exhibitor list, so "which fields" is not enough on its own;
  * the admin endpoints still serve the whole rows. The content did not become
    unreachable, it became authenticated — if this test ever fails, the fix
    broke the admin panel instead of the leak.
"""
import datetime
import secrets

import pytest
from fastapi.testclient import TestClient

from app.routers.public import SUGGESTION_LIMIT


# The answer body of a seeded row. Distinctive enough that finding it anywhere
# in a public response is proof, not coincidence.
SECRET_ANSWER = "ZZ-COMMERCIAL-BODY-DO-NOT-LEAK-QQ"
SECRET_TITLE = "شرکت آزمایشی الف"
SECRET_TITLE_EN = "Test Company Alpha"


def _seed(conn, count):
    """`count` dataset rows, all carrying the same secret answer body."""
    for i in range(count):
        conn.execute(
            "INSERT INTO dataset (id, title, text, video_url, title_en, text_en,"
            " position) VALUES (?,?,?,?,?,?,?)",
            (f"leak-{i:03d}", f"{SECRET_TITLE} {i}", SECRET_ANSWER, "",
             f"{SECRET_TITLE_EN} {i}", SECRET_ANSWER, (i + 1) * 10))
        conn.execute(
            "INSERT INTO questions (question, dataset_id, video_url)"
            " VALUES (?,?,?)",
            (f"سوال {i}؟", f"leak-{i:03d}", ""))
    conn.commit()


@pytest.fixture
def rows():
    """How many rows the fixture seeds: far more than the endpoint may serve,
    so the cap is tested against a real surplus and not an empty table."""
    return SUGGESTION_LIMIT * 5


@pytest.fixture
def client(tmp_path, monkeypatch, rows):
    """A visitor's client: no cookie, no session, nothing."""
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "public_api.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    from app.main import app
    with TestClient(app) as c:
        from app.db.connection import get_db_connection
        conn = get_db_connection()
        try:
            _seed(conn, rows)
        finally:
            conn.close()
        yield c


@pytest.fixture
def admin_client(client):
    """The same install, with a real admin session cookie.

    A live session row rather than a dependency override, so the admin
    endpoints are exercised through the auth they actually ship with.
    """
    from app.db.connection import get_db_connection
    token = secrets.token_hex(16)
    conn = get_db_connection()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO admins (username, password_hash, salt,"
            " security_question, security_answer_hash)"
            " VALUES ('leakadmin','x','y','q','z')")
        conn.execute(
            "INSERT INTO admin_sessions (token, username, expiry) VALUES (?,?,?)",
            (token, "leakadmin",
             (datetime.datetime.now() + datetime.timedelta(hours=1)).isoformat()))
        conn.commit()
    finally:
        conn.close()
    client.cookies.set("admin_session", token)
    return client


# ── The leaking endpoints are gone ──────────────────────────────────────

@pytest.mark.parametrize("path", ["/api/dataset", "/api/questions"])
def test_the_old_public_dumps_are_gone(client, path):
    """404, not 401 and not an empty list. The route must not exist."""
    assert client.get(path).status_code == 404


@pytest.mark.parametrize("path", ["/api/dataset", "/api/questions"])
def test_the_old_public_dumps_are_gone_for_an_admin_too(admin_client, path):
    """The removal is of the ROUTE. If these still answered for a session
    holder, the route was merely gated and a stale bookmark would still work
    from any browser that had ever logged in."""
    assert admin_client.get(path).status_code == 404


# ── The replacement ─────────────────────────────────────────────────────

def test_suggestions_are_served_without_any_authentication(client):
    """The chat page is anonymous by design — it has no credentials to send."""
    res = client.get("/api/suggestions")
    assert res.status_code == 200, res.text
    assert isinstance(res.json(), list)


def test_suggestions_never_carry_an_answer_body(client):
    """The leak itself.

    Asserted against the raw response TEXT, not against parsed field names: a
    renamed key, an answer appended to a title, or a body hidden anywhere else
    in the payload is the same leak and must fail the same way.
    """
    res = client.get("/api/suggestions")
    assert SECRET_ANSWER not in res.text


def test_suggestions_carry_only_the_two_label_fields(client):
    """Anything else is a field the page does not print. Row ids and video
    paths are as much of a map of the knowledge base as the text is."""
    for row in client.get("/api/suggestions").json():
        assert set(row) == {"title", "title_en"}


def test_suggestions_are_capped_however_big_the_dataset_is(client, rows):
    """Every title would still be a scrape of the exhibitor list. The cap is
    the second half of the fix; restricting the fields alone is not enough."""
    served = client.get("/api/suggestions").json()
    assert rows > SUGGESTION_LIMIT, "fixture must seed a real surplus"
    assert len(served) == SUGGESTION_LIMIT


def test_suggestions_are_the_head_of_the_curated_order(client):
    """Not an arbitrary ten. The chips are the install's curated menu, so they
    must be the FIRST rows of the display order the admin arranged."""
    titles = [row["title"] for row in client.get("/api/suggestions").json()]
    assert titles == [f"{SECRET_TITLE} {i}" for i in range(SUGGESTION_LIMIT)]


def test_suggestions_carry_the_english_label(client):
    """The chip menu is bilingual; without title_en the English side falls back
    to Persian labels and the language switch looks broken."""
    row = client.get("/api/suggestions").json()[0]
    assert row["title_en"] == f"{SECRET_TITLE_EN} 0"


def test_an_empty_knowledge_base_serves_an_empty_list(tmp_path, monkeypatch):
    """A brand-new install has no content. The page must still boot: an error
    here would leave the visitor with no chips AND a console exception."""
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "empty.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    from app.main import app
    with TestClient(app) as c:
        res = c.get("/api/suggestions")
        assert res.status_code == 200
        assert res.json() == []


# ── The content is authenticated, not unreachable ───────────────────────

def test_the_admin_dataset_endpoint_still_serves_the_full_rows(admin_client, rows):
    res = admin_client.get("/admin/api/dataset")
    assert res.status_code == 200, res.text
    body = res.json()
    assert len(body) == rows
    assert body[0]["text"] == SECRET_ANSWER
    assert set(body[0]) >= {"id", "title", "text", "video_url",
                            "title_en", "text_en"}


def test_the_admin_questions_endpoint_still_serves_the_full_rows(admin_client, rows):
    res = admin_client.get("/admin/api/questions")
    assert res.status_code == 200, res.text
    body = res.json()
    assert len(body) == rows
    assert set(body[0]) >= {"id", "question", "dataset_id", "video_url"}


@pytest.mark.parametrize("path", ["/admin/api/dataset", "/admin/api/questions"])
def test_the_admin_endpoints_refuse_an_anonymous_caller(client, path):
    """The other half of "authenticated, not unreachable". If this ever passes
    with a 200, the leak simply moved to a longer URL."""
    assert client.get(path).status_code in (401, 403)


# ── The visitor still gets an answer ────────────────────────────────────

CHIP_TITLE = "ساعت بازدید نمایشگاه چگونه است"
CHIP_ANSWER = "نمایشگاه هر روز از ساعت ۹ تا ۱۸ باز است."
CHIP_VIDEO = "/media/videos/hours.mp4"


@pytest.fixture
def chat_client(tmp_path, monkeypatch):
    """One chip's row, and a client that can talk to /chat.

    TF-IDF backend: no embedding model and no trained intent head, so this
    stays offline and deterministic — the same arrangement the other /chat
    tests use.
    """
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "chip.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    from app.main import app
    from app.auth import security
    security._chat_rate_limits.clear()
    with TestClient(app) as c:
        from app.db.queries import set_setting
        set_setting("openai_enabled", "true")
        set_setting("search_backend", "tfidf")

        from app.db.connection import get_db_connection
        conn = get_db_connection()
        try:
            conn.execute(
                "INSERT INTO dataset (id, title, text, video_url, title_en,"
                " text_en, position) VALUES (?,?,?,?,?,?,?)",
                ("chip-hours", CHIP_TITLE, CHIP_ANSWER, CHIP_VIDEO,
                 "Opening hours", "Open 9 to 18 daily.", 10))
            conn.commit()
        finally:
            conn.close()
        from app.services import search
        search.reindex_and_publish()

        from app.auth.security import generate_chat_token
        c.headers.update({"Origin": "http://localhost",
                          "X-Chat-Token": generate_chat_token()})
        yield c
    security._chat_rate_limits.clear()


def test_tapping_a_chip_still_returns_the_answer_and_its_video(
        chat_client, monkeypatch):
    """The invariant the whole change rests on.

    The answer bodies left the public payload, so the page can no longer
    answer a chip out of its own memory — it sends the chip's title to /chat
    instead, exactly as the English chips and every typed question already
    did. If this fails, the leak was closed by breaking the feature: the
    visitor taps a suggestion and gets nothing back.

    The AI tier is made fatal because it must not be reached. A chip's title
    is an exact match against its own row, so Tier 1 answers it — free,
    offline, and no slower than the local lookup it replaced.
    """
    import app.routers.chat as chat

    async def no_ai(*_a, **_k):
        pytest.fail("a chip's own title must not need the AI tier")

    monkeypatch.setattr(chat, "classify_intent", no_ai)
    monkeypatch.setattr(chat, "get_openai_response", no_ai)

    title = chat_client.get("/api/suggestions").json()[0]["title"]
    assert title == CHIP_TITLE

    res = chat_client.post("/chat", json={"message": title, "lang": "fa"})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["text"] == CHIP_ANSWER
    assert body["type"] == "video"
    assert body["video_url"] == CHIP_VIDEO
