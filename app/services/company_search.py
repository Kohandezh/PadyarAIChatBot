"""Company-list tier: deterministic answers for "which companies ..." questions.

Why this exists (measured in production, 2026-08-27): «شرکت‌های هوش مصنوعی
اینوتکس را معرفی کن» is a LIST question, but single-document retrieval can
only ever pick ONE entry — list questions were structurally unanswerable and
the outcome depended on phrasing. Worse, the faq-20 entry is literally the
out-of-scope REFUSAL text and contains «هوش مصنوعی اینوتکس», which made it a
token magnet: Tier 1 served the refusal at 0.81 as the "answer". The knowledge
base actually holds ~169 companies (one `companies` row each, carrying
activity_field), so the right answer was a list of the AI companies.

This tier answers list questions straight from the database — no LLM, no
similarity model. Everything here is deterministic: a conservative
list-intent check on the tokens the visitor actually typed, a read of
`companies`, and an all-keywords filter. When anything is off (no list
intent, no companies, a topic no company matches), it returns None and the
existing pipeline proceeds unchanged.
"""

from difflib import SequenceMatcher

from app.config import logger
from app.services.rerank import content_tokens
from app.utils.normalizer import normalize_persian

# List-intent vocabulary. normalize_persian folds ZWNJ to a space, so the
# plural «شرکت‌های» arrives as the two tokens «شرکت های»; the fully attached
# spelling «شرکتهای» survives as one token. Both spellings must trigger.
_PLURAL_SUFFIXES = {"ها", "های", "هایی"}
# «شرکتای» is the colloquial spelling of «شرکت‌های» and half the visitors type
# it. Found live, 2026-08-28.
_ATTACHED_PLURALS = {"شرکتها", "شرکتهای", "شرکتهایی", "شرکتای", "شرکتا"}
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


# ── Topic matching ───────────────────────────────────────────────────────
#
# WHY THIS IS NOT A WORD LIST (measured live, 2026-08-28). Topic keywords used
# to be "every content token not on the _MACHINERY blocklist", and every one of
# them had to appear in a company's row. That works for «شرکت های حوزه هوش
# مصنوعی» and breaks the moment a visitor writes a sentence:
#
#   ...رو اطلاعات شون رو میخوام  ->  needs «میخوام», «شون», «رو» in a company row
#   دیگه چه شرکت هایی داریم؟      ->  needs «دیگه» in a company row
#   حوضه (one wrong letter)       ->  needs «حوضه» in a company row
#
# None of those exist in any company record, so the tier returned None and the
# question fell through to a path that cannot see the company table. A
# blocklist of "words that are not topics" can never be finished: it has to
# anticipate every word a person might say around the topic.
#
# So the vocabulary comes from the DATA instead. activity_field, province and
# company_type are controlled vocabularies the organizer fills in, each value a
# FACET. A word is a topic only if a facet uses it, and the facet with the most
# overlap wins. Everything else the visitor said is, by construction, ignored.

# «the rest of them». A closed, meaningful category — not an open-ended list of
# things people say — and the ONLY leftover words that still mean "no filter".
_REST_WORDS = {"دیگه", "دیگر", "دیگری", "بقیه", "باقی", "سایر", "بیشتر"}

# «حوضه» is «حوزه» misspelled, and it is common enough to have broken a live
# query. It belongs with the machinery it is a misspelling OF.
_MACHINERY_EXTRA = {"حوضه", "فن", "آوری"}

# Alef, ye and kaf variants folded so two spellings of one word compare equal.
# «فن آوری» joined is «فنآوری», and the facet spells it «فناوری» — one alef
# apart. normalize_persian does not fold these, and it should not: they are
# distinct letters in general text.
_FOLD = str.maketrans({"آ": "ا", "أ": "ا", "إ": "ا", "ي": "ی", "ك": "ک", "ۀ": "ه"})


def _fold(token: str) -> str:
    return (token or "").translate(_FOLD)


# Defined here and not beside _MACHINERY because it needs _fold above.
_MACHINERY_FOLDED = {_fold(t) for t in _MACHINERY | _MACHINERY_EXTRA}


def _query_forms(tokens: list) -> set:
    """Every shape a facet word could take in what the visitor typed.

    Adjacent tokens are also compared JOINED. «فن آوری» is two tokens here and
    one word («فناوری») in the facet, and plain equality can never join them.
    Joining only NEIGHBOURS, never arbitrary pairs, keeps this from inventing
    words out of a long sentence.
    """
    forms = {_fold(t) for t in tokens}
    forms |= {_fold(tokens[i] + tokens[i + 1]) for i in range(len(tokens) - 1)}
    return forms


