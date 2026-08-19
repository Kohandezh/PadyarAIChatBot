"""Moonshot / Kimi — compatible adapter with Kimi-specific metadata.

Research: docs/engineering/ai-providers/research/kimi.md (2026-08-18).

Encoded facts:
  * Two platforms with NON-PORTABLE keys: api.moonshot.ai (intl, USD) and
    api.moonshot.cn (mainland, CNY). A cross-platform key 401s — the admin
    picks the platform and the config stores it.
  * temperature / top_p / n / penalties are PINNED on all K-series models
    (sending them errors). They are modifiable only on the sunsetting
    moonshot-v1 family. sampling_policy() encodes exactly that split.
  * The token cap parameter is `max_completion_tokens` (whether legacy
    `max_tokens` still aliases it is UNKNOWN — do not rely).
  * K2.x reasoning: {"thinking": {"type": enabled|disabled}}; K3 uses
    `reasoning_effort` (low/high/max) and ALWAYS reasons.
  * `usage.cached_tokens` is FLAT — not nested under prompt_tokens_details
    like OpenAI. extract_usage reads it directly.
  * 400 error.type "content_filter" is a safety rejection — Moonshot rejects
    risky content with 400, not 200.
  * 429 splits into engine_overloaded / rate_limit_reached (retry) vs
    exceeded_current_quota (billing — never retry).
  * Discovery: GET /v1/models returns context_length + capability flags —
    rich enough to auto-populate the catalog.
"""
from ..errors import (
    AUTHENTICATION_FAILED, CONTENT_REJECTED, MODEL_NOT_FOUND,
    PERMISSION_DENIED, PROVIDER_UNAVAILABLE, QUOTA_EXCEEDED, RATE_LIMITED,
    SERVER_ERROR, TIMEOUT,
)
from .base import ProviderRuntime
from .openai_compatible import OpenAICompatibleAdapter

INTERNATIONAL = "https://api.moonshot.ai/v1"
MAINLAND = "https://api.moonshot.cn/v1"

# K2.7-code family: "thinking always on, only {"type":"enabled","keep":"all"}
# accepted" — sending "disabled" is a documented rejection, not a preference.
_THINKING_LOCKED_ON = ("kimi-k2.7-code",)


