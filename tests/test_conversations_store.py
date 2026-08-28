"""The durable store for visitors, conversations and messages.

WHAT IS BROKEN TODAY: the transcript lives in
`localStorage['inotex_chat_history']` and the registered person lives in
`localStorage['inotex-visitor']`. Clearing a kiosk browser destroys both. The
registration profile is written to `otp_challenges`, a table keyed by a
challenge and built to expire.

THE FEATURE under test is migrations/0010_conversations.sql (mirrored for
SQLite in app/db/connection.py) plus app/services/conversations.py, which is
the only seam the chat router and the admin panel are meant to use.

These tests call the service directly. The router wiring and the admin screens
are separate changes and are not exercised here.
"""
import pytest


@pytest.fixture
def db(tmp_path, monkeypatch):
    """A throwaway database with the real schema, built by init_db()."""
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "conversations.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    from app.db.connection import init_db
    init_db()
    yield


@pytest.fixture
def store(db):
    from app.services import conversations
    return conversations


def _rows(sql, args=()):
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    try:
        return [dict(r) for r in conn.execute(sql, args)]
    finally:
        conn.close()


def _backdate(table: str, column: str, when: str, where_sql: str = "1 = 1"):
    """Move rows into the past. Retention compares against
    `datetime('now', ...)`, which writes 'YYYY-MM-DD HH:MM:SS' — an isoformat
    string with a 'T' would sort wrong and the test would prove nothing."""
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    try:
        conn.execute(f"UPDATE {table} SET {column} = ? WHERE {where_sql}", (when,))
        conn.commit()
    finally:
        conn.close()


# ── The schema ───────────────────────────────────────────────────────────

