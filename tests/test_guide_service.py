"""The guide knowledge tier: deterministic answers from the app's own tables.

WHY THESE TABLES AND NOT `dataset` ROWS (the owner's call, 2026-08-31):
opening hours, entrances, transit stations, restaurants and news are
STRUCTURED facts with their own query shapes — key-value, points of interest,
listable collections. Flattening them into dataset rows loses the types and
makes a deterministic answer impossible. So each entity gets its own table
(migrations/0020_guide_tables.sql, mirrored for SQLite in
app/db/connection.py) and app/services/guide.py answers guide questions
straight from them — no AI, no embeddings.

The tests call the SERVICE directly. The chat-router wiring is a separate
change and is not exercised here.

CRITICAL GUARANTEE (the reason this tier exists as a gate, not a grabber):
«شرکت‌های هوش مصنوعی را معرفی کن» and «غرفه شرکت X کجاست؟» are retrieval
questions about companies. A guide tier that stole them would break the
chatbot's main job, so a query naming a company or a booth («شرکت», «غرفه»)
must return None.
"""
import importlib.util
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


HOURS = "۸ صبح تا ۵ عصر"
ADDRESS = "تهران، محل دائمی نمایشگاه‌های بین‌المللی شهر آفتاب"
WEATHER = "معتدل و کوهستانی"
DATES = "۲۵ تا ۲۸ مرداد ۱۴۰۵"
PEAK = "روزهای اول و ساعت‌های ابتدای روز خلوت‌تر است"

GATES = [
    ("درب اصلی", "اصلی", "از بلوار شمالی، سمت غرب"),
    ("درب شرقی", "فرعی", "از خیابان شرقی، بعد از پارکینگ"),
]

# One metro and one BRT station so the mode preference is observable:
# the metro answer must carry the metro row and the BRT answer the BRT row.
STATIONS = [
    ("چهارراه فردوسی", "metro", "خط ۴", "ده دقیقه پیاده تا درب اصلی", 35.700, 51.401),
    ("میدان آزادی", "brt", "خط ۲", "پنج دقیقه پیاده تا درب شرقی", 35.699, 51.400),
]

RESTAURANTS = [
    # Seeded deliberately NOT in serving order: the outside row first.
    ("r-out", "رستوران البرز", "سنتی", "بیرون نمایشگاه", "۱ کیلومتر", 0),
    ("r-in", "رستوران اپونا", "بین‌المللی", "داخل نمایشگاه", "", 1),
    ("r-in2", "کافه دامنه", "کافی‌شاپ", "سالن اصلی", "۲۰۰ متر", 1),
]

NEWS = [
    # Oldest first in the seed so the newest-first ordering has to be built.
    ("n1", "خبر کهن", "2026-08-20", "۱۴۰۵/۰۵/۲۹", "خلاصه کهن", 0),
    ("n2", "خبر دوم", "2026-08-25", "۱۴۰۵/۰۶/۰۳", "خلاصه دوم", 0),
    ("n3", "خبر سوم", "2026-08-27", "۱۴۰۵/۰۶/۰۵", "خلاصه سوم", 0),
    ("n4", "خبر چهارم", "2026-08-29", "۱۴۰۵/۰۶/۰۷", "خلاصه چهارم", 0),
    ("n5", "خبر پنجم", "2026-08-30", "۱۴۰۵/۰۶/۰۸", "خلاصه پنجم", 0),
    ("n6", "خبر تازه", "2026-08-31", "۱۴۰۵/۰۶/۰۹", "خلاصه تازه", 0),
]


@pytest.fixture
def db(tmp_path, monkeypatch):
    """A throwaway database with the real schema, built by init_db().

    Same shape as tests/test_conversations_store.py: the five guide tables
    must come out of the ordinary SQLite mirror — the service has no private
    schema path of its own.
    """
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "guide.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    from app.db.connection import init_db
    init_db()
    yield


