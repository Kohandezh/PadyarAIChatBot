"""Company-list tier: deterministic answers for "which companies ..." questions.

Why this exists (measured in production, 2026-08-27): «شرکت‌های هوش مصنوعی
اینوتکس را معرفی کن» is a LIST question, but single-document retrieval can
only ever pick ONE entry — list questions were structurally unanswerable and
the outcome depended on phrasing. Worse, the faq-20 entry is literally the
out-of-scope REFUSAL text and contains «هوش مصنوعی اینوتکس», which made it a
token magnet: Tier 1 served the refusal at 0.81 as the "answer". The dataset
actually holds ~169 company entries (one dataset row per company, each with a
company_profiles row carrying activity_field), so the right answer was a list
of the AI companies.

This tier answers list questions straight from the database — no LLM, no
similarity model. Everything here is deterministic: a conservative
list-intent check on the tokens the visitor actually typed, a JOIN over
dataset × company_profiles, and an all-keywords filter. When anything is off
(no list intent, no profiles table, a topic no company matches), it returns
None and the existing pipeline proceeds unchanged.
"""

from app.config import logger
from app.services.rerank import content_tokens
from app.utils.normalizer import normalize_persian

# List-intent vocabulary. normalize_persian folds ZWNJ to a space, so the
# plural «شرکت‌های» arrives as the two tokens «شرکت های»; the fully attached
# spelling «شرکتهای» survives as one token. Both spellings must trigger.
_PLURAL_SUFFIXES = {"ها", "های", "هایی"}
_ATTACHED_PLURALS = {"شرکتها", "شرکتهای", "شرکتهایی"}
# Question words that turn a singular «شرکت» into a list request. «چند» and
# «کدام» are also rerank STOPWORDS, which is fine: intent detection runs on
# the raw token list, not on content_tokens.
_LIST_TRIGGERS = {"چند", "کدام", "لیست", "معرفی"}

# The machinery of a list question: these words signal "give me companies"
# but never narrow WHICH companies, so they are stripped before the keyword
# filter (content_tokens has already dropped the rerank stopwords). The verbs
# are the ways visitors phrase the request itself («معرفی کن», «داریم؟»).
_MACHINERY = _ATTACHED_PLURALS | _PLURAL_SUFFIXES | _LIST_TRIGGERS | {
    "شرکت", "حوزه", "زمینه", "فعال", "فعالیت", "نمایشگاه", "اینوتکس",
    "کن", "کنید", "بگو", "بگویید", "بده", "بدهید", "نام",
    "داریم", "دارید", "دارند", "دارد", "هست", "هستند", "حضور",
    # «شرکت‌های استان اصفهان» names the province the same way «حوزه» names the
    # field: the word is how the visitor points at the column, not a value to
    # match. Left in, «استان» matched no company's text and the tier returned
    # None for a question the database could answer exactly.
    "استان", "شهر",
}

# Answer-length cap: a wall of 169 names is not an answer a visitor can use.
_MAX_NAMES = 15


def _wants_company_list(tokens: list) -> bool:
    """Conservative, deterministic list-intent check on the normalized,
    UNexpanded token list. Tight scope on purpose: a false positive here
    hijacks a single-company question, a false negative just keeps today's
    behavior."""
    if any(t in _ATTACHED_PLURALS for t in tokens):
        return True
    for i, t in enumerate(tokens[:-1]):
        if t == "شرکت" and tokens[i + 1] in _PLURAL_SUFFIXES:
            return True
    if "شرکت" in tokens and any(t in _LIST_TRIGGERS for t in tokens):
        return True
    return False


