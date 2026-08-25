from fastapi import APIRouter, HTTPException, Request, Response

from app.models import ChatRequest, ChatResponse
from app.config import (
    logger,
    TRUSTED_MATCH_THRESHOLD,
    LOCAL_FALLBACK_THRESHOLD,
    QUESTIONS_FALLBACK_THRESHOLD,
    INTENT_TRUST_THRESHOLD,
    COOKIE_SECURE,
    CHAT_TOKEN_REFRESH_GRACE,
    CONV_COOKIE_MAX_AGE,
)
from app.auth import security
from app.auth.security import (
    client_ip, validate_chat_token, validate_request_origin,
)
import time as _perf

from app.db.queries import get_setting, log_chat
from app.services import applog
from app.services.search import find_best_match, find_similar_question, classify_intent_local
from app.services.openai import classify_intent, get_openai_response
from app.utils.normalizer import strip_leading_greeting


router = APIRouter()

# The one entry whose answer is personalised. Everything else in the knowledge
# base is the same for every visitor, which is what makes it verifiable.
TARGETED_VISIT_ID = "inotex-targeted-visit"


def _targeted_visit_suffix(entry: dict, visitor, lang: str) -> str:
    """Sections matching this visitor, appended to the targeted-visit answer.

    Empty for anyone who has not described their work — the stock answer
    already explains how to get suggestions, and inventing a "personalised"
    list from an empty profile would be theatre.
    """
    if entry.get("id") != TARGETED_VISIT_ID or visitor is None:
        return ""
    try:
        from app.services import visit_plan
        text = visit_plan.plan_text(visitor.model_dump(), lang)
    except Exception as e:  # noqa: BLE001 — a planner fault must not lose the answer
        logger.error(f"[visit-plan] skipped: {type(e).__name__}: {e}")
        return ""
    return f"\n\n{text}" if text else ""


def _answer_from_entry(entry: dict, score: float, source: str, user_query: str,
                       tokens: int = 0, cost: float = 0.0, lang: str = "fa",
                       visitor=None) -> ChatResponse:
    """Build (and log) a chat response from a dataset entry."""
    # `or ""` (not .get(k, "")) so a NULL column -> None is coerced to "" —
    # .get's default only fires when the key is *absent*, not when it's None.
    video_url = (entry.get("video_url") or "").strip()
    response_type = "video" if video_url else "text"
    # English falls back to Persian whenever a translation is missing, so a
    # partially translated knowledge base still answers rather than going blank.
    response_text = ""
    if lang == "en":
        response_text = (entry.get("text_en") or "").strip()
    if not response_text:
        response_text = entry.get("text") or ""
    response_text += _targeted_visit_suffix(entry, visitor, lang)
    log_chat(user_query, response_text, response_type, source, score, tokens, cost)
    applog.info("chat", "conversation.answer.served", "پاسخ به بازدیدکننده داده شد",
                subcategory=source, outcome="ok",
                tokens_in=tokens or None, cost=cost or None,
                metadata={"tier": source, "score": round(float(score or 0), 3),
                          "response_type": response_type,
                          "entry_id": str(entry.get("id", ""))[:60]})
    return ChatResponse(
        type=response_type,
        text=response_text,
        video_url=video_url if video_url else None,
        confidence=score,
        source=source,
    )


