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
            # video_url comes along because a listed company is a PICKABLE
            # company: the chip the visitor taps carries its booth clip, and a
            # title and its clip must never be looked up separately.
            "SELECT d.id, d.title, d.title_en, d.text, d.video_url,"
            " p.activity_field, p.province, p.company_type"
            " FROM dataset d JOIN company_profiles p ON p.dataset_id = d.id"
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


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

    # The RENDERING is delegated, the SELECTION above is not. answer.render_options
    # is the single writer of the displayed slice and the single producer of
    # offer_state, so the numbered list a visitor reads and the ids stored for
    # their next turn come from the same place and cannot disagree.
    #
    # The filter words are printed in the headline on purpose: «۶۹ شرکت در این
    # زمینه» hides WHICH zemine, so a wrong SET looked confidently right.
    from app.services.answer import render_options
    # Printed in the visitor's own word order, not sorted: «هوش مصنوعی» reads
    # as a field name, «مصنوعی هوش» reads as a bug.
    ordered = [t for t in dict.fromkeys(tokens) if t in keywords]
    filter_label = " ".join(ordered)
    # The query travels into offer_state so «بیشتر» can rebuild this same
    # matched set on the next turn. offer_state caps its ids, and with 70 AI
    # companies that cap made page 2 report «۵۰ شرکت» and left 51..70
    # unreachable (measured 2026-08-28).
    text, options, offer_state = render_options(
        matched, "", lang, start_index=1, total=len(matched),
        filter_label=filter_label, source_query=query or "")
    return {
        "text": text,
        "count": len(matched),
        "matched_ids": [c["id"] for c in matched],
        "displayed_ids": [o["id"] for o in options],
        "options": options,
        "offer_state": offer_state,
        "keywords": sorted(keywords),
        # The filter words in the visitor's own order — the headline reads as
        # a field name, and the pager needs the same string on every page.
        "filter_label": filter_label,
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
        # `label`/`value` alongside the prose: the answer is DATA the caller may
        # need (a log row, a future chip), not only a sentence.
        return {"text": "\n".join(lines), "field": "withheld",
                "label": "", "value": phone or website or ""}

    # English address when we have one and the visitor is reading English.
    if field == "address" and lang == "en" and profile.get("address_en"):
        field = "address_en"
    value = profile.get(field, "")
    if not value:
        # Nothing recorded for this company — decline so the visitor still
        # gets the company's own entry instead of a blank line.
        return None

    if lang == "en":
        label = _FIELD_LABELS_EN[field]
        return {"text": f"{label} for {title}: {value}", "field": field,
                "label": label, "value": value}
    label = _FIELD_LABELS_FA[field]
    return {"text": f"{label} {title}: {value}", "field": field,
            "label": label, "value": value}
