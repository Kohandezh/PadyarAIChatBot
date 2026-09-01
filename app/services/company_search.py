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

import re
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


# SUFFIX DERIVATION (live failure, Elecomp 2026-09-01): «چه بانک هایی هستن
# تو نمایشگاه» came back «اطلاعات دقیقی ندارم» because the visitor's word
# «بانک» never matched the facet token «بانکداری» — the organizer names
# fields with DERIVED nouns, the visitor types the base word. One closed
# suffix list, and only ever FACET = QUERY + suffix, never the other way: the
# facet vocabulary is the organizer's controlled list, so growing a word by a
# known derivational suffix is safe there, while trimming arbitrary endings
# off query words («بانکک» for «بانکک‌ها») would match things nobody typed.
_STEM_SUFFIXES = ("داری", "ها", "سازی", "ی")


def _stem_hit(facet_token: str, forms: set) -> bool:
    """Does this facet token derive from a word the visitor typed?

    «بانکداری» is «بانک» + «داری» — a fact about how the field is NAMED, not
    a similarity guess, which is why this runs beside exact matching and
    before the fuzzy corrector. A base of two letters or fewer never stems:
    with «ی» attached, two letters make a three-letter word and half the
    short function words would suddenly "derive" from something («آب» ->
    «آبها»). And «بانک» must not match «بانکک» — «ک» is not on the closed
    suffix list, so it cannot.
    """
    for form in forms:
        if len(form) <= 2:
            continue
        if (facet_token.startswith(form)
                and facet_token[len(form):] in _STEM_SUFFIXES):
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
    stem_only = set()
    for value, toks in facets.items():
        # Glue words organize a facet's DESCRIPTION, they are never the
        # topic the visitor means — keep them from creating hits at all.
        toks = toks - _FACET_GLUE_WORDS
        exact = toks & forms
        # Exact first, then suffix derivation (still a fact about the
        # organizer's own naming), fuzzy last and only for the words exact
        # and stem did not already find, and only FROM query words the corpus
        # does not know. An exact match must never be displaced by an
        # approximate one.
        stemmed = {t for t in toks - exact if _stem_hit(t, forms)}
        # A stem hit (بانک -> بانکداری) is a fact about the field's NAME but
        # never a DISTINCTIVE one: the organizer writes the same field as
        # «بانکداری» for one exhibitor and «بانکداری دیجیتال» for the next,
        # so the derived token legitimately sits in several facet VALUES and
        # the shared==1 rule below rejects it exactly when the data is
        # richest (live failure, Elecomp 2026-09-01: «چه بانک هایی…» met
        # both spellings and got NOTHING). Stem-only facets therefore take
        # the UNION path — and only when exact scoring found nothing, since
        # an exact distinctive word always beats a stem.
        if stemmed and not exact:
            stem_only.add(value)
        hit = exact | stemmed
        hit = hit | {t for t in toks - hit if _fuzzy_hit(t, correctable)}
        if len(hit) >= 2 or (len(hit) == 1 and shared[next(iter(hit))] == 1):
            scored[value] = len(hit)
    if not scored:
        # The union of every facet the visitor's base word derives into.
        # Several tied exact facets already return as a set; a stem tie gets
        # the same treatment — the filter label falls back to the visitor's
        # own words, which is exactly right for «بانک».
        return stem_only or None
    best = max(scored.values())
    return {v for v, n in scored.items() if n == best}


