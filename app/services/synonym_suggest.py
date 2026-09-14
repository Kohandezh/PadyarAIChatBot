"""AI-suggested synonyms, approved by a human before anything is stored.

WHY THIS EXISTS
---------------
Synonyms were managed purely by hand, and the person who knows which words
visitors actually type is not the person editing the table. Two signals this
install already owns say it for them: the curated `questions` corpus (the
vocabulary the knowledge base speaks) and `chat_logs` (the vocabulary the
visitors speak — the low-confidence and unanswered turns are exactly the
queries the synonym table failed to bridge).

THE CONTRACT THE MODEL CANNOT BREAK
-----------------------------------
The model SUGGESTS; the code decides. `suggest_synonyms()` asks the model
ONCE (one prompt, one paid call), parses strict JSON, and every pair passes
`clean_pair()` before the operator ever sees it: both sides non-empty and at
most 40 chars, the two sides must normalize DIFFERENTLY (a pair that differs
only in Arabic ی/ي spelling is spelling advice, not a synonym), the pair must
not already sit in the `synonyms` table, and duplicates collapse. Suggest
never writes anything — the apply route on the synonyms page inserts the
operator's picks through the same write path as the manual add form, so the
normalization reload and the index-version bump stay single-source.

COST CONTROL
------------
One call per COOLDOWN_SECONDS across the whole install (a shared settings
key, so it holds across workers): the button spends a paid-model request,
and a double-click or two operators clicking at once must not spend two.
The cooldown is consumed when the request is ACCEPTED, before the AI call —
a failed call still costs the window, and the honest retry is a minute later.
"""
import json
import time
from collections import Counter

from app.config import logger

# Same shape the synonyms table's own routes enforce (the form caps are the
# product's, not the model's). Repeated here rather than imported so a future
# widening of the table's contract fails THESE tests too.
MAX_SIDE_CHARS = 40
MAX_REASON_CHARS = 200
DEFAULT_LIMIT = 20

# Signal budgets: enough vocabulary for a useful prompt, small enough that
# one call stays well under the proxy timeout on any install.
TERMS_LIMIT = 40
QUESTIONS_SCAN_LIMIT = 2000
WEAK_QUERIES_LIMIT = 40
WEAK_QUERY_CHARS = 80

# The weak-turn definition app/services/conversations.weak_answers already
# owns for the admin panel (confidence < 0.19). `source = 'no_answer'` marks
# the turns that got no answer at all. Same bar here, so "weak" means the
# same thing on both screens.
WEAK_CONFIDENCE = 0.19

COOLDOWN_SECONDS = 60
_COOLDOWN_KEY = "synonym_suggest_last_ts"


class SynonymSuggestUnavailable(Exception):
    """The model could not be reached — nothing was suggested."""


class SuggestCooldownActive(Exception):
    """The paid-model cooldown window is still open."""

    def __init__(self, seconds_left: float):
        self.seconds_left = max(1, int(round(seconds_left)))
        super().__init__(f"cooldown {self.seconds_left}s")


def _norm(text) -> str:
    """Character-level normalization only — the curated vocabulary must not
    be run through synonym expansion (expanding both sides of a comparison
    counts one mention twice, the bug normalizer.py's `expand_synonyms=False`
    exists for)."""
    from app.utils.normalizer import normalize_persian
    return normalize_persian(str(text or ""), expand_synonyms=False)


def clean_pair(word, suggestion):
    """One (word, suggestion) the synonyms table may actually hold, or None.

    Single source for the shape rules: `suggest_synonyms()` filters the
    model's answer through it, and the apply route validates the operator's
    payload through the same function — no second copy to drift.
    """
    if not isinstance(word, str) or not isinstance(suggestion, str):
        return None
    word = " ".join(word.split())
    suggestion = " ".join(suggestion.split())
    if not word or not suggestion:
        return None
    if len(word) > MAX_SIDE_CHARS or len(suggestion) > MAX_SIDE_CHARS:
        return None
    if _norm(word) == _norm(suggestion):
        return None
    return (word, suggestion)


