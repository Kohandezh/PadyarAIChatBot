"""The routing engine: task → ordered targets → retry → failover.

Flow (phase spec, locked):

    Padyar AI Wrapper
           ↓ task (chat | classify)
      load route (priority order)
           ↓
      eligibility: route enabled · target enabled · provider enabled ·
                   provider configured (has secret) · circuit permits ·
                   kill switch off
           ↓
      adapter.invoke
           ↓
      success → record + return
      failure → retry THIS target while retryable (bounded, backoff)
             → then fail over to the next target IFF failover_eligible
      all exhausted / nothing eligible → AIError(all_routes_failed)

LOCKED PRINCIPLES ENFORCED HERE
-------------------------------
* retryable != failover_eligible (errors.py carries both flags):
  auth failure — no same-provider retry, YES failover; context limit —
  neither; rate limit — both; invalid_request / content_rejected /
  structured_output_failed — NEVER fail over (our bug / our content: cycling
  would turn one visible error into nine invisible ones and burn quota).
* One request never revisits a target (attempted-set loop protection —
  A → B → A is impossible even with admin reordering mid-flight).
* Per-task retry defaults preserve today's observed behaviour
  (00-current-state-audit.md §3.4): chat = 2 attempts, classify = 1.
* Per-attempt observability rows (llm.request.*) + ONE usage row per wrapper
  call with tokens/cost summed across attempts (all attempts were billed).
* No database transaction is ever held across a provider HTTP call: config
  and circuit state are read/committed before, results written after.
"""
import asyncio
import os
import random

from . import circuit, errors as ai_errors, pricing, store
from .adapters import adapter_for
from .request import AIRequest, AIResponse

# Per-task retry defaults (target.max_attempts overrides).
TASK_MAX_ATTEMPTS = {"chat": 2, "classify": 1}
TASK_TIMEOUT_S = {"chat": 45.0, "classify": 45.0}
TASK_MAX_OUTPUT_TOKENS = {"chat": 555, "classify": 1500}
RETRY_BACKOFF_S = 1.5          # fixed wait today; jitter added per attempt
RETRY_BACKOFF_JITTER_S = 0.5

# Ceiling on simultaneous external provider calls per process. Without one,
# every ambiguous question under a load spike fans out its own classifier +
# generation call; the burst then depends entirely on the provider's 429s.
# Requests past the cap queue here instead (nginx's 120s window covers the
# wait). 0 disables the gate.
AI_MAX_CONCURRENCY = max(0, int(os.getenv("AI_MAX_CONCURRENCY", "16")))
_concurrency_gate = asyncio.Semaphore(AI_MAX_CONCURRENCY) if AI_MAX_CONCURRENCY else None


def _kill_switch_on() -> bool:
    """The external-AI kill switch. Deliberately the LEGACY `openai_enabled`
    key — building on it rather than introducing a competing flag
    (audit §2). False = external AI allowed."""
    from app.db.queries import get_setting
    return (get_setting("openai_enabled", "true") or "true").lower() != "true"


def _target_eligible(t: dict, attempted: set) -> tuple:
    if t["id"] in attempted:
        return False, "already attempted (loop protection)"
    if not t["target_enabled"]:
        return False, "target disabled"
    if not t["provider_enabled"]:
        return False, "provider disabled"
    if not t["has_secret"]:
        return False, "no API key configured"
    # The circuit is checked separately by the caller: once before the first
    # attempt on this target, and again before every same-target RETRY (the
    # state can change mid-request — our own failure may have just tripped it).
    return True, ""


