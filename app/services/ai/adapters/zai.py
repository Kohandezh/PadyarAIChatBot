"""Z.AI / GLM — compatible adapter with Z.AI-specific metadata.

Research: docs/engineering/ai-providers/research/zai-glm.md (2026-08-18).

Encoded facts:
  * International base: https://api.z.ai/api/paas/v4/ (Bearer). The mainland
    platform (open.bigmodel.cn) is a SEPARATE platform with separate keys and
    CNY pricing — an admin chooses one; keys are not portable.
  * API keys are `id.secret` and therefore contain a DOT — never reject that.
  * temperature range is [0.0, 1.0] — NOT OpenAI's [0,2]; a caller
    preference of 0.66 is fine, anything above 1 is clamped before sending.
  * `thinking` DEFAULTS TO ENABLED, and glm-4.7 / glm-4.7-flash / glm-4.5v
    "think compulsorily" when enabled. Classification must always send
    {"thinking": {"type": "disabled"}} — this adapter sends an explicit
    thinking value on EVERY call, never inherits the default.
  * `response_format` supports json_object ONLY (no json_schema).
  * NO model-listing endpoint exists (verified absent from the OpenAPI) —
    SUPPORTS_DISCOVERY False; the catalog is bootstrap + manual, and the
    connection probe is a free glm-4.7-flash completion with thinking off.
  * Error bodies disagree with each other ({"error":{"code":"1302"}},
    flat {"code":500,"msg":...}, sometimes on HTTP 200). 429 is heavily
    overloaded: only codes 1302/1305 are genuinely retryable; 1113 and
    1308–1321 are billing/quota exhaustion that must not be retried.
  * finish_reason carries failures: "sensitive" (moderation),
    "model_context_window_exceeded", "network_error".
"""
from ..errors import (
    AIError, AUTHENTICATION_FAILED, CONTENT_REJECTED, CONTEXT_LIMIT_EXCEEDED,
    INVALID_REQUEST, MODEL_NOT_FOUND, PERMISSION_DENIED, QUOTA_EXCEEDED,
    RATE_LIMITED, SERVER_ERROR,
)
from ..request import AIRequest, AIResponse, FINISH_STOP
from .base import ProviderRuntime
from .openai_compatible import OpenAICompatibleAdapter

INTERNATIONAL = "https://api.z.ai/api/paas/v4"
MAINLAND = "https://open.bigmodel.cn/api/paas/v4"

# Research-verified: only these 429s are throughput (retryable). The rest of
# the 429 family (1113, 1308–1321) is quota/billing exhaustion.
_RETRYABLE_RATE_CODES = {"1302", "1305", "1313"}
# 500-family codes documented as "retry after backoff".
_SERVER_CODES = {"1200", "1230", "1234"}
# Models whose `thinking` can only be ENABLED (depth via reasoning_effort).
# Sending {"type":"disabled"} to these is a documented impossibility, so the
# "off" preference becomes the cheapest available effort instead.
_THINKING_ALWAYS_ON = ("glm-5.3",)


