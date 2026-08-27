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
            "SELECT d.id, d.title, d.title_en, d.text, p.activity_field"
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
        hay = set(normalize_persian(
            f"{c.get('activity_field') or ''} {c.get('title') or ''} {c.get('text') or ''}",
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
