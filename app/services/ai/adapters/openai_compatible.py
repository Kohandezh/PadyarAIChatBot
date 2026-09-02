"""Generic OpenAI-compatible Chat Completions adapter — the BASE transport.

This is the wire shape third parties mean by "OpenAI-compatible":
`POST {base}/chat/completions`, `Authorization: Bearer`, `messages[]`,
`choices[].message.content`, `usage.prompt_tokens`.

It is deliberately NOT the OpenAI provider adapter: OpenAI itself now
recommends the Responses API, whose wire shape differs from this one in every
dimension (`input` vs `messages`, `output[]` vs `choices[]`, `input_tokens`
vs `prompt_tokens`) — see research/openai.md. One code path cannot serve both.

Padyar's EXISTING configured provider (GapGPT proxy) is an instance of this
adapter, which is what the legacy-config import creates.

Six compatible providers (Z.AI, Kimi, DeepSeek, Qwen, xAI, Mistral) subclass
this and override only their documented divergences — pinned sampling
parameters, thinking controls, cache-token vocabularies, error envelopes and
model catalogs. Where the docs say a parameter is rejected (e.g. Kimi pins
temperature on K-series, DeepSeek rejects it while thinking is on) the
subclass's sampling_policy() says so and the parameter is dropped here —
never sent and discovered by a 400.
"""
from ..errors import AIError, INVALID_REQUEST
from ..request import (
    AIRequest, AIResponse, FINISH_STOP, RESPONSE_JSON_OBJECT,
)
from .base import (
    BaseAdapter, OPENAI_FINISH_MAP, ProviderMetadata, ProviderRuntime,
)

# Reasoning preference → body fragment, per control style. Subclasses that
# support a reasoning parameter override `apply_reasoning_body`.


