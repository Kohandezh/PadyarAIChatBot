"""OpenAI — native Responses API adapter.

Research: docs/engineering/ai-providers/research/openai.md (fetched 2026-08-18).

Why Responses and not Chat Completions: OpenAI's own guidance says Responses
is "recommended for all new projects", and the two wire shapes differ in every
dimension (`input` vs `messages`, `output[]` vs `choices[]`,
`input_tokens` vs `prompt_tokens`). A third-party "OpenAI-compatible" server
implements Chat Completions — that is the openai_compatible adapter, not this.

Facts encoded here from the research:
  * `store` defaults to TRUE server-side — privacy requires sending
    "store": false explicitly.
  * HTTP 200 can carry status:"failed" + a populated error object, or
    status:"incomplete" with incomplete_details.reason — parsing must never
    trust the status code alone.
  * Several 429s are BILLING failures (credit_balance_exhausted,
    *_spend_limit_exceeded) that must not be retried — handled in base's
    error_code_from_body.
  * Refusals arrive as a separate content item type, not as text.
  * reasoning is {effort, summary, context}; no documented "off" — so
    reasoning "off" is expressed by omission, never by a guessed value.
  * Model list exposes shutdown_date — feeds lifecycle status directly.
"""
from ..errors import (
    AIError, CONTENT_REJECTED, INVALID_RESPONSE, SERVER_ERROR,
    PROVIDER_UNAVAILABLE, RATE_LIMITED,
)
from ..request import (
    AIRequest, AIResponse, FINISH_CONTENT_FILTER, FINISH_LENGTH, FINISH_STOP,
    RESPONSE_JSON_OBJECT,
)
from .base import BaseAdapter, ProviderMetadata, ProviderRuntime

BASE_URL = "https://api.openai.com/v1"