class ZAIAdapter(OpenAICompatibleAdapter):
    PROVIDER_TYPE = "zai"
    DEFAULT_BASE_URL = INTERNATIONAL
    SUPPORTS_DISCOVERY = False

    def metadata(self):
        from .base import ProviderMetadata
        return ProviderMetadata(
            type_key="zai", display_name="Z.AI (GLM)",
            docs_url="https://docs.z.ai/",
            native=False, supports_discovery=False,
            note_fa="فهرست مدل‌ها API ندارد؛ فهرست راه‌انداز + مدل دستی.",
        )

    def configuration_schema(self):
        from .base import ConfigField
        return [
            ConfigField("api_key", "کلید API", type_="password", required=True,
                        help_fa="شکل id.secret است و نقطه دارد — طبیعی است."),
            ConfigField("platform", "پلتفرم", type_="enum", required=True,
                        default="international",
                        options=[("international", "بین‌المللی (api.z.ai — دلار)"),
                                 ("mainland", "چین (open.bigmodel.cn — یوان)")],
                        help_fa="کلیدها بین دو پلتفرم قابل تعویض نیستند."),
        ]

    def resolve_base(self, rt: ProviderRuntime) -> str:
        if rt.config.get("platform") == "mainland":
            return (rt.config.get("base_url") or MAINLAND).rstrip("/")
        return (rt.config.get("base_url") or INTERNATIONAL).rstrip("/")

    def sampling_policy(self, model_id: str) -> dict:
        return {"temperature": True, "top_p": True}   # supported, range [0,1]

    def supports_json_object(self, model_id: str) -> bool:
        return True                                      # json_object only

    def reasoning_control(self, model_id: str) -> dict:
        # glm-5.3: "thinking can only be enabled; depth is controlled by
        # reasoning_effort" (ChatThinking schema). Declaring can_disable True
        # for it would be a capability lie.
        if str(model_id or "").startswith(_THINKING_ALWAYS_ON):
            return {"can_disable": False, "param": "reasoning_effort"}
        return {"can_disable": True, "param": "thinking"}

    def build_body(self, rt, model_id, req: AIRequest) -> dict:
        body = super().build_body(rt, model_id, req)
        # Clamp sampling into Z.AI's documented ranges — a wider preference
        # must not become a 400. temperature [0,1], top_p [0.01,1].
        if "temperature" in body:
            body["temperature"] = max(0.0, min(1.0, float(body["temperature"])))
        if "top_p" in body:
            body["top_p"] = max(0.01, min(1.0, float(body["top_p"])))
        # ALWAYS explicit: thinking defaults to enabled server-side and the
        # 4.7 family thinks compulsorily — silence would burn output tokens.
        # "default" (no preference) is stated EXPLICITLY as enabled rather
        # than inherited, so the wire request is deterministic.
        always_on = str(model_id or "").startswith(_THINKING_ALWAYS_ON)
        if req.reasoning == "off":
            if always_on:
                # Cannot be disabled on this family; buy the cheapest depth
                # the reasoning_effort enum offers instead of sending a
                # value the schema says is impossible.
                body["thinking"] = {"type": "enabled"}
                body["reasoning_effort"] = "minimal"
            else:
                body["thinking"] = {"type": "disabled"}
        elif req.reasoning in ("low", "medium", "high"):
            body["thinking"] = {"type": "enabled"}
            body["reasoning_effort"] = req.reasoning
        else:
            body["thinking"] = {"type": "enabled"}
        return body

    def check_finish_reason(self, rt: ProviderRuntime, finish_reason, body) -> None:
        fr = str(finish_reason or "")
        if fr == "sensitive":
            raise AIError(code=CONTENT_REJECTED, provider_type=rt.provider_type,
                          provider_instance_id=rt.instance_id, status_code=200,
                          provider_detail="finish_reason: sensitive (moderation)")
        if fr == "model_context_window_exceeded":
            raise AIError(code=CONTEXT_LIMIT_EXCEEDED, provider_type=rt.provider_type,
                          provider_instance_id=rt.instance_id, status_code=200,
                          provider_detail="finish_reason: model_context_window_exceeded")
        if fr == "network_error":
            raise AIError(code=SERVER_ERROR, provider_type=rt.provider_type,
                          provider_instance_id=rt.instance_id, status_code=200,
                          provider_detail="finish_reason: network_error")

    async def invoke(self, rt, model_id, req):
        # Z.AI can answer HTTP 200 with a flat {"code":..,"msg":..} error
        # body. The compatible invoke checks status only, so the flat shape
        # is caught here before parsing choices.
        body = self.build_body(rt, model_id, req)
        status, resp, headers = await self.http(
            rt, "POST", self.chat_url(rt), headers=self.auth_headers(rt),
            body=body, timeout_s=req.timeout_s)
        if status == 200 and isinstance(resp, dict) and resp.get("code") and "choices" not in resp:
            e = self.http_error(rt, status, resp)
            raise e
        if status != 200:
            raise self.http_error(rt, status, resp, self._request_id(headers))
        return self._parse_chat(rt, model_id, req, resp, headers)

    def _parse_chat(self, rt, model_id, req, resp, headers):
        choice = ((resp or {}).get("choices") or [{}])[0]
        message = choice.get("message") or {}
        content = message.get("content")
        content = "" if content is None else str(content)
        self.check_finish_reason(rt, choice.get("finish_reason"), resp)
        usage = self.extract_usage(resp)
        fr = choice.get("finish_reason")
        return AIResponse(
            content=content,
            # "length" must survive as length — a truncated answer reported as
            # a clean stop is how silent truncation reaches a visitor.
            finish_reason=self.FINISH_MAP.get(str(fr), FINISH_STOP) if fr else FINISH_STOP,
            task=req.task, provider_type=rt.provider_type,
            provider_instance_id=rt.instance_id, provider_name=rt.display_name,
            model=model_id,
            tokens_input=usage.get("tokens_in"), tokens_output=usage.get("tokens_out"),
            tokens_total=usage.get("tokens_total"), cached_tokens=usage.get("cached"),
            reasoning_tokens=usage.get("reasoning"),
            latency_ms=0,
            provider_request_id=str(resp.get("request_id") or self._request_id(headers))[:80],
            request_id=req.request_id, correlation_id=req.correlation_id,
        )

    def error_code_from_body(self, status: int, body) -> str:
        err = (body or {}).get("error") if isinstance(body, dict) else None
        code = ""
        if isinstance(err, dict):
            code = str(err.get("code") or "")
        elif isinstance(body, dict):
            code = str(body.get("code") or "")
        if code in _RETRYABLE_RATE_CODES:
            return RATE_LIMITED
        # "Codes 1113 and 1308–1321 are billing/quota exhaustion and must NOT
        # be retried" — expressed as the documented RANGE, so an undocumented
        # member of the family (1312) is not silently treated as throughput.
        if code == "1113" or (code.isdigit() and 1308 <= int(code) <= 1321):
            return QUOTA_EXCEEDED
        if code in ("1000", "1001", "1003", "1005"):
            return AUTHENTICATION_FAILED
        if code == "1220":
            return PERMISSION_DENIED
        if code in ("1211",):
            return MODEL_NOT_FOUND
        if code == "1301":
            return CONTENT_REJECTED
        if code == "1261":
            return CONTEXT_LIMIT_EXCEEDED
        if code in ("1210", "1212", "1213", "1214", "1215", "1221", "1222"):
            return INVALID_REQUEST
        if code in _SERVER_CODES:
            return SERVER_ERROR
        # The flat {"code":500,"msg":...} shape arrives with HTTP 200, so the
        # status fallback would classify it `unknown`. Trust the body's own
        # HTTP-shaped code instead.
        if status == 200 and code.isdigit():
            n = int(code)
            if n >= 500:
                return SERVER_ERROR
            if n == 404:
                return MODEL_NOT_FOUND
            if n in (401, 403):
                return PERMISSION_DENIED if n == 403 else AUTHENTICATION_FAILED
            if n == 429:
                return RATE_LIMITED
            if 400 <= n < 500:
                return INVALID_REQUEST
        return ""

    async def list_models(self, rt) -> list:
        """There is no model-listing endpoint. Say so; never probe for one.

        The inherited implementation would GET {base}/models, which is absent
        from the OpenAPI spec. Z.AI authenticates at the gateway BEFORE
        routing, so a nonexistent path answers 401 exactly like a real one —
        a blind call would be reported to the admin as "bad credentials".
        """
        raise AIError(code=INVALID_REQUEST, provider_type=rt.provider_type,
                      provider_instance_id=rt.instance_id,
                      provider_detail="Z.AI has no model-listing endpoint; "
                                      "use the bootstrap catalog or add the model manually")

    def _probe_model(self, rt: ProviderRuntime) -> str:
        return "glm-4.7-flash"        # free — the probe costs nothing

    async def test_connection(self, rt) -> dict:
        import time as _t
        t0 = _t.perf_counter()
        try:
            probe = AIRequest(task="test", messages=[], system_prompt="",
                              max_output_tokens=1, reasoning="off")
            body = self.build_body(rt, self._probe_model(rt), probe)
            body["messages"] = [{"role": "user", "content": "ping"}]
            status, resp, _h = await self.http(rt, "POST", self.chat_url(rt),
                                               headers=self.auth_headers(rt),
                                               body=body, timeout_s=20.0)
            if status == 200 and isinstance(resp, dict) and resp.get("choices"):
                return self.test_result(True, "connected", "پاسخ آزمایشی رایگان دریافت شد",
                                        (_t.perf_counter() - t0) * 1000)
            e = self.http_error(rt, status, resp)
            return self.test_result(False, e.code, e.redacted_detail(),
                                    (_t.perf_counter() - t0) * 1000)
        except AIError as e:
            return self.test_result(False, e.code, e.redacted_detail(),
                                    (_t.perf_counter() - t0) * 1000)
