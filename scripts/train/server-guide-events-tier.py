"""Guide knowledge tier: deterministic answers for visitor-guide questions.

WHY THIS EXISTS: opening hours, entrances, transit, restaurants, news and
the talks/panels/pitches program are
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


# Talks/panels/pitches (talksiran crawl, migrations/0021_talks_events.sql).
# Event words beat the fact words when both appear: «تاریخ پنل ...» asks
# about a talk, not the show dates. «برنامه» alone stays with retrieval —
# only day-scoped program phrases are ours.
_EVENT_TYPE_WORDS = {"پنل": "panel", "تاکس": "talk", "پیچ": "pitch"}
_EVENT_WORDS = ("پنل", "تاکس", "پیچ", "رویداد")
_PROGRAM_PHRASES = ("برنامه امروز", "برنامه فردا", "برنامه دیروز",
                    "برنامه نمایشگاه", "برنامه رویداد", "برنامه ها",
                    "برنامه روز", "چه برنامه")
_DAY_OFFSETS = {"امروز": 0, "فردا": 1, "دیروز": -1}
_EVENT_TYPE_LABELS = {"panel": "پنل", "talk": "تاکس", "pitch": "پیچ"}
_EVENT_LIST_MAX = 8
_MONTH_NAMES = {1: "فروردین", 2: "اردیبهشت", 3: "خرداد", 4: "تیر",
                5: "مرداد", 6: "شهریور", 7: "مهر", 8: "آبان",
                9: "آذر", 10: "دی", 11: "بهمن", 12: "اسفند"}


def _jalali_from_gregorian(gy: int, gm: int, gd: int):
    g_d_m = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]
    jy = 979
    gy -= 1600
    gy2 = gy + 1 if gm > 2 else gy
    days = (365 * gy + (gy2 + 3) // 4 - (gy2 + 99) // 100
            + (gy2 + 399) // 400 - 80 + gd + g_d_m[gm - 1])
    jy += 33 * (days // 12053)
    days %= 12053
    jy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        jy += (days - 1) // 365
        days = (days - 1) % 365
    if days < 186:
        jm, jd = 1 + days // 31, days % 31 + 1
    else:
        jm, jd = 7 + (days - 186) // 30, (days - 186) % 30 + 1
    return jy, jm, jd


def _jalali_today():
    """(jy, jm, jd) in Tehran time."""
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone(timedelta(hours=3, minutes=30)))
    return _jalali_from_gregorian(now.year, now.month, now.day)


def _jdate(jy: int, jm: int, jd: int) -> str:
    return f"{jy:04d}/{jm:02d}/{jd:02d}"


def _resolve_day(norm: str, rows) -> str:
    """The jdate the visitor asked about, '' when they did not."""
    import re
    from datetime import date, datetime, timedelta, timezone
    m = re.search(r"\d{4}/\d{2}/\d{2}", norm)
    if m and any(r["jdate"] == m.group(0) for r in rows):
        return m.group(0)
    # Shift in GREGORIAN days, then read the Jalali calendar off the result.
    today = datetime.now(timezone(timedelta(hours=3, minutes=30))).date()
    tokens = set(norm.split())
    for word, off in _DAY_OFFSETS.items():
        if word in tokens:
            g = today + timedelta(days=off)
            return _jdate(*_jalali_from_gregorian(g.year, g.month, g.day))
    return ""


_SPECIFIC_WORDS = ("کی", "کیه", "کیست", "چیست", "چیه", "کجاست", "چه موضوع")
_TITLE_STOPWORDS = {"کی", "چی", "چه", "هست", "این", "آن", "برای", "اند",
                    "هستم", "است", "شد", "بود", "میشه", "هستند",
                    "های", "ها", "ان", "برنامه", "پنل", "تاکس", "پیچ",
                    "رویداد", "موضوع"}


def _event_title_answer(tokens, rows, etype: str = "") -> dict | None:
    pool = [r for r in rows if not etype or (r["etype"] or "") == etype] or rows
    best, best_score = None, 0
    for r in pool:
        title = (r["title"] or "").strip()
        if not title:
            continue
        ttokens = set(normalize_persian(title, expand_synonyms=False).split())
        score = 0
        for t in tokens:
            if len(t) < 3 or t in _TITLE_STOPWORDS:
                continue
            if t in ttokens:
                score += 1
            elif len(t) >= 6 and t in title:
                score += 1
        if score > best_score:
            best, best_score = r, score
    if best is None or best_score < 2:
        return None
    label = _EVENT_TYPE_LABELS.get((best["etype"] or "").lower(), "رویداد")
    when = " ".join(p for p in (best["jdate"], best["start_time"]) if p)
    where = best["hall"] or ""
    desc = (best["description"] or "").strip()
    if len(desc) > 350:
        desc = desc[:350].rsplit(" ", 1)[0] + "…"
    lines = [f"{label} «{best['title']}»"]
    if when:
        lines.append(f"زمان: {when}" + (f"، {where}" if where else ""))
    elif where:
        lines.append(f"محل: {where}")
    if desc:
        lines.append(desc)
    return {"kind": "event", "text": "\n".join(lines), "confidence": 0.9}


def _events_answer(conn, norm: str, padded: str) -> dict | None:
    rows = conn.execute(
        "SELECT etype, title, description, jdate, start_time, hall"
        " FROM talks_events ORDER BY jdate, start_time").fetchall()
    if not rows:
        return None
    tokens = norm.split()

    etype = ""
    for word, code in _EVENT_TYPE_WORDS.items():
        if f" {word} " in padded or f" {word} ها " in padded:
            etype = code
            break
    day = _resolve_day(norm, rows)

    title_hit = _event_title_answer(tokens, rows, etype)
    if title_hit is not None:
        return title_hit

    # «پنل X کی هست» with no title hit is a SPECIFIC question this tier
    # cannot answer — defer to the model tiers instead of listing whatever
    # happens to run today. A day word or a plural («پنل‌ها») still lists.
    if day == "" and "ها" not in tokens \
            and any(w in tokens for w in _SPECIFIC_WORDS):
        return None

    picked = [r for r in rows
              if (not etype or (r["etype"] or "") == etype)
              and (not day or r["jdate"] == day)]
    if not picked and day:
        # The asked-about day has nothing: serve the nearest day that does,
        # under ITS OWN date — never list one day's program under another.
        picked = [r for r in rows if not etype or (r["etype"] or "") == etype]
        day = ""
    if not picked:
        return None

    if not day:
        jy, jm, jd = _jalali_today()
        today = _jdate(jy, jm, jd)
        days = sorted({r["jdate"] for r in picked if r["jdate"]})
        day = next((d for d in days if d >= today), days[-1] if days else "")
        picked = [r for r in picked if r["jdate"] == day]

    label = _EVENT_TYPE_LABELS.get(etype, "برنامه")
    jy, jm, jd = (int(x) for x in day.split("/")) if day else (0, 1, 0)
    title = f"{label}‌های {jd} {_MONTH_NAMES.get(jm, '')}:" if day else f"{label}:"

    lines = []
    for r in picked[:_EVENT_LIST_MAX]:
        when = r["start_time"] or ""
        hall = r["hall"] or ""
        line = f"• {when} — {r['title']}" if when else f"• {r['title']}"
        if hall:
            line += f" ({hall})"
        lines.append(line)
    extra = len(picked) - _EVENT_LIST_MAX
    if extra > 0:
        lines.append(f"و {extra} مورد دیگر…")
    return {"kind": "events", "text": title + "\n" + "\n".join(lines),
            "confidence": 0.9}


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
        # Event words beat the fact words when both appear: «تاریخ پنل ...»
        # asks about a talk, not the show dates. A missing talks_events
        # table lands in the except below and this tier stays inert.
        if _has(padded, _EVENT_WORDS) or _has(padded, _PROGRAM_PHRASES):
            ev = _events_answer(conn, norm, padded)
            if ev is not None:
                return ev

        # Facts beat everything: a hours/dates/weather/address/crowding word
        # is answered from guide_facts or not at all — no guessing tier.
        # Except dates: «تاریخ/کی برگزار پنل X» names an EVENT, and the
        # events tier above just deferred it to the model tiers on purpose.
        for key, words in _FACT_TRIGGERS:
            if key == "dates" and _has(padded, _EVENT_WORDS):
                continue
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
