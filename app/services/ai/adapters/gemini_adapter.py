"""Google Gemini — native Interactions API adapter.

Research: docs/engineering/ai-providers/research/gemini.md (2026-08-18).

Why Interactions and not generateContent: generateContent is explicitly
legacy ("no new features land there"), and Interactions is what Google's own
quickstart shows. The two surfaces differ in EVERY structural dimension.

Facts encoded here from the research:
  * Auth is `x-goog-api-key` (NOT Bearer — except in the separate
    OpenAI-compat surface, which we deliberately do not use).
  * `Api-Revision: 2026-05-20` pins the response schema — Google flipped the
    schema under this header in May–June 2026; the pin is cheap insurance.
  * The model goes in the BODY (legacy puts it in the URL path — a constant
    endpoint string cannot express that surface).
  * `system_instruction` is a plain STRING here (a Content object in legacy).
  * temperature/top_p/top_k are DEPRECATED (2026-07-21 notes) in favour of
    thinking_level — this adapter never sends them.
  * `store` defaults to TRUE (server-side conversation persistence) — we send
    false explicitly for stateless operation.
  * HTTP 200 can carry an `error[]` array (platform faults), and a safety
    block is HTTP 200 + a valid body + NO text. Detection rule (from the
    research): HTTP 2xx + zero extracted text ⇒ content_rejected.
  * usage names are total_input_tokens / total_output_tokens / total_tokens
    / total_cached_tokens / total_thought_tokens (the migration guide's
    OpenAI-style names are a documented conflict — parse defensively).
  * Interactions error bodies: {"error": {"code": <snake_case string>}} —
    error.code is a STRING here and an INTEGER on legacy.
  * Model list: `name` is "models/<id>" (strip the prefix), paginated with
    nextPageToken.
  * Reasoning CANNOT be disabled on Gemini 2.5 Pro / 3.x — "off" maps to
    thinking_level "minimal".
"""
from ..errors import (
    AIError, CONTENT_REJECTED, INVALID_RESPONSE, MODEL_NOT_FOUND,
    QUOTA_EXCEEDED,
)
from ..request import (
    AIRequest, AIResponse, FINISH_LENGTH, FINISH_STOP,
    RESPONSE_JSON_OBJECT,
)
from .base import BaseAdapter, ProviderMetadata, ProviderRuntime

BASE_URL = "https://generativelanguage.googleapis.com"
API_REVISION = "2026-05-20"

_ERR_MAP = {
    "authentication": "authentication_failed",
    "permission_denied": "permission_denied",
    "not_found": MODEL_NOT_FOUND,
    "model_not_found": MODEL_NOT_FOUND,
    "rate_limit_exceeded": "rate_limited",
    "quota_exceeded": "quota_exceeded",
    "deadline_exceeded": "timeout",
    "service_unavailable": "provider_unavailable",
    "api_error": "server_error",
    "invalid_request": "invalid_request",
    "failed_precondition": "invalid_request",
    "parameter_unknown": "invalid_request",
}
_BLOCK_CODES = {"safety", "recitation", "language", "prohibited_content", "spii",
                "blocklist", "image_safety", "image_prohibited_content",
                "image_recitation", "image_other", "content_blocked"}