class OpenAIAdapter(BaseAdapter):
    PROVIDER_TYPE = "openai"

    def metadata(self) -> ProviderMetadata:
        return ProviderMetadata(
            type_key="openai", display_name="OpenAI",
            docs_url="https://developers.openai.com/api/reference/",
            native=True, supports_discovery=True,
        )

    def configuration_schema(self) -> list:
        from .base import ConfigField
        return [
            ConfigField("api_key", "کلید API", type_="password", required=True),
            ConfigField("base_url", "نشانی پایه (اختیار)", type_="url",
                        default=BASE_URL,
                        help_fa="پیش‌فرض رسمی OpenAI؛ فقط برای گیت‌وی واسط تغییر دهید."),
            ConfigField("organization", "Organization (اختیار)"),
            ConfigField("project", "Project (اختیار)"),
        ]

    def resolve_base(self, rt: ProviderRuntime) -> str:
        return (rt.config.get("base_url") or BASE_URL).rstrip("/")

    def endpoint_url(self, rt: ProviderRuntime) -> str:
        return f"{self.resolve_base(rt)}/responses"

    def auth_headers(self, rt: ProviderRuntime) -> dict:
        h = {"Content-Type": "application/json"}
        if rt.secret:
            h["Authorization"] = f"Bearer {rt.secret}"
        if rt.config.get("organization"):
            h["OpenAI-Organization"] = rt.config["organization"]
        if rt.config.get("project"):
            h["OpenAI-Project"] = rt.config["project"]
        return h

    def reasoning_control(self, model_id: str) -> dict:
        # No documented "off"; levels are expressed via reasoning.effort.
        return {"can_disable": False, "param": "reasoning_effort"}

    # ── Invoke ──────────────────────────────────────────────────────────

    async def invoke(self, rt: ProviderRuntime, model_id: str,
                     req: AIRequest) -> AIResponse:
        body = {
            "model": model_id,
            "store": False,                       # privacy: default is TRUE
            "max_output_tokens": req.max_output_tokens or 1024,
        }
        # `instructions` is the Responses home for the system prompt — never
        # a system-role message appended to `input`.
        if req.system_prompt:
            body["instructions"] = req.system_prompt
        body["input"] = [{"role": m.role, "content": m.content}
                         for m in req.messages]
        # Go through the capability gate even though the answer is currently
        # "yes" for every documented model — a hardcoded send makes
        # sampling_policy() a lie the day one model stops accepting them.
        sampling = self.sampling_policy(model_id)
        if req.temperature is not None and sampling["temperature"]:
            body["temperature"] = req.temperature
        if req.top_p is not None and sampling["top_p"]:
            body["top_p"] = req.top_p
        if req.response_format == RESPONSE_JSON_OBJECT:
            body["text"] = {"format": {"type": "json_object"}}
        if req.reasoning in ("low", "medium", "high"):
            body["reasoning"] = {"effort": req.reasoning}

        status, resp, headers = await self.http(
            rt, "POST", f"{self.resolve_base(rt)}/responses",
            headers=self.auth_headers(rt), body=body, timeout_s=req.timeout_s)
        if status != 200:
            raise self.http_error(rt, status, resp, self._rid(headers, resp))
        self._check_in_band_status(rt, resp)

        content, refusal = self._extract_text(resp)
        finish = FINISH_STOP
        details = resp.get("incomplete_details") or {}
        if resp.get("status") == "incomplete":
            finish = (FINISH_CONTENT_FILTER
                      if details.get("reason") == "content_filter"
                      else FINISH_LENGTH)
        if refusal:
            finish = FINISH_CONTENT_FILTER
            if not content:
                # A refusal carries no output_text, so returning it as a
                # "successful" empty answer would ship an empty bubble to the
                # user. It is the same outcome as a Gemini safety block:
                # content_rejected, and NOT failover-eligible.
                raise AIError(code=CONTENT_REJECTED, provider_type=rt.provider_type,
                              provider_instance_id=rt.instance_id, status_code=200,
                              provider_detail=f"refusal: {refusal}"[:300])

        usage = self.extract_usage(resp)
        return AIResponse(
            content=content,
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
            provider_request_id=self._rid(headers, resp),
            request_id=req.request_id,
            correlation_id=req.correlation_id,
        )

    def _check_in_band_status(self, rt: ProviderRuntime, resp: dict) -> None:
        """HTTP 200 carrying a non-success `status`.

        The status enum is completed | failed | in_progress | cancelled |
        queued | incomplete. Only `completed` and `incomplete` are answers;
        every other value is a failure wearing a 200, and `error` may be null
        even on "failed" — so the status alone has to be enough to reject.
        """
        if not isinstance(resp, dict):
            return
        state = str(resp.get("status") or "")
        if state == "failed":
            err = resp.get("error") if isinstance(resp.get("error"), dict) else {}
            code = str(err.get("code") or "")
            if code in ("invalid_prompt", "bio_policy"):
                mapped = CONTENT_REJECTED
            elif code == "rate_limit_exceeded":
                mapped = RATE_LIMITED
            elif code in ("server_error", "vector_store_timeout"):
                mapped = PROVIDER_UNAVAILABLE
            else:
                mapped = SERVER_ERROR
            raise AIError(code=mapped, provider_type=rt.provider_type,
                          provider_instance_id=rt.instance_id,
                          status_code=200,
                          provider_detail=str(err.get("message") or code
                                              or "response status: failed")[:400])
        if state in ("cancelled", "queued", "in_progress"):
            # We never send background:true, so a non-terminal or cancelled
            # response is a broken contract, not an answer.
            raise AIError(code=INVALID_RESPONSE, provider_type=rt.provider_type,
                          provider_instance_id=rt.instance_id, status_code=200,
                          provider_detail=f"unexpected response status: {state}")
        details = resp.get("incomplete_details") or {}
        reason = str(details.get("reason") or "")
        if reason == "content_filter":
            raise AIError(code=CONTENT_REJECTED, provider_type=rt.provider_type,
                          provider_instance_id=rt.instance_id, status_code=200,
                          provider_detail="response incomplete: content_filter")

    @staticmethod
    def _extract_text(resp: dict):
        """Walk output[] for message items — the array "often has more than
        one item" (reasoning items, tool calls) so index [0] is unsafe.
        Returns (text, refusal)."""
        texts, refusal = [], ""
        for item in (resp.get("output") or []):
            if item.get("type") != "message":
                continue
            for c in item.get("content") or []:
                if c.get("type") == "output_text" and c.get("text"):
                    texts.append(str(c["text"]))
                elif c.get("type") == "refusal" and c.get("refusal"):
                    refusal = str(c["refusal"])[:200]
        return "".join(texts), refusal

    @staticmethod
    def _rid(headers: dict, resp: dict) -> str:
        return str(headers.get("x-request-id") or (resp or {}).get("id") or "")[:80]

    def extract_usage(self, body) -> dict:
        u = (body or {}).get("usage") or {}
        in_details = u.get("input_tokens_details") or {}
        out_details = u.get("output_tokens_details") or {}
        return {
            "tokens_in": self._int(u.get("input_tokens")),
            "tokens_out": self._int(u.get("output_tokens")),
            "tokens_total": self._int(u.get("total_tokens")),
            "cached": self._int(in_details.get("cached_tokens")),
            "reasoning": self._int(out_details.get("reasoning_tokens")),
        }

    # ── Discovery / health ──────────────────────────────────────────────

    async def list_models(self, rt: ProviderRuntime) -> list:
        status, body, _h = await self.http(
            rt, "GET", f"{self.resolve_base(rt)}/models",
            headers=self.auth_headers(rt), timeout_s=15.0)
        if status != 200:
            raise self.http_error(rt, status, body)
        out = []
        for item in (body or {}).get("data") or []:
            mid = item.get("id")
            if not mid:
                continue
            status_ = "deprecated" if item.get("shutdown_date") else "available"
            out.append({
                "model_id": str(mid),
                "display_name": str(mid),
                "status": status_,
                "metadata": {k: item[k] for k in ("owned_by", "created", "shutdown_date")
                             if item.get(k) is not None},
            })
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
