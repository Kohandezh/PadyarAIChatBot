"""Mistral — compatible adapter with Mistral-specific metadata.

Research: docs/engineering/ai-providers/research/mistral.md (2026-08-18).

Encoded facts:
  * OpenAI-compatible base https://api.mistral.ai/v1 (officially documented:
    the stock OpenAI client works with only the base URL swapped).
  * ChatCompletionRequest is additionalProperties:false — unknown fields are
    REJECTED, not ignored. This adapter never sends OpenAI-only fields
    (no stream_options, no seed, no logprobs, no user).
  * reasoning_effort enum: none|minimal|low|medium|high|xhigh — richer than
    OpenAI's, and "none" exists (unlike xAI 4.5/4.6).
  * finish_reason adds "model_length" (not an OpenAI value) — mapped.
  * GET /v1/models is the RICHEST catalog of all nine providers: 14
    capability flags, max_context_length, aliases, deprecation date and
    deprecation_replacement_model, default_model_temperature. The parser
    keeps all of it for the catalog.
  * Two error body shapes: {"object":"error", message, type, param, code}
    for most statuses and a FastAPI {"detail":[...]} for 422.
  * A retired model id returns 404 — a catalog-staleness signal, not an
    outage.
"""
from .openai_compatible import OpenAICompatibleAdapter

BASE = "https://api.mistral.ai/v1"


class MistralAdapter(OpenAICompatibleAdapter):
    PROVIDER_TYPE = "mistral"
    DEFAULT_BASE_URL = BASE
    SUPPORTS_DISCOVERY = True

    def metadata(self):
        from .base import ProviderMetadata
        return ProviderMetadata(
            type_key="mistral", display_name="Mistral",
            docs_url="https://docs.mistral.ai/",
            native=False, supports_discovery=True,
        )

    def configuration_schema(self):
        from .base import ConfigField
        return [
            ConfigField("api_key", "کلید API", type_="password", required=True),
            ConfigField("base_url", "نشانی پایه (اختیار)", type_="url", default=BASE),
        ]

    def reasoning_control(self, model_id: str) -> dict:
        return {"can_disable": True, "param": "reasoning_effort"}

    def build_body(self, rt, model_id, req) -> dict:
        body = super().build_body(rt, model_id, req)
        if req.reasoning == "off":
            body["reasoning_effort"] = "none"
        elif req.reasoning in ("low", "medium", "high"):
            body["reasoning_effort"] = req.reasoning
        return body

    def error_code_from_body(self, status: int, body) -> str:
        if isinstance(body, dict):
            etype = str(body.get("type") or "")
            code = str(body.get("code") or "")
            if etype == "authentication_error":
                return "authentication_failed"
            if code == "unknown_model":
                return "model_not_found"
            if etype == "rate_limit_error":
                return "rate_limited"
            if etype == "server_error":
                return "server_error"
        return super().error_code_from_body(status, body)

    async def list_models(self, rt) -> list:
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
            caps = item.get("capabilities") or {}
            dep = item.get("deprecation")
            out.append({
                "model_id": str(mid),
                "display_name": str(item.get("name") or mid),
                "status": "deprecated" if dep else "available",
                "supports_chat": bool(caps.get("completion_chat", True)),
                "supports_reasoning": bool(caps.get("reasoning")),
                "supports_tools": bool(caps.get("function_calling")),
                "supports_vision": bool(caps.get("vision")),
                "context_window": self._int(item.get("max_context_length")),
                "metadata": {
                    "aliases": item.get("aliases") or [],
                    "deprecation": dep,
                    "replacement": item.get("deprecation_replacement_model"),
                    "default_temperature": item.get("default_model_temperature"),
                    # All 14 documented capability flags, not just the four
                    # the catalog columns model — ocr / classification /
                    # moderation / audio* / completion_fim / fine_tuning /
                    # unified_resources would otherwise be flattened away.
                    "capabilities": {k: bool(v) for k, v in caps.items()},
                    "model_type": item.get("type"),
                    "description": item.get("description"),
                },
            })
        return out
