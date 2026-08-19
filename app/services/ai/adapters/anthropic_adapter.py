"""Anthropic / Claude — native Messages API adapter.

Research: docs/engineering/ai-providers/research/anthropic.md (2026-08-18).

Facts encoded here (all from the research file, not memory):
  * Auth is `x-api-key` + MANDATORY `anthropic-version: 2023-06-01`. A plain
    Bearer header is a different (Workload Identity) mechanism.
  * `system` is a TOP-LEVEL field. A {"role":"system"} message at index 0 is
    a hard 400. The neutral request keeps them separate, so this adapter is
    the only place they join.
  * `max_tokens` is REQUIRED — no default server-side.
  * temperature/top_p/top_k are REJECTED (400) on Claude 4.7 and later:
    Opus 5, Sonnet 5, Fable 5, Mythos 5, Opus 4.8, Opus 4.7. This adapter
    drops them for those models via sampling_policy().
  * thinking: {"type": ...} — enabled only on 4.5/4.6-era; adaptive on 4.6+;
    cannot be disabled at all on Fable 5 / Mythos 5. Effort lives in
    output_config.effort (low/medium/high/xhigh/max) and Haiku 4.5 lacks it.
  * Usage: `input_tokens` counts ONLY tokens after the last cache breakpoint.
    True input = cache_read + cache_creation + input. There is no
    total_tokens. Getting this wrong under-reports cost by orders of
    magnitude on cached conversations — extract_usage computes it.
  * 529 overloaded_error is a normal "try again" condition.
  * Content is ALWAYS a block array; text = join of type=="text" blocks.
"""
import re

from ..errors import (
    AIError, CONTENT_REJECTED, CONTEXT_LIMIT_EXCEEDED, PROVIDER_UNAVAILABLE,
    QUOTA_EXCEEDED,
)
from ..request import (
    AIRequest, AIResponse, FINISH_CONTENT_FILTER, FINISH_LENGTH,
    FINISH_OTHER, FINISH_STOP, FINISH_TOOL_CALLS,
)
from .base import BaseAdapter, ProviderMetadata, ProviderRuntime

BASE_URL = "https://api.anthropic.com"
API_VERSION = "2023-06-01"

_FINISH_MAP = {
    "end_turn": FINISH_STOP,
    "max_tokens": FINISH_LENGTH,
    "stop_sequence": FINISH_STOP,
    "tool_use": FINISH_TOOL_CALLS,
    "refusal": FINISH_CONTENT_FILTER,
}

_VERSION_RE = re.compile(r"claude-[a-z]+-(\d+)(?:-(\d+))?")


def _generation(model_id: str) -> float:
    """Claude generation as a number: 4.5, 4.6, 4.7, 5.0... Model ids are
    'internally inconsistent' (dateless pins for 4.6+, dated snapshots before)
    so the version is parsed from the family segment, never from the date."""
    m = _VERSION_RE.search(model_id or "")
    if not m:
        return 99.0            # unknown future model: assume newest behavior
    major = int(m.group(1))
    minor = int(m.group(2) or 0)
    return major + minor / 10


