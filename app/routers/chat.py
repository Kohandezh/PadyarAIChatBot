from fastapi import (APIRouter, BackgroundTasks, Depends, HTTPException,
                     Request, Response)

from app.models import ChatRequest, ChatResponse
from app.config import (
    logger,
    is_module_enabled,
    TRUSTED_MATCH_THRESHOLD,
    LOCAL_FALLBACK_THRESHOLD,
    QUESTIONS_FALLBACK_THRESHOLD,
    INTENT_TRUST_THRESHOLD,
    COOKIE_SECURE,
    CHAT_TOKEN_REFRESH_GRACE,
    CONV_COOKIE_MAX_AGE,
    ANSWER_TOPK,
    HISTORY_TURNS,
    PICK_WINDOW_MINUTES,
)
from app.auth import security
from app.auth import visitor as visitor_auth
from app.auth.security import (
    client_ip, validate_chat_token, validate_request_origin,
)
import time as _perf

from app.db.queries import (get_setting, log_chat, recent_turns,
                            last_offer_state)
from app.services import applog, conversations, scope
from app.services.search import (find_best_match, find_similar_question,
                                 classify_intent_local, unknown_salient_tokens,
                                 resolve_named_entity, named_entity_hits,
                                 entry_mentions,
                                 entity_coverage, find_top_matches, get_entry)
from app.services.answer import (select_records, render_options, resolve_pick,
                                 resolve_more, parse_offer, dump_offer,
                                 is_followup, generated_prose_is_grounded)
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


# What the summary is called where the model reads it. A label, not a claim:
# the line beside it is a compression of this conversation, nothing else.
SUMMARY_LABEL_FA = "خلاصهٔ بخش‌های قبلی همین گفتگو"
SUMMARY_LABEL_EN = "Summary of the earlier part of this conversation"


def _history_for(conversation_id: str, lang: str) -> list:
    """The prior turns handed to the model, newest first.

    TWO THINGS HAPPEN HERE.

    A no-answer turn is dropped. «متاسفانه در این خصوص نمی‌توانم پاسخی به شما
    بدهم» is a sentence WE wrote, and replaying it as a prior assistant answer
    teaches the model to write it again. recent_turns() already drops the old
    `system` sentinels for exactly this reason; this is the same rule for the
    tier that replaced the 503.

    Then the rolling summary, when there is one, takes the OLDEST slot. The
    model sees one short paragraph covering everything before the recent turns
    and the recent turns themselves word for word, which is how a long
    conversation stays useful without the prompt growing forever. One slot is
    given up to make room for it — the block the model reads is capped, and a
    summary that pushed the newest turn out would be a trade in the wrong
    direction.

    BOTH HALVES ARE BOUNDED BY HISTORY_WINDOW_MINUTES. recent_turns() drops
    old turns, and get_summary() drops the summary of a conversation that went
    quiet. On a shared kiosk everything said before that gap was said by
    somebody who has walked away, and a summary is those same words, only
    compressed.
    """
    turns = [t for t in recent_turns(conversation_id, limit=HISTORY_TURNS)
             if t.get("source") != "no_answer"]
    summary = conversations.get_summary(conversation_id)
    if not summary:
        return turns
    label = SUMMARY_LABEL_EN if lang == "en" else SUMMARY_LABEL_FA
    return turns[:max(1, HISTORY_TURNS - 1)] + [
        {"query": label, "response": summary, "source": "summary"}]


def _log_turn(user_query: str, answer_text: str, r_type: str, source: str,
              confidence, tokens: int = 0, cost: float = 0.0, *,
              conversation_id: str = "", entry_id: str = "",
              offer_state: str = "", video_url: str = "",
              answered: bool = True) -> None:
    """Record one turn. THE one place a turn is written, in both stores.

    This endpoint has more than a dozen answering branches — pick, company
    list, company field, the questions index, local retrieval, the trained
    intent head, selection, options, the legacy classifier, the refusal, the
    two AI-down fallbacks. Every one of them already had to call log_chat, so
    log_chat's call site IS the chokepoint, and this function is it. Anything
    that answers a visitor calls this and gets the transcript for free; a new
    tier that forgets it also forgets chat_logs, which is the failure a
    reviewer notices.

    TWO STORES, ON PURPOSE. `chat_logs` is the flat per-turn telemetry the
    admin dashboard aggregates today. `messages` is the durable transcript:
    one row per message, tied to a conversation, tied to a person. See
    migrations/0010_conversations.sql for why both exist.

    `answered=False` writes the visitor's question and NO assistant message.
    That is the AI-outage path, and a question nobody answered is precisely
    the thing `chat_logs` cannot represent and `messages` can.

    A storage fault here must never cost a visitor their answer, so
    everything below either swallows on its own (log_chat, and every write in
    app/services/conversations.py) or is swallowed here.
    """
    # Kept EXACTLY as it was: when there is no conversation, log_chat is
    # called with its original seven positional arguments. tests/
    # test_ai_legacy_import.py wraps it with a seven-argument spy.
    memory = {}
    if conversation_id:
        memory = {"conversation_id": conversation_id, "entry_id": entry_id,
                  "offer_state": offer_state}
    log_chat(user_query, answer_text, r_type, source, confidence, tokens, cost,
             **memory)
    try:
        conversations.append_visitor_message(conversation_id, user_query)
        if answered:
            conversations.append_assistant_message(
                conversation_id, answer_text, source=source,
                confidence=confidence, entry_id=entry_id, video_url=video_url,
                tokens=tokens, cost=cost)
    except Exception as e:  # noqa: BLE001 — chat is the product, logging is not
        logger.error(f"[transcript] turn not recorded: {type(e).__name__}: {e}")