def _existing_pairs() -> set:
    """Every (source, target) already stored, both directions — a suggestion
    that merely reverses a stored row adds nothing the expansion pass does
    not already do."""
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    try:
        rows = conn.execute("SELECT source, target FROM synonyms").fetchall()
    finally:
        conn.close()
    pairs = {(r["source"], r["target"]) for r in rows}
    return pairs | {(t, s) for s, t in pairs}


def _frequent_terms(limit: int = TERMS_LIMIT) -> list:
    """The questions corpus's own vocabulary, most repeated first.

    These are the words the knowledge base speaks — the `suggestion` side a
    good synonym points at. Persian-only stopword lists would be a second
    taxonomy to maintain; length + digit filters plus the model's own
    judgment are enough for a suggestion list a human approves anyway.
    """
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    try:
        rows = conn.execute(
            "SELECT question FROM questions ORDER BY id DESC LIMIT ?",
            (QUESTIONS_SCAN_LIMIT,)).fetchall()
    finally:
        conn.close()
    counts = Counter()
    for r in rows:
        for token in _norm(r["question"]).split():
            if len(token) < 3 or token.isdigit():
                continue
            counts[token] += 1
    return [term for term, _ in counts.most_common(limit)]


def _weak_queries(limit: int = WEAK_QUERIES_LIMIT) -> list:
    """Real visitor queries the bot answered weakly or not at all.

    Same weak definition as conversations.weak_answers, read from chat_logs
    because that is where the VISITOR'S wording lives next to its confidence
    (the messages store pairs them across two rows). Deduped — a kiosk asks
    the same failed question many times in a row.
    """
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    try:
        rows = conn.execute(
            "SELECT query FROM chat_logs"
            " WHERE COALESCE(query, '') <> ''"
            "   AND ((confidence IS NOT NULL AND confidence < ?)"
            "        OR source = 'no_answer')"
            " ORDER BY id DESC LIMIT ?",
            (WEAK_CONFIDENCE, limit * 4)).fetchall()
    finally:
        conn.close()
    seen, out = set(), []
    for r in rows:
        query = " ".join(str(r["query"] or "").split())[:WEAK_QUERY_CHARS]
        if query and query not in seen:
            seen.add(query)
            out.append(query)
            if len(out) >= limit:
                break
    return out


def _prompt(terms: list, weak_queries: list, limit: int) -> str:
    terms_text = "\n".join(f"- {t}" for t in terms) or "—"
    weak_text = "\n".join(f"- {q}" for q in weak_queries) or "—"
    return (
        "تو دستیار مدیریت مترادف‌های یک چت‌بات فارسی هستی. کاربران واژه‌هایی"
        " می‌نویسند که با واژگان دانش‌نامه فرق دارد؛ مترادف درست همین فاصله"
        " را پر می‌کند.\n"
        "در پایین دو فهرست سیگنال آمده است: واژه‌های پرتکرار پرسش‌های"
        " دانش‌نامه (واژگان خود دانش‌نامه) و پرسش‌های واقعی کاربرانی که"
        " چت‌بات به آن‌ها پاسخ مطمئنی نداده است.\n"
        "قواعد:\n"
        "۱. در هر پیشنهاد، «word» واژه‌ای است که کاربر می‌نویسد و"
        " «suggestion» معادل همان در واژگان دانش‌نامه است.\n"
        f"۲. هر دو سمت غیرخالی و حداکثر {MAX_SIDE_CHARS} نویسه باشند. اگر دو"
        " واژه فقط در شکل نوشتار فرق کنند (مثل ی و ي عربی) پیشنهاد نده.\n"
        "۳. «reason» یک خط دلیل کوتاه فارسی است.\n"
        f"۴. حداکثر {limit} پیشنهاد بده و فقط جایی که واقعاً به جستجو کمک"
        " می‌کند.\n"
        "۵. فقط JSON برگردان با همین شکل:\n"
        '{"suggestions": [{"word": "…", "suggestion": "…", "reason": "…"}]}\n\n'
        f"واژه‌های پرتکرار پرسش‌های دانش‌نامه:\n{terms_text}\n\n"
        f"پرسش‌های بی‌پاسخ یا کم‌اطمینان کاربران:\n{weak_text}"
    )