# A category label is short by nature: «هوش مصنوعی و داده» is four words. Two
# of the organizer's 170 rows have the company's whole DESCRIPTION pasted into
# the field column, and a paragraph contains «فناوری», «هوش», «سلامت», «برق»
# and «آموزش» — so it matched almost every question and «تا کی بازه؟» came back
# as a list of one company. Reading the vocabulary from the data means the data
# can poison it, and a length bound is what stops one bad cell from doing it.
_FACET_MAX_TOKENS = 8
_FACET_MAX_CHARS = 70


def _is_category_label(value: str) -> bool:
    return (len(value) <= _FACET_MAX_CHARS
            and len(value.split()) <= _FACET_MAX_TOKENS)


def _facets(companies: list) -> dict:
    """{facet value -> its folded content tokens}, read off the rows.

    activity_field is pipe-separated («هوش مصنوعی و داده | اتوماسیون، رباتیک و
    هوشمندسازی»), so it is split first: a company sits in up to three fields at
    once and must be findable under any of them.
    """
    out = {}
    for c in companies:
        for value in _company_facets(c):
            if value in out or not _is_category_label(value):
                continue
            # A facet word that is also list machinery is not a topic. «نوع
            # مجموعه» holds «صندوق سرمایه‌گذاری خطرپذیر شرکتی», and «شرکت» is
            # how a visitor asks for companies at all — counting it made
            # «دیگه چه شرکت هایی هستند؟» filter down to the rows that spell it.
            toks = {_fold(t) for t in content_tokens(normalize_persian(
                value, expand_synonyms=False))}
            # Fuzzy, not just exact: the value is «...خطرپذیر شرکتی» and the
            # machinery word is «شرکت». Exact removal leaves «شرکتی», which the
            # fuzzy matcher then happily connects back to the «شرکت» in the
            # visitor's question — so «دیگه چه شرکت هایی هستند؟» filtered down
            # to the one row that spells it.
            out[value] = {t for t in toks
                          if t not in _MACHINERY_FOLDED
                          and not _fuzzy_hit(t, _MACHINERY_FOLDED)}
    return out


def _company_facets(c: dict) -> set:
    values = set()
    for column in ("activity_field", "province", "company_type"):
        for part in str(c.get(column) or "").split("|"):
            part = part.strip()
            if part:
                values.add(part)
    return values


# Fuzzy matching is allowed only from here up. Below it, one edit is most of
# the word: «موش» and «هوش» are one letter apart and mean mouse and mind.
_FUZZY_MIN_LEN = 4
# 0.82 accepts one edit in a 5-letter word and two in a 9-letter one, and
# rejects «موش»/«هوش» (0.67) even before the length gate.
_FUZZY_CUTOFF = 0.82


def _unknown_forms(forms: set) -> set:
    """The query words the corpus has never seen — the only ones worth correcting.

    «سلام» and «سلامت» are one letter apart, and «تجهیزات پزشکی و سلامت دیجیتال»
    is a real facet, so a greeting came back as a list of 16 health companies
    (measured on a copy of the production content, 2026-08-28). The difference
    between that and «اصفحان» is not the edit distance, it is that «سلام» is a
    real word this install already holds and «اصفحان» is not a word at all.

    So: a word the corpus knows is taken at face value. Only a word it has
    never seen is a candidate for correction. Same source of truth as the
    unknown-entity guard in app/services/search.py, which is the other place
    that has to tell a typo from a word.
    """
    from app.services import search
    known = getattr(search, "_corpus_vocab", None) or set()
    if not known:
        # Not indexed yet. Correcting nothing is the safe half of the trade:
        # an exact match still works, and no greeting becomes a list.
        return set()
    folded_known = {_fold(t) for t in known}
    return {f for f in forms if f not in folded_known}


def _fuzzy_hit(facet_token: str, forms: set) -> bool:
    """Did the visitor write this facet word, allowing for a slip?

    THE POINT OF THIS FUNCTION. Ignoring an unknown word already survives a
    typo in the MACHINERY («حوضه» for «حوزه»), because machinery words are not
    topics. It does nothing when the TOPIC itself is misspelled: «هوش مصنوی»
    names a field and matches none of its tokens.

    Fixing that one report and waiting for the next is not a plan. What makes
    the general fix cheap is that the vocabulary is CLOSED — around thirty
    field names, a handful of provinces — so every query word can be compared
    against all of them. On a known, tiny set of right answers, edit distance
    is more dependable than asking a model, and it costs nothing.
    """
    if len(facet_token) < _FUZZY_MIN_LEN:
        return False
    for form in forms:
        if len(form) < _FUZZY_MIN_LEN:
            continue
        if SequenceMatcher(None, facet_token, form).ratio() >= _FUZZY_CUTOFF:
            return True
    return False