def _seed():
    """Fill the five guide tables through the app layer, then hand over."""
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    for table in ("guide_facts", "gates", "stations", "restaurants", "news"):
        conn.execute(f"DELETE FROM {table}")
    for key, value in (("hours", HOURS), ("address", ADDRESS),
                       ("weather", WEATHER), ("dates", DATES), ("peak", PEAK)):
        conn.execute("INSERT INTO guide_facts (key, value) VALUES (?, ?)",
                     (key, value))
    for name, gate_type, route in GATES:
        conn.execute("INSERT INTO gates (name, gate_type, route_text)"
                     " VALUES (?, ?, ?)", (name, gate_type, route))
    for name, kind, line, desc, lat, lng in STATIONS:
        conn.execute("INSERT INTO stations (name, kind, line, description,"
                     " lat, lng) VALUES (?, ?, ?, ?, ?, ?)",
                     (name, kind, line, desc, lat, lng))
    for rid, name, cuisine, area, distance, in_venue in RESTAURANTS:
        conn.execute("INSERT INTO restaurants (id, name, cuisine, area,"
                     " distance, note, links, in_venue)"
                     " VALUES (?, ?, ?, ?, ?, '', '[]', ?)",
                     (rid, name, cuisine, area, distance, in_venue))
    for slug, title, iso, jalali, summary, featured in NEWS:
        conn.execute("INSERT INTO news (slug, title, date_iso, date_jalali,"
                     " summary, body, featured) VALUES (?, ?, ?, ?, ?, '', ?)",
                     (slug, title, iso, jalali, summary, featured))
    conn.commit()
    conn.close()


def _ask(query):
    from app.services.guide import match_guide
    return match_guide(query)


# ── One test per kind ─────────────────────────────────────────────────────


def test_hours_question_answers_the_seeded_fact(db):
    """«ساعت بازدید» is a fact question: one sentence built from the DB
    value, never a hardcoded answer."""
    _seed()
    r = _ask("ساعت بازدید نمایشگاه چقدر است؟")
    assert r is not None
    assert r["kind"] == "fact"
    assert r["key"] == "hours"
    assert HOURS in r["text"]
    assert r["confidence"] == 0.95


def test_restaurant_question_lists_in_venue_first(db):
    """«رستوران نزدیک» is a guide question; the entries render as an options
    list, so they come back as dicts — inside-the-venue restaurants first."""
    _seed()
    r = _ask("رستوران نزدیک نمایشگاه کجاست؟")
    assert r is not None
    assert r["kind"] == "restaurants"
    ids = [e["id"] for e in r["entries"]]
    assert ids.index("r-in") < ids.index("r-out")
    assert ids.index("r-in2") < ids.index("r-out")
    assert r["entries"][0]["in_venue"] is True
    assert r["confidence"] == 0.9


def test_news_question_returns_newest_first_capped_at_five(db):
    """Six seeded items, five served, newest (2026-08-31) on top."""
    _seed()
    r = _ask("اخبار نمایشگاه چیست؟")
    assert r is not None
    assert r["kind"] == "news"
    assert len(r["entries"]) == 5
    assert r["entries"][0]["slug"] == "n6"
    slugs = [e["slug"] for e in r["entries"]]
    assert slugs == ["n6", "n5", "n4", "n3", "n2"]
    assert r["confidence"] == 0.85


def test_gate_question_lists_every_entrance(db):
    _seed()
    r = _ask("درب ورودی نمایشگاه از کجاست؟")
    assert r is not None
    assert r["kind"] == "gates"
    assert "درب اصلی" in r["text"]
    assert "درب شرقی" in r["text"]
    assert r["confidence"] == 0.9


def test_metro_question_serves_the_metro_station(db):
    """«مترو» picks the metro rows; the BRT station stays out of the answer."""
    _seed()
    r = _ask("با مترو چطور به نمایشگاه بریم؟")
    assert r is not None
    assert r["kind"] == "stations"
    assert "چهارراه فردوسی" in r["text"]
    assert "میدان آزادی" not in r["text"]


def test_brt_question_prefers_the_brt_line(db):
    """«اتوبوس»/BRT picks the BRT rows instead of the metro ones."""
    _seed()
    r = _ask("برای رفتن با اتوبوس به نمایشگاه چطور برم؟")
    assert r is not None
    assert r["kind"] == "stations"
    assert "میدان آزادی" in r["text"]
    assert "چهارراه فردوسی" not in r["text"]


# ── The disambiguation: this tier must never steal retrieval queries ──────


def test_a_booth_query_is_not_a_guide_question(db):
    _seed()
    assert _ask("غرفه شرکت دکیو کجاست؟") is None


def test_a_company_list_query_is_not_a_guide_question(db):
    """CRITICAL: «شرکت‌های هوش مصنوعی را معرفی کن» belongs to the
    company-list tier. A guide tier that answered it would break the
    chatbot's main job."""
    _seed()
    assert _ask("شرکت‌های هوش مصنوعی را معرفی کن") is None


def test_an_off_topic_query_is_not_a_guide_question(db):
    _seed()
    assert _ask("قیمت بلیت چقدر است؟") is None


def test_parking_without_metro_means_the_venue_gates(db):
    """On this site «پارکینگ» alone means the venue parking entrances, so it
    routes to gates — not to the transit answer."""
    _seed()
    r = _ask("پارکینگ نمایشگاه کدام درب است؟")
    assert r is not None
    assert r["kind"] == "gates"