def test_init_db_builds_all_three_tables(db):
    """A new table (unlike a new column) DOES land on an existing SQLite file,
    because CREATE TABLE IF NOT EXISTS runs on every boot."""
    names = {r["name"] for r in
             _rows("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"visitors", "conversations", "messages"} <= names, sorted(names)


def test_chat_logs_is_still_there(db):
    """0010 adds tables; it does not take the dashboard's table away. The
    stats, low-confidence and CSV endpoints all still read chat_logs."""
    names = {r["name"] for r in
             _rows("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert "chat_logs" in names


# ── Conversations ────────────────────────────────────────────────────────

def test_a_conversation_is_created_once_and_then_reused(store):
    """The padyar_conv cookie is the key. A second message in the same session
    must land in the same conversation, not start a new one."""
    first = store.get_or_create_conversation(
        "conv-1", lang="fa", ip="10.0.0.1", user_agent="kiosk/1")
    second = store.get_or_create_conversation("conv-1", lang="en", ip="10.0.0.9")

    assert first["id"] == "conv-1"
    assert second["id"] == "conv-1"
    assert len(_rows("SELECT id FROM conversations")) == 1
    # The session keeps the identity it began with; a later message never
    # rewrites started_at, ip, user_agent or lang.
    assert second["lang"] == "fa"
    assert second["ip"] == "10.0.0.1"
    assert second["user_agent"] == "kiosk/1"


def test_an_empty_conversation_id_writes_nothing(store):
    """The router computes an id before it calls, but a storage layer that
    happily creates a row keyed on '' would merge every visitor into one."""
    assert store.get_or_create_conversation("") == {}
    assert store.append_visitor_message("", "سلام") == 0
    assert _rows("SELECT id FROM conversations") == []


def test_an_anonymous_conversation_records_messages_with_no_visitor(store):
    """Most people at a booth never register. Losing their conversations would
    defeat the purpose, so visitor_id stays '' and everything still works."""
    store.append_visitor_message("conv-anon", "ساعت کاری چنده؟", ip="10.0.0.2")
    store.append_assistant_message("conv-anon", "از ۹ تا ۱۷",
                                   source="local", confidence=0.82,
                                   entry_id="faq-hours")

    conversation = store.get_conversation("conv-anon")
    assert conversation["visitor_id"] == ""
    assert conversation["message_count"] == 2

    messages = store.conversation_messages("conv-anon")
    assert [m["role"] for m in messages] == ["visitor", "assistant"]
    assert messages[1]["entry_id"] == "faq-hours"
    assert messages[1]["confidence"] == pytest.approx(0.82)
    # A visitor's question has no score. 0.0 would read as a terrible answer.
    assert messages[0]["confidence"] is None


def test_messages_come_back_in_the_order_they_were_written(store):
    """Ordered by id, not created_at. SQLite's CURRENT_TIMESTAMP has
    one-second resolution, so these four tie on time — and a transcript
    ordered by a tie shows the bot answering before it was asked."""
    store.append_visitor_message("conv-order", "یک")
    store.append_assistant_message("conv-order", "دو")
    store.append_visitor_message("conv-order", "سه")
    store.append_assistant_message("conv-order", "چهار")

    stamps = {r["created_at"] for r in _rows("SELECT created_at FROM messages")}
    assert len(stamps) < 4, "the point of this test is that timestamps tie"

    assert [m["text"] for m in store.conversation_messages("conv-order")] == \
        ["یک", "دو", "سه", "چهار"]


def test_a_message_can_never_be_written_into_a_missing_conversation(store):
    """Appending creates the conversation on the same connection. Without
    that, PostgreSQL rejects the foreign key and SQLite (which does not
    enforce one) silently keeps an orphan — a bug on one backend only."""
    store.append_assistant_message("conv-fresh", "پاسخ", source="ai_free")
    assert len(_rows("SELECT id FROM conversations WHERE id = 'conv-fresh'")) == 1


# ── Registration ─────────────────────────────────────────────────────────

def test_registering_mid_conversation_keeps_the_earlier_messages(store):
    """Somebody asks four questions and only then registers. Those questions
    are theirs, and the conversation they are already in gets their name."""
    store.append_visitor_message("conv-mid", "سلام")
    store.append_assistant_message("conv-mid", "سلام، بفرمایید", source="local")

    visitor_id = store.register_visitor("conv-mid", {
        "first_name": "سارا", "last_name": "احمدی",
        "phone": "09121234567", "job": "مهندس",
        "position": "مدیر", "interests": "هوش مصنوعی"})

    assert visitor_id
    conversation = store.get_conversation("conv-mid")
    assert conversation["visitor_id"] == visitor_id
    assert conversation["first_name"] == "سارا"
    # The two messages from before the registration are untouched and still
    # on the same conversation.
    assert [m["text"] for m in store.conversation_messages("conv-mid")] == \
        ["سلام", "سلام، بفرمایید"]


def test_the_same_phone_registering_twice_updates_one_row(store):
    """Keyed on the HMAC of the phone, the same convention
    migrations/0005_leads.sql already set for company_leads.phone_hash."""
    first = store.upsert_visitor(first_name="سارا", phone="09121234567",
                                 job="مهندس")
    second = store.upsert_visitor(first_name="سارا", phone="0912 123 4567",
                                  position="مدیر عامل")

    assert first == second
    assert len(_rows("SELECT id FROM visitors")) == 1
    visitor = store.get_visitor(first)
    # The second registration adds what it carried and erases nothing: someone
    # re-verifying to fix a typo must not lose their name.
    assert visitor["job"] == "مهندس"
    assert visitor["position"] == "مدیر عامل"


def test_two_visitors_without_a_phone_stay_two_visitors(store):
    """Nothing to match on. Merging two strangers because neither gave a
    number is worse than two rows."""
    a = store.upsert_visitor(first_name="الف")
    b = store.upsert_visitor(first_name="ب")
    assert a != b
    assert len(_rows("SELECT id FROM visitors")) == 2


def test_the_raw_phone_is_stored_and_the_hash_is_not_the_phone(store):
    """Contacting people after the exhibition is the point, so the number is
    kept readable — but the dedupe key is a keyed HMAC, so that path never
    needs the plaintext."""
    visitor_id = store.upsert_visitor(first_name="سارا", phone="0912 123-4567")
    row = _rows("SELECT phone, phone_hash FROM visitors WHERE id = ?",
                (visitor_id,))[0]
    assert row["phone"] == "09121234567"
    assert row["phone_hash"] and row["phone_hash"] != row["phone"]
    assert store.find_visitor_by_phone("09121234567")["id"] == visitor_id


def test_the_answers_bag_takes_new_questions_without_a_migration(store):
    """The one mechanism 0010 picked for whatever the bot collects later."""
    visitor_id = store.upsert_visitor(first_name="سارا", phone="09121110000",
                                      answers={"budget": "بالا"})
    assert store.record_answers(visitor_id, {"visit_day": "سه‌شنبه"})

    bag = store.get_visitor(visitor_id)["answers"]
    assert bag == {"budget": "بالا", "visit_day": "سه‌شنبه"}


# ── Admin reads ──────────────────────────────────────────────────────────

@pytest.fixture
def seeded(store):
    """Three conversations: one anonymous and weak, one registered and
    confident, one old."""
    store.append_visitor_message("c-weak", "این چیه؟")
    store.append_assistant_message("c-weak", "نمی‌دانم", source="ai_free",
                                   confidence=0.10, entry_id="")

    store.append_visitor_message("c-good", "غرفه شرکت آلفا کجاست؟")
    store.append_assistant_message("c-good", "سالن ۳", source="local",
                                   confidence=0.91, entry_id="co-alfa")
    store.register_visitor("c-good", {"first_name": "سارا",
                                      "phone": "09121234567",
                                      "job": "مهندس"})

    store.append_visitor_message("c-old", "پارسال")
    store.append_assistant_message("c-old", "پاسخ کهنه", source="local",
                                   confidence=0.55)
    _backdate("conversations", "started_at", "2020-01-01 00:00:00",
              "id = 'c-old'")
    _backdate("conversations", "last_message_at", "2020-01-01 00:00:00",
              "id = 'c-old'")
    _backdate("messages", "created_at", "2020-01-01 00:00:00",
              "conversation_id = 'c-old'")
    return store


def test_listing_conversations_returns_all_of_them_newest_first(seeded):
    ids = [c["id"] for c in seeded.list_conversations()]
    assert set(ids) == {"c-weak", "c-good", "c-old"}
    assert ids[-1] == "c-old", ids


def test_the_date_range_filter_bounds_the_start_of_the_conversation(seeded):
    recent = [c["id"] for c in seeded.list_conversations(since="2021-01-01")]
    assert set(recent) == {"c-weak", "c-good"}

    old = [c["id"] for c in seeded.list_conversations(until="2021-01-01")]
    assert old == ["c-old"]


def test_the_has_visitor_filter_splits_registered_from_anonymous(seeded):
    registered = [c["id"] for c in seeded.list_conversations(has_visitor=True)]
    assert registered == ["c-good"]
    assert seeded.list_conversations(has_visitor=True)[0]["first_name"] == "سارا"

    anonymous = {c["id"] for c in seeded.list_conversations(has_visitor=False)}
    assert anonymous == {"c-weak", "c-old"}
    # An anonymous row joins to no visitor. The panel renders these straight,
    # so it gets '' and not None.
    assert all(c["first_name"] == "" for c in
               seeded.list_conversations(has_visitor=False))


def test_the_source_filter_matches_the_tier_that_answered(seeded):
    assert [c["id"] for c in seeded.list_conversations(source="ai_free")] == \
        ["c-weak"]
    assert set(c["id"] for c in seeded.list_conversations(source="local")) == \
        {"c-good", "c-old"}
    assert seeded.list_conversations(source="nothing_answers_with_this") == []


def test_the_confidence_filter_finds_the_answers_that_went_wrong(seeded):
    """This is the read the owner asked for: the turns where the bot answered
    badly, so the record behind them can be fixed."""
    weak = [c["id"] for c in seeded.list_conversations(max_confidence=0.3)]
    assert weak == ["c-weak"]

    strong = [c["id"] for c in seeded.list_conversations(min_confidence=0.9)]
    assert strong == ["c-good"]


def test_the_free_text_filter_searches_the_message_bodies(seeded):
    assert [c["id"] for c in seeded.list_conversations(q="آلفا")] == ["c-good"]
    # It reads the bot's words too, not only the visitor's.
    assert [c["id"] for c in seeded.list_conversations(q="نمی‌دانم")] == ["c-weak"]
    assert seeded.list_conversations(q="کلمه‌ای که وجود ندارد") == []


def test_a_typed_wildcard_matches_itself(seeded):
    """An operator searching for % must not get every conversation."""
    assert seeded.list_conversations(q="%") == []


def test_filters_combine(seeded):
    assert seeded.list_conversations(has_visitor=True, max_confidence=0.3) == []
    hits = seeded.list_conversations(has_visitor=True, source="local")
    assert [c["id"] for c in hits] == ["c-good"]


def test_listing_visitors_carries_their_session_count(seeded):
    people = seeded.list_visitors()
    assert len(people) == 1
    assert people[0]["first_name"] == "سارا"
    assert people[0]["conversation_count"] == 1


def test_visitors_can_be_filtered_by_text_and_by_date(seeded):
    assert [v["job"] for v in seeded.list_visitors(q="مهندس")] == ["مهندس"]
    assert seeded.list_visitors(q="نجار") == []
    assert seeded.list_visitors(since="2021-01-01")
    assert seeded.list_visitors(until="2021-01-01") == []


def test_weak_answers_lists_the_recent_low_confidence_turns(seeded):
    weak = seeded.weak_answers(threshold=0.19)
    assert [m["text"] for m in weak] == ["نمی‌دانم"]
    # A visitor's question carries no confidence, so it can never be mistaken
    # for a bad answer. `این چیه؟` is the question that produced this row.
    assert all("این چیه؟" != m["text"] for m in weak)
    assert seeded.weak_answers(threshold=0.95)[0]["text"] == "سالن ۳"


# ── Retention ────────────────────────────────────────────────────────────

def test_retention_zero_keeps_everything(seeded):
    """Default 0 = keep forever, so no install loses data by upgrading."""
    from app.db.queries import set_setting
    set_setting("chat_log_retention_days", "0")

    assert seeded.purge_expired() == {"messages": 0, "conversations": 0}
    assert len(_rows("SELECT id FROM conversations")) == 3
    assert len(_rows("SELECT id FROM messages")) == 6


def test_retention_deletes_the_transcript_past_the_window(seeded):
    from app.db.queries import set_setting
    set_setting("chat_log_retention_days", "7")

    removed = seeded.purge_expired()
    assert removed == {"messages": 2, "conversations": 1}

    left = {c["id"] for c in seeded.list_conversations()}
    assert left == {"c-weak", "c-good"}
    assert seeded.conversation_messages("c-old") == []


def test_retention_never_deletes_a_visitor(seeded):
    """A visitor row is a registration the person gave on purpose and is the
    lead data the product exists to capture. The dial is about unredacted
    conversation text, and it already leaves company_leads alone."""
    from app.db.queries import set_setting
    set_setting("chat_log_retention_days", "7")
    _backdate("visitors", "created_at", "2020-01-01 00:00:00")
    _backdate("visitors", "last_seen_at", "2020-01-01 00:00:00")

    seeded.purge_expired()
    assert len(_rows("SELECT id FROM visitors")) == 1


def test_the_one_retention_cycle_prunes_both_stores(seeded):
    """app/main.py runs ONE purge loop. Two purges on two schedules drift
    apart, and the half that stops running is the half nobody notices."""
    from app.db.connection import get_db_connection
    from app.db.queries import purge_chat_logs, set_setting

    conn = get_db_connection()
    conn.execute("INSERT INTO chat_logs (query, response, created_at)"
                 " VALUES ('کهنه', 'پاسخ', '2020-01-01 00:00:00')")
    conn.commit()
    conn.close()

    set_setting("chat_log_retention_days", "7")
    # The return value is still the chat_logs count — app/main.py and an
    # existing test both read it that way.
    assert purge_chat_logs() == 1
    assert _rows("SELECT id FROM chat_logs") == []
    assert {c["id"] for c in seeded.list_conversations()} == {"c-weak", "c-good"}