@router.post("/chat", response_model=ChatResponse)
async def chat_endpoint(request: ChatRequest, http_request: Request,
                        response: Response):
    # Maintenance blocks VISITOR traffic only; the admin panel stays reachable
    # so an operator can watch and end the maintenance they started.
    from app.services.maintenance import guard as _maintenance_guard
    _maintenance_guard()
    validate_request_origin(http_request)
    nonce = validate_chat_token(http_request)
    # Two-tier limit: the tight bucket is the visitor's signed-token nonce, so
    # one abuser behind the booth's shared NAT exhausts only their own budget;
    # the loose per-IP backstop bounds the refresh-to-mint-a-fresh-identity
    # trick. A legacy (v1) token carries no nonce and falls back to the
    # IP-keyed tight bucket — exactly the pre-nonce behaviour. Limits are read
    # off the security module at CALL time so the enforcing module stays the
    # one place tests and operators tune.
    ip = client_ip(http_request) or "unknown"
    security.check_rate_limits(http_request, [
        (f"chat:{nonce or 'ip:' + ip}", security.CHAT_RATE_LIMIT),
        (f"chatip:{ip}", security.CHAT_IP_RATE_LIMIT),
    ])

    lang = "en" if (request.lang or "").lower().startswith("en") else "fa"
    user_query = request.message.strip()
    if not user_query:
        raise HTTPException(status_code=400, detail="Message cannot be empty")

    # Same content policy as the structured row below — the stdlib log must
    # not become the one place a visitor's PII lands unredacted.
    logger.info(f"Received query: {applog.apply_content_policy(user_query)}")

    # One id for the whole logical operation. The middleware already stamped a
    # request id; this binds the CONVERSATION so message -> retrieval -> LLM ->
    # answer can be reconstructed from a single value in the log explorer.
    conversation_id = (http_request.cookies.get("padyar_conv") or "")[:64] or applog.new_id()
    # Echo the conversation id on EVERY successful response. The read above
    # shipped long ago but nothing ever wrote the cookie, so every message
    # got a fresh random id and message → retrieval → LLM → answer could not
    # be reconstructed from one value in the log explorer. Echoing (not
    # set-once) also slides the 24h window for long conversations. Same
    # attribute set as the leads visitor cookie; HttpOnly because only the
    # server reads it. Exception paths (400/429/503) skip this — no answer
    # was served, and the next successful message starts/continues the cookie.
    response.set_cookie(
        key="padyar_conv", value=conversation_id,
        httponly=True, secure=COOKIE_SECURE, samesite="lax",
        max_age=CONV_COOKIE_MAX_AGE,
    )
    applog.set_request_context(correlation_id=applog.current_request_id() or applog.new_id())
    _chat_started = _perf.perf_counter()
    applog.info("chat", "conversation.message.received", "پیام بازدیدکننده دریافت شد",
                conversation_id=conversation_id, actor_type="visitor",
                subcategory=lang,
                metadata={"query": applog.apply_content_policy(user_query),
                          "chars": len(user_query)})

    # A polite opener ("سلام، ...") must not hijack the real question. Match on the
    # message with the greeting removed — unless the message is *only* a greeting,
    # in which case keep it whole so it can match the intro.
    core_query, only_greeting = strip_leading_greeting(user_query)
    match_query = user_query if only_greeting else core_query

    best_match, score = find_best_match(match_query)

    # Tier 0 — an (almost) exact hit in the curated questions index is the most
    # precise signal available: those rows are hand-mapped query→answer pairs.
    # Jaccard-only, so fuzzy semantic similarity cannot masquerade as exact.
    # It outranks description-level similarity, which on short corpora can be
    # confidently wrong (measured on the golden set).
    exact_match, exact_score = find_similar_question(match_query, exact_only=True)
    if exact_match and exact_score >= 0.9:
        return _answer_from_entry(exact_match, exact_score, "local_questions", user_query, lang=lang, visitor=request.visitor)

    question_match, q_score = find_similar_question(match_query)

    # Tier 1 — trust only a near-exact local match.
    if score >= TRUSTED_MATCH_THRESHOLD and best_match:
        return _answer_from_entry(best_match, score, "local", user_query, lang=lang, visitor=request.visitor)

    if question_match and q_score >= TRUSTED_MATCH_THRESHOLD:
        return _answer_from_entry(question_match, q_score, "local_questions", user_query, lang=lang, visitor=request.visitor)

    # Tier 1.5 — this installation's own trained intent classifier (logistic
    # regression over local embeddings, retrained on every dataset edit). A
    # confident verdict answers here with zero external calls; anything less
    # falls through to the AI classifier exactly as before.
    intent_entry, intent_prob = classify_intent_local(match_query)
    if intent_entry and intent_prob >= INTENT_TRUST_THRESHOLD:
        logger.info(f"Local intent classifier → {intent_entry.get('id')} (p={intent_prob:.2f})")
        return _answer_from_entry(intent_entry, intent_prob, "local_intent", user_query, lang=lang, visitor=request.visitor)

    # Tier 2 — below the trust bar, the AI classifier decides intent. We do NOT
    # serve the low-confidence local match here: that is exactly what produced
    # confident-but-wrong answers (e.g. a cost question returning an unrelated entry).
    is_openai_enabled = get_setting('openai_enabled', 'true') == 'true'
    if is_openai_enabled:
        logger.info(f"Low confidence local match (tfidf={score:.2f}, questions={q_score:.2f}), asking GPT to classify intent...")
        try:
            classified_entry, cls_tokens, cls_cost = await classify_intent(match_query)

            if classified_entry:
                logger.info(f"GPT classified → {classified_entry.get('id')}")
                return _answer_from_entry(
                    classified_entry, score, "openai_classified", user_query, cls_tokens, cls_cost,
                    lang=lang, visitor=request.visitor,
                )

            # GPT says out_of_domain → trust it and give a real AI answer instead
            # of falling back to a weak local match it just rejected.
            gpt_response, tokens, cost = await get_openai_response(user_query, lang=lang)
            log_chat(user_query, gpt_response, "text", "openai", score,
                     cls_tokens + tokens, cls_cost + cost)
            return ChatResponse(
                type="text", text=gpt_response, video_url=None,
                confidence=score, source="openai",
            )
        except Exception as e:
            logger.error(f"Error in classification flow: {type(e).__name__}: {e}")
            # AI unavailable — fall back to a *strong* local match only, else 503.
            if score >= LOCAL_FALLBACK_THRESHOLD and best_match:
                return _answer_from_entry(best_match, score, "local", user_query, lang=lang, visitor=request.visitor)
            if question_match and q_score >= QUESTIONS_FALLBACK_THRESHOLD:
                return _answer_from_entry(question_match, q_score, "local_questions", user_query, lang=lang, visitor=request.visitor)
            log_chat(user_query, "ai_unavailable_no_strong_match", "text", "system", score)
            raise HTTPException(status_code=503, detail="AI service unavailable")

    # OpenAI disabled — answer only from a reasonably strong local match.
    if score >= LOCAL_FALLBACK_THRESHOLD and best_match:
        return _answer_from_entry(best_match, score, "local", user_query, lang=lang, visitor=request.visitor)
    if question_match and q_score >= QUESTIONS_FALLBACK_THRESHOLD:
        return _answer_from_entry(question_match, q_score, "local_questions", user_query, lang=lang, visitor=request.visitor)

    log_chat(user_query, "no_confident_match", "text", "system", score)
    from app.services.search import report_empty_retrieval
    report_empty_retrieval(user_query, score)
    applog.warning("chat", "conversation.answer.failed",
                   "پاسخ مطمئنی برای پرسش پیدا نشد",
                   outcome="no_match",
                   duration_ms=int((_perf.perf_counter() - _chat_started) * 1000),
                   metadata={"score": round(float(score or 0), 3)})
    raise HTTPException(status_code=503, detail="AI service unavailable")