def _consume_cooldown(now=None) -> None:
    """One paid-model call per COOLDOWN_SECONDS, shared across workers.

    The settings table is the shared clock every worker already reads, so the
    window holds process-wide. A storage fault fails OPEN on purpose: the
    cooldown protects spend, it is not a security gate, and a broken settings
    read must not take the feature down with it.
    """
    from app.db.queries import get_setting, set_setting
    now = time.time() if now is None else float(now)
    try:
        last = float(get_setting(_COOLDOWN_KEY, "0", fresh=True) or 0)
    except (TypeError, ValueError):
        last = 0.0
    except Exception as e:  # noqa: BLE001 — see docstring: fail open
        logger.info("[synonym_suggest] cooldown read skipped: %s: %s",
                    type(e).__name__, e)
        return
    if now - last < COOLDOWN_SECONDS:
        raise SuggestCooldownActive(COOLDOWN_SECONDS - (now - last))
    try:
        set_setting(_COOLDOWN_KEY, str(now))
    except Exception as e:  # noqa: BLE001 — same fail-open rule
        logger.info("[synonym_suggest] cooldown write skipped: %s: %s",
                    type(e).__name__, e)


async def suggest_synonyms(actor: str = "", limit: int = DEFAULT_LIMIT) -> list:
    """One button press: ask the model once, return validated suggestions.

    Returns `[{"word", "suggestion", "reason"}, …]` — at most `limit`, none
    of them stored. Raises SuggestCooldownActive inside the window and
    SynonymSuggestUnavailable when the model cannot be reached; both are the
    router's to translate, never the visitor's to see.
    """
    from app.services import applog

    _consume_cooldown()
    limit = max(1, min(int(limit or DEFAULT_LIMIT), DEFAULT_LIMIT))
    terms = _frequent_terms()
    weak_queries = _weak_queries()

    from app.services.ai.wrapper import padyar_ai
    from app.services.ai.request import AIMessage
    from app.services.ai.errors import AIError
    try:
        resp = await padyar_ai.generate(
            [AIMessage(role="user",
                       content="چه مترادف‌هایی به جدول اضافه شود؟")],
            system_prompt=_prompt(terms, weak_queries, limit),
            # The routed chat task — no new task name means no routing-table
            # migration and no admin change for one button.
            task="chat",
            max_output_tokens=1500,
            temperature=0.0,
            response_format="json_object",
            timeout_s=45.0,
        )
    except AIError as e:
        applog.error("content", "synonyms.suggest.ai_unavailable",
                     "گرفتن پیشنهاد مترادف لغو شد — هوش مصنوعی در دسترس نیست",
                     actor=actor or None, outcome="unavailable",
                     error_code=e.code)
        raise SynonymSuggestUnavailable(
            f"هوش مصنوعی در دسترس نیست ({e.code}).") from e

    try:
        data = json.loads(resp.content)
    except (ValueError, TypeError):
        logger.info("[synonym_suggest] model answer was not JSON; "
                    "returning no suggestions")
        return []
    raw = data.get("suggestions") if isinstance(data, dict) else None
    if not isinstance(raw, list):
        return []

    existing = _existing_pairs()
    out, seen = [], set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        pair = clean_pair(item.get("word"), item.get("suggestion"))
        if pair is None or pair in existing:
            continue
        key = (_norm(pair[0]), _norm(pair[1]))
        if key in seen:
            continue
        seen.add(key)
        reason = " ".join(str(item.get("reason") or "").split())[:MAX_REASON_CHARS]
        out.append({"word": pair[0], "suggestion": pair[1], "reason": reason})
        if len(out) >= limit:
            break

    applog.info("content", "synonyms.suggest.run",
                "پیشنهاد مترادف‌ها گرفته شد",
                actor=actor or None, outcome="ok",
                metadata={"suggested": len(out), "signals": len(terms),
                          "weak_queries": len(weak_queries)})
    return out