def _answer_from_entry(entry: dict, score: float, source: str, user_query: str,
                       tokens: int = 0, cost: float = 0.0, lang: str = "fa",
                       visitor=None, conversation_id: str = "",
                       offer_state: str = "") -> ChatResponse:
    """Build (and log) a chat response from a dataset entry.

    The ONLY place in the app that emits a video, and it derives the clip from
    the RECORD. Any tier that knows WHICH record it used gets the booth clip
    for free — which is why the pick tier lands here.

    `offer_state` is re-stored on a pick turn so the same list stays pickable
    for a following "4" and the freshness clock restarts: visitors compare.
    """
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
    # The hedge. Below the trust bar we are serving the best record we found
    # while knowing it may not be the one the visitor meant, and the honest
    # thing is to say so and invite the correction. Above it we say nothing:
    # a line on every answer is a line nobody reads, and then it no longer
    # collects the correction it exists for.
    #
    # ONE threshold, no lower bound. A band ("hedge between the fallback floor
    # and the trust bar") would leave the LEAST confident answers of all — an
    # AI-selected record whose retrieval score was 0.2 — with no hedge at all,
    # which is backwards.
    if float(score or 0) < TRUSTED_MATCH_THRESHOLD:
        response_text += "\n\n" + scope.hedge_text(lang)
    _log_turn(user_query, response_text, response_type, source, score,
              tokens, cost, conversation_id=conversation_id,
              entry_id=str(entry.get("id", "")), offer_state=offer_state,
              video_url=video_url)
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
                        response: Response, background_tasks: BackgroundTasks):
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

    # WHO IS ASKING. Resolved by the `resolve_visitor` middleware from the
    # padyar_vs cookie and nothing else — no header, no body field, no query
    # string. The profile below used to arrive in this request's BODY, which
    # meant a caller could describe themselves however they liked and the
    # targeted-visit planner would believe them. getattr with a default so a
    # test that calls this function directly, without the middleware, still
    # gets an anonymous visitor rather than an AttributeError.
    visitor_id = getattr(http_request.state, "visitor_id", "") or ""
    visitor = getattr(http_request.state, "visitor", None)

    # The registration gate, and it is the SERVER side of one. Blocking the
    # first message until somebody signs up has always been done in
    # static/companion/registration.js (ChatConfig.sendGateFn), which is a
    # courtesy, not a control: a direct POST carrying a valid chat token
    # walked straight past it.
    #
    # BOTH conditions matter, and the first one protects a live install. The
    # elecomp deployment does not load the registration module at all — it has
    # no /verify page, no OTP endpoints, no way for anyone to obtain a
    # session — so demanding one there would lock every visitor out of the
    # chatbot. "Is the module loaded" is asked of the registry
    # (app/modules/registry.py, via config.is_module_enabled) and never of an
    # ImportError, because a module that fails to import is BROKEN, not
    # switched off, and the two must not behave the same. The second
    # condition is the operator's own switch, the same `registration_enabled`
    # row the admin panel and /api/auth/registration-status read.
    #
    # require_visitor raises 401 with a machine-readable `code`, so the
    # frontend opens the signup modal instead of printing an error sentence.
    if (is_module_enabled("registration")
            and get_setting("registration_enabled", "false") == "true"):
        visitor_auth.require_visitor(http_request)

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
    #
    # AND WHOSE conversation is it. `padyar_conv` is an unsigned id in a cookie,
    # so anybody can paste anybody else's into their browser and, until now,
    # get that person's transcript appended to and their history fed to the
    # model. continuable_conversation_id() returns "" for a conversation that
    # belongs to a DIFFERENT visitor, and a fresh id is minted instead. An
    # unowned conversation still passes, because the person who registers
    # halfway through has to keep the questions they already asked.
    conversation_id = conversations.continuable_conversation_id(
        (http_request.cookies.get("padyar_conv") or "")[:64],
        visitor_id) or applog.new_id()
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
    # conversation_id goes into the logging context, not only onto the one row
    # below. Before this, every conversation.answer.served row carried an empty
    # string and the log explorer's conversation filter was dead.
    applog.set_request_context(
        correlation_id=applog.current_request_id() or applog.new_id(),
        conversation_id=conversation_id)
    # The durable transcript's session row. Created here, once, so the
    # conversation carries the language, address and browser of the message
    # that STARTED it — the per-message writes below only add messages. It
    # swallows its own faults and returns {} when storage is unhappy.
    # `visitor_id` stamps the OWNER onto a conversation this call creates, so
    # a signed-in visitor's new conversation is defended from its first
    # message. An existing row keeps whatever owner it already had (or none),
    # which is what leaves the mid-chat signup claim to _promote_to_visitor.
    conversations.get_or_create_conversation(
        conversation_id, lang=lang, ip=ip,
        user_agent=http_request.headers.get("user-agent", ""),
        visitor_id=visitor_id)
    # Refresh the rolling summary AFTER this answer has been sent. Starlette
    # runs background tasks once the response is on the wire, so a slow or
    # dead provider costs the visitor nothing: this turn was answered from the
    # recent turns alone, and so is the next one if this never finishes.
    background_tasks.add_task(conversations.update_summary,
                              conversation_id, lang=lang)
    _chat_started = _perf.perf_counter()
    applog.info("chat", "conversation.message.received", "پیام بازدیدکننده دریافت شد",
                conversation_id=conversation_id, actor_type="visitor",
                subcategory=lang,
                metadata={"query": applog.apply_content_policy(user_query),
                          "chars": len(user_query)})

    # Conversation memory. Every reader here returns its empty default on ANY
    # problem — including a chat_logs table that predates migration 0009 — so
    # an unmigrated install simply behaves like today's chatbot.
    history = _history_for(conversation_id, lang)
    offer = parse_offer(last_offer_state(conversation_id,
                                         within_minutes=PICK_WINDOW_MINUTES))

    # Pick tier — the answer to the product owner's complaint, and it costs
    # ZERO network calls. The visitor typed "3", tapped a chip, or wrote
    # «دومی»; we look the record up by the id we stored last turn and serve it
    # through the unchanged _answer_from_entry, so that company's booth clip
    # plays. Runs BEFORE retrieval on purpose: a bare "3" means nothing to a
    # retriever, and this way the whole list → pick → video path works with the
    # AI provider switched off.
    if offer is not None:
        picked_id = resolve_pick(user_query, offer, lang)
        if picked_id:
            picked = get_entry(picked_id)
            if picked is not None:
                logger.info(f"Pick → {picked_id}")
                # Re-store the SAME offer: visitors compare, so a following "4"
                # must still resolve and the 15-minute clock restarts.
                return _answer_from_entry(
                    picked, 0.9, "local_pick", user_query, lang=lang,
                    visitor=visitor, conversation_id=conversation_id,
                    offer_state=dump_offer(offer))
            # The record was edited or deleted between the two turns — staff
            # correct content WHILE visitors ask. Fall through quietly rather
            # than raising or serving a stale dict.
            logger.info(f"Pick {picked_id} no longer exists; falling through")

        # The pager. Without it, capping the list at five names is a straight
        # loss for the visitor who wanted the sixth.
        if resolve_more(user_query, offer) and offer["shown"] < offer["total"]:
            # The shown prefix and the unshown tail are resolved SEPARATELY.
            # A record an admin deleted mid-conversation is dropped rather than
            # printed as a gap in the numbering, and render_options stores the
            # compacted list — so the names printed and the ids stored still
            # agree, which is the invariant a pick depends on. But compacting a
            # list and then slicing it at the ABSOLUTE position `shown` are two
            # different things: delete one of the five names already on screen
            # and everything after it shifts left, so the sixth company is
            # stepped over and never printed on any page. The next page starts
            # after whatever is LEFT of what the visitor saw, not after five.
            prefix = [e for e in (get_entry(i)
                                  for i in offer["ids"][:offer["shown"]]) if e]
            tail = [e for e in (get_entry(i)
                                for i in offer["ids"][offer["shown"]:]) if e]
            page_entries = prefix + tail
            next_start = len(prefix) + 1
            page_total, page_filter = offer["total"], offer["filter"]
            # offer_state keeps at most OFFER_IDS_MAX ids, so a long match is
            # only partly in there. Measured 2026-08-28 with 70 AI companies:
            # page 1 said «۷۰ شرکت در زمینه «هوش مصنوعی»» and every «بیشتر»
            # after it said «۵۰ شرکت» with no filter words, and companies
            # 51..70 were unreachable. Re-running the deterministic list tier
            # on the query that produced the list brings the whole set back.
            if offer["query"] and len(offer["ids"]) < offer["total"]:
                from app.services.company_search import answer_company_list
                again = answer_company_list(offer["query"], lang=lang)
                # Only when the list still STARTS the same way. Staff correct
                # content while visitors read it, and a set that shifted under
                # the numbering would make "7" a different company on the two
                # turns. On a mismatch we page through the stored ids, exactly
                # as before.
                if again is not None and (again["matched_ids"][:offer["shown"]]
                                          == offer["ids"][:offer["shown"]]):
                    page_entries = [e for e in (get_entry(i)
                                                for i in again["matched_ids"]) if e]
                    # The prefix matched id for id against a set the database
                    # just produced, so all `shown` of them still exist and the
                    # absolute position is the right one again.
                    next_start = offer["shown"] + 1
                    page_total = again["count"]
                    page_filter = again["filter_label"]
            # Never announce more names than this page set can print. A record
            # deleted mid-conversation, or a re-derivation that did not come
            # back, must shrink the count rather than advertise a name «بیشتر»
            # can never reach.
            page_total = min(page_total, len(page_entries))
            # ...but the guard above counted the ids we STORED, and a bulk
            # edit or a reindex between two turns is what takes those away.
            # With every record gone the pager still ran and printed «۰ شرکت:»
            # followed by "which one would you like?" — a count of nothing and
            # a question about nothing. Count what SURVIVED instead: the page
            # render_options is about to slice starts at `shown`, so anything
            # at or below that leaves it empty. An empty page is not a page —
            # fall through to normal retrieval, exactly like a list with no
            # next page.
            if len(page_entries) >= next_start:
                more_text, more_options, more_offer = render_options(
                    page_entries, "", lang, start_index=next_start,
                    total=page_total, filter_label=page_filter,
                    source_query=offer["query"])
                _log_turn(user_query, more_text, "text", "local_company_search",
                          0.9, conversation_id=conversation_id,
                          offer_state=more_offer)
                applog.info("chat", "conversation.answer.served",
                            "پاسخ به بازدیدکننده داده شد",
                            subcategory="local_company_search", outcome="ok",
                            metadata={"tier": "local_company_search", "score": 0.9,
                                      "response_type": "text", "page": "more"})
                return ChatResponse(type="text", text=more_text, video_url=None,
                                    confidence=0.9, source="local_company_search",
                                    options=more_options)
            logger.info("Nothing left of the offered list to page to; falling through")

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
    question_match, q_score = find_similar_question(match_query)

    # Unknown-entity gate (the الکامپ incident, 2026-08-26): a query naming
    # something the WHOLE corpus knows nothing about must not be answered by
    # any local tier. Lexical retrievers silently drop unknown tokens, so
    # «تاریخ برگزاری نمایشگاه الکامپ» degraded to its common words and the
    # questions blend served the INOTEX date at 0.844 — confidently wrong.
    # Nulling every local candidate walks the ladder to the AI tier, which can
    # actually judge an out-of-domain entity, and keeps 503 (not a wrong
    # answer) when the AI tier is unavailable.
    unknown_tokens = unknown_salient_tokens(match_query)
    if unknown_tokens:
        logger.info(f"Unknown salient tokens {unknown_tokens}; deferring local tiers to AI")
        exact_match = question_match = best_match = None
        score = q_score = 0.0

    # Named-entity anchor (measured 2026-08-27): the guard above covers
    # entities the corpus does NOT know; this covers confusion BETWEEN known
    # entries. «شماره مدیرعامل دوندگان لبه علم» anchored on «شماره/تلفن» and
    # served the دبیرخانه phone FAQ at 0.87 — wrong entity, high confidence —
    # and «درباره دکیو بهم بگو» reached only 0.691 and paid for an LLM refusal
    # although the دکیو entry existed. Principle: no similarity score may
    # override the entity the visitor actually named, and a query naming
    # exactly one known entity is answered from that entity's own entry.
    # NOT resolved when unknown_tokens fired: an unknown salient token means
    # the AI tier must judge the query, entity token or not.
    entity_entry, entity_tokens = (None, set())
    if not unknown_tokens:
        entity_entry, entity_tokens = resolve_named_entity(match_query)

        # Naming TWO known entities is not a low-confidence query, it is an
        # ambiguous one. resolve_named_entity already refuses to pick between
        # them, but that only silenced the anchor: retrieval still ran and
        # «دوندگان لبه علم یا دکیو» came back as one of the two at 0.98 through
        # the questions index. Same wrong-entity failure, different door. So
        # the local tiers are cleared here too and the query goes on to the
        # tiers that can ASK which one was meant.
        if len(named_entity_hits(match_query)) > 1:
            logger.info("Query names more than one known entity; deferring local tiers")
            exact_match = question_match = best_match = None
            score = q_score = 0.0

    # The score every anchor-backed answer is served with: the share of the
    # query's content tokens found in the entity's own entry, floored at the
    # trust bar — a real, deterministic signal for the logs and the response's
    # confidence field, not a number borrowed from the retriever that just
    # picked the wrong entry.
    ent_score = (max(TRUSTED_MATCH_THRESHOLD, entity_coverage(entity_entry, match_query))
                 if entity_entry is not None else 0.0)

    # Company-field tier (2026-08-27): the visitor named a company AND asked
    # for one recorded fact about it — «شماره تماس شرکت دکیو چیست؟» was
    # answered with that company's generic description because nothing in this
    # pipeline ever read the company's profile fields. This serves the field
    # itself when it is on the public allowlist, and refuses when the request
    # is about a PERSON. Confidence is the entity coverage above: the answer
    # comes from the entry the anchor resolved.
    #
    # Looked up HERE, once, and not inside _entity_answer(): that ran only on
    # the anchor's OVERRIDE and RESCUE paths, so the tier was invisible
    # whenever a trusted local candidate already WAS the named company — no
    # override fired and the generic blurb won. A company whose own curated
    # question happened to contain the field word shadowed the tier completely
    # (measured 2026-08-27 on inotex.padyar.com: «سایت شرکت دکیو» matched the
    # دکیو question row itself at 0.99 and never reached this code, while
    # «شماره تماس شرکت دکیو» matched a different entry, took the override, and
    # answered correctly). Now every branch that is about to serve THAT
    # company consults it. Tier 0 is untouched: a near-exact curated hit
    # (exact_score >= 0.9) still wins outright, the same rule the anchor
    # already follows.
    field_answer = None
    if entity_entry is not None:
        from app.services.company_search import answer_company_field
        field_answer = answer_company_field(match_query, entity_entry, lang=lang)

    def _serve_field_answer() -> ChatResponse:
        """Serve (and log) the company-field answer. A helper because three
        branches reach it — the anchor's two paths and the trusted local
        branches that are already serving the named company."""
        _log_turn(user_query, field_answer["text"], "text",
                  "local_company_field", ent_score,
                  conversation_id=conversation_id,
                  entry_id=str(entity_entry.get("id", "")))
        applog.info("chat", "conversation.answer.served",
                    "پاسخ به بازدیدکننده داده شد",
                    subcategory="local_company_field", outcome="ok",
                    metadata={"tier": "local_company_field",
                              "score": round(float(ent_score), 3),
                              "response_type": "text",
                              "field": field_answer["field"],
                              "entry_id": str(entity_entry.get("id", ""))[:60]})
        return ChatResponse(
            type="text", text=field_answer["text"], video_url=None,
            confidence=ent_score, source="local_company_field",
        )

    def _entity_answer() -> ChatResponse:
        if field_answer is not None:
            return _serve_field_answer()
        return _answer_from_entry(entity_entry, ent_score, "local_entity",
                                  user_query, lang=lang, visitor=visitor,
                                  conversation_id=conversation_id)

    def _is_named_entity(candidate: dict) -> bool:
        # The candidate a local tier is about to serve IS the entry the
        # visitor named. Same identity test as _names_other_entity, other way
        # round — this is the case where nothing conflicts and the branch would
        # otherwise return the company's generic description.
        return (entity_entry is not None
                and candidate is not None
                and candidate.get("id") == entity_entry.get("id"))

    def _names_other_entity(candidate: dict) -> bool:
        # A candidate conflicts when it is a DIFFERENT entry than the one the
        # visitor named AND never even mentions that entity. Privacy note: the
        # entry served here is dataset/companies.text, which is public by
        # definition. Profile data only ever reaches a visitor through
        # company_profiles.public_profile() — its allowlist keeps the contact
        # person's mobile and email out of every answer, so a "give me the
        # CEO's number" query gets a refusal, never that person's record.
        return (entity_entry is not None
                and candidate is not None
                and candidate.get("id") != entity_entry.get("id")
                and not entry_mentions(candidate, entity_tokens))

    if exact_match and exact_score >= 0.9:
        # Tier 0 stays authoritative: a near-exact hit on a hand-curated
        # question is a deliberate mapping, never overridden by the anchor.
        return _answer_from_entry(exact_match, exact_score, "local_questions", user_query,
                                  lang=lang, visitor=visitor,
                                  conversation_id=conversation_id)

    # Company-list tier (measured 2026-08-27): «شرکت‌های هوش مصنوعی اینوتکس را
    # معرفی کن» is a LIST question, but single-document retrieval can only pick
    # one entry — Tier 1 served faq-20, the out-of-scope REFUSAL text, at 0.81
    # because it contains «هوش مصنوعی اینوتکس» and is a token magnet. The real
    # answer was a list built from the ~169 company rows. This tier answers
    # such questions straight from the `companies` table (see
    # migrations/0013_companies.sql), so it must run BEFORE the trusted
    # T1/questions block. Gated on the two guards above:
    # an unknown salient token still defers to AI, and a query naming ONE
    # specific company («شرکت دکیو چیست؟») is about that company, not a list.
    if not unknown_tokens and entity_entry is None:
        from app.services.company_search import answer_company_list
        company_list = answer_company_list(match_query, lang=lang)
        if company_list is not None:
            # 0.9 is nominal: the answer is a deterministic database listing,
            # not a similarity estimate — there is no score to report.
            list_score = 0.9
            # offer_state is what makes the next turn's "3" resolvable. It is
            # produced by the same function that rendered the list, so the
            # names printed and the ids stored can never disagree.
            _log_turn(user_query, company_list["text"], "text",
                      "local_company_search", list_score,
                      conversation_id=conversation_id,
                      offer_state=company_list["offer_state"])
            applog.info("chat", "conversation.answer.served",
                        "پاسخ به بازدیدکننده داده شد",
                        subcategory="local_company_search", outcome="ok",
                        metadata={"tier": "local_company_search",
                                  "score": list_score,
                                  "response_type": "text",
                                  "companies": company_list["count"],
                                  "shown": len(company_list["displayed_ids"]),
                                  "filter": company_list["keywords"]})
            return ChatResponse(
                type="text", text=company_list["text"], video_url=None,
                confidence=list_score, source="local_company_search",
                options=company_list["options"],
            )

    # Tier 1 — trust only a near-exact local match. When BOTH local signals
    # clear the trust bar, the higher score wins: sibling FAQ entries can
    # overlap the query's common tokens, so serving the dataset match first
    # unconditionally picked the wrong entry (measured 2026-08-27, «اینوتکس
    # امسال چه زمانی» — dataset "programs" at 0.95 beat the correct
    # questions-blend inotex-date at 0.965). On an exact tie the questions
    # match wins: those rows are hand-mapped query→answer pairs, more precise
    # than description-level similarity.
    t1_trusted = best_match and score >= TRUSTED_MATCH_THRESHOLD
    q_trusted = question_match and q_score >= TRUSTED_MATCH_THRESHOLD
    if t1_trusted and (not q_trusted or score > q_score):
        if _names_other_entity(best_match):
            logger.info(f"Entity override: {best_match.get('id')} → {entity_entry.get('id')} (named {sorted(entity_tokens)})")
            return _entity_answer()
        if field_answer is not None and _is_named_entity(best_match):
            logger.info(f"Company field: {entity_entry.get('id')} → {field_answer['field']}")
            return _serve_field_answer()
        return _answer_from_entry(best_match, score, "local", user_query, lang=lang,
                                  visitor=visitor,
                                  conversation_id=conversation_id)

    if q_trusted:
        if _names_other_entity(question_match):
            logger.info(f"Entity override: {question_match.get('id')} → {entity_entry.get('id')} (named {sorted(entity_tokens)})")
            return _entity_answer()
        if field_answer is not None and _is_named_entity(question_match):
            logger.info(f"Company field: {entity_entry.get('id')} → {field_answer['field']}")
            return _serve_field_answer()
        return _answer_from_entry(question_match, q_score, "local_questions", user_query,
                                  lang=lang, visitor=visitor,
                                  conversation_id=conversation_id)

    # Entity rescue (the دکیو case above): no local tier qualified, but the
    # visitor named exactly one known entity — answer from that entity's own
    # entry instead of paying for an LLM call just because generic similarity
    # was mediocre.
    if entity_entry is not None:
        logger.info(f"Entity rescue → {entity_entry.get('id')} (named {sorted(entity_tokens)})")
        return _entity_answer()

    # Tier 1.5 — this installation's own trained intent classifier (logistic
    # regression over local embeddings, retrained on every dataset edit). A
    # confident verdict answers here with zero external calls; anything less
    # falls through to the AI classifier exactly as before.
    intent_entry, intent_prob = classify_intent_local(match_query)
    if not unknown_tokens and intent_entry and intent_prob >= INTENT_TRUST_THRESHOLD:
        logger.info(f"Local intent classifier → {intent_entry.get('id')} (p={intent_prob:.2f})")
        return _answer_from_entry(intent_entry, intent_prob, "local_intent", user_query,
                                  lang=lang, visitor=visitor,
                                  conversation_id=conversation_id)

    # Tier 2 — below the trust bar, the AI classifier decides intent. We do NOT
    # serve the low-confidence local match here: that is exactly what produced
    # confident-but-wrong answers (e.g. a cost question returning an unrelated entry).
    is_openai_enabled = get_setting('openai_enabled', 'true') == 'true'
    if is_openai_enabled:
        logger.info(f"Low confidence local match (tfidf={score:.2f}, questions={q_score:.2f}), asking GPT to classify intent...")
        try:
            # Selection tier — the missing last box of the RAG diagram. Instead
            # of an LLM GUESS from a title list (which is what classify_intent
            # is), the model is shown the records retrieval actually found and
            # must answer with their ids. It CHOOSES; the renderer below writes
            # every visitor-visible string out of the database.
            decision, candidates = None, []
            if unknown_tokens:
                # «تاریخ برگزاری نمایشگاه الکامپ»: a query naming something the
                # whole corpus has never heard of must not be shown candidates
                # at all, or the 2026-08-26 incident reopens through the model
                # instead of through retrieval.
                logger.info("Unknown salient tokens; skipping the selection tier")
            else:
                candidates = [
                    {**entry, "score": float(cand_score)}
                    for entry, cand_score, _signals
                    in find_top_matches(match_query, k=ANSWER_TOPK)
                ]
                # Follow-up gate. «و آن یکی؟» after a list is ABOUT the list, so
                # what was just offered goes in front of the model. Prepending
                # unconditionally would put stale companies at the top on every
                # turn inside the window, including the turn where the visitor
                # changed the subject — so it takes a word that points back at
                # the list. The old test was the message's token COUNT, which
                # 58 of the 60 golden queries pass; see answer.is_followup for
                # the measurement and the rules that replaced it.
                if is_followup(match_query, offer):
                    known = {c["id"] for c in candidates}
                    prior = []
                    for offered_id in offer["ids"][:offer["shown"]]:
                        entry = get_entry(offered_id)
                        if entry is not None and offered_id not in known:
                            known.add(offered_id)
                            prior.append({**entry, "score": 0.0})
                    candidates = (prior + candidates)[:13]

                decision = await select_records(user_query, candidates,
                                                history, lang)
                if decision is not None:
                    # WITHOUT THIS ROW THE TIER IS UNDIAGNOSABLE: the first
                    # wrong answer at the booth has to be explainable from the
                    # log explorer alone.
                    applog.info("retrieval", "conversation.selection.decided",
                                "مدل از میان رکوردهای بازیابی‌شده انتخاب کرد",
                                subcategory=decision["mode"], outcome="ok",
                                provider=decision["provider"],
                                model=decision["model"],
                                tokens_in=decision["tokens"] or None,
                                cost=decision["cost"] or None,
                                metadata={
                                    "candidates": [
                                        [c["id"], round(float(c["score"]), 3)]
                                        for c in candidates],
                                    "mode": decision["mode"],
                                    "chosen": decision["ids"],
                                    "reason": decision["reason"],
                                })

            # The provider billed for the selection call whatever it decided,
            # so every exit below has to carry it into chat_logs. Two of them
            # used to drop it — mode "none", and the fall-through where the
            # named record could not be resolved — and the admin dashboard
            # sums these columns, so it under-reported the day's real spend.
            sel_tokens = decision["tokens"] if decision is not None else 0
            sel_cost = decision["cost"] if decision is not None else 0.0

            if decision is not None and decision["mode"] == "answer":
                chosen = get_entry(decision["ids"][0])
                if chosen is not None:
                    chosen_score = next(
                        (float(c["score"]) for c in candidates
                         if c["id"] == decision["ids"][0]), 0.0)
                    return _answer_from_entry(
                        chosen, chosen_score, "ai_selected", user_query,
                        decision["tokens"], decision["cost"],
                        lang=lang, visitor=visitor,
                        conversation_id=conversation_id)

            if decision is not None and decision["mode"] == "options":
                chosen_entries = [e for e in (get_entry(i) for i in decision["ids"]) if e]
                if len(chosen_entries) >= 2:
                    opt_text, opt_list, opt_offer = render_options(
                        chosen_entries, decision["lead"], lang,
                        start_index=1, total=len(chosen_entries),
                        filter_label="")
                    top_score = max((float(c["score"]) for c in candidates), default=0.0)
                    _log_turn(user_query, opt_text, "text", "ai_options",
                              top_score, decision["tokens"], decision["cost"],
                              conversation_id=conversation_id,
                              offer_state=opt_offer)
                    applog.info("chat", "conversation.answer.served",
                                "پاسخ به بازدیدکننده داده شد",
                                subcategory="ai_options", outcome="ok",
                                tokens_in=decision["tokens"] or None,
                                cost=decision["cost"] or None,
                                metadata={"tier": "ai_options",
                                          "score": round(top_score, 3),
                                          "response_type": "text",
                                          "shown": len(opt_list)})
                    # No video on an options turn: playing one booth clip while
                    # offering five companies shows the visitor a company they
                    # did not choose. The clip plays one turn later, on the pick.
                    return ChatResponse(
                        type="text", text=opt_text, video_url=None,
                        confidence=top_score, source="ai_options",
                        options=opt_list)

            cls_tokens = cls_cost = 0
            if decision is None or decision["mode"] != "none":
                # No usable decision — the provider was down, answered in prose,
                # truncated, or named nothing we proposed. Fall through to
                # today's untouched classifier path.
                classified_entry, cls_tokens, cls_cost = await classify_intent(match_query)

                if classified_entry:
                    logger.info(f"GPT classified → {classified_entry.get('id')}")
                    return _answer_from_entry(
                        classified_entry, score, "openai_classified", user_query,
                        sel_tokens + cls_tokens, sel_cost + cls_cost,
                        lang=lang, visitor=visitor,
                        conversation_id=conversation_id,
                    )
            # mode "none" skips classify_intent entirely: a model that just read
            # eight candidates and said "none of these" has already answered
            # that question, and a third sequential provider call per visitor
            # message is a queue every other visitor waits behind.

            # Out of domain → a real generated answer instead of a weak local
            # match that was just rejected. This is the ONE place the model
            # still writes what a visitor reads, so it is verified.
            gpt_response, tokens, cost = await get_openai_response(user_query, lang=lang)
            grounded, why = generated_prose_is_grounded(gpt_response, lang)
            if not grounded:
                refusal = scope.refusal_text(lang)
                logger.warning(f"[prose] generated answer rejected: {why}")
                applog.warning("llm", "generation.prose.rejected",
                               "پاسخ تولیدشده به‌دلیل اطلاعات تأییدنشده جایگزین شد",
                               subcategory="chat", outcome="rejected",
                               metadata={"reason": why, "lang": lang})
                _log_turn(user_query, refusal, "text", "refuse", score,
                          sel_tokens + cls_tokens + tokens,
                          sel_cost + cls_cost + cost,
                          conversation_id=conversation_id)
                # 200, not 503: we DID answer — we said we cannot answer this.
                return ChatResponse(
                    type="text", text=refusal, video_url=None,
                    confidence=score, source="refuse",
                )
            _log_turn(user_query, gpt_response, "text", "openai", score,
                      sel_tokens + cls_tokens + tokens,
                      sel_cost + cls_cost + cost,
                      conversation_id=conversation_id)
            return ChatResponse(
                type="text", text=gpt_response, video_url=None,
                confidence=score, source="openai",
            )
        except Exception as e:
            logger.error(f"Error in classification flow: {type(e).__name__}: {e}")
            # AI unavailable — fall back to a *strong* local match only, else 503.
            if score >= LOCAL_FALLBACK_THRESHOLD and best_match:
                return _answer_from_entry(best_match, score, "local", user_query, lang=lang,
                                  visitor=visitor,
                                  conversation_id=conversation_id)
            if question_match and q_score >= QUESTIONS_FALLBACK_THRESHOLD:
                return _answer_from_entry(question_match, q_score, "local_questions", user_query,
                                  lang=lang, visitor=visitor,
                                  conversation_id=conversation_id)
            # An HONEST 503, and the only one left in this endpoint: the AI
            # tier was asked and it is genuinely down, and no local match was
            # strong enough to stand in for it. static/chat/core.js turns this
            # into "the AI service is unavailable", which is true here.
            # `answered=False` records the question with no answer beside it.
            _log_turn(user_query, "ai_unavailable_no_strong_match", "text",
                      "system", score, conversation_id=conversation_id,
                      answered=False)
            raise HTTPException(status_code=503, detail="AI service unavailable")

    # OpenAI disabled — answer only from a reasonably strong local match.
    if score >= LOCAL_FALLBACK_THRESHOLD and best_match:
        return _answer_from_entry(best_match, score, "local", user_query, lang=lang,
                                  visitor=visitor,
                                  conversation_id=conversation_id)
    if question_match and q_score >= QUESTIONS_FALLBACK_THRESHOLD:
        return _answer_from_entry(question_match, q_score, "local_questions", user_query,
                                  lang=lang, visitor=visitor,
                                  conversation_id=conversation_id)

    # Nothing answered this question. That is NOT an outage, and for a long
    # time this line said it was: it raised 503, and static/chat/core.js turns
    # any 503 into "the AI service is unavailable" — so a visitor who asked
    # something we simply have no record for was told the machine was broken.
    # It was not. We looked, and we have nothing.
    #
    # 200 with a sentence, exactly like the grounding refusal above: we DID
    # answer — we said we cannot answer this. The wording is a setting
    # (app/services/scope.py) so a customer can change it without a deploy.
    no_answer = scope.no_answer_text(lang)
    _log_turn(user_query, no_answer, "text", "no_answer", score,
              conversation_id=conversation_id)
    from app.services.search import report_empty_retrieval
    report_empty_retrieval(user_query, score)
    applog.warning("chat", "conversation.answer.failed",
                   "پاسخ مطمئنی برای پرسش پیدا نشد",
                   outcome="no_match",
                   duration_ms=int((_perf.perf_counter() - _chat_started) * 1000),
                   metadata={"score": round(float(score or 0), 3)})
    return ChatResponse(type="text", text=no_answer, video_url=None,
                        confidence=score, source="no_answer")