# ── The crawl import round-trip ───────────────────────────────────────────
#
# The crawl schema (crawl.*) exists only on the production PostgreSQL
# database; the SQLite mirror has no crawl tables. So the test does NOT seed
# crawl.* — it monkeypatches the script's crawl-reader to return canned rows
# and lets the import run against the real app tables, which is the half
# that can break.

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "import-guide-from-crawl.py"

CANNED_CRAWL = {
    "facts": [("hours", "۹ صبح تا ۶ عصر"), ("address", "شهرک آفتاب، جاده اصلی")],
    "gates": [("درب جنوبی", "اصلی", "از جاده جنوبی")],
    "stations": [("ایستگاه نمونه", "metro", "خط ۱", "پنج دقیقه پیاده", 35.70, 51.40)],
    "restaurants": [("r-9", "کافه زمین", "کافی‌شاپ", "غرب", "۵۰۰ متر",
                     "چشم‌انداز دارد", json.dumps([{"map": "https://x"}]), False)],
    "news": [("slug-x", "عنوان نمونه", "2026-08-30", "۱۴۰۵/۰۶/۰۸",
              "خلاصه", "متن کامل", False)],
}


def _load_script():
    """The hyphenated filename is not an importable module name."""
    spec = importlib.util.spec_from_file_location("import_guide_from_crawl",
                                                  SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _app_counts():
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    try:
        return {t: conn.execute(f"SELECT COUNT(*) AS c FROM {t}"
                                ).fetchone()["c"]
                for t in ("guide_facts", "gates", "stations",
                          "restaurants", "news")}
    finally:
        conn.close()


def test_import_dry_run_writes_nothing(db, monkeypatch):
    mod = _load_script()
    monkeypatch.setattr(mod, "read_crawl", lambda conn: CANNED_CRAWL)
    assert mod.main([]) == 0
    assert set(_app_counts().values()) == {0}


def test_import_apply_round_trips_the_crawl_rows(db, monkeypatch):
    mod = _load_script()
    monkeypatch.setattr(mod, "read_crawl", lambda conn: CANNED_CRAWL)
    assert mod.main(["--apply"]) == 0
    counts = _app_counts()
    assert counts == {"guide_facts": 2, "gates": 1, "stations": 1,
                      "restaurants": 1, "news": 1}
    # The values really landed, and the importer answered through them.
    assert _ask("ساعت بازدید نمایشگاه چقدر است؟")["text"].find("۹ صبح تا ۶ عصر") >= 0


def test_import_apply_is_idempotent(db, monkeypatch):
    """Upserts, not appends: running the import twice leaves one row per
    primary key (the re-run-to-refresh rollback story depends on it)."""
    mod = _load_script()
    monkeypatch.setattr(mod, "read_crawl", lambda conn: CANNED_CRAWL)
    mod.main(["--apply"])
    mod.main(["--apply"])
    assert _app_counts()["guide_facts"] == 2


# ── Router wiring: the tier serves through POST /chat ────────────────────

@pytest.fixture
def client(tmp_path, monkeypatch):
    """Same client shape as tests/test_conversational.py, sharing the test's
    tmp database with the `db` fixture (tmp_path is one directory per test,
    so both land on the same guide.db)."""
    import app.config as config
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "guide.db"))
    monkeypatch.setattr(config, "SEED_DEFAULT_CONTENT", False)
    from app.main import app
    from app.auth import security
    security._chat_rate_limits.clear()
    with TestClient(app) as c:
        from app.auth.security import generate_chat_token
        c.headers.update({"Origin": "http://localhost",
                          "X-Chat-Token": generate_chat_token()})
        yield c
    security._chat_rate_limits.clear()


def test_guide_questions_served_through_the_chat_endpoint(db, client):
    _seed()
    r = client.post("/chat",
                    json={"message": "ساعت بازدید چند است؟", "lang": "fa"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source"] == "local_guide", body
    assert HOURS in body["text"]


def test_company_questions_never_reach_the_guide_tier(db, client):
    """The tier must never steal a real retrieval query — the guard the
    whole pipeline depends on (a guide answer to «شرکت‌ها…» would be the
    confident-but-wrong class this codebase is built against)."""
    _seed()
    r = client.post("/chat",
                    json={"message": "شرکت‌های هوش مصنوعی را معرفی کن",
                          "lang": "fa"})
    # Empty corpus + no AI in this test DB: the endpoint may fall back to a
    # 503 — the property under test is only that the GUIDE tier did not
    # answer, which holds whichever way the rest of the pipeline lands.
    assert r.json().get("source") != "local_guide", r.text
