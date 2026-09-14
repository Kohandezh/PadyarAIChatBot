"""AI-suggested question paraphrases for one dataset entry, human-approved.

WHY THIS EXISTS
---------------
A knowledge entry is matched by its curated questions (Tier 0/1 of the chat
pipeline), but visitors phrase the same need many ways and nobody types ten
paraphrases by hand. The result is an entry that answers a question nobody
asks in exactly those words. This module is one button in the entry edit
modal: ask the model — through the Padyar AI Wrapper, the only AI path — for
question variants grounded in that entry's own title, text and existing
questions, show them as checkboxes, and insert only the ones the admin
picks.

THE CONTRACT THE MODEL CANNOT BREAK
----------------------------------
The model SUGGESTS; the code decides. Nothing is saved here: suggest returns
a validated list, and the apply endpoint in app/routers/dataset.py inserts
through the same INSERT statement as the single question-create endpoint.
Every suggestion must pass, in order:
  * a string, whitespace-collapsed, 5..120 chars;
  * Persian — English survives only when the entry already carries an
    English question (a Persian entry suggesting English questions would
    poison the Persian question index);
  * normalized-unique against the entry's existing questions AND against
    the suggestions already accepted in this batch (dedupe);
  * the list is capped at `count`.
A suggestion that fails is dropped, never repaired.
"""
import json
import re

from app.config import logger

MIN_CHARS = 5
MAX_CHARS = 120
DEFAULT_COUNT = 10
# One apply batch accepts at most this many questions (bounded writes; the
# suggest step never returns more than DEFAULT_COUNT anyway).
MAX_APPLY = 20
# The prompt carries the entry's own text, capped like company_autofill: the
# model paraphrases one entry, it does not need the whole knowledge base.
TEXT_LIMIT = 900

_ASCII_LETTER = re.compile(r"[A-Za-z]")


class QuestionAssistUnavailable(Exception):
    """The model could not be reached — nothing was suggested."""


def _has_persian(s: str) -> bool:
    return any("\u0600" <= ch <= "\u06FF" for ch in s)


def _looks_english(s: str) -> bool:
    return not _has_persian(s) and bool(_ASCII_LETTER.search(s))


def _load_entry(entry_id: str):
    """(entry, existing_questions); entry is None when the id is unknown."""
    from app.db.connection import get_db_connection
    conn = get_db_connection()
    try:
        row = conn.execute(
            "SELECT id, title, text FROM dataset WHERE id = ?",
            (entry_id,)).fetchone()
        if row is None:
            return None, []
        qs = conn.execute(
            "SELECT question FROM questions WHERE dataset_id = ? ORDER BY id",
            (entry_id,)).fetchall()
        return dict(row), [q["question"] for q in qs]
    finally:
        conn.close()


def _prompt(entry: dict, existing: list, count: int) -> str:
    existing_text = "\n".join(f"- {q}" for q in existing) or "—"
    return (
        "تو دستیار نگهداری دانش یک چت‌بات هستی. برای یک مورد دانش،"
        " سوال‌هایی که یک بازدیدکننده واقعاً ممکن است بپرسد پیشنهاد می‌کنی.\n"
        "قواعد:\n"
        "۱. هر سوال باید با محتوای همین مورد پاسخ داده شود؛ چیزی که در"
        " متن نیست را از خودت نساز.\n"
        "۲. سوال تکراری یا همان با جمله‌بندی دیگر ننویس؛ پیشنهادها باید"
        " با سوال‌های موجود تفاوت واقعی داشته باشند.\n"
        "۳. هر سوال بین ۵ تا ۱۲۰ نویسه باشد.\n"
        "۴. به همان زبانی بنویس که سوال‌های موجود هستند.\n"
        f"۵. فقط JSON برگردان با این شکل:\n"
        f'{{"questions": ["سوال اول", "سوال دوم"]}}\n'
        f"{count} پیشنهاد بده.\n\n"
        f"عنوان مورد: {entry.get('title') or ''}\n"
        f"متن پاسخ:\n{(entry.get('text') or '')[:TEXT_LIMIT]}\n"
        f"سوال‌های موجود:\n{existing_text}"
    )


async def _ask_model(entry: dict, existing: list, count: int):
    """Ask the model for paraphrase questions. (raw list, usage) — raises
    QuestionAssistUnavailable when the AI stack is down, never for a merely
    unparsable answer (that yields an empty list, an honest «nothing new»)."""
    from app.services.ai.wrapper import padyar_ai
    from app.services.ai.request import AIMessage, FINISH_LENGTH
    from app.services.ai.errors import AIError

    try:
        resp = await padyar_ai.generate(
            [AIMessage(role="user",
                       content=f"برای مورد «{entry.get('title') or ''}»"
                               " چه سوال‌هایی پیشنهاد می‌کنی؟")],
            system_prompt=_prompt(entry, existing, count),
            # The routed chat task: no new task name means no routing-table
            # migration and no admin change for one button (company_autofill
            # precedent).
            task="chat",
            max_output_tokens=800,
            temperature=0.3,
            response_format="json_object",
            timeout_s=45.0,
        )
    except AIError as e:
        raise QuestionAssistUnavailable(
            f"هوش مصنوعی در دسترس نیست ({e.code}).") from e

    if getattr(resp, "finish_reason", "") == FINISH_LENGTH:
        # A truncated JSON array is not a suggestion list; a half-visible
        # question is worse than none.
        return [], resp
    try:
        data = json.loads(resp.content)
    except (ValueError, TypeError):
        return [], resp
    raw = data.get("questions") if isinstance(data, dict) else None
    if not isinstance(raw, list):
        raw = []
    return raw, resp


def _clean_suggestions(raw: list, existing: list, count: int) -> list:
    """Keep only suggestions that pass every rule, capped at `count`."""
    from app.utils.normalizer import normalize_persian
    seen = {normalize_persian(q or "", expand_synonyms=False) for q in existing}
    # Without synonym expansion: expansion is a retrieval aid, not an identity
    # — two questions that differ only by a synonym ARE different questions.
    english_ok = any(_looks_english(str(q or "")) for q in existing)
    out = []
    for item in raw:
        if not isinstance(item, str):
            continue
        s = " ".join(item.split())
        if not MIN_CHARS <= len(s) <= MAX_CHARS:
            continue
        if not (_has_persian(s) or (english_ok and _looks_english(s))):
            continue
        key = normalize_persian(s, expand_synonyms=False)
        if key in seen:
            continue
        seen.add(key)
        out.append({"question": s, "is_new": True})
        if len(out) == count:
            break
    return out


async def suggest_questions(entry_id: str, count: int = DEFAULT_COUNT) -> list:
    """Validated question suggestions for one entry. Nothing is written.

    Raises LookupError for an unknown entry_id and
    QuestionAssistUnavailable when the AI stack cannot be reached.
    """
    count = max(1, min(int(count or DEFAULT_COUNT), MAX_APPLY))
    entry, existing = _load_entry(entry_id)
    if entry is None:
        raise LookupError(entry_id)
    raw, _resp = await _ask_model(entry, existing, count)
    suggestions = _clean_suggestions(raw, existing, count)
    logger.info("[question_assist] entry=%s raw=%d kept=%d",
                entry_id, len(raw), len(suggestions))
    return suggestions