@router.post("/api/chat/new-conversation")
async def new_conversation(http_request: Request, response: Response):
    """Forget this browser's conversation. One tap, one plain label.

    A booth kiosk is ONE browser and ONE cookie shared by many people. The
    15-minute offer window SHRINKS that problem; this button CLOSES it — the
    next person's "1" cannot land on the previous person's list.

    Same guards as a chat turn: it is a state-changing visitor endpoint on the
    public surface, so origin and the signed chat token both apply.
    """
    validate_request_origin(http_request)
    validate_chat_token(http_request)
    response.delete_cookie(key="padyar_conv", httponly=True,
                           secure=COOKIE_SECURE, samesite="lax")
    return {"ok": True}


@router.get("/api/chat/conversations",
           dependencies=[Depends(validate_request_origin)])
async def list_my_conversations(visitor_id: str = Depends(visitor_auth.require_visitor)):
    """A signed-in visitor's own past conversations — the hamburger drawer's
    "my chats" list. Anonymous gets a 401 carrying the registration_required
    marker (from require_visitor), which is what opens the signup card.

    Origin-checked like the other per-visitor endpoints even though it only
    reads: this is one visitor's private transcript list, not public data.
    Declared via `dependencies=` (not an in-body call) so it shows up in the
    dependency graph — tests/test_visitor_auth_otp.py walks every route that
    requires a visitor session and asserts each one also checks origin.
    """
    return {"conversations": conversations.list_conversations_for_visitor(visitor_id)}


