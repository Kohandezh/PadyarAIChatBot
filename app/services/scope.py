"""What this assistant is about, and what it says when it cannot answer.

WHY THIS TINY MODULE EXISTS. The out-of-scope refusal used to live in exactly
one place: a Persian sentence inside the system prompt, as an INSTRUCTION to
the model. So when the code itself had to refuse — a generated answer that
failed the grounding check, for instance — it had nothing to say, and the only
options were silence or a 503.

These functions turn that wording into VALUES the code can emit, read from the
same settings the prompt is built from. The string the model is told to say and
the string the code says are then the same string, and a deployment in a
different category changes a setting instead of a Python literal.

THREE DIFFERENT SENTENCES, AND THEY MUST STAY DIFFERENT
-------------------------------------------------------
refusal_text()   "your question is not about this exhibition" — the visitor
                 asked something out of scope, or a generated answer failed
                 the grounding check.
no_answer_text() "I looked and I have nothing for you" — the question is
                 perfectly in scope, we simply found no record that answers
                 it. This is NOT a 503: nothing is unavailable and telling a
                 visitor the service is down when it is up is a lie the app
                 used to tell (app/routers/chat.py).
hedge_text()     appended to an answer we ARE serving but are not sure about.
                 It invites the correction instead of pretending confidence.

A fourth message, "the AI service is unavailable", stays in the frontend
(static/chat/core.js, on a real 503) and is deliberately not here: that one is
about the machine, not about the question.
"""

# Defaults reproduce today's shipped wording, with {org} filled in by the
# caller-facing function below. An install that never touched these settings
# refuses in exactly the words it refuses in now.
DEFAULT_REFUSAL_FA = "من فقط می‌توانم درباره {org} و خدمات آن کمک کنم."
DEFAULT_REFUSAL_EN = "I can only help with {org} information and services."

# What the assistant is FOR, in one short phrase. Used inside the system
# prompt's fixed sections so a new customer does not need a code change.
DEFAULT_DOMAIN_FA = "نمایشگاه اینوتکس"
DEFAULT_DOMAIN_EN = "the INOTEX exhibition"


def _customer_text(key_fa: str, key_en: str, default_fa: str, default_en: str,
                   lang: str) -> str:
    """One admin-editable sentence, in the visitor's language, {org} filled in.

    str.replace, never .format(): admin-entered text containing a stray brace
    must not raise. Same rule as build_system_prompt().
    """
    from app.db.queries import get_setting
    from app.services.openai import DEFAULT_ASSISTANT_ORG

    org = get_setting("assistant_org", DEFAULT_ASSISTANT_ORG) or DEFAULT_ASSISTANT_ORG
    if lang == "en":
        text = get_setting(key_en, "") or default_en
    else:
        text = get_setting(key_fa, "") or default_fa
    return text.replace("{org}", org)


def refusal_text(lang: str = "fa") -> str:
    """The sentence a visitor sees when their question is out of scope."""
    return _customer_text("refusal_text_fa", "refusal_text_en",
                          DEFAULT_REFUSAL_FA, DEFAULT_REFUSAL_EN, lang)


def domain(lang: str = "fa") -> str:
    """The subject this assistant answers about, for the prompt's fixed parts."""
    from app.db.queries import get_setting
    if lang == "en":
        return get_setting("assistant_domain_en", "") or DEFAULT_DOMAIN_EN
    return get_setting("assistant_domain", "") or DEFAULT_DOMAIN_FA


# What the bot says when the question is in scope but nothing in the knowledge
# base answers it. The product owner wrote this sentence.
DEFAULT_NO_ANSWER_FA = "متاسفانه در این خصوص نمی‌توانم پاسخی به شما بدهم."
DEFAULT_NO_ANSWER_EN = "I'm sorry, I don't have an answer for that."

# Appended to an answer we are NOT confident about. Short, spoken, and it asks
# for the one thing that fixes a wrong guess: what the visitor actually meant.
DEFAULT_HEDGE_FA = "اگر منظورت چیز دیگه‌ای بود بهم بگو."
DEFAULT_HEDGE_EN = "If you meant something else, just tell me."


def no_answer_text(lang: str = "fa") -> str:
    """The sentence a visitor sees when we found nothing that answers them."""
    return _customer_text("no_answer_text_fa", "no_answer_text_en",
                          DEFAULT_NO_ANSWER_FA, DEFAULT_NO_ANSWER_EN, lang)


def hedge_text(lang: str = "fa") -> str:
    """The line appended to an answer we are not confident about.

    Only on uncertain answers. Put it on every answer and a visitor stops
    reading it, which costs us the one correction it exists to collect.
    """
    return _customer_text("hedge_text_fa", "hedge_text_en",
                          DEFAULT_HEDGE_FA, DEFAULT_HEDGE_EN, lang)