def _select_facets(tokens: list, companies: list):
    """The facets this query filters by, or None when it names none.

    A facet is a candidate when the visitor said at least TWO of its words, or
    exactly one word that no other facet uses. Two words is what «فناوری
    اطلاعات» gives; the distinctive single word is what «رباتیک» gives. A
    shared single word is not enough — «فناوری» alone sits in three different
    facets and picking one of them would be a guess.
    """
    facets = _facets(companies)
    if not facets:
        return None
    forms = _query_forms(tokens)
    correctable = _unknown_forms(forms)
    shared = {}
    for toks in facets.values():
        for t in toks:
            shared[t] = shared.get(t, 0) + 1

    scored = {}
    for value, toks in facets.items():
        hit = toks & forms
        # Exact first, fuzzy only for the words it did not already find, and
        # only FROM query words the corpus does not know. An exact match must
        # never be displaced by an approximate one.
        hit = hit | {t for t in toks - hit if _fuzzy_hit(t, correctable)}
        if len(hit) >= 2 or (len(hit) == 1 and shared[next(iter(hit))] == 1):
            scored[value] = len(hit)
    if not scored:
        return None
    best = max(scored.values())
    return {v for v, n in scored.items() if n == best}


def _load_companies() -> list:
    """Every company. See migrations/0013_companies.sql: a company used to be
    a `dataset` row with a matching `company_profiles` row (this was a JOIN);
    it is now one row of `companies`, so every row this reads back IS a
    company, no join and no separate "is this one?" test needed.
    """
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    try:
        rows = conn.execute(
            # video_url comes along because a listed company is a PICKABLE
            # company: the chip the visitor taps carries its booth clip, and a
            # title and its clip must never be looked up separately.
            "SELECT id, title, title_en, text, video_url,"
            " activity_field, province, company_type FROM companies"
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

    try:
        companies = _load_companies()
    except Exception as e:  # noqa: BLE001 — missing table or any DB fault: tier off
        logger.info(f"[company-search] tier unavailable: {type(e).__name__}: {e}")
        return None
    if not companies:
        return None

    selected = _select_facets(tokens, companies)

    # NAMING A FIELD IS ASKING FOR ITS COMPANIES, whether or not the visitor
    # says the word «شرکت». Measured with scripts/persona_probe.py against the
    # live install on 2026-08-28: only ONE of 28 conversation turns reached
    # this tier, because the intent check needed that word. «من به رباتیک
    # علاقه دارم چیزی هست؟» and «بازی سازی هم دارین؟» are both requests for
    # exhibitors and neither contains it.
    #
    # A facet match is a safe trigger precisely because the facet vocabulary is
    # the organizer's own: the visitor used a word from a list the customer
    # filled in, about the only thing that list describes. A question that
    # names no field («تا کی بازه؟») still needs the explicit word.
    if not (selected or _wants_company_list(tokens)):
        return None

    if selected is None:
        # No facet matched. Two very different questions land here.
        #
        #   دیگه چه شرکت هایی هستند؟          -> all of them. A real answer.
        #   شرکت‌های زیست فناوری را معرفی کن  -> a field we do not have. Defer.
        #
        # What separates them is whether anything TOPIC-SHAPED is left after
        # the list machinery. _MACHINERY is still a hand-written list, but its
        # job here is much smaller than it used to be: it no longer decides
        # what the topic IS (the data does that now), only whether one was
        # named at all. A word missing from it now causes a DEFER, which is
        # safe, instead of an empty match that looked like an answer.
        leftover = (content_tokens(norm) - _MACHINERY - _MACHINERY_EXTRA
                    - _REST_WORDS)
        if leftover:
            return None
        matched, filter_label = list(companies), ""
    else:
        matched = [c for c in companies if _company_facets(c) & selected]
        if not matched:
            return None
        # The facet's OWN name, not the words the visitor typed around it. The
        # headline has to say which zemine, and «۶۹ شرکت در زمینه «اطلاعات شون
        # رو میخوام»» is worse than no headline at all.
        filter_label = "، ".join(sorted(selected))

    matched.sort(key=lambda c: c.get("title") or "")

    # The RENDERING is delegated, the SELECTION above is not. answer.render_options
    # is the single writer of the displayed slice and the single producer of
    # offer_state, so the numbered list a visitor reads and the ids stored for
    # their next turn come from the same place and cannot disagree.
    #
    # The filter words are printed in the headline on purpose: «۶۹ شرکت در این
    # زمینه» hides WHICH zemine, so a wrong SET looked confidently right.
    from app.services.answer import render_options
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
        "keywords": sorted(selected) if selected else [],
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

    `entry` is the entry resolve_named_entity() already produced, so the
    company is settled before this runs and this only has to decide WHICH
    field. None means "not mine" — not a field question, no matching company
    row, the requested field empty for this company, or any DB fault. This
    tier degrades, it never raises.
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
