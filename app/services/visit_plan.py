"""Targeted-visit planner: match a visitor's profile to INOTEX sections.

WHAT THIS RECOMMENDS — and what it deliberately does not
--------------------------------------------------------
It ranks the OFFICIAL sections and side events of INOTEX 2026 (the ones
verified from inotex.com and listed in content/sources.json) against the
visitor's field of work, job title and interests.

It does NOT name individual exhibitors or booth numbers: the official site
has not published an exhibitor directory, so inventing "booth 42, company X"
would be fabrication dressed as personalisation. When a visitor asks for
specific companies the planner says the directory is not published yet and
points at the official channels — the same rule the rest of the knowledge
base follows.

Matching is keyword-based over normalised Persian/English text, with the
local embedding index used as a fallback when the wording shares no tokens
with the taxonomy (a visitor who writes «پزشکی» should still reach the
health-adjacent sections). Scores are relative, never presented as
certainty.
"""
from typing import List, Optional

from app.config import logger
from app.services import taxonomy
from app.utils.normalizer import normalize_persian

def _norm(text: str) -> str:
    """Character-level normalisation only.

    The DB synonym table is tuned for the Q&A retriever, not for this curated
    vocabulary. Expanding both the keywords and the visitor's words through it
    made a single mention of «هوش مصنوعی» register as two separate hits, because
    the keyword itself expanded into a multi-word string that then matched the
    equally expanded profile text.
    """
    return normalize_persian(text, expand_synonyms=False)


MAX_RESULTS = 4
# Below this many real matches, the general sections are added underneath —
# clearly marked as general, never dressed up as personal matches.
MIN_PLAN = 3

# Normalised keywords, rebuilt only when the taxonomy document is replaced.
# `taxonomy` swaps its document atomically, so identity is an exact cache key.
_cache_doc = None
_cache_sections: List[dict] = []


def sections() -> List[dict]:
    """Current sections with their keywords normalised for matching.

    Normalisation folds ZWNJ into a space, so «هوش‌مصنوعی» and «هوش مصنوعی»
    collapse to one string — the set then stops a single written mention from
    being counted twice.
    """
    global _cache_doc, _cache_sections
    doc = taxonomy.document()
    if doc is not _cache_doc:
        _cache_sections = [
            dict(s, _norms=sorted({_norm(k) for k in s["keywords"]}))
            for s in doc["sections"]
        ]
        _cache_doc = doc
    return _cache_sections

# A Persian token this long or longer may match by prefix, so «استارتاپی»
# still finds «استارتاپ» and «مشاوره» finds «مشاور». Shorter stems would start
# matching unrelated words.
PREFIX_MIN = 4


def _profile_text(profile: dict) -> str:
    parts = [profile.get("job", ""), profile.get("position", ""), profile.get("interests", "")]
    # A picked interest may be worded differently from the section that
    # covers it ("IoT" vs «اینترنت اشیا»); the taxonomy carries those
    # synonyms, so fold them in before matching.
    joined = taxonomy.expand_interests(" ".join(p for p in parts if p))
    return _norm(joined)


def _hits(text: str, section: dict) -> int:
    """How many distinct keywords of this section the visitor's words contain.

    Matching is token-aware rather than plain substring: a two-letter latin
    keyword like "ai" must be its own word, otherwise "email marketing" would
    register as an artificial-intelligence profile. Persian single tokens may
    match by prefix to absorb the language's suffixes.
    """
    tokens = set(text.split())
    count = 0
    for kw in section["_norms"]:
        if " " in kw:
            if kw in text:
                count += 1
        elif kw in tokens:
            count += 1
        elif kw.isascii():
            continue  # latin stems only ever match whole words
        elif len(kw) >= PREFIX_MIN and any(tok.startswith(kw) for tok in tokens):
            count += 1
    return count


def _keyword_score(text: str, section: dict) -> float:
    """Match strength in 0..1 from the visitor's own words."""
    if not text:
        return 0.0
    # Two hits already means a solid match; more should not let a section that
    # simply lists more synonyms outrank a precise one.
    return min(1.0, _hits(text, section) / 2.0)


def _semantic_scores(text: str) -> Optional[List[float]]:
    """Embedding similarity per section, or None when unavailable.

    Used only to rescue profiles whose wording shares no literal token with
    the taxonomy; a failure here must never break the plan.
    """
    try:
        from app.services import embeddings
        if not embeddings.available():
            return None
        index = embeddings.build_index(
            [_norm(s["fa"] + " " + " ".join(s["keywords"])) for s in sections()]
        )
        if index is None:
            return None
        import numpy as np
        model = embeddings._get_model(index.model_name)
        vec = np.asarray(model.encode([text]), dtype=np.float32)[0]
        norm = np.linalg.norm(vec)
        if norm == 0:
            return None
        return [float(v) for v in (index.matrix @ (vec / norm))]
    except Exception as e:  # noqa: BLE001 — the plan must survive this
        logger.error("[visit-plan] semantic scoring unavailable: %s", type(e).__name__)
        return None


