"""What this assistant is about, and what it says when a question is outside it.

WHY THIS TINY MODULE EXISTS. The out-of-scope refusal used to live in exactly
one place: a Persian sentence inside the system prompt, as an INSTRUCTION to
the model. So when the code itself had to refuse — a generated answer that
failed the grounding check, for instance — it had nothing to say, and the only
options were silence or a 503.

These two functions turn that wording into a VALUE the code can emit, read from
the same settings the prompt is built from. The string the model is told to say
and the string the code says are then the same string, and a deployment in a
different category changes a setting instead of a Python literal.
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


def refusal_text(lang: str = "fa") -> str:
    """The sentence a visitor sees when their question is out of scope."""
    from app.db.queries import get_setting
    from app.services.openai import DEFAULT_ASSISTANT_ORG

    org = get_setting("assistant_org", DEFAULT_ASSISTANT_ORG) or DEFAULT_ASSISTANT_ORG
    if lang == "en":
        text = get_setting("refusal_text_en", "") or DEFAULT_REFUSAL_EN
    else:
        text = get_setting("refusal_text_fa", "") or DEFAULT_REFUSAL_FA
    # str.replace, never .format(): admin-entered text containing a stray brace
    # must not raise. Same rule as build_system_prompt().
    return text.replace("{org}", org)


def domain(lang: str = "fa") -> str:
    """The subject this assistant answers about, for the prompt's fixed parts."""
    from app.db.queries import get_setting
    if lang == "en":
        return get_setting("assistant_domain_en", "") or DEFAULT_DOMAIN_EN
    return get_setting("assistant_domain", "") or DEFAULT_DOMAIN_FA