class KimiAdapter(OpenAICompatibleAdapter):
    PROVIDER_TYPE = "kimi"
    DEFAULT_BASE_URL = INTERNATIONAL
    SUPPORTS_DISCOVERY = True

    def metadata(self):
        from .base import ProviderMetadata
        return ProviderMetadata(
            type_key="kimi", display_name="Moonshot / Kimi",
            docs_url="https://platform.kimi.ai/docs/api/overview",
            native=False, supports_discovery=True,
        )

    def configuration_schema(self):
        from .base import ConfigField
        return [
            ConfigField("api_key", "کلید API", type_="password", required=True),
            ConfigField("platform", "پلتفرم", type_="enum", required=True,
                        default="international",
                        options=[("international", "بین‌المللی (api.moonshot.ai — دلار)"),
                                 ("mainland", "چین (api.moonshot.cn — یوان)")],
                        help_fa="کلید بین دو پلتفرم قابل تعویض نیست؛ کلید اشتباه خطای 401 می‌دهد."),
        ]

    def resolve_base(self, rt: ProviderRuntime) -> str:
        if rt.config.get("platform") == "mainland":
            return (rt.config.get("base_url") or MAINLAND).rstrip("/")
        return (rt.config.get("base_url") or INTERNATIONAL).rstrip("/")

    def sampling_policy(self, model_id: str) -> dict:
        # PINNED on K-series (documented as an error, not a warning).
        # Modifiable only on the legacy moonshot-v1 family.
        if (model_id or "").startswith("kimi-"):
            return {"temperature": False, "top_p": False}
        return {"temperature": True, "top_p": True}

    def reasoning_control(self, model_id: str) -> dict:
        mid = model_id or ""
        if mid.startswith("kimi-k3"):
            return {"can_disable": False, "param": "reasoning_effort"}
        if mid.startswith(_THINKING_LOCKED_ON):
            # Coding models reason unconditionally — declaring otherwise
            # would let a caller believe classification can be made cheap here.
            return {"can_disable": False, "param": "thinking"}
        if mid.startswith("kimi-"):
            return {"can_disable": True, "param": "thinking"}
        return {"can_disable": True, "param": ""}

    def build_body(self, rt, model_id, req) -> dict:
        # max_completion_tokens, not max_tokens (documented parameter).
        body = super().build_body(rt, model_id, req)
        if "max_tokens" in body:
            body["max_completion_tokens"] = body.pop("max_tokens")
        mid = model_id or ""
        if mid.startswith("kimi-k3"):
            # K3 ALWAYS reasons and `reasoning_effort` DEFAULTS TO "max" — on
            # the most expensive model in the catalog. Silence is therefore a
            # cost trap exactly like DeepSeek's default-on thinking, so the
            # effort is always stated: "off" buys the cheapest tier the enum
            # offers (there is no disable), never the default.
            body["reasoning_effort"] = {
                "off": "low", "low": "low", "medium": "high", "high": "high",
            }.get(req.reasoning, "max")
        elif mid.startswith(_THINKING_LOCKED_ON):
            # Only this exact object is accepted; "disabled" would be a 400.
            body["thinking"] = {"type": "enabled", "keep": "all"}
        elif mid.startswith("kimi-"):
            body["thinking"] = {"type": "disabled" if req.reasoning == "off" else "enabled"}
        return body

    def extract_usage(self, body) -> dict:
        u = (body or {}).get("usage") or {}
        if not u:
            # Kimi puts usage at choices[0].usage in streaming mode, "not at
            # the top level" — the docs tell a client to read both locations
            # defensively, so a usage-bearing choice is not thrown away.
            choice = ((body or {}).get("choices") or [{}])[0]
            u = (choice or {}).get("usage") or {}
        inp = self._int(u.get("prompt_tokens"))
        out = self._int(u.get("completion_tokens"))
        return {
            "tokens_in": inp,
            "tokens_out": out,
            "tokens_total": self._int(u.get("total_tokens")),
            "cached": self._int(u.get("cached_tokens")),   # FLAT on Kimi
            "reasoning": None,
        }

    def error_code_from_body(self, status: int, body) -> str:
        err = (body or {}).get("error") if isinstance(body, dict) else None
        if isinstance(err, dict):
            etype = str(err.get("type") or "")
            if etype == "content_filter":
                return CONTENT_REJECTED
            if etype in ("invalid_authentication_error", "incorrect_api_key_error"):
                # Also what a key from the OTHER platform (.ai vs .cn) returns.
                return AUTHENTICATION_FAILED
            if etype == "permission_denied_error":
                return PERMISSION_DENIED
            if etype == "resource_not_found_error":
                return MODEL_NOT_FOUND
            if etype == "exceeded_current_quota_error":
                return QUOTA_EXCEEDED
            if etype in ("rate_limit_reached_error", "engine_overloaded_error"):
                return RATE_LIMITED
            if etype == "server_unavailable":
                return PROVIDER_UNAVAILABLE
            if etype in ("server_error", "unexpected_output"):
                return SERVER_ERROR
            if etype == "client_closed_request":
                # HTTP 499 — no status fallback covers it, so it would
                # otherwise land on `unknown` and never be retried.
                return TIMEOUT
        return ""

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
            legacy = str(mid).startswith("moonshot-v1")
            out.append({
                "model_id": str(mid),
                "display_name": str(mid),
                "status": "deprecated" if legacy else "available",
                "context_window": self._int(item.get("context_length")),
                "supports_reasoning": bool(item.get("supports_reasoning")),
                "supports_vision": bool(item.get("supports_image_in")),
                "metadata": {},
            })
        return out
