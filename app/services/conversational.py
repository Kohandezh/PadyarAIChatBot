"""The conversational gate: small talk, self-introductions, gibberish.

WHY THIS MODULE EXISTS (Elecomp, 2026-08-31, two live failures):

1. A visitor said «سلام چطوری؟ اسم من سینا هست اسم تو چی هست؟». The
   named-entity anchor saw «سینا», matched the COMPANY «گسترش فناوری‌های
   پیشرفته و هوشمند سینا», and served its profile. The visitor was
   introducing THEMSELF. A person's own name must never trigger the entity
   anchor — so the router nulls every local tier for a self-introduction and
   lets the model, which reads the whole sentence, answer.

2. A visitor said «بگو» after the bot had offered something and got the
   welcome introduction again. «بگو» is an AFFIRMATIVE to the last offer,
   and the router replays the offer instead of re-answering from scratch.

Everything here sits behind the `chat_conversational_tier` settings row
("0" restores the previous behaviour with no deploy), and NOTHING in this
module talks to the network: classification is pattern matching over
normalized text, so the gate costs microseconds and cannot take the chat
down.
"""
import json
import re
import time

from app.config import logger, PICK_WINDOW_MINUTES
from app.utils.normalizer import normalize_persian, strip_leading_greeting


# Small talk the model should answer, not the retrieval tiers. Stored RAW
# and normalized once at import: comparing normalized-to-normalized is what
# makes the match tolerant to Arabic ی/ک spellings and stray punctuation
# (the normalizer folds both away).
SMALLTALK_PHRASES = (
    "چطوری", "چطورم", "خوبی", "حالت چطوره", "چه حالی", "ممنون", "مرسی",
    "دستت درد نکنه", "خداحافظ", "بای", "فعلا",
    "تو کی هستی", "تو چی هستی", "اسمت چیه", "اسم تو چیه",
    "چه کمکی می‌تونی بکنی", "چه کارهایی می‌تونی بکنی", "قابلیت‌هات چیه",
)

_SMALLTALK_NORM = frozenset(
    normalize_persian(p, expand_synonyms=False) for p in SMALLTALK_PHRASES)

# «اسمت چی هست» / «اسم تو چی هست» are the listed «اسمت چیه» / «اسم تو چیه»
# with the verb spelled out. The distinction matters: the Elecomp incident
# message ENDS «اسم تو چی هست؟», and if that tail counted as content the
# self-introduction rule would classify the whole message "none", the anchor
# would fire on the name, and the exact bug this module exists to close
# reopens on a rephrasing of its own trigger.
_NAME_QUESTION = re.compile(r"^(?:اسمت|اسم تو) (?:چیه|چی هست)$")


def _is_smalltalk(text: str) -> bool:
    """True when the whole (already normalized) text is one small-talk
    phrase — a sentence about the CONVERSATION, not about the exhibition."""
    if not text:
        return False
    return text in _SMALLTALK_NORM or bool(_NAME_QUESTION.match(text))


def _strip_leading_smalltalk(text: str) -> str:
    """Drop ONE leading small-talk phrase («سلام چطوری؟ اسم من …» reaches
    here as «چطوری اسم من …» once the greeting is gone). Longest-first so a
    phrase that prefixes another cannot shadow it."""
    for phrase in sorted(_SMALLTALK_NORM, key=len, reverse=True):
        if text == phrase:
            return ""
        if text.startswith(phrase + " "):
            return text[len(phrase):].strip()
    return text


# «اسم من X هست/هستم», «اسمم X هست/هستم», «من X هستم», «من X ام». The name
# is 1-4 tokens and the verb must be a token of its own. Matching happens on
# NORMALIZED text (punctuation and ZWNJ folded to spaces), so «اسمم سینا
# هست.» matches clean, and the optional trailing group is what lets the
# CRITICAL RULE below inspect what was said AFTER the introduction.
_SELF_INTRO_PATTERNS = tuple(re.compile(p) for p in (
    r"^اسم من (.+?) (?:هست|هستم)(?: (.*))?$",
    r"^اسمم (.+?) (?:هست|هستم)(?: (.*))?$",
    r"^من (.+?) (?:هستم|ام)(?: (.*))?$",
))


def classify_conversational(query: str) -> tuple:
    """(kind, visitor_name); kind ∈ {"none", "smalltalk", "self_intro"}.

    THE RULE THAT DECIDES THE HARD CASE: a self-introduction counts as one
    ONLY when nothing else is being said. Strip the greeting, strip the
    intro phrase itself, and what is left must be EMPTY or small talk.
    «اسم من سینا هست، شرکت سینا کجاست؟» is a question that happens to open
    with an introduction — the introduction is CONTEXT for the model, and
    classifying it self_intro would null the local tiers and bury the very
    question the visitor asked. Hence "none" there.

    A bare greeting is "none" on purpose: the intro dataset entry already
    answers it, and that path predates this module.
    """
    core, only_greeting = strip_leading_greeting(query or "")
    if only_greeting or not core.strip():
        return ("none", None)
    text = normalize_persian(core, expand_synonyms=False)
    if _is_smalltalk(text):
        return ("smalltalk", None)
    body = _strip_leading_smalltalk(text)
    for pattern in _SELF_INTRO_PATTERNS:
        m = pattern.match(body)
        if not m:
            continue
        name = (m.group(1) or "").strip()
        rest = (m.group(2) or "").strip()
        tokens = name.split()
        if not 1 <= len(tokens) <= 4:
            continue
        if rest and not _is_smalltalk(rest):
            return ("none", None)
        return ("self_intro", " ".join(tokens))
    return ("none", None)