@router.post("/api/chat-token")
async def refresh_chat_token(http_request: Request):
    """Mint a fresh chat token for a visitor who already holds one.

    Without this, a token that expired mid-conversation killed the chat until
    a manual page reload — which wipes the DOM-only history. The frontend
    calls this reactively (one silent retry on a 403 from /chat), so the
    visitor never notices.

    POST, not GET: a same-origin fetch POST always carries an Origin header
    (a GET often relies on Referer, which referrer-policy can strip), so the
    origin check below works reliably — and POST is never a cached response.

    Guards, in the same order as /chat:
      1. origin — as everywhere;
      2. chat token with CHAT_TOKEN_REFRESH_GRACE seconds of grace — the
         caller must POSSESS a token this server signed within the grace
         window. That possession is the only thing keeping this endpoint
         from being an unauthenticated minting oracle: origin and rate-limit
         headers alone are client-controlled and never sufficient;
      3. its own per-IP token-refresh bucket, SEPARATE from the chat bucket,
         so a refresh+retry pair never eats the visitor's chat budget at a
         NAT'd booth. (A bare key= would be one global bucket; the explicit
         IP suffix keeps it per-address.) Defaults (CHAT_RATE_LIMIT per
         CHAT_RATE_WINDOW) apply — the chat IP backstop from the rate-limit
         plan already bounds the mint-fresh-identity cycle this enables.
    """
    validate_request_origin(http_request)
    validate_chat_token(http_request, grace_seconds=CHAT_TOKEN_REFRESH_GRACE)
    ip = client_ip(http_request) or "unknown"
    security.check_rate_limit(http_request, key=f"token-refresh:{ip}")
    return {"token": security.generate_chat_token()}