def _load_companies() -> list:
    """Every dataset row that IS a company (has a company_profiles row).

    Deliberately no ensure_tables() call: an install without the leads module
    has no company_profiles table, and that absence simply means this tier is
    off — creating the table here would grow schema the install never ordered.
    """
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    try:
        rows = conn.execute(
            "SELECT d.id, d.title, d.title_en, d.text,"
            " p.activity_field, p.province, p.company_type"
            " FROM dataset d JOIN company_profiles p ON p.dataset_id = d.id"
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def _render(matched: list, keywords: set, lang: str) -> str:
    """Count + bulleted names + one invitation line. Short lines, no jargon,
    no markdown headers — a first-time visitor must read it in one glance."""
    names = []
    for c in matched[:_MAX_NAMES]:
        if lang == "en":
            name = (c.get("title_en") or "").strip() or (c.get("title") or "")
        else:
            name = c.get("title") or ""
        names.append(f"• {name.strip()}")
    extra = len(matched) - _MAX_NAMES

    if lang == "en":
        if keywords:
            head = f"{len(matched)} companies work in this field:"
        else:
            head = f"{len(matched)} companies are at the exhibition:"
        lines = [head, *names]
        if extra > 0:
            lines.append(f"... and {extra} more companies.")
        lines.append("Ask about any company by name to learn more about it.")
    else:
        if keywords:
            head = f"{len(matched)} شرکت در این زمینه در نمایشگاه حضور دارند:"
        else:
            head = f"{len(matched)} شرکت در نمایشگاه حضور دارند:"
        lines = [head, *names]
        if extra > 0:
            lines.append(f"و {extra} شرکت دیگر")
        lines.append("برای آشنایی بیشتر، نام هر شرکت را بپرسید.")
    return "\n".join(lines)


def answer_company_list(query: str, lang: str = "fa"):
    """The list answer for a "which companies ..." query, or None.

    None means "not mine": no list intent, no company data (missing table,
    DB fault, empty join), or a topic keyword no company matches — in every
    such case the caller's existing pipeline must proceed as if this tier
    did not exist. This tier degrades, it never raises.
    """
    # UNexpanded normalization, same as the entity guards: intent detection
    # must see what the visitor actually typed, not what synonym rows added.
    norm = normalize_persian(query or "", expand_synonyms=False)
    tokens = norm.split()
    if not _wants_company_list(tokens):
        return None

    try:
        companies = _load_companies()
    except Exception as e:  # noqa: BLE001 — missing table or any DB fault: tier off
        logger.info(f"[company-search] tier unavailable: {type(e).__name__}: {e}")
        return None
    if not companies:
        return None

    # Topic keywords = the query's content tokens minus the list machinery.
    # What remains (e.g. «هوش», «مصنوعی») is what the visitor filtered by.
    keywords = content_tokens(norm) - _MACHINERY

    matched = []
    for c in companies:
        # Province and company type join the haystack for the same reason
        # activity_field is in it: «شرکت‌های استان اصفهان» filters on a column
        # the database holds, and the company's own description rarely repeats
        # its province.
        hay = set(normalize_persian(
            f"{c.get('activity_field') or ''} {c.get('province') or ''}"
            f" {c.get('company_type') or ''}"
            f" {c.get('title') or ''} {c.get('text') or ''}",
            expand_synonyms=False,
        ).split())
        # ALL keywords must be present: «هوش مصنوعی» must not list every
        # company that merely says «هوش» somewhere.
        if keywords <= hay:
            matched.append(c)

    if keywords and not matched:
        # A topic we cannot confirm locally — the AI tier can actually judge
        # it; listing zero companies would be a confident non-answer.
        return None

    # No keywords left («چه شرکت‌هایی در نمایشگاه هستند؟») → all companies.
    matched.sort(key=lambda c: c.get("title") or "")
    return {
        "text": _render(matched, keywords, lang),
        "count": len(matched),
        "matched_ids": [c["id"] for c in matched],
    }


# ── The company-field tier ───────────────────────────────────────────────
#
# Why this exists (measured 2026-08-27): «شماره تماس شرکت دکیو چیست؟» names
# ONE company and asks for ONE recorded fact, and the pipeline answered with
# that company's generic description — nothing in the chat path had ever read
# company_profiles, so the phone the organizer already holds was unreachable.
#
# Person-scoped request words. These must be tested BEFORE the public map,
# because «شماره مدیرعامل شرکت دکیو» contains «شماره», which maps to the
# PUBLIC company_phone. Without this precedence the collision answers a
# question about a PERSON out of that person's own record. «مدیر» covers the
# spaced spelling «مدیر عامل» too (normalize_persian folds the ZWNJ in
# «مدیر‌عامل» to a space, so both arrive as separate tokens).
_WITHHELD_WORDS = {
    "مدیرعامل", "مدیر", "رئیس", "موبایل", "همراه", "ایمیل",
    "مسئول", "نماینده",
}

# Request word → public column, in priority order. First match wins, so a
# query carrying two of them («استان ... کجاست») answers the more specific
# one. «وب‌سایت» normalizes to the two tokens «وب سایت», so «سایت» catches
# every spelling of it.
_FIELD_WORDS = (
    ("company_phone", {"تلفن", "شماره", "تماس"}),
    ("website", {"سایت", "وبسایت"}),
    ("province", {"استان", "شهر"}),
    ("address", {"آدرس", "نشانی", "کجاست"}),
    ("activity_field", {"حوزه"}),
)

_FIELD_LABELS_FA = {
    "company_phone": "شماره تماس",
    "website": "وب‌سایت",
    "address": "نشانی",
    "address_en": "نشانی",
    "province": "استان",
    "activity_field": "زمینه فعالیت",
}
_FIELD_LABELS_EN = {
    "company_phone": "Phone",
    "website": "Website",
    "address": "Address",
    "address_en": "Address",
    "province": "Province",
    "activity_field": "Field of work",
}


def _requested_field(tokens: list):
    """The public column this query asks for, or None. Deterministic word
    lookup on the UNexpanded normalized tokens — same approach as the list
    tier and the entity guards: field detection must see what the visitor
    actually typed, not what a synonym row added."""
    for field, words in _FIELD_WORDS:
        if any(t in words for t in tokens):
            return field
    # «زمینه فعالیت» only as the pair: «زمینه» alone is too general a word to
    # read as a request for the activity column.
    for i, t in enumerate(tokens[:-1]):
        if t == "زمینه" and tokens[i + 1] == "فعالیت":
            return "activity_field"
    return None


def answer_company_field(query: str, entry: dict, lang: str = "fa"):
    """The one recorded fact this query asks about a named company, or None.

    `entry` is the dataset row resolve_named_entity() already produced, so the
    company is settled before this runs and this only has to decide WHICH
    field. None means "not mine" — not a field question, no profile row, the
    requested field empty for this company, or any DB fault (an install
    without the leads module has no company_profiles table). This tier
    degrades, it never raises.
    """
    if not entry:
        return None
    norm = normalize_persian(query or "", expand_synonyms=False)
    tokens = norm.split()

    withheld = any(t in _WITHHELD_WORDS for t in tokens)
    field = _requested_field(tokens)
    if not withheld and field is None:
        return None

    try:
        # Never read a withheld column: the allowlist is the only door.
        from app.services.company_profiles import public_profile
        profile = public_profile(entry.get("id", ""))
    except Exception as e:  # noqa: BLE001 — missing table or any DB fault: tier off
        logger.info(f"[company-field] tier unavailable: {type(e).__name__}: {e}")
        return None
    if not profile:
        return None

    title = ((entry.get("title_en") or "").strip() if lang == "en" else "") \
        or (entry.get("title") or "").strip()
    phone = profile.get("company_phone", "")
    website = profile.get("website", "")

    if withheld:
        # Say plainly what is not shared, then hand over what is. No mention
        # of a record or a database: the visitor asked a person's number, and
        # the honest short answer is "we do not give those out".
        if lang == "en":
            lines = ["We do not share the personal contact details of individuals."]
            if phone or website:
                lines.append(f"Here is the public contact information for {title}:")
            if phone:
                lines.append(f"Phone: {phone}")
            if website:
                lines.append(f"Website: {website}")
        else:
            lines = ["شماره و اطلاعات تماس شخصی افراد را در اختیار کسی نمی‌گذاریم."]
            if phone or website:
                lines.append(f"اطلاعات تماس عمومی {title} این است:")
            if phone:
                lines.append(f"شماره تماس: {phone}")
            if website:
                lines.append(f"وب‌سایت: {website}")
        return {"text": "\n".join(lines), "field": "withheld"}

    # English address when we have one and the visitor is reading English.
    if field == "address" and lang == "en" and profile.get("address_en"):
        field = "address_en"
    value = profile.get(field, "")
    if not value:
        # Nothing recorded for this company — decline so the visitor still
        # gets the company's own entry instead of a blank line.
        return None

    if lang == "en":
        return {"text": f"{_FIELD_LABELS_EN[field]} for {title}: {value}",
                "field": field}
    return {"text": f"{_FIELD_LABELS_FA[field]} {title}: {value}", "field": field}