def _load_companies() -> list:
    """Every APPROVED company. See migrations/0013_companies.sql: a company
    used to be a `dataset` row with a matching `company_profiles` row (this
    was a JOIN); it is now one row of `companies`, so every row this reads
    back IS a company, no join and no separate "is this one?" test needed.

    `text <> ''` excludes a company a visitor just proposed at the booth (see
    app/services/leads.py's `propose_company`): that row has a real title but
    an empty `text` until an admin approves its first pending edit, and this
    is a chatbot-facing read — a shell nobody has reviewed yet must not be
    named in a "which companies..." answer.
    """
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    try:
        rows = conn.execute(
            # video_url comes along because a listed company is a PICKABLE
            # company: the chip the visitor taps carries its booth clip, and a
            # title and its clip must never be looked up separately. hall and
            # booth_number (migrations 0015/0016) come along for the same
            # reason a title does: the hall-list and booth-lookup tiers answer
            # from these same approved rows, and reading them in a second
            # query would just re-state the `text <> ''` approval rule.
            "SELECT id, title, title_en, text, video_url,"
            " activity_field, province, company_type, priority_boost,"
            " hall, booth_number"
            " FROM companies"
            " WHERE text <> ''"
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
        if len(selected) == 1:
            # The facet's OWN name, not the words the visitor typed around it.
            # The headline has to say which zemine, and «۶۹ شرکت در زمینه
            # «اطلاعات شون رو میخوام»» is worse than no headline at all.
            filter_label = next(iter(selected))
        else:
            # Several facets TIED at the same score. The union of their names
            # is not a zemine anyone can read: on the elecomp install
            # (2026-08-31) «هوش مصنوعی» sits inside 45 of the organizer's
            # activity fields, so «سکوی هوش مصنوعی چی هست» headed its answer
            # with an 800-character comma string — half the response, and
            # half a minute of the phone typing it out. The honest label for
            # a tie is the visitor's OWN matched words: they are what did
            # the filtering, in the order they were asked.
            facet_words = set()
            facet_map = _facets(companies)
            for value in selected:
                facet_words |= facet_map.get(value, set())
            # A stem-union tie (بانک across بانکداری spellings) has the
            # visitor's word in NO facet token, so the label would fall
            # back to the facet NAMES — the 800-character headline this
            # branch exists to prevent. Let a query word that DERIVES into
            # a facet token count as the visitor's own matched word.
            def _derives(t):
                f = _fold(t)
                return any(ft.startswith(f) and ft[len(f):] in _STEM_SUFFIXES
                           for ft in facet_words if len(f) > 2)
            hit = [t for t in tokens
                   if _fold(t) in facet_words or _derives(t)]
            hit += [tokens[i] + tokens[i + 1]
                    for i in range(len(tokens) - 1)
                    if _fold(tokens[i] + tokens[i + 1]) in facet_words]
            filter_label = (" ".join(dict.fromkeys(hit))
                            or "، ".join(sorted(selected)))

    return _render_list(matched, selected, filter_label, lang, query or "")


def _render_list(matched: list, selected, filter_label: str, lang: str,
                 query: str, lead: str = "") -> dict:
    """Sort, render and return the list answer every list-shaped tier serves.

    Split out of answer_company_list (2026-09-01) because a second tier needed
    the exact same tail: the hall list filters by hall instead of by facet,
    but the sorted page, the offer_state and the returned dict must stay ONE
    shape — the router logs `count`/`displayed_ids`/`keywords` and hands
    `offer_state` to the next turn without knowing which tier produced them.

    `lead` is a head line the CALLER authored deterministically (the hall tier
    names the hall). It is not offered to render_options' lead parameter:
    that pathway exists for MODEL sentences and its firewall rejects ANY digit
    — «شرکت‌های سالن ۶:» carries the hall number and would be logged as
    rejected on every hall answer. Our head carries only the organizer's own
    hall value, the same trust level as the filter_label digits render_options
    itself prints inside its standard head; swapping line 0 keeps the numbered
    slice and the offer_state single-written by render_options.
    """
    # Boosted companies first (organizer-set sponsor placement), alphabetical
    # within each group — a boost changes ORDER only, never WHICH companies
    # matched above. See migrations/0014_company_priority_boost.sql.
    matched.sort(key=lambda c: (0 if c.get("priority_boost") else 1,
                                c.get("title") or ""))

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
    if (lead or "").strip():
        text = "\n".join([lead.strip(), *text.splitlines()[1:]])
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


# ── The hall-list and booth-lookup tiers ──────────────────────────────────
#
# Why these exist (live, Elecomp 2026-09-01): «شرکت های سالن 6» was REFUSED
# («من فقط می‌توانم درباره نمایشگاه...») because no tier had a hall
# dimension, and «غرفه 377» went out of scope because nothing looked a booth
# number up. Both facts are already recorded per company (migrations
# 0015/0016: `hall` like «سالن ۶» / «میلاد (31B)» / «سالن ۳۸B», `booth_number`
# like «377» / «5-4»), so both questions are answerable deterministically from
# the same approved rows the list tier reads — no AI, no similarity model.

# normalize_persian deliberately keeps Persian digits as Persian, so both
# sides of a hall/booth comparison run through this fold first: ۰-۹ (and the
# Arabic ٠-٩ variants) become ASCII, and the alef/ye/kaf fold above applies so
# «سالن» spelled with ي still compares equal.
_DIGIT_FOLD = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


def _hall_key(value: str) -> str:
    """One canonical form for a hall label or a query token: normalized,
    digit-folded, letter-folded. «سالن ۳۸B» and «سالن 38b» meet here."""
    return _fold(normalize_persian(value or "",
                                   expand_synonyms=False).translate(_DIGIT_FOLD))


def _match_hall(qtokens: list, halls: dict):
    """The canonical hall key this query names, or None.

    Two match shapes, because hall labels come in two shapes. «سالن ۳۸B» is
    fully typed by the visitor, so its whole token run is looked for in the
    query — a run, never a substring, so «سالن ۳» cannot hit «سالن ۳۸B» and
    «37» cannot hit «377». «میلاد (31B)» carries a site code the visitor
    does NOT type — they say «میلاد پایین» — and «فضای باز» is said as
    «محوطه», so for those the label's own word is the anchor and a
    پایین/بالا hint only PREFERs among several میلاد halls, it never rejects
    the one hall an install actually has.
    """
    ordered = sorted(halls, key=lambda k: (-len(k.split()), k))
    for key in ordered:
        ktoks = key.split()
        span = len(ktoks)
        if span and any(qtokens[i:i + span] == ktoks
                        for i in range(len(qtokens) - span + 1)):
            return key
    if "میلاد" in qtokens:
        milad = [k for k in ordered if "میلاد" in k.split()]
        if milad:
            level = ("پایین" if "پایین" in qtokens
                     else "بالا" if "بالا" in qtokens else "")
            if level:
                for k in milad:
                    if level in k.split():
                        return k
            return milad[0]
    if "محوطه" in qtokens or ("فضای" in qtokens and "باز" in qtokens):
        for k in ordered:
            if {"فضای", "باز", "محوطه"} & set(k.split()):
                return k
    return None


def answer_hall_list(query: str, lang: str = "fa") -> dict | None:
    """The list of companies in the hall this query names, or None.

    None means "not mine": no hall word+identifier the visitor typed, a hall
    no recorded company sits in, no hall data at all, or any DB fault — in
    every such case the caller's pipeline proceeds as if this tier did not
    exist. Companies without a hall value never match any hall question.
    Degrades, never raises.
    """
    norm = normalize_persian(query or "", expand_synonyms=False)
    tokens = norm.split()

    try:
        companies = _load_companies()
    except Exception as e:  # noqa: BLE001 — missing table or any DB fault: tier off
        logger.info(f"[hall-list] tier unavailable: {type(e).__name__}: {e}")
        return None

    # The DISTINCT hall values, each keeping its first-seen DB spelling so
    # the headline prints the organizer's own label («سالن ۶», not the
    # visitor's «سالن 6»).
    halls: dict = {}
    for c in companies:
        label = (c.get("hall") or "").strip()
        if not label:
            continue
        key = _hall_key(label)
        entry = halls.setdefault(key, {"label": label, "rows": []})
        entry["rows"].append(c)
    if not halls:
        return None

    key = _match_hall([_hall_key(t) for t in tokens], halls)
    if key is None:
        return None
    hall = halls[key]
    if not hall["rows"]:
        return None
    # The lead names the hall in the organizer's spelling: at a booth, «۲
    # شرکت در زمینه «سالن ۶»» makes the visitor parse a zemine phrase to
    # learn where to walk, while «شرکت‌های سالن ۶:» says it directly.
    lead = (f"Companies in {hall['label']}:" if lang == "en"
            else f"شرکت‌های {hall['label']}:")
    return _render_list(hall["rows"], None, hall["label"], lang,
                        query or "", lead=lead)


# normalize_persian turns every non-word character into a space, so the
# hyphen in a booth like «6-10» arrives as TWO tokens («6», «10») while the
# recorded value keeps it. Comparing digits-only on both sides joins them
# back without ever making «37» equal «377»: equality is still whole-string.
_BOOTH_DIGITS = re.compile(r"[0-9]+")


def _booth_candidate(tokens: list):
    """The booth number this query carries, digits-folded, or None.

    «غرفه 377» (also «شماره غرفه 377» — the word غرفه with the number right
    after it) and a bare all-digit query — the ROUTER decides when a bare
    number is booth-shaped enough to send here; this only reads it.
    """
    folded = [t.translate(_DIGIT_FOLD) for t in tokens]
    for i, t in enumerate(folded):
        if t == "غرفه":
            j, digits = i + 1, []
            while j < len(folded) and _BOOTH_DIGITS.fullmatch(folded[j]):
                digits.append(folded[j])
                j += 1
            if digits:
                return "".join(digits)
    if folded and all(_BOOTH_DIGITS.fullmatch(t) for t in folded):
        return "".join(folded)
    return None


def answer_booth_lookup(query: str, lang: str = "fa") -> dict | None:
    """The company at the booth number this query carries, or None.

    Mirrors the answer_company_field contract (text/field/label/value — the
    router serves it as that company's answer) plus `confidence` 0.95: the
    match is an exact database equality, not a similarity estimate, so it is
    trusted the way the other deterministic tiers are. None when the query
    carries no booth token or no recorded booth matches. Degrades, never
    raises.
    """
    norm = normalize_persian(query or "", expand_synonyms=False)
    candidate = _booth_candidate(norm.split())
    if not candidate:
        return None

    try:
        companies = _load_companies()
    except Exception as e:  # noqa: BLE001 — missing table or any DB fault: tier off
        logger.info(f"[booth-lookup] tier unavailable: {type(e).__name__}: {e}")
        return None

    # Whole-string equality on the folded digits: «377» matches «377», never
    # «37» or «3777». Two companies CAN share a booth; the visitor still
    # needs one answer, so the same boost-then-alphabetical order the lists
    # use picks it, deterministically.
    matched = []
    for c in companies:
        key = re.sub(r"\D", "",
                     (c.get("booth_number") or "").translate(_DIGIT_FOLD))
        if key and key == candidate:
            matched.append(c)
    if not matched:
        return None
    matched.sort(key=lambda c: (0 if c.get("priority_boost") else 1,
                                c.get("title") or ""))
    c = matched[0]

    title = ((c.get("title_en") or "").strip() if lang == "en" else "") \
        or (c.get("title") or "").strip()
    booth = (c.get("booth_number") or "").strip()
    # «غرفه 377: …» plus the company text the public may see — the same
    # `text <> ''` approved description every other company answer serves.
    if lang == "en":
        label = "Booth number"
        text = f"Booth {booth}: {title}\n{(c.get('text') or '').strip()}".rstrip()
    else:
        label = "شماره غرفه"
        text = f"غرفه {booth}: {title}\n{(c.get('text') or '').strip()}".rstrip()
    return {"text": text, "field": "booth_number", "label": label,
            "value": booth, "confidence": 0.95,
            "company_id": c.get("id", ""), "title": title,
            "video_url": (c.get("video_url") or "").strip()}


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
#
# `booth_number` MUST come before `company_phone`: «شماره غرفه» carries
# «شماره», which company_phone's own word set also claims. Same precedence
# problem as the WITHHELD check above, same fix — the more specific field
# wins by being checked first.
#
# «پلاک» is deliberately NOT a trigger here even though it can colloquially
# mean "booth number": it is also the ordinary Persian word for a STREET
# number (an address ends in «... پلاک 12»), and «غرفه» alone already covers
# every booth phrasing that matters («شماره غرفه», «پلاک غرفه»). Adding it
# would risk answering an address question with a booth number instead.
_FIELD_WORDS = (
    ("booth_number", {"غرفه"}),
    ("hall", {"سالن"}),
    ("company_phone", {"تلفن", "شماره", "تماس"}),
    ("website", {"سایت", "وبسایت"}),
    ("province", {"استان", "شهر"}),
    ("address", {"آدرس", "نشانی", "کجاست", "کجاس"}),
    ("activity_field", {"حوزه"}),
)

# A follow-up field question names NO entity and NO topic — only a field
# word plus conversation fillers («کجاس؟», «کدوم غرفه س کدوم سالن», «بابا
# کدوم غرفه س کدوم سالن»). Live failures, Elecomp 2026-09-01: both of
# those got «متوجه منظورت نشدم» and a markdown essay, while the company
# being discussed was one turn up. Anything LEFT over after the field words
# and these fillers is content — a facet («هوش مصنوعی کجاس» is a LIST
# question), an entity, a guide word — and must run the ordinary pipeline.
# GLUE WORDS INSIDE FACET NAMES (live failure, Elecomp 2026-09-01):
# «نمایشگاه امسال شامل چه حوزه های هست؟» matched the facet «فناوری
# اطلاعات شامل سخت‌افزار و نرم‌افزار» on the ordinary word «شامل» — it
# sat in exactly one facet value, so the distinctive-single rule fired and
# the exhibition-wide question got ONE company. These words organize a
# field's DESCRIPTION; no visitor ever means them as a topic.
_FACET_GLUE_WORDS = {"شامل", "انواع", "سایر", "مرکز", "مراکز"}


def answer_category_overview(query: str, lang: str = "fa") -> dict | None:
    """The exhibition's own field overview, or None.

    «نمایشگاه امسال شامل چه حوزه های هست؟» asks about the CATEGORY SET,
    not any one category: the honest answer is the organizer's own fields
    with their company counts, exactly what a visitor scans before picking
    one. None when the query is not an overview question — a query naming a
    SPECIFIC field («حوزه هوش مصنوعی») is the company-list tier's, and
    this must never take it.
    """
    norm = normalize_persian(query or "", expand_synonyms=False)
    tokens = set(norm.split())
    overview_words = {"حوزه", "حوزهها", "حوزههای", "دسته", "دستهبندی",
                      "زمینه", "زمینهها", "زمینههای"}
    if not (tokens & overview_words):
        return None
    question_words = {"چه", "چی", "چیه", "کدام", "لیست", "فهرست", "ها", "های"}
    if not (tokens & question_words):
        return None
    try:
        companies = _load_companies()
    except Exception:  # noqa: BLE001 — degrade, never raise
        return None
    if not companies:
        return None
    # A SPECIFIC field word would make this a list question, not an
    # overview — if the query matches real facets, decline.
    if _select_facets(norm.split(), companies) is not None:
        return None
    counts = {}
    for c in companies:
        for value in _company_facets(c):
            counts[value] = counts.get(value, 0) + 1
    if not counts:
        return None
    top = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:8]
    lines = [f"حوزههای اصلی نمایشگاه امسال:"
             + " (روی هر کدام بزنید تا شرکت‌هایش را ببینید)" if lang == "fa"
             else "This year's main fields:"]
    for name, n in top:
        lines.append(f"• {name} ({n} شرکت)")
    more = len(counts) - len(top)
    if more > 0:
        lines.append(f"و {more} حوزه دیگر.")
    return {"kind": "facet_overview", "text": "\n".join(lines),
            "categories": [name for name, _ in top],
            "confidence": 0.9}


_FIELD_QUESTION_WORDS = {w for _f, words in _FIELD_WORDS for w in words}
_FIELD_FOLLOWUP_FILLERS = {
    "کدوم", "س", "بابا", "خب", "یعنی", "چیه", "هست", "است", "این", "شرکتش",
    "برای", "من", "را", "رو", "از", "تو", "در", "با", "و", "هم", "الان",
}


def bare_field_followup(query: str, lang: str = "fa") -> bool:
    """True when the query is ONLY a field question about the last entity."""
    norm = normalize_persian(query or "", expand_synonyms=False)
    tokens = set(norm.split())
    if not tokens or not (tokens & _FIELD_QUESTION_WORDS):
        return False
    leftover = tokens - _FIELD_QUESTION_WORDS - _FIELD_FOLLOWUP_FILLERS
    return not leftover


_FIELD_LABELS_FA = {
    "booth_number": "شماره غرفه",
    "hall": "سالن",
    "company_phone": "شماره تماس",
    "website": "وب‌سایت",
    "address": "نشانی",
    "address_en": "نشانی",
    "province": "استان",
    "activity_field": "زمینه فعالیت",
}
_FIELD_LABELS_EN = {
    "booth_number": "Booth number",
    "hall": "Hall",
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