class AnthropicAdapter(BaseAdapter):
    PROVIDER_TYPE = "anthropic"

    def metadata(self) -> ProviderMetadata:
        return ProviderMetadata(
            type_key="anthropic", display_name="Anthropic (Claude)",
            docs_url="https://platform.claude.com/docs/en/api/overview",
            native=True, supports_discovery=True,
        )

    def configuration_schema(self) -> list:
        from .base import ConfigField
        return [
            ConfigField("api_key", "کلید API", type_="password", required=True,
                        help_fa="با پیشوند sk-ant- از کنسول Anthropic."),
            ConfigField("base_url", "نشانی پایه (اختیار)", type_="url",
                        default=BASE_URL),
        ]

    def resolve_base(self, rt: ProviderRuntime) -> str:
        return (rt.config.get("base_url") or BASE_URL).rstrip("/")

    def endpoint_url(self, rt: ProviderRuntime) -> str:
        return f"{self.resolve_base(rt)}/v1/messages"

    def auth_headers(self, rt: ProviderRuntime) -> dict:
        # anthropic-version is mandatory — a missing one fails before auth.
        return {
            "Content-Type": "application/json",
            "x-api-key": rt.secret or "",
            "anthropic-version": API_VERSION,
        }

    # ── Capability gating ───────────────────────────────────────────────

    def sampling_policy(self, model_id: str) -> dict:
        # Rejected on 4.7+ — sending them is a 400, not a warning.
        ok = _generation(model_id) < 4.7
        return {"temperature": ok, "top_p": ok}

    def _effort_capable(self, model_id: str) -> bool:
        # output_config.effort: 4.6-generation and later, but NOT Haiku 4.5.
        return _generation(model_id) >= 4.6 and "haiku" not in model_id

    def reasoning_control(self, model_id: str) -> dict:
        gen = _generation(model_id)
        if "fable" in model_id or "mythos" in model_id:
            return {"can_disable": False, "param": "effort"}   # always thinking
        if gen >= 4.7:
            # thinking:{"type":"enabled"|"disabled"} is gone on 4.7+ (the 400
            # says to use adaptive + output_config.effort). Nothing documented
            # turns thinking OFF, so can_disable must not claim it can —
            # "off" is expressed as the lowest documented effort instead.
            return {"can_disable": False, "param": "effort"}
        return {"can_disable": True, "param": "effort" if gen >= 4.6 else "thinking"}

    # ── Invoke ──────────────────────────────────────────────────────────

    async def invoke(self, rt: ProviderRuntime, model_id: str,
                     req: AIRequest) -> AIResponse:
        body = {
            "model": model_id,
            # REQUIRED here and nowhere else; the wrapper resolves it always.
            "max_tokens": req.max_output_tokens or 1024,
            # Only "assistant" and "user" may reach the wire. A role:"system"
            # entry at index 0 is a HARD 400 here, so even if a future caller
            # leaks one past the neutral request it is sent as a user turn
            # rather than killing the call.
            "messages": [{"role": "assistant" if m.role == "assistant" else "user",
                          "content": m.content} for m in req.messages],
        }
        if req.system_prompt:
            body["system"] = req.system_prompt        # top-level, never messages[0]
        sampling = self.sampling_policy(model_id)
        if req.temperature is not None and sampling["temperature"]:
            body["temperature"] = req.temperature
        if req.top_p is not None and sampling["top_p"]:
            body["top_p"] = req.top_p

        gen = _generation(model_id)
        is_always_thinking = "fable" in model_id or "mythos" in model_id
        if req.reasoning == "off":
            if gen < 4.7 and not is_always_thinking:
                # 4.5/4.6-era accept an explicit disable.
                body["thinking"] = {"type": "disabled"}
            elif self._effort_capable(model_id):
                # 4.7+ cannot disable thinking at all. Sending nothing means
                # the documented DEFAULT effort ("high") — i.e. every
                # classification silently pays for maximum hidden reasoning
                # (matrix §5). "low" is the lowest documented effort value and
                # is the closest honest expression of "off" on these models.
                body["output_config"] = {"effort": "low"}
        elif req.reasoning in ("low", "medium", "high"):
            if self._effort_capable(model_id):
                body["output_config"] = {"effort": req.reasoning}
            elif gen < 4.7:
                body["thinking"] = {"type": "enabled"}

        status, resp, headers = await self.http(
            rt, "POST", f"{self.resolve_base(rt)}/v1/messages",
            headers=self.auth_headers(rt), body=body, timeout_s=req.timeout_s)
        if status != 200:
            raise self.http_error(rt, status, resp, self._rid(headers, resp))

        stop_reason = str(resp.get("stop_reason") or "")
        if stop_reason == "model_context_window_exceeded":
            raise AIError(code=CONTEXT_LIMIT_EXCEEDED, provider_type=rt.provider_type,
                          provider_instance_id=rt.instance_id, status_code=200,
                          provider_detail="model_context_window_exceeded")
        if stop_reason == "refusal":
            raise AIError(code=CONTENT_REJECTED, provider_type=rt.provider_type,
                          provider_instance_id=rt.instance_id, status_code=200,
                          provider_detail=str((resp.get("stop_details") or {})
                                              .get("explanation") or "refusal")[:300])

        texts = []
        for block in resp.get("content") or []:
            if block.get("type") == "text" and block.get("text"):
                texts.append(str(block["text"]))
        usage = self.extract_usage(resp)
        return AIResponse(
            content="".join(texts),
            finish_reason=_FINISH_MAP.get(stop_reason, FINISH_OTHER),
            task=req.task,
            provider_type=rt.provider_type,
            provider_instance_id=rt.instance_id,
            provider_name=rt.display_name,
            model=model_id,
            tokens_input=usage.get("tokens_in"),
            tokens_output=usage.get("tokens_out"),
            tokens_total=usage.get("tokens_total"),
            cached_tokens=usage.get("cached"),
            reasoning_tokens=usage.get("reasoning"),
            latency_ms=0,
            provider_request_id=self._rid(headers, resp),
            request_id=req.request_id,
            correlation_id=req.correlation_id,
        )

    def extract_usage(self, body) -> dict:
        u = (body or {}).get("usage") or {}
        # tokens_input is COMPUTED: input alone is post-cache-breakpoint only.
        inp = self._int(u.get("input_tokens"))
        cache_read = self._int(u.get("cache_read_input_tokens")) or 0
        cache_create = self._int(u.get("cache_creation_input_tokens")) or 0
        total_in = None
        if inp is not None or cache_read or cache_create:
            # A wholly-cached prompt can report input_tokens absent/0 while the
            # cache fields carry the real (billable) input.
            total_in = (inp or 0) + cache_read + cache_create
        out = self._int(u.get("output_tokens"))
        out_details = u.get("output_tokens_details") or {}
        total = total_in + out if (total_in is not None and out is not None) else None
        return {
            "tokens_in": total_in,
            "tokens_out": out,
            "tokens_total": total,
            "cached": cache_read or None,
            "reasoning": self._int(out_details.get("thinking_tokens")),
        }

    def error_code_from_body(self, status: int, body) -> str:
        err = (body or {}).get("error") if isinstance(body, dict) else None
        if isinstance(err, dict):
            etype = str(err.get("type") or "")
            if etype == "authentication_error":
                return "authentication_failed"
            if etype == "billing_error":
                return QUOTA_EXCEEDED
            if etype == "permission_error":
                return "permission_denied"
            if etype == "rate_limit_error":
                return "rate_limited"
            if etype == "request_too_large":
                return CONTEXT_LIMIT_EXCEEDED
            if etype == "overloaded_error" or status == 529:
                return PROVIDER_UNAVAILABLE
            if etype == "timeout_error":
                return "timeout"
            if etype == "not_found_error":
                return "model_not_found"
        return ""

    @staticmethod
    def _rid(headers: dict, resp: dict) -> str:
        return str(headers.get("request-id") or (resp or {}).get("request_id") or "")[:80]

    # ── Discovery / health ──────────────────────────────────────────────

    async def list_models(self, rt: ProviderRuntime) -> list:
        """GET /v1/models, following the after_id/has_more cursor (this API
        does NOT use page/next_page)."""
        out, after, seen = [], "", 0
        while True:
            url = f"{self.resolve_base(rt)}/v1/models?limit=100"
            if after:
                url += f"&after_id={after}"
            status, body, _h = await self.http(
                rt, "GET", url, headers=self.auth_headers(rt), timeout_s=15.0)
            if status != 200:
                raise self.http_error(rt, status, body)
            page = (body or {}).get("data") or []
            for item in page:
                mid = item.get("id")
                if not mid:
                    continue
                caps = item.get("capabilities") or {}
                out.append({
                    "model_id": str(mid),
                    "display_name": str(item.get("display_name") or mid),
                    "status": "available",
                    "supports_structured": bool((caps.get("structured_outputs") or {})
                                                .get("supported")),
                    "supports_reasoning": bool((caps.get("thinking") or {})
                                               .get("supported")),
                    "max_output_tokens": self._int(item.get("max_tokens")),
                    "metadata": {"max_input_tokens": item.get("max_input_tokens"),
                                 "created_at": item.get("created_at")},
                })
            seen += len(page)
            if not (body or {}).get("has_more") or not page or seen >= 1000:
                break
            after = str(page[-1].get("id") or "")
            if not after:
                break
        return out

    async def test_connection(self, rt: ProviderRuntime) -> dict:
        import time as _t
        t0 = _t.perf_counter()
        try:
            # ?limit=1: zero tokens, exercises auth + version + permission.
            status, body, _h = await self.http(
                rt, "GET", f"{self.resolve_base(rt)}/v1/models?limit=1",
                headers=self.auth_headers(rt), timeout_s=15.0)
            if status == 200:
                n = len((body or {}).get("data") or [])
                return self.test_result(True, "connected", f"{n} مدل دیده شد",
                                        (_t.perf_counter() - t0) * 1000)
            e = self.http_error(rt, status, body)
            return self.test_result(False, e.code, e.redacted_detail(),
                                    (_t.perf_counter() - t0) * 1000)
        except AIError as e:
            return self.test_result(False, e.code, e.redacted_detail(),
                                    (_t.perf_counter() - t0) * 1000)
