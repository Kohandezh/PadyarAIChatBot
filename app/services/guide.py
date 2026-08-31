"""Guide knowledge tier: deterministic answers for visitor-guide questions.

WHY THIS EXISTS: opening hours, entrances, transit, restaurants and news are
questions the database can answer EXACTLY — they live in their own tables
(migrations/0020_guide_tables.sql, filled by scripts/import-guide-from-crawl.py)
with their own query shapes. Sending them through retrieval or the model pays
money for an answer that can be looked up, and a wrong-tier hit can serve an
unrelated entry. This tier reads the tables and builds the sentence from the
stored values — no AI, no embeddings, nothing hardcoded but the frame.

None means "not mine": no guide keyword, a query naming a company or a booth
(«شرکت»/«غرفه» — those belong to the retrieval tiers, and stealing them is
the one way this tier could break the chatbot), a keyword whose table is
empty, or any DB fault. This tier degrades, it never raises.
"""

from app.config import logger
from app.utils.normalizer import normalize_persian

# Fact keyword group -> guide_facts key. Checked in this order, so a query
# carrying two fact words («ساعت و تاریخ نمایشگاه») answers hours: one
# question, one fact, deterministically.
_FACT_TRIGGERS = (
    ("hours", ("ساعت بازدید", "ساعات", "چند ساعت", "چه ساعتی")),
    ("dates", ("تاریخ", "کی برگزار", "چه زمانی", "تا کی")),
    ("weather", ("آب و هوا", "هوای")),
    ("address", ("آدرس", "کجاست نمایشگاه", "محل برگزاری")),
    ("peak", ("شلوغی", "اوج", "خلوت")),
)

# The sentence FRAME is ours; every VALUE comes from guide_facts. Grandmother
# test: one short line, no jargon, the fact stated plainly.
_FACT_SENTENCES = {
    "hours": "ساعت بازدید نمایشگاه {value} است.",
    "dates": "تاریخ برگزاری نمایشگاه {value} است.",
    "weather": "آب و هوای روزهای نمایشگاه {value} است.",
    "address": "محل برگزاری نمایشگاه {value} است.",
    "peak": "وضعیت شلوغی نمایشگاه: {value}",
}

# Entrances and transit. «پارکینگ» on this site means the venue parking
# entrances, so it routes to gates — UNLESS the query also says مترو, which
# makes it a transit question (handled by checking the transit mode first).
_GATE_TRIGGERS = ("درب", "ورودی", "ورود", "پارکینگ")
# Mode words pick WHICH transit rows serve; the rest only say "transit".
_STATION_MODES = (
    ("metro", ("مترو",)),
    ("brt", ("brt", "اتوبوس")),
)
_STATION_TRIGGERS = ("ایستگاه", "چطور برم", "مسیریابی", "چطور بریم", "رسیدن")

_RESTAURANT_TRIGGERS = ("رستوران", "ناهار", "غذا", "غذاخوری",
                        "کافه", "بوفه", "فست فود")
_NEWS_TRIGGERS = ("خبر", "اخبار", "چه خبر", "اطلاعیه", "تازه ها")

# stations.kind values as the visitor reads them.
_KIND_LABELS = {"metro": "مترو", "brt": "بی‌آر‌تی"}

_NEWS_MAX = 5


def _has(padded: str, needles) -> bool:
    """Word-boundary keyword check on the padded normalized query.

    normalize_persian folds ZWNJ to a space and collapses whitespace, so
    single spaces are the token edges: padding both sides makes every check
    whole-word. «تازه‌ها» arrives as «تازه ها» and matches as a phrase;
    «تاریخچه» does NOT fire the «تاریخ» keyword.
    """
    return any(f" {n} " in padded for n in needles)


def _fact_answer(conn, key: str):
    row = conn.execute("SELECT value FROM guide_facts WHERE key = ?",
                       (key,)).fetchone()
    value = (row["value"] or "").strip() if row else ""
    if not value:
        return None
    return {"kind": "fact", "key": key,
            "text": _FACT_SENTENCES[key].format(value=value),
            "confidence": 0.95}


def _gates_answer(conn):
    rows = conn.execute(
        "SELECT name, gate_type, route_text FROM gates ORDER BY name"
    ).fetchall()
    if not rows:
        return None
    lines = []
    for r in rows:
        label = f"{r['name']} ({r['gate_type']})" if r["gate_type"] else r["name"]
        lines.append(f"{label}: {r['route_text']}")
    return {"kind": "gates",
            "text": "ورودی‌های نمایشگاه این‌ها هستند:\n" + "\n".join(lines),
            "confidence": 0.9}