class OpenAICompatibleAdapter(BaseAdapter):
    """Chat Completions over a configurable base URL."""

    PROVIDER_TYPE = "openai_compatible"

    # subclasses override these
    DEFAULT_BASE_URL = ""                    # generic adapter has none — it is configured
    FINISH_MAP = OPENAI_FINISH_MAP
    SUPPORTS_DISCOVERY = True

    def metadata(self) -> ProviderMetadata:
        return ProviderMetadata(
            type_key=self.PROVIDER_TYPE,
            display_name="سرویس سازگار با OpenAI",
            docs_url="https://platform.openai.com/docs/api-reference/chat",
            native=False,
            supports_discovery=self.SUPPORTS_DISCOVERY,
            note_fa="هر سرویس‌دهنده‌ای که قرارداد Chat Completions را پیاده کند (گیت‌وی سازمانی، vLLM، LiteLLM و…).",
        )

    def configuration_schema(self) -> list:
        from .base import ConfigField
        return [
            ConfigField("base_url", "نشانی پایه (Base URL)", type_="url", required=True,
                        help_fa="مثال: https://api.gapgpt.app/v1 — باید /chat/completions را سرو کند."),
            ConfigField("api_key", "کلید API", type_="password",
                        help_fa="فقط ذخیره می‌شود؛ هرگز نمایش داده نمی‌شود."),
        ]
        # DELIBERATELY ABSENT: an "extra headers" field. Free-form header
        # names/values from the Admin form are a header-injection surface and
        # a second place credentials could be stored unencrypted. The only
        # header this adapter sends is the Bearer built from the secret
        # column. Add one only with normalization + secret storage designed in.
        # Also absent: "api_version" — this wire protocol has no version
        # header, so the field was config the admin could fill in with no
        # effect anywhere in the code.

    def resolve_base(self, rt: ProviderRuntime) -> str:
        base = (rt.config.get("base_url") or self.DEFAULT_BASE_URL or "").rstrip("/")
        if not base:
            raise AIError(code=INVALID_REQUEST, provider_type=rt.provider_type,
                          provider_instance_id=rt.instance_id,
                          provider_detail="no base_url configured")
        return base

    def endpoint_url(self, rt: ProviderRuntime) -> str:
        return self.chat_url(rt)

    def chat_url(self, rt: ProviderRuntime) -> str:
        return f"{self.resolve_base(rt)}/chat/completions"

    def models_url(self, rt: ProviderRuntime) -> str:
        return f"{self.resolve_base(rt)}/models"

    def auth_headers(self, rt: ProviderRuntime) -> dict:
        h = {"Content-Type": "application/json"}
        if rt.secret:
            h["Authorization"] = f"Bearer {rt.secret}"
        return h

    # ── Request building ────────────────────────────────────────────────

    def build_messages(self, rt: ProviderRuntime, model_id: str, req: AIRequest) -> list:
        """messages[] with the system prompt as role:"system" at index 0.

        The neutral request keeps the system prompt separate; THIS surface is
        the one that wants it as message zero, so the join happens here, at
        the edge, per adapter — exactly what the capability matrix (§3)
        requires. Compatible providers without separate handling accept it.
        """
        msgs = []
        if req.system_prompt:
            msgs.append({"role": "system", "content": req.system_prompt})
        for m in req.messages:
            msgs.append({"role": m.role, "content": m.content})
        return msgs

    def build_body(self, rt: ProviderRuntime, model_id: str, req: AIRequest) -> dict:
        body = {
            "model": model_id,
            "messages": self.build_messages(rt, model_id, req),
            "max_tokens": req.max_output_tokens or 1024,
            "stream": False,
        }
        sampling = self.sampling_policy(model_id)
        if req.temperature is not None and sampling.get("temperature", True):
            body["temperature"] = req.temperature
        if req.top_p is not None and sampling.get("top_p", True):
            body["top_p"] = req.top_p
        if req.response_format == RESPONSE_JSON_OBJECT and self.supports_json_object(model_id):
            body["response_format"] = {"type": "json_object"}
        body.update(self.apply_reasoning_body(rt, model_id, req))
        return body

    def apply_reasoning_body(self, rt: ProviderRuntime, model_id: str,
                             req: AIRequest) -> dict:
        """Provider-specific reasoning/thinking controls. Default: none —
        an unknown compatible server must not receive undocumented fields.

        Opt-in per instance via config `reasoning_param: "enable_thinking"`
        (the vLLM/SGLang switch behind chat_template_kwargs). Verified live
        on RMG Pilot (Rayen) 2026-09-01: reasoning_effort is rejected by
        every model there (litellm maps them to plain openai), while
        chat_template_kwargs.enable_thinking is accepted and measurably
        changes output length. "off" sends the explicit False so a backend
        whose default is thinking-on can be quieted."""
        if (rt.config or {}).get("reasoning_param") != "enable_thinking":
            return {}
        if req.reasoning in ("low", "medium", "high"):
            return {"chat_template_kwargs": {"enable_thinking": True}}
        if req.reasoning == "off":
            return {"chat_template_kwargs": {"enable_thinking": False}}
        return {}

    # ── Invoke ──────────────────────────────────────────────────────────

    async def invoke(self, rt: ProviderRuntime, model_id: str,
                     req: AIRequest) -> AIResponse:
        status, body, headers = await self.http(
            rt, "POST", self.chat_url(rt),
            headers=self.auth_headers(rt),
            body=self.build_body(rt, model_id, req),
            timeout_s=req.timeout_s)
        if status != 200:
            raise self.http_error(rt, status, body, self._request_id(headers))

        choice = ((body or {}).get("choices") or [{}])[0]
        message = choice.get("message") or {}
        content = message.get("content")
        content = "" if content is None else str(content)
        self.check_finish_reason(rt, choice.get("finish_reason"), body)

        usage = self.extract_usage(body)
        tokens_in = usage.get("tokens_in")
        tokens_out = usage.get("tokens_out")
        return AIResponse(
            content=content,
            finish_reason=self.FINISH_MAP.get(str(choice.get("finish_reason")), FINISH_STOP)
            if choice.get("finish_reason") else FINISH_STOP,
            task=req.task,
            provider_type=rt.provider_type,
            provider_instance_id=rt.instance_id,
            provider_name=rt.display_name,
            model=model_id,
            tokens_input=tokens_in,
            tokens_output=tokens_out,
            tokens_total=usage.get("tokens_total")
            or ((tokens_in or 0) + (tokens_out or 0) if tokens_in is not None or tokens_out is not None else None),
            cached_tokens=usage.get("cached"),
            reasoning_tokens=usage.get("reasoning"),
            latency_ms=0,
            provider_request_id=self._request_id(headers) or str((body or {}).get("id") or "")[:80],
            request_id=req.request_id,
            correlation_id=req.correlation_id,
        )

    def check_finish_reason(self, rt: ProviderRuntime, finish_reason, body) -> None:
        """Hook for providers with failure-bearing finish_reason values
        (Z.AI: sensitive / model_context_window_exceeded / network_error)."""

    @staticmethod
    def _request_id(headers: dict) -> str:
        for k in ("request-id", "x-request-id", "request_id"):
            if headers.get(k):
                return str(headers[k])[:80]
        return ""

    # ── Discovery / health ──────────────────────────────────────────────

    async def list_models(self, rt: ProviderRuntime) -> list:
        status, body, _h = await self.http(rt, "GET", self.models_url(rt),
                                           headers=self.auth_headers(rt),
                                           timeout_s=15.0)
        if status != 200:
            raise self.http_error(rt, status, body)
        out = []
        for item in (body or {}).get("data") or []:
            mid = item.get("id")
            if not mid:
                continue
            out.append({
                "model_id": str(mid),
                "display_name": str(mid),
                "status": "available",
                "metadata": {k: item[k] for k in ("owned_by", "created", "shutdown_date")
                             if item.get(k) is not None},
            })
        return out

    async def test_connection(self, rt: ProviderRuntime) -> dict:
        import time as _t
        t0 = _t.perf_counter()
        if self.SUPPORTS_DISCOVERY:
            try:
                models = await self.list_models(rt)
                return self.test_result(True, "connected",
                                        f"{len(models)} مدل دریافت شد",
                                        (_t.perf_counter() - t0) * 1000)
            except AIError as e:
                return self.test_result(False, e.code, e.redacted_detail(),
                                        (_t.perf_counter() - t0) * 1000)
        # No discovery endpoint: minimal generation probe.
        probe = AIRequest(task="test", messages=[], system_prompt="",
                          max_output_tokens=1)
        try:
            body = self.build_body(rt, self._probe_model(rt), probe)
            body["messages"] = [{"role": "user", "content": "ping"}]
            status, resp, _h = await self.http(rt, "POST", self.chat_url(rt),
                                               headers=self.auth_headers(rt),
                                               body=body, timeout_s=20.0)
            if status == 200:
                return self.test_result(True, "connected", "پاسخ آزمایشی دریافت شد",
                                        (_t.perf_counter() - t0) * 1000)
            e = self.http_error(rt, status, resp)
            return self.test_result(False, e.code, e.redacted_detail(),
                                    (_t.perf_counter() - t0) * 1000)
        except AIError as e:
            return self.test_result(False, e.code, e.redacted_detail(),
                                    (_t.perf_counter() - t0) * 1000)

    def _probe_model(self, rt: ProviderRuntime) -> str:
        raise NotImplementedError("probe model required when discovery is unsupported")