class GeminiAdapter(BaseAdapter):
    PROVIDER_TYPE = "gemini"

    def metadata(self) -> ProviderMetadata:
        return ProviderMetadata(
            type_key="gemini", display_name="Google Gemini",
            docs_url="https://ai.google.dev/api/interactions-api",
            native=True, supports_discovery=True,
        )

    def configuration_schema(self) -> list:
        from .base import ConfigField
        return [
            ConfigField("api_key", "کلید API", type_="password", required=True,
                        help_fa="کلید AI Studio — با هدر x-goog-api-key ارسال می‌شود."),
            ConfigField("base_url", "نشانی پایه (اختیار)", type_="url",
                        default=BASE_URL),
            ConfigField("api_version", "نسخهٔ API", type_="enum",
                        default="v1beta",
                        options=[("v1beta", "v1beta — پیش‌فرض مستندات رسمی"),
                                 ("v1", "v1 — پایدار")]),
        ]

    def resolve_base(self, rt: ProviderRuntime) -> str:
        version = rt.config.get("api_version") or "v1beta"
        return (rt.config.get("base_url") or BASE_URL).rstrip("/") + f"/{version}"

    def endpoint_url(self, rt: ProviderRuntime) -> str:
        return f"{self.resolve_base(rt)}/interactions"

    def auth_headers(self, rt: ProviderRuntime) -> dict:
        return {
            "Content-Type": "application/json",
            "x-goog-api-key": rt.secret or "",
            "Api-Revision": API_REVISION,
        }

    def sampling_policy(self, model_id: str) -> dict:
        # Deprecated 2026-07-21; guidance is thinking_level. Never send.
        return {"temperature": False, "top_p": False}

    def reasoning_control(self, model_id: str) -> dict:
        # Cannot be disabled on 2.5 Pro / 3.x; "off" → "minimal".
        return {"can_disable": False, "param": "thinking_level"}

    # ── Invoke ──────────────────────────────────────────────────────────

    async def invoke(self, rt: ProviderRuntime, model_id: str,
                     req: AIRequest) -> AIResponse:
        body = {
            "model": model_id,
            "store": False,                     # default TRUE server-side
            "input": self._steps(req),
            "generation_config": {
                "max_output_tokens": req.max_output_tokens or 1024,
            },
        }
        if req.system_prompt:
            body["system_instruction"] = req.system_prompt    # plain string
        if req.reasoning == "off":
            body["generation_config"]["thinking_level"] = "minimal"
        elif req.reasoning in ("low", "medium", "high"):
            body["generation_config"]["thinking_level"] = req.reasoning
        if req.response_format == RESPONSE_JSON_OBJECT:
            body["response_format"] = {"type": "text",
                                       "mime_type": "application/json"}

        status, resp, headers = await self.http(
            rt, "POST", f"{self.resolve_base(rt)}/interactions",
            headers=self.auth_headers(rt), body=body, timeout_s=req.timeout_s)
        if status != 200:
            raise self.http_error(rt, status, resp, headers.get("x-goog-request-id", ""))

        # In-band faults: an error[] array can ride on HTTP 200. The reference
        # documents an array, but the same key is a single object on the legacy
        # surface — normalize, and never let a shape surprise escape as a raw
        # AttributeError instead of a classified AIError.
        faults = resp.get("error") or []
        if isinstance(faults, dict):
            faults = [faults]
        elif not isinstance(faults, list):
            faults = []
        for fault in faults:
            if not isinstance(fault, dict):
                continue
            code = str(fault.get("code") or "")
            if code in _BLOCK_CODES:
                raise AIError(code=CONTENT_REJECTED, provider_type=rt.provider_type,
                              provider_instance_id=rt.instance_id, status_code=200,
                              provider_detail=str(fault.get("message") or code)[:300])
            if code:
                mapped = _ERR_MAP.get(code, "server_error")
                raise AIError(code=mapped, provider_type=rt.provider_type,
                              provider_instance_id=rt.instance_id, status_code=200,
                              provider_detail=str(fault.get("message") or code)[:300])

        # status enum: in_progress | requires_action | completed | failed |
        # cancelled | incomplete | budget_exceeded | queued. Only `completed`
        # and `incomplete` are answers; the rest must NOT fall through to the
        # no-text rule below, which would label a spend cap a safety block.
        status_value = str(resp.get("status") or "")
        if status_value == "failed":
            raise AIError(code="provider_unavailable", provider_type=rt.provider_type,
                          provider_instance_id=rt.instance_id, status_code=200,
                          provider_detail="interaction status: failed")
        if status_value == "budget_exceeded":
            raise AIError(code=QUOTA_EXCEEDED, provider_type=rt.provider_type,
                          provider_instance_id=rt.instance_id, status_code=200,
                          provider_detail="interaction status: budget_exceeded")
        if status_value in ("in_progress", "queued", "requires_action", "cancelled"):
            # We send neither background:true nor tools, so none of these can
            # legitimately terminate a call of ours.
            raise AIError(code=INVALID_RESPONSE, provider_type=rt.provider_type,
                          provider_instance_id=rt.instance_id, status_code=200,
                          provider_detail=f"unexpected interaction status: {status_value}")

        texts = self._extract_text(resp)
        finish = FINISH_STOP
        if status_value == "incomplete":        # documented as "hit max_tokens"
            finish = FINISH_LENGTH
        if not texts and finish == FINISH_STOP:
            # The documented safety-block signature: HTTP 200, valid body,
            # no usable text. Distinct from a provider outage and NOT
            # failover-eligible — every provider refuses the same content.
            raise AIError(code=CONTENT_REJECTED, provider_type=rt.provider_type,
                          provider_instance_id=rt.instance_id, status_code=200,
                          provider_detail="no text in completed interaction")

        usage = self.extract_usage(resp)
        return AIResponse(
            content="".join(texts),
            finish_reason=finish,
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
            provider_request_id=str(resp.get("id") or "")[:80],
            request_id=req.request_id,
            correlation_id=req.correlation_id,
        )

    @staticmethod
    def _steps(req: AIRequest) -> list:
        """Neutral messages → typed steps. Role lives in `type` here, not in
        a role key; the assistant turn is type model_output with typed
        content blocks. NEVER "assistant" — that role does not exist."""
        steps = []
        for m in req.messages:
            if m.role == "assistant":
                steps.append({"type": "model_output",
                              "content": [{"type": "text", "text": m.content}]})
            else:
                steps.append({"type": "user_input", "content": m.content})
        return steps

    @staticmethod
    def _extract_text(resp: dict):
        """Walk steps[] for model_output, then content[] for text blocks.
        `output_text` is SDK-only — NOT on the wire."""
        texts = []
        for step in resp.get("steps") or []:
            if step.get("type") != "model_output":
                continue
            for c in step.get("content") or []:
                if c.get("type") == "text" and c.get("text"):
                    texts.append(str(c["text"]))
        return texts

    def extract_usage(self, body) -> dict:
        u = (body or {}).get("usage") or {}
        # Parse defensively: the migration guide shows OpenAI-style names in
        # conflict with the reference's total_* names. Accept either.
        inp = self._int(u.get("total_input_tokens", u.get("prompt_tokens")))
        out = self._int(u.get("total_output_tokens", u.get("completion_tokens")))
        return {
            "tokens_in": inp,
            "tokens_out": out,
            "tokens_total": self._int(u.get("total_tokens")),
            "cached": self._int(u.get("total_cached_tokens")),
            "reasoning": self._int(u.get("total_thought_tokens")),
        }

    def error_code_from_body(self, status: int, body) -> str:
        err = (body or {}).get("error") if isinstance(body, dict) else None
        if isinstance(err, dict):
            code = str(err.get("code") or "")
            if code in _BLOCK_CODES:
                return CONTENT_REJECTED
            if code in _ERR_MAP:
                return _ERR_MAP[code]
        return ""

    # ── Discovery / health ──────────────────────────────────────────────

    async def list_models(self, rt: ProviderRuntime) -> list:
        out, token, pages = [], "", 0
        while pages < 20:
            url = f"{self.resolve_base(rt)}/models?pageSize=1000"
            if token:
                url += f"&pageToken={token}"
            status, body, _h = await self.http(
                rt, "GET", url, headers=self.auth_headers(rt), timeout_s=15.0)
            if status != 200:
                raise self.http_error(rt, status, body)
            for item in (body or {}).get("models") or []:
                name = str(item.get("name") or "")
                mid = name[7:] if name.startswith("models/") else name  # strip prefix
                if not mid:
                    continue
                methods = item.get("supportedGenerationMethods") or []
                out.append({
                    "model_id": mid,
                    "display_name": str(item.get("displayName") or mid),
                    "status": "preview" if "preview" in mid else "available",
                    "context_window": self._int(item.get("inputTokenLimit")),
                    "max_output_tokens": self._int(item.get("outputTokenLimit")),
                    "supports_reasoning": bool(item.get("thinking")),
                    "metadata": {"supported_methods": methods},
                })
            token = (body or {}).get("nextPageToken") or ""
            pages += 1
            if not token:
                break
        return out

    async def test_connection(self, rt: ProviderRuntime) -> dict:
        import time as _t
        t0 = _t.perf_counter()
        try:
            models = await self.list_models(rt)
            return self.test_result(True, "connected",
                                    f"{len(models)} مدل دریافت شد",
                                    (_t.perf_counter() - t0) * 1000)
        except AIError as e:
            return self.test_result(False, e.code, e.redacted_detail(),
                                    (_t.perf_counter() - t0) * 1000)
