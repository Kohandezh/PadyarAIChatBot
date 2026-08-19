"""DeepSeek — compatible adapter with DeepSeek-specific metadata.

Research: docs/engineering/ai-providers/research/deepseek.md (2026-08-18).

Encoded facts:
  * Base is the BARE HOST https://api.deepseek.com — NO /v1 segment; /v1 is
    undocumented and must not be configured.
  * Thinking is ENABLED BY DEFAULT at effort "high". Silence is a silent
    cost multiplier — this adapter sends an explicit thinking value on every
    call, exactly like the Z.AI adapter.
  * temperature / top_p are unsupported while thinking is on (the default).
    That is a REQUEST-level fact, not a model-level one — the same model
    accepts them with thinking off — so it cannot live in
    sampling_policy(model_id), which only sees the model. build_body drops
    them whenever it enables thinking.
  * `response_format: json_object` additionally requires the literal word
    "json" somewhere in the prompt, or the call is rejected.
  * frequency_penalty / presence_penalty are deprecated API-wide — never sent.
  * usage carries DeepSeek's OWN cache vocabulary: prompt_cache_hit_tokens /
    prompt_cache_miss_tokens; prompt_tokens = hit + miss. Pricing must use
    the hit rate for cached tokens or it over-prices by ~31x.
  * 402 = insufficient balance (a billing failure, not auth) — mapped to
    quota_exceeded.
  * Discovery: GET /models, minimal shape (id/owned_by only).
"""
from ..request import RESPONSE_JSON_OBJECT
from .openai_compatible import OpenAICompatibleAdapter

BASE = "https://api.deepseek.com"


class DeepSeekAdapter(OpenAICompatibleAdapter):
    PROVIDER_TYPE = "deepseek"
    DEFAULT_BASE_URL = BASE
    SUPPORTS_DISCOVERY = True

    def metadata(self):
        from .base import ProviderMetadata
        return ProviderMetadata(
            type_key="deepseek", display_name="DeepSeek",
            docs_url="https://api-docs.deepseek.com/",
            native=False, supports_discovery=True,
        )

    def configuration_schema(self):
        from .base import ConfigField
        return [
            ConfigField("api_key", "کلید API", type_="password", required=True),
            ConfigField("base_url", "نشانی پایه (اختیار)", type_="url", default=BASE,
                        help_fa="بدون /v1 — میزبان خالی، مطابق مستندات رسمی."),
        ]

    def validate_config(self, cfg: dict, trust_class: str = "public") -> dict:
        """Normalize the base URL: the documented host carries NO version
        segment, and `/v1` is undocumented — an admin who types it would be
        building on a path we are told not to depend on."""
        cleaned = super().validate_config(cfg, trust_class)
        base = (cleaned.get("base_url") or "").rstrip("/")
        if base.endswith("/v1"):
            cleaned["base_url"] = base[: -len("/v1")]
        return cleaned

    def reasoning_control(self, model_id: str) -> dict:
        return {"can_disable": True, "param": "thinking"}

    def build_messages(self, rt, model_id, req) -> list:
        msgs = super().build_messages(rt, model_id, req)
        # JSON mode is documented as requiring the literal word "json" in the
        # prompt. Without it the documented failure is an empty/rejected
        # answer, which would surface as an unexplained blank reply.
        if req.response_format == RESPONSE_JSON_OBJECT and not any(
                "json" in str(m.get("content") or "").lower() for m in msgs):
            msgs.insert(0, {"role": "system",
                            "content": "Respond with a single valid JSON object."})
        return msgs

    def build_body(self, rt, model_id, req) -> dict:
        body = super().build_body(rt, model_id, req)
        thinking_on = req.reasoning != "off"
        if thinking_on:
            # Unsupported while thinking is on — drop, do not discover by 400.
            body.pop("temperature", None)
            body.pop("top_p", None)
            body["thinking"] = {"type": "enabled"}
            # enum is low|high|max; "medium" has no wire value, so it is
            # stated as the documented default rather than silently omitted.
            if req.reasoning in ("low", "high", "medium"):
                body["reasoning_effort"] = "high" if req.reasoning == "medium" else req.reasoning
        else:
            # Explicit disable: thinking defaults to enabled/high server-side.
            body["thinking"] = {"type": "disabled"}
        return body

    def extract_usage(self, body) -> dict:
        u = (body or {}).get("usage") or {}
        inp = self._int(u.get("prompt_tokens"))
        out = self._int(u.get("completion_tokens"))
        return {
            "tokens_in": inp,
            "tokens_out": out,
            "tokens_total": self._int(u.get("total_tokens")),
            # DeepSeek's own cache vocabulary — not prompt_tokens_details.
            "cached": self._int(u.get("prompt_cache_hit_tokens")),
            "reasoning": self._int((u.get("completion_tokens_details") or {})
                                  .get("reasoning_tokens"))
            if isinstance(u.get("completion_tokens_details"), dict) else None,
        }