async def execute_request(req: AIRequest) -> AIResponse:
    from app.services import applog
    started = asyncio.get_event_loop().time()

    if _kill_switch_on():
        raise ai_errors.AIError(
            code="provider_unavailable",
            provider_detail="external AI is disabled (kill switch)",
            request_id=req.request_id, correlation_id=req.correlation_id)

    task = req.task
    # Resolve task-level defaults into the request.
    if not req.max_output_tokens:
        req.max_output_tokens = TASK_MAX_OUTPUT_TOKENS.get(task, 1024)
    if req.reasoning == "default":
        # CLASSIFICATION defaults reasoning OFF (matrix §5): five providers
        # think by default and bill it as output tokens.
        req.reasoning = "off" if task == "classify" else "default"
    if not req.request_id:
        req.request_id = applog.current_request_id() or applog.new_id()
    if not req.correlation_id:
        req.correlation_id = applog.current_correlation_id()

    targets = store.ordered_targets(task)
    # The caller's own cap, captured ONCE. Resolving each target's timeout
    # into `req.timeout_s` (as this used to do) let target #1's override leak
    # into every later target of the same request: a 5 s primary silently
    # became a 5 s cap on a 45 s backup.
    caller_timeout_s = req.timeout_s
    attempted: set = set()
    route_failures: list = []
    attempt_no = 0
    failovers = 0
    totals = {"tokens_in": 0, "tokens_out": 0, "tokens_cached": 0,
              "cost": 0.0, "currency": "", "pricing_eff": None}
    any_currency_known = False
    last_error: ai_errors.AIError | None = None
    last_provider = ("", "", "")     # type, instance, model of the last attempt

    applog.info("llm", "llm.request.started", "درخواست هوش مصنوعی آغاز شد",
                subcategory=task, route=task,
                metadata={"targets": len(targets), "request_id": req.request_id})

    for t in targets:
        eligible, why = _target_eligible(t, attempted)
        if not eligible:
            if why != "already attempted (loop protection)":
                applog.info("llm", "llm.route.skipped",
                            f"هدف مسیر رد شد: {why}", subcategory=task,
                            provider=t["provider_type"], model=t["model_id"],
                            metadata={"reason": why,
                                      "provider_instance_id": t["provider_instance_id"]})
            continue
        allowed, circuit_why = circuit.allows(t["provider_instance_id"])
        if not allowed:
            applog.info("llm", "llm.route.skipped",
                        "هدف مسیر رد شد: مدار باز است", subcategory=task,
                        provider=t["provider_type"], model=t["model_id"],
                        error_code="circuit_open",
                        metadata={"reason": circuit_why,
                                  "provider_instance_id": t["provider_instance_id"]})
            continue

        attempted.add(t["id"])
        rt = store.runtime_for(t["provider_instance_id"])
        if rt is None:
            continue
        adapter = adapter_for(t["provider_type"])
        max_attempts = t.get("max_attempts") or TASK_MAX_ATTEMPTS.get(task, 1)
        timeout_s = (t.get("timeout_s") or caller_timeout_s
                     or TASK_TIMEOUT_S.get(task, 45.0))
        req.timeout_s = timeout_s

        for attempt in range(1, max_attempts + 1):
            attempt_no += 1
            applog.info("llm", "llm.route.selected", "مسیر انتخاب شد",
                        subcategory=task, provider=t["provider_type"],
                        model=t["model_id"],
                        route=f"{task}#{t['priority']}",
                        metadata={"priority": t["priority"], "attempt": attempt,
                                  "provider_instance_id": t["provider_instance_id"],
                                  "provider_name": t["display_name"]})
            t0 = asyncio.get_event_loop().time()
            try:
                if _concurrency_gate is None:
                    resp = await adapter.invoke(rt, t["model_id"], req)
                else:
                    async with _concurrency_gate:
                        resp = await adapter.invoke(rt, t["model_id"], req)
                # An EMPTY chat answer is a failure, not a success. gpt-5-nano
                # once spent its whole budget on hidden reasoning and returned
                # content="" with finish_reason=length; adapters coerce a null
                # content to "", so without this the visitor gets HTTP 200 and
                # a blank bubble — no error, no failover, no log. Normalized as
                # `invalid_response`: retryable AND failover-eligible.
                # CLASSIFY is deliberately excluded — an empty classification
                # is meaningful there (it is the out_of_domain branch, a real
                # success path in app/routers/chat.py).
                if task == "chat" and not (resp.content or "").strip():
                    raise ai_errors.AIError(
                        code=ai_errors.INVALID_RESPONSE,
                        provider_request_id=resp.provider_request_id,
                        provider_detail=("provider returned empty content "
                                         f"(finish_reason={resp.finish_reason})"))
            except ai_errors.AIError as e:
                e.provider_type = e.provider_type or t["provider_type"]
                e.provider_instance_id = e.provider_instance_id or t["provider_instance_id"]
                e.model = e.model or t["model_id"]
                e.attempts = attempt
                latency = int((asyncio.get_event_loop().time() - t0) * 1000)
                circuit.record_failure(t["provider_instance_id"], e)
                applog.warning("llm", "llm.request.failed",
                               "فراخوانی سرویس‌دهنده ناموفق بود",
                               subcategory=task, provider=t["provider_type"],
                               model=t["model_id"], retry_count=attempt - 1,
                               error_type="AIError", error_code=e.code,
                               duration_ms=latency, outcome="failed",
                               **_safe_error_fields(e))
                route_failures.append({
                    "provider_instance_id": t["provider_instance_id"],
                    "provider_type": t["provider_type"],
                    "model": t["model_id"],
                    "error_code": e.code,
                    "status_code": e.status_code,
                    "detail": e.redacted_detail(),
                })
                last_error = e
                last_provider = (t["provider_type"], t["provider_instance_id"], t["model_id"])
                if e.retryable and attempt < max_attempts:
                    # The failure we just recorded may have TRIPPED this
                    # instance's breaker (threshold reached, or a burst from
                    # other workers). Retrying into an open circuit is exactly
                    # the hammering the breaker exists to stop, so re-ask
                    # before spending the second attempt.
                    still_ok, why_now = circuit.allows(t["provider_instance_id"])
                    if not still_ok:
                        applog.info("llm", "llm.route.skipped",
                                    "تلاش دوباره لغو شد: مدار باز شد",
                                    subcategory=task, provider=t["provider_type"],
                                    model=t["model_id"], error_code="circuit_open",
                                    metadata={"reason": why_now, "attempt": attempt,
                                              "provider_instance_id": t["provider_instance_id"]})
                        break
                    wait = RETRY_BACKOFF_S * attempt + random.uniform(0, RETRY_BACKOFF_JITTER_S)
                    applog.info("llm", "llm.retry.triggered",
                                "تلاش دوباره برای همان هدف", subcategory=task,
                                provider=t["provider_type"], model=t["model_id"],
                                retry_count=attempt, duration_ms=int(wait * 1000),
                                metadata={"wait_s": round(wait, 2),
                                          "error_code": e.code})
                    await asyncio.sleep(wait)
                    continue
                break                        # target exhausted → failover decision

            # ── Success ────────────────────────────────────────────────
            latency = int((asyncio.get_event_loop().time() - t0) * 1000)
            resp.latency_ms = latency
            circuit.record_success(t["provider_instance_id"])
            cost, currency = pricing.estimate(
                t["provider_type"], t["model_id"],
                resp.tokens_input, resp.tokens_output, resp.cached_tokens)
            pricing_row = store.lookup_pricing(t["provider_type"], t["model_id"])
            resp.cost = cost
            resp.currency = currency
            resp.route_priority = t["priority"]
            resp.attempt_count = attempt_no
            resp.failover_count = failovers
            # Correlation is the ENGINE's guarantee, not each adapter's: an
            # adapter that forgets to copy the ids must not be able to break
            # the trace that ties a reply to its request.
            resp.request_id = req.request_id
            resp.correlation_id = req.correlation_id

            if resp.tokens_input:
                totals["tokens_in"] += resp.tokens_input
            if resp.tokens_output:
                totals["tokens_out"] += resp.tokens_output
            if resp.cached_tokens:
                totals["tokens_cached"] += resp.cached_tokens
            if cost is not None:
                totals["cost"] += cost
                totals["currency"] = currency
                any_currency_known = True
            if pricing_row:
                totals["pricing_eff"] = pricing_row["effective_from"]

            applog.info("llm", "llm.request.completed", "پاسخ سرویس‌دهنده دریافت شد",
                        subcategory=task, provider=t["provider_type"],
                        model=t["model_id"], duration_ms=latency,
                        tokens_in=resp.tokens_input, tokens_out=resp.tokens_output,
                        cost=cost, retry_count=attempt - 1,
                        route=f"{task}#{t['priority']}", outcome="ok",
                        metadata={"provider_instance_id": t["provider_instance_id"],
                                  "provider_name": t["display_name"],
                                  "cached_tokens": resp.cached_tokens,
                                  "reasoning_tokens": resp.reasoning_tokens,
                                  "finish_reason": resp.finish_reason,
                                  "failovers": failovers,
                                  "provider_request_id": resp.provider_request_id[:80]})
            _record_usage(req, "success", t, attempt_no, failovers, totals, latency,
                          error_code="")
            return resp

        # Target exhausted. Fail over only when the failure says another
        # provider could actually help.
        if last_error is not None and last_error.failover_eligible:
            failovers += 1
            applog.warning("llm", "llm.failover.triggered",
                           "جابه‌جایی به سرویس‌دهندهٔ بعدی مسیر",
                           subcategory=task, provider=t["provider_type"],
                           model=t["model_id"], error_code=last_error.code,
                           metadata={"failover": failovers,
                                     "from_instance": t["provider_instance_id"]})
            continue
        # Not failover-eligible: our request/content is the problem — stop.
        applog.error("llm", "llm.provider.failed",
                     "خطای غیرقابل جابه‌جایی — مسیر متوقف شد",
                     subcategory=task, provider=t["provider_type"],
                     model=t["model_id"], error_code=last_error.code if last_error else "",
                     **(_safe_error_fields(last_error) if last_error else {}))
        break

    total_latency = int((asyncio.get_event_loop().time() - started) * 1000)
    if last_error is None:
        last_error = ai_errors.AIError(
            code=ai_errors.ALL_ROUTES_FAILED,
            provider_detail="no eligible route target",
            request_id=req.request_id, correlation_id=req.correlation_id)
    else:
        # Failover-eligible failures across every target → all_routes_failed.
        # A non-failover-eligible failure is OUR problem (bad request,
        # rejected content, oversized prompt): surfacing the ORIGINAL code is
        # what stops operator-visible bugs from hiding behind provider cycling.
        final_code = (ai_errors.ALL_ROUTES_FAILED
                      if last_error.failover_eligible else last_error.code)
        last_error = ai_errors.AIError(
            code=final_code,
            provider_type=last_provider[0],
            provider_instance_id=last_provider[1],
            model=last_provider[2],
            status_code=last_error.status_code,
            provider_detail=last_error.provider_detail,
            provider_request_id=last_error.provider_request_id,
            attempts=attempt_no,
            request_id=req.request_id,
            correlation_id=req.correlation_id,
            route_failures=route_failures)
    _record_usage(req, "failed", {"provider_type": last_provider[0],
                                  "provider_instance_id": last_provider[1],
                                  "model_id": last_provider[2]},
                  attempt_no, failovers, totals, total_latency,
                  error_code=last_error.code)
    applog.error("llm", "llm.request.failed", "هیچ سرویس‌دهنده‌ای پاسخ نداد",
                 subcategory=task, provider=last_provider[0], model=last_provider[2],
                 duration_ms=total_latency, retry_count=attempt_no,
                 error_type="AIError", error_code=last_error.code, outcome="failed",
                 **_safe_error_fields(last_error))
    raise last_error