def _stations_answer(conn, mode: str):
    # `kind` holds the crawl-side metro/brt discriminant; without a mode the
    # whole table serves (the «چطور برم» / route-planning shape).
    where, params = "", ()
    if mode in ("metro", "brt"):
        where, params = " WHERE kind = ?", (mode,)
    rows = conn.execute(
        f"SELECT name, kind, line, description FROM stations{where}"
        f" ORDER BY name", params).fetchall()
    if not rows:
        return None
    lines = []
    for r in rows:
        parts = [f"ایستگاه {r['name']}"]
        kind = _KIND_LABELS.get((r["kind"] or "").lower(), r["kind"] or "")
        if kind:
            parts.append(kind)
        if r["line"]:
            parts.append(f"خط {r['line']}")
        if r["description"]:
            parts.append(r["description"])
        lines.append("، ".join(parts))
    lead = {
        "metro": "برای رفتن با مترو، این ایستگاه‌ها نزدیک نمایشگاه هستند:",
        "brt": "برای رفتن با اتوبوس بی‌آر‌تی، این ایستگاه‌ها نزدیک نمایشگاه هستند:",
        "all": "این ایستگاه‌های حمل‌ونقل عمومی نزدیک نمایشگاه هستند:",
    }[mode]
    return {"kind": "stations", "text": lead + "\n" + "\n".join(lines),
            "confidence": 0.9}


def _restaurants_answer(conn):
    rows = conn.execute(
        "SELECT id, name, cuisine, area, distance, in_venue"
        " FROM restaurants ORDER BY name"
    ).fetchall()
    if not rows:
        return None
    # in_venue first (walk, not drive), then name — a stable order so the
    # options list and any pager agree between turns.
    rows = sorted(rows, key=lambda r: (0 if r["in_venue"] else 1, r["name"]))
    entries = [{"id": r["id"], "name": r["name"], "cuisine": r["cuisine"],
                "area": r["area"], "distance": r["distance"],
                "in_venue": bool(r["in_venue"])} for r in rows]
    return {"kind": "restaurants", "entries": entries,
            "text": "این رستوران‌ها نزدیک نمایشگاه هستند:",
            "confidence": 0.9}


def _news_answer(conn):
    rows = conn.execute(
        "SELECT slug, title, date_jalali, summary FROM news"
        " ORDER BY date_iso DESC, slug LIMIT ?",
        (_NEWS_MAX,)).fetchall()
    if not rows:
        return None
    entries = [{"slug": r["slug"], "title": r["title"],
                "date_jalali": r["date_jalali"], "summary": r["summary"]}
               for r in rows]
    return {"kind": "news", "entries": entries,
            "text": "آخرین اخبار نمایشگاه:", "confidence": 0.85}


def match_guide(query: str, lang: str = "fa") -> dict | None:
    """The guide answer for a visitor question, or None.

    This is the function the chat router calls, before any paid tier. The
    detection is deterministic keyword matching on the UNexpanded normalized
    query — intent must see what the visitor actually typed, not what a
    synonym row added. `lang` is part of the router's call shape; the guide
    tables are Persian-only, so an English query matches no keyword and
    returns None on its own.
    """
    norm = normalize_persian(query or "", expand_synonyms=False)
    tokens = norm.split()
    if not tokens:
        return None
    # A booth or company question is retrieval's job, whatever guide word it
    # also carries («غرفه کجاست» asks about a booth, not the venue).
    if any(t.startswith("غرفه") or t.startswith("شرکت") for t in tokens):
        return None
    padded = f" {norm} "

    from app.db.connection import get_db_connection
    conn = get_db_connection()
    try:
        # Facts beat everything: a hours/dates/weather/address/crowding word
        # is answered from guide_facts or not at all — no guessing tier.
        for key, words in _FACT_TRIGGERS:
            if _has(padded, words):
                return _fact_answer(conn, key)

        # Transit mode words win over gates (the پارکینگ+مترو case), then
        # gates, then the transit phrasings that name no mode.
        for mode, words in _STATION_MODES:
            if _has(padded, words):
                return _stations_answer(conn, mode)
        if _has(padded, _GATE_TRIGGERS):
            return _gates_answer(conn)
        if _has(padded, _STATION_TRIGGERS):
            return _stations_answer(conn, "all")

        if _has(padded, _RESTAURANT_TRIGGERS):
            return _restaurants_answer(conn)
        if _has(padded, _NEWS_TRIGGERS):
            return _news_answer(conn)
        return None
    except Exception as e:  # noqa: BLE001 — missing table or any DB fault
        logger.info(f"[guide] tier unavailable: {type(e).__name__}: {e}")
        return None
    finally:
        conn.close()