@router.get("/api/chat/conversations/{conversation_id}",
           dependencies=[Depends(validate_request_origin)])
async def get_my_conversation(conversation_id: str, response: Response,
                              visitor_id: str = Depends(visitor_auth.require_visitor)):
    """One of the visitor's own conversations, replayed — "click to reopen".

    Ownership is checked in the service layer against the SESSION's visitor
    id, never the id in the URL: a signed-in visitor guessing another
    conversation's id gets the same 404 as a conversation that never existed.

    Opening a conversation also makes it the ACTIVE one: the padyar_conv
    cookie is rebound to it, the same cookie /chat itself sets. A message
    typed right after clicking a history item continues THAT thread — /chat's
    own continuable_conversation_id() already lets an owned conversation keep
    going, so nothing there needs to change for this to work.
    """
    result = conversations.get_conversation_for_visitor(conversation_id, visitor_id)
    if not result:
        raise HTTPException(status_code=404, detail="گفتگویی یافت نشد.")
    response.set_cookie(
        key="padyar_conv", value=conversation_id,
        httponly=True, secure=COOKIE_SECURE, samesite="lax",
        max_age=CONV_COOKIE_MAX_AGE,
    )
    return result


@router.delete("/api/chat/conversations/{conversation_id}",
              dependencies=[Depends(validate_request_origin)])
async def delete_my_conversation(conversation_id: str,
                                 visitor_id: str = Depends(visitor_auth.require_visitor)):
    """Let a visitor delete one of their own past conversations.

    Same ownership rule as the read above: the id in the URL only ever
    matches something to delete when it also belongs to this session's
    visitor_id.
    """
    if not conversations.delete_conversation_for_visitor(conversation_id, visitor_id):
        raise HTTPException(status_code=404, detail="گفتگویی یافت نشد.")
    return {"ok": True}


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