def _safe_error_fields(e: ai_errors.AIError) -> dict:
    """applog kwargs for an AIError — provider text only via redaction."""
    return {
        "metadata": {"provider_instance_id": e.provider_instance_id,
                     "status_code": e.status_code,
                     "provider_request_id": (e.provider_request_id or "")[:80],
                     "attempts": e.attempts,
                     "retryable": e.retryable,
                     "failover_eligible": e.failover_eligible,
                     "provider_error": e.redacted_detail()},
    }


def _record_usage(req: AIRequest, status: str, t: dict, attempts: int,
                  failovers: int, totals: dict, latency_ms: int,
                  error_code: str) -> None:
    store.record_usage({
        "task": req.task, "status": status,
        "provider_type": t.get("provider_type", ""),
        "provider_instance_id": t.get("provider_instance_id", ""),
        "model": t.get("model_id", ""),
        "attempts": attempts, "failovers": failovers,
        "tokens_in": totals.get("tokens_in") or None,
        "tokens_out": totals.get("tokens_out") or None,
        "tokens_cached": totals.get("tokens_cached") or None,
        "tokens_total": (totals.get("tokens_in") or 0) + (totals.get("tokens_out") or 0) or None,
        "latency_ms": latency_ms,
        "cost": totals.get("cost") or None,
        "currency": totals.get("currency", ""),
        "pricing_effective_from": totals.get("pricing_eff"),
        "error_code": error_code,
        "request_id": req.request_id,
        "correlation_id": req.correlation_id,
        "metadata": {"reasoning": req.reasoning},
    })
