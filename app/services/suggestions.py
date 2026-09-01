"""Deterministic, context-based suggestion chips.

After (almost) every answer the assistant offers 3-4 SHORT tappable
follow-up questions built from the CONVERSATION CONTEXT, so the visitor
never has to think about what to ask next (product rule: every screen
understandable in 3 seconds, every action <= 3 clicks).

This module is deliberately boring: no AI calls, no randomness, no I/O.
The router can call it on every turn at zero cost, including on the
local-only fallback paths where no model is reachable. All strings are
Persian and simple on purpose.

Public API (called by app/routers/chat.py):
    build_suggestions(context: dict, lang: str = "fa") -> list[str]

`context` keys, all optional:
    kind               one of "entry" | "options" | "converse" | "guide_fact"
                       | "unknown"
    entry              the served dataset/company dict (needs a "title")
    options_titles     list of str — the titles of a served options list
    category           str — the entry's category/field label
    hall               str — the hall/booth location, when known
    conversation_kind  "smalltalk" | "self_intro" | "none"

`lang` exists for the router's contract; only Persian strings are defined
today, so a non-"fa" value still gets the Persian set rather than an empty
list — an English visitor is served by the page's own UI translations, and
a missing chip row is worse than a Persian one.
"""

from __future__ import annotations

import itertools

# Hard cap on chips per answer: the UI row is designed for at most four
# one-line questions on a phone.
MAX_SUGGESTIONS = 4

# Every chip must fit one line. Persian questions read fine at this length
# and the frontend chip wraps cleanly at 32 characters.
MAX_CHARS = 32

# The evergreen rotation: useful whatever the conversation was about. A
# module-level tuple (the rule) so admins and tests can see and reuse the
# exact strings. Rotation advances one step per call, so consecutive
# unknown turns offer different follow-ups — deterministic, never random.
EVERGREEN_SUGGESTIONS: tuple[str, ...] = (
    "ساعت بازدید چند است؟",
    "چطور برم نمایشگاه؟",
    "رستوران‌های نزدیک کجان؟",
    "اخبار نمایشگاه؟",
    "شرکت‌های هوش مصنوعی کیا هستن؟",
)

# The assistant proactively ORIENTS a chatty visitor: instead of more
# smalltalk, these three point the conversation at the event itself.
ORIENTATION_SUGGESTIONS: tuple[str, ...] = (
    "ساعت بازدید چند است؟",
    "چطور برم نمایشگاه؟",
    "رستوران‌های نزدیک کجان؟",
)

# The rotation cursor. itertools.count() is deterministic across calls
# (0, 1, 2, ...) and safe to bump from any thread — worst case two
# concurrent requests share one step, which only means identical chips.
_rotation = itertools.count()


def _fit(text: str, limit: int) -> str:
    """Trim `text` to `limit` chars without cutting a word in half.

    Drops whole tokens from the end while the joined text is too long; a
    single token longer than the limit is hard-clipped, because the <= 32
    chars rule is binding for every chip.
    """
    if len(text) <= limit:
        return text
    tokens = text.split()
    while tokens and len(" ".join(tokens)) > limit:
        tokens.pop()
    joined = " ".join(tokens)
    return joined[:limit].rstrip()


def _clip(text: str, limit: int = MAX_CHARS) -> str:
    """Final safety net: never return a chip longer than `limit`."""
    return text if len(text) <= limit else text[:limit].rstrip()


def _short_name(title: str, limit: int = 18) -> str:
    """First token(s) of a title, capped at `limit` chars.

    Two tokens read naturally for Persian company names («شرکت فناوران…»);
    18 keeps «غرفه {name} کجاست؟» inside the 32-char chip budget.
    """
    tokens = (title or "").split()
    if not tokens:
        return ""
    return _fit(" ".join(tokens[:2]), limit)


def _entry_questions(context: dict) -> list[str]:
    """Follow-ups for an answer about one company/entry."""
    entry = context.get("entry")
    if not isinstance(entry, dict):
        return []
    name = _short_name((entry.get("title") or "").strip())
    if not name:
        return []

    questions: list[str] = []
    # Only when we can actually point somewhere — a booth question with no
    # hall behind it would be a dead chip.
    if (context.get("hall") or "").strip():
        questions.append(f"غرفه {name} کجاست؟")
    questions.append(f"وب‌سایت {name}؟")
    category = (context.get("category") or "").strip()
    if category:
        # The template is fixed, so the CATEGORY is what must fit: 32 minus
        # the words around it (see MAX_CHARS).
        questions.append(
            f"شرکت‌های {_fit(category, 9)} دیگه کیا هستن؟")
    return [_clip(q) for q in questions]


def _options_questions(context: dict) -> list[str]:
    """Follow-ups for a served numbered-options list."""
    titles = context.get("options_titles")
    if not isinstance(titles, list) or not titles:
        return []
    questions = ["بیشتر"]
    first = (titles[0] or "").strip() if titles else ""
    if first:
        questions.append(f"{_short_name(first)} رو بگو")
    return [_clip(q) for q in questions]


def build_suggestions(context: dict, lang: str = "fa") -> list[str]:
    """Build 3-4 follow-up chip texts from the conversation context.

    Deterministic and side-effect free apart from advancing the evergreen
    rotation. Priority order (per the spec): entry > options > converse
    orientation; guide_fact and unknown fall through to the evergreen
    rotation, and every branch is topped up with evergreens so the row is
    never shorter than three.
    """
    context = context or {}
    kind = (context.get("kind") or "").strip()

    if kind == "converse" and context.get("conversation_kind") in (
            "smalltalk", "self_intro"):
        # Exactly the three orientation questions — a chatty visitor is
        # pointed at the event, not offered an evergreen mix.
        return list(dict.fromkeys(ORIENTATION_SUGGESTIONS))[:MAX_SUGGESTIONS]

    picked: list[str] = []
    if kind == "entry":
        picked = _entry_questions(context)
    elif kind == "options":
        picked = _options_questions(context)
    # "guide_fact", "unknown", "converse/none" and anything unrecognised:
    # no context-specific questions, the evergreens carry the row.

    # Dedup in priority order before filling, so a context question that
    # happens to equal an evergreen never appears twice.
    seen: set[str] = set()
    unique: list[str] = []
    for q in picked:
        if q and q not in seen:
            seen.add(q)
            unique.append(q)

    # Top up with the rotated evergreens up to the cap.
    start = next(_rotation)
    total = len(EVERGREEN_SUGGESTIONS)
    i = 0
    while len(unique) < MAX_SUGGESTIONS and i < total:
        q = EVERGREEN_SUGGESTIONS[(start + i) % total]
        if q not in seen:
            seen.add(q)
            unique.append(q)
        i += 1

    return unique[:MAX_SUGGESTIONS]
