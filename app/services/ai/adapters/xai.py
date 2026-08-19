"""xAI / Grok — compatible adapter with xAI-specific metadata.

Research: docs/engineering/ai-providers/research/xai.md (2026-08-18).

Encoded facts:
  * Base https://api.x.ai/v1, plain Bearer (keys prefixed xai-).
  * Chat Completions is labelled legacy but fully compatible and is the
    research-recommended surface for our needs (Responses gets new features
    first — a recorded tradeoff, not a permanent choice).
  * reasoning_effort: low/medium/high (default high) /xhigh; CANNOT be
    disabled on grok-4.6 / grok-4.5 — only grok-4.3 documents "none".
    A trivial prompt still burns reasoning tokens at the full completion
    rate — reasoning "off" is mapped to the cheapest expressible level.
  * stop / presence_penalty / frequency_penalty ERROR on reasoning models —
    this adapter never sends any of them.
  * usage adds reasoning_tokens, nested cached_tokens, and xAI-only
    cost_in_usd_ticks (10_000_000_000 ticks = $1) — surfaced in response
    metadata for cost cross-checks.
  * The documented error envelope is NOT OpenAI-shaped: a flat
    {"code": ..., "error": "<message>"} was observed live. The parser
    handles both (error as object OR as string).
  * Discovery: GET /v1/language-models is the RICHER route (documented:
    "additional information compared to /v1/models includes modalities,
    fingerprint and alias(es)") and its root key is `models`, NOT `data`.
    We read it first and fall back to GET /v1/models (root `data`) if the
    richer route is unavailable — both key shapes are parsed.
    Price integers are "USD cents per 100 million tokens", i.e. USD-per-1M =
    value / 10_000. The catalog converts.
  * `max_tokens` is documented DEPRECATED on Chat Completions;
    `max_completion_tokens` is the current field — that is what we send.
  * Documented example responses return `"id": "latest"` with the real slug
    only in `aliases[]`, so the display name prefers the alias.
"""
from .openai_compatible import OpenAICompatibleAdapter

BASE = "https://api.x.ai/v1"
_TICKS_PER_USD = 10_000_000_000


class XAIAdapter(OpenAICompatibleAdapter):
    PROVIDER_TYPE = "xai"
    DEFAULT_BASE_URL = BASE
    SUPPORTS_DISCOVERY = True

    def metadata(self):
        from .base import ProviderMetadata
        return ProviderMetadata(
            type_key="xai", display_name="xAI / Grok",
            docs_url="https://docs.x.ai/developers/rest-api-reference/inference",
            native=False, supports_discovery=True,
        )

    def configuration_schema(self):
        from .base import ConfigField
        return [
            ConfigField("api_key", "کلید API", type_="password", required=True,
                        help_fa="کلیدها با پیشوند xai- صادر می‌شوند."),
            ConfigField("base_url", "نشانی پایه (اختیار)", type_="url", default=BASE),
        ]

    def reasoning_control(self, model_id: str) -> dict:
        mid = model_id or ""
        if mid.startswith("grok-4.3"):
            return {"can_disable": True, "param": "reasoning_effort"}
        return {"can_disable": False, "param": "reasoning_effort"}

    def build_body(self, rt, model_id, req) -> dict:
        body = super().build_body(rt, model_id, req)
        # `max_tokens` is DEPRECATED on xAI Chat Completions (research §Request
        # shape); `max_completion_tokens` is the documented current field.
        # NOTE: stop / presence_penalty / frequency_penalty are never built
        # here — they are a documented ERROR on every reasoning model, and
        # every current xAI text model is a reasoning model.
        if "max_tokens" in body:
            body["max_completion_tokens"] = body.pop("max_tokens")
        if req.reasoning == "off":
            # Cannot disable on 4.5/4.6; grok-4.3 accepts "none".
            body["reasoning_effort"] = "none" if (model_id or "").startswith("grok-4.3") else "low"
        elif req.reasoning in ("low", "medium", "high"):
            body["reasoning_effort"] = req.reasoning
        return body

    def extract_usage(self, body) -> dict:
        u = (body or {}).get("usage") or {}
        in_details = u.get("prompt_tokens_details") or {}
        out_details = u.get("completion_tokens_details") or {}
        ticks = u.get("cost_in_usd_ticks")
        return {
            "tokens_in": self._int(u.get("prompt_tokens")),
            "tokens_out": self._int(u.get("completion_tokens")),
            "tokens_total": self._int(u.get("total_tokens")),
            "cached": self._int(in_details.get("cached_tokens")),
            "reasoning": self._int(out_details.get("reasoning_tokens")),
            # xAI-only exact cost; USD = ticks / 1e10.
            "cost_hint_usd": (float(ticks) / _TICKS_PER_USD) if ticks is not None else None,
        }

    def error_code_from_body(self, status: int, body) -> str:
        if isinstance(body, dict):
            # Observed live: flat {"code": "...", "error": "message"} — the
            # message is a STRING under "error", not an object.
            code = str(body.get("code") or "")
            if code.startswith("unauthenticated"):
                return "authentication_failed"
        return super().error_code_from_body(status, body)

    def language_models_url(self, rt) -> str:
        """The RICH catalog route. Root key is `models`, not `data`."""
        return f"{self.resolve_base(rt)}/language-models"

    async def list_models(self, rt) -> list:
        # Richest documented route first; fall back to the minimal /models
        # only if this deployment does not serve it (404).
        status, body, _h = await self.http(rt, "GET", self.language_models_url(rt),
                                           headers=self.auth_headers(rt),
                                           timeout_s=15.0)
        if status == 404:
            status, body, _h = await self.http(rt, "GET", self.models_url(rt),
                                               headers=self.auth_headers(rt),
                                               timeout_s=15.0)
        if status != 200:
            raise self.http_error(rt, status, body)
        # `/language-models` → {"models": [...]}; `/models` → {"data": [...]}.
        items = (body or {}).get("models")
        if not isinstance(items, list):
            items = (body or {}).get("data") or []
        out = []
        for item in items:
            mid = item.get("id")
            if not mid:
                continue
            # Prices are cents per 100M tokens → USD per 1M = /10000.
            def usd(v):
                try:
                    return round(float(v) / 10_000, 6) if v is not None else None
                except (TypeError, ValueError):
                    return None
            out.append({
                "model_id": str(mid),
                "display_name": str((item.get("aliases") or [mid])[0]),
                "status": "available",
                "context_window": self._int(item.get("context_length")),
                "metadata": {
                    "aliases": item.get("aliases") or [],
                    "input_modalities": item.get("input_modalities") or [],
                    "output_modalities": item.get("output_modalities") or [],
                    "fingerprint": item.get("fingerprint"),
                    "long_context_threshold": self._int(item.get("long_context_threshold")),
                    "price_input_usd_per_m": usd(item.get("prompt_text_token_price")),
                    "price_cached_usd_per_m": usd(item.get("cached_prompt_text_token_price")),
                    "price_output_usd_per_m": usd(item.get("completion_text_token_price")),
                },
            })
        return out