def recommend(profile: dict, lang: str = "fa") -> dict:
    """Rank sections for this visitor.

    Returns the plan plus an explicit note about exhibitor-level data, so the
    caller can show the visitor what this is and is not.
    """
    text = _profile_text(profile)
    scored = []
    for section in sections():
        hits = _hits(text, section) if text else 0
        scored.append([section, min(1.0, hits / 2.0), hits])

    if text and max(s[1] for s in scored) == 0.0:
        semantic = _semantic_scores(text)
        if semantic:
            for pair, sim in zip(scored, semantic):
                # Calibrated the same way as the retriever: below ~0.45 cosine
                # is noise on this corpus. Capped at 0.6 — a rescue match is
                # never presented as strongly as the visitor's own words.
                pair[1] = min(0.6, max(0.0, (sim - 0.45) / 0.35) * 0.6)

    # Hit count breaks ties between two sections that both saturated the score,
    # so the ordering is deterministic instead of taxonomy order by accident.
    scored.sort(key=lambda p: (p[1], p[2]), reverse=True)
    picks = [p[:2] for p in scored if p[1] > 0][:MAX_RESULTS]
    matched_count = len(picks)

    # Top up to a plan worth walking. The added sections carry score 0 and no
    # `why`, so nothing claims to match a visitor it did not match.
    if matched_count < MIN_PLAN:
        by_id = {s["id"]: s for s in sections()}
        chosen = {s["id"] for s, _ in picks}
        for fid in taxonomy.fallback_ids():
            if len(picks) >= MIN_PLAN:
                break
            if fid in by_id and fid not in chosen:
                picks.append([by_id[fid], 0.0])

    return {
        "matched": matched_count > 0,
        "sections": [
            {
                "id": s["id"],
                "title": s["fa"] if lang == "fa" else s["en"],
                "why": (s["why_fa"] if lang == "fa" else s["why_en"]) if score > 0 else "",
                # True = a general recommendation, not a match on this profile.
                "general": score == 0.0,
                "score": round(score, 3),
            }
            for s, score in picks
        ],
        # Stated on every plan, in the visitor's language: this is a map of
        # official sections, not an exhibitor directory.
        "note": (
            "این پیشنهادها بر اساس بخش‌های رسمی اینوتکس ۲۰۲۶ است. فهرست غرفه‌داران "
            "هنوز روی سایت رسمی منتشر نشده؛ برای فهرست شرکت‌ها https://inotex.com/ را دنبال کنید."
            if lang == "fa" else
            "These suggestions map to the official INOTEX 2026 sections. The exhibitor "
            "directory is not published on the official site yet — follow https://inotex.com/ for it."
        ),
        "empty_hint": (
            "برای پیشنهاد دقیق‌تر، در «بازدید هوشمند» شغل و زمینه‌های مورد علاقه‌تان را بنویسید."
            if lang == "fa" else
            "For sharper suggestions, add your field of work and interests in Smart Visit."
        ),
    }


def plan_text(profile: dict, lang: str = "fa") -> str:
    """The plan as a chat message, or "" when there is nothing personal to say.

    Returning "" on a generic plan is deliberate: the stock targeted-visit
    answer already invites the visitor to fill the form, and appending a
    "recommendation" that ignored their input would read as fake personalisation.
    """
    plan = recommend(profile, lang)
    if not plan["matched"]:
        return ""
    lines = [
        "بر اساس آنچه دربارهٔ کار و علاقه‌تان نوشته‌اید، پیشنهاد می‌کنم اول سراغ این بخش‌ها بروید:"
        if lang == "fa" else
        "Based on what you told us about your work and interests, start with these:"
    ]
    for s in plan["sections"]:
        if not s["general"]:
            lines.append(f"• {s['title']} — {s['why']}")

    # The general sections get their own heading. Listing them alongside the
    # matches would imply the profile pointed at them.
    general = [s for s in plan["sections"] if s["general"]]
    if general:
        lines.append(
            "و اگر وقت داشتید، این بخش‌ها برای همهٔ بازدیدکنندگان مفیدند:"
            if lang == "fa" else
            "And if you have time, these are worth seeing for any visitor:"
        )
        for s in general:
            lines.append(f"• {s['title']}")

    lines.append(plan["note"])
    return "\n".join(lines)