def corpus_known_tokens() -> set:
    """The retrieval layer's corpus vocabulary, read at CALL time.

    search.load_dataset_internal REBINDS this set on every reindex, so an
    import-time copy would go stale the moment an admin edits the dataset —
    the exact drift the cross-worker version stamp exists to prevent. Reading
    through the module attribute is the one form that always sees the
    current build, and it reaches for a private name on purpose: duplicating
    the vocabulary builder here would give the gibberish check a second,
    quietly divergent definition of "known".
    """
    from app.services import search
    return search._corpus_vocab


def is_gibberish(query: str, known_tokens: set) -> bool:
    """True when the message is too short and too unknown to be a question.

    THE UNKNOWN-GATE'S BLIND SPOT: unknown_salient_tokens ignores tokens
    under 4 characters (they are almost always function words), so «ثطسث» is
    salient but «ب س» is invisible to it — both then walk the whole ladder
    and end at the model, which bills a call to say nothing useful. A
    message of at most two tokens, every one of them at most 4 characters
    and absent from the corpus, is not a question in any language this
    install speaks: answer locally and stop.

    An EMPTY vocabulary returns False: with no corpus loaded nothing can
    testify that a token is unknown, and "gibberish" would then fire on
    every short message of a freshly booted install.
    """
    if not known_tokens:
        return False
    tokens = normalize_persian(query or "", expand_synonyms=False).split()
    if not tokens or len(tokens) > 2:
        return False
    return all(len(t) <= 4 and t not in known_tokens for t in tokens)


# ── The pending proposal ──────────────────────────────────────────────────
#
# WHERE THIS LIVES AND WHY. The pick tier's offer state is a column on
# chat_logs — append-only telemetry, written once per answered turn and
# never updated, which is exactly wrong for a proposal: take_proposal must
# CONSUME the value so a second «بله» does not replay the same offer twice.
# The settings key-value table gives the same properties that put offer
# state in the DB in the first place (shared across workers, survives a
# restart) plus a delete, with one namespaced row per conversation:
# `chat_proposal:{conversation_id}`. A settings row is also the one store
# this feature is allowed to add without a migration.

_PROPOSAL_KEY_PREFIX = "chat_proposal:"
_PROPOSAL_QUERY_MAX = 300


def _proposal_key(conversation_id: str) -> str:
    return f"{_PROPOSAL_KEY_PREFIX}{(conversation_id or '')[:64]}"


def store_proposal(conversation_id: str, query: str) -> None:
    """Remember the query whose answer PROPOSED something (a tier-2 decision
    carrying `"proposal": True` — the model ended by offering more). The
    visitor's «بگو» then replays THIS query through the pipeline.

    A storage fault must never cost the answer being served around it:
    swallowed, like every write on the visitor's hot path.
    """
    q = (query or "").strip()
    if not conversation_id or not q:
        return
    payload = json.dumps({"q": q[:_PROPOSAL_QUERY_MAX], "ts": time.time()},
                         ensure_ascii=False)
    try:
        from app.db.queries import set_setting
        set_setting(_proposal_key(conversation_id), payload)
    except Exception as e:  # noqa: BLE001 — see the docstring
        logger.error("[conversational] store_proposal failed: %s: %s",
                     type(e).__name__, e)


def take_proposal(conversation_id: str) -> "str | None":
    """The stored proposal query, consumed. None when there is none.

    CONSUMED EVEN WHEN EXPIRED. The PICK_WINDOW_MINUTES bound exists because
    a booth kiosk is one browser shared by strangers, and a «بله» typed long
    after the offer must not replay another visitor's conversation. Leaving
    an expired row behind would resurrect it on the NEXT affirmative, so the
    row goes no matter what the verdict is — same lazy-cleanup-on-read
    pattern as the stale conversation summary.
    """
    if not conversation_id:
        return None
    key = _proposal_key(conversation_id)
    try:
        from app.db.queries import get_setting
        raw = get_setting(key, "", fresh=True)
    except Exception as e:  # noqa: BLE001 — no proposal is a valid answer
        logger.info("[conversational] take_proposal unavailable: %s: %s",
                    type(e).__name__, e)
        return None
    _delete_proposal(key)
    if not raw:
        return None
    try:
        data = json.loads(raw)
        query = str(data.get("q") or "").strip()
        stored_at = float(data.get("ts") or 0.0)
    except (TypeError, ValueError):
        return None
    if not query or stored_at <= 0:
        return None
    if time.time() - stored_at > max(1, PICK_WINDOW_MINUTES) * 60:
        return None
    return query


def _delete_proposal(key: str) -> None:
    from app.db.connection import get_db_connection
    try:
        conn = get_db_connection()
        try:
            conn.execute("DELETE FROM settings WHERE key = ?", (key,))
            conn.commit()
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001 — a leftover row is harmless
        logger.error("[conversational] proposal cleanup failed: %s: %s",
                     type(e).__name__, e)
