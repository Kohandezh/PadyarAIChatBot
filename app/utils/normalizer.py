import re
from typing import List

from app.config import logger


# --- Active synonyms state ---
active_synonyms: List[tuple] = []


def load_synonyms_from_db():
    """Load synonyms from the database into memory."""
    global active_synonyms
    try:
        from app.db.connection import get_db_connection
        conn = get_db_connection()
        rows = conn.execute('SELECT source, target FROM synonyms').fetchall()
        active_synonyms = [(row['source'], row['target']) for row in rows]
        conn.close()
    except Exception as e:
        logger.error(f"Could not load synonyms: {e}")
        active_synonyms = []


# Common Persian openers users prepend out of politeness. These are stripped
# from the *user query* before matching, so "سلام، هزینه فمتو؟" is matched as
# "هزینه فمتو؟" instead of being hijacked by a greeting entry. NEVER applied
# when building the search index — only to the incoming query.
# Longer phrases must come before the shorter ones they contain (e.g. before "سلام").
_GREETING_PREFIXES = (
    "سلام علیکم", "سلام عليكم", "با سلام و احترام", "سلام و درود", "با سلام",
    "سلام وقت بخیر", "سلام خسته نباشید", "خسته نباشید", "وقت بخیر",
    "صبح بخیر", "ظهر بخیر", "عصر بخیر", "شب بخیر", "روز بخیر",
    "درود", "سلام", "های",
)
_GREETING_SEPS = " ،,.!؟:؛\t\n"


def strip_leading_greeting(text: str):
    """Remove a leading greeting from a user message.

    Returns ``(core_text, was_only_greeting)``:
    - If the message is *essentially just* a greeting, ``core_text`` is the
      original text and ``was_only_greeting`` is True (caller should let it
      match the intro, not treat it as a content question).
    - Otherwise ``core_text`` is the message with the greeting removed.
    """
    if not text:
        return text, False
    core = text.strip()
    changed = True
    while changed:
        changed = False
        for g in _GREETING_PREFIXES:
            after = core[len(g):len(g) + 1]
            if core == g or (core.startswith(g) and after in _GREETING_SEPS):
                core = core[len(g):].lstrip(_GREETING_SEPS)
                changed = True
                break
    was_only = core.strip() == ""
    return (text if was_only else core.strip()), was_only


def normalize_persian(text: str, expand_synonyms: bool = True) -> str:
    """نرمالایز پیشرفته متن فارسی برای بهبود جستجو

    ``expand_synonyms=False`` skips the DB synonym pass and returns only the
    character-level normalisation. Callers that match a *curated vocabulary*
    against user text need this: expanding both sides turns one term into a
    multi-word blob and counts a single mention twice. The default stays True,
    so the retrieval pipeline is unchanged.
    """
    # تبدیل کاراکترهای عربی به فارسی
    text = text.replace('ي', 'ی').replace('ك', 'ک').replace('ى', 'ی').replace('ھ', 'ه')

    # حذف نیم‌فاصله‌های اضافه و نرمال‌سازی فاصله‌ها
    text = text.replace('‌', ' ').replace('‍', ' ')  # نیم‌فاصله و پیونددهنده

    # حذف علائم نگارشی غیرضروری (حفظ حروف و اعداد فارسی/انگلیسی)
    text = re.sub(r'[^\w\sآ-یا-یئؤإأءًٌٍَُِّ]', ' ', text)

    # نرمال‌سازی فاصله‌های چندگانه
    text = re.sub(r'\s+', ' ', text).strip()

    # تبدیل به حروف کوچک (برای بخش انگلیسی متن)
    text = text.lower()

    if not expand_synonyms:
        return text

    # جایگزینی مترادف‌های پزشکی از دیتابیس
    for src, dst in active_synonyms:
        text = text.replace(src, dst)

    # Replacement text may itself contain a ZWNJ (the synonym rows are authored
    # by hand), which would leave the "normalised" output un-normalised. Fold
    # once more so every caller sees the same character set.
    return text.replace('‌', ' ').replace('‍', ' ')
