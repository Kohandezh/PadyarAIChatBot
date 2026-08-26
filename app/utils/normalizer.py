import re
from typing import List

from app.config import logger


# --- Active synonyms state ---
# One entry per (source, target) row, in table order. A source appears as many
# times as it has synonyms.
active_synonyms: List[tuple] = []

# Cache for _expansions(): (the rows it was built from, the result).
_expansions_cache: tuple = ((), ())
# Cache for _expansion_pattern(): keyed on the built expansions so a synonym
# edit invalidates it together with _expansions_cache.
_expansion_pattern_cache: tuple = ((), None)


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


def _expansions():
    """One replacement per source, merging all of that source's targets.

    Replacing row by row loses synonyms: the first target consumes the source,
    so the second row for the same word never fires and which one wins depends
    on table order. Merging is the fix.

    The replacement carries ONLY words the source does not already contain.
    Repeating the source inside its own replacement is not neutral: it raises
    the source's term frequency for TF-IDF/BM25 without adding meaning, and on
    the embedding side a word multiplied several times pushed the whole query
    outside the model's region — measured as dense=0.000 for
    «هزینه غرفه چقدر است؟» on the 2026-08-26 diagnostic run. A later target
    contributes only words an earlier one did not already supply; the source
    itself is present in the query already, so it contributes nothing either.

    Cached on the rows themselves. `active_synonyms` is rebound wholesale by
    `load_synonyms_from_db` and by tests, so comparing it is enough.
    """
    global _expansions_cache
    rows = tuple(active_synonyms)
    if _expansions_cache[0] == rows:
        return _expansions_cache[1]

    grouped = {}
    for src, dst in rows:
        grouped.setdefault(src, []).append(dst or "")

    built = []
    for src, targets in grouped.items():
        # The source stays exactly once (replace() consumes it, so the first
        # target must put it back for exact-token matches in BM25/title
        # overlap), and every synonym word appears at most once across all
        # targets. Doubling either is what pushed queries out of the
        # embedding model's region (dense=0.000, 2026-08-26 diagnostic).
        seen = set(src.split())
        parts = list(seen)
        for dst in targets:
            fresh = [w for w in dst.split() if w not in seen]
            seen.update(fresh)
            parts.extend(fresh)
        built.append((src, " ".join(parts)))

    built = tuple(built)
    _expansions_cache = (rows, built)
    return built


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


def _expansion_pattern(built: tuple):
    """One compiled alternation over all sources, longest-first, cached on
    the expansion table itself. `(?<!\S)`/`(?!\S)` delimit a source as a whole
    whitespace-bounded token run — the Persian equivalent of \b, which the
    regex engine does not apply to non-ASCII word characters.
    """
    global _expansion_pattern_cache
    if _expansion_pattern_cache[0] != built:
        keys = sorted((src for src, _ in built if src), key=len, reverse=True)
        pattern = re.compile(
            "(?<!\\S)(" + "|".join(re.escape(k) for k in keys) + ")(?!\\S)"
        ) if keys else None
        _expansion_pattern_cache = (built, pattern)
    return _expansion_pattern_cache[1]


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

    # جایگزینی مترادف‌ها از دیتابیس — در «یک» پاس.
    #
    # The old loop (`for src, dst: text = text.replace(src, dst)`) cascaded:
    # «هزینه» → «قیمت تعرفه…», then the «قیمت» row fired on the word the
    # FIRST replacement just inserted and re-inserted «هزینه», and «تعرفه»
    # fired again after that. One typed word could end up four times in the
    # expanded query (measured on the live synonym table, 2026-08-26). A
    # single-pass alternation only ever matches tokens of the ORIGINAL text,
    # so every source expands exactly once and nothing re-fires.
    built = _expansions()
    if built:
        pattern = _expansion_pattern(built)
        if pattern is not None:
            mapping = dict(built)
            text = pattern.sub(lambda m: mapping[m.group(0)], text)

    # Replacement text may itself contain a ZWNJ (the synonym rows are authored
    # by hand), which would leave the "normalised" output un-normalised. Fold
    # once more so every caller sees the same character set.
    return text.replace('‌', ' ').replace('‍', ' ')
