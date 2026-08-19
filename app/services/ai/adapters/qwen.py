"""Alibaba / Qwen (Model Studio) — compatible adapter.

Research: docs/engineering/ai-providers/research/qwen.md (2026-08-18).

Encoded facts:
  * The base URL is a FUNCTION of (domain family, region, workspace) — the
    hardest configuration form of all nine providers:
      - dashscope shared:  https://dashscope-intl.aliyuncs.com/compatible-mode/v1
        (Singapore) — cn-beijing and cn-hongkong have their own shared hosts;
      - workspace-dedicated (recommended): https://{workspace}.{region}.maas.aliyuncs.com/compatible-mode/v1
      - trial: https://trial.{region}.maas.aliyuncs.com/compatible-mode/v1
    validate_config builds and SSRF-checks the resulting URL at save time.
  * Keys are per-region and per-billing-plan; wrong pairing is a 401.
  * Coding/Token plan endpoints are documented "not for backend services" —
    deliberately not offered.
  * `enable_thinking` MUST be false for non-streaming calls, otherwise 400.
    This adapter never streams, so it always sends enable_thinking:false
    when reasoning is off — and CANNOT enable thinking without streaming
    (recorded in capabilities, not silently ignored).
  * json_object mode requires the word "json" in the prompt (caller's duty —
    classification prompts that want JSON must contain it).
  * NO model-listing endpoint in either mode — bootstrap + manual only; the
    probe is a minimal non-streaming completion.
  * Error codes arrive dual-named (DashScope/openai): InvalidApiKey,
    ModelNotFound, Throttling.*, Arrearage (400!), DataInspectionFailed.
"""
from ..errors import AIError, CONTENT_REJECTED, MODEL_NOT_FOUND
from .base import ProviderRuntime
from .openai_compatible import OpenAICompatibleAdapter

_REGIONS = {
    "ap-southeast-1": "سنگاپور (پیشنهاد بین‌المللی)",
    "cn-beijing": "چین — پکن",
    "cn-hongkong": "چین — هنگ‌کنگ",
    "us-east-1": "آمریکا — ویرجینیا",
    "ap-northeast-1": "ژاپن — توکیو",
    "eu-central-1": "آلمان — فرانکفورت",
}
_DOMAINS = {
    "dashscope": "دامنهٔ اشتراکی DashScope",
    "workspace": "دامنهٔ اختصاصی Workspace (پیشنهاد رسمی)",
    "trial": "دامنهٔ آزمایشی (غیر محصولی)",
}


def build_base_url(cfg: dict) -> str:
    region = cfg.get("region") or "ap-southeast-1"
    domain = cfg.get("domain") or "dashscope"
    if cfg.get("base_url"):
        return cfg["base_url"].rstrip("/")
    if domain == "workspace":
        ws = (cfg.get("workspace_id") or "").strip()
        if not ws:
            raise AIError(code="invalid_request",
                          provider_detail="workspace_id required for the workspace domain")
        return f"https://{ws}.{region}.maas.aliyuncs.com/compatible-mode/v1"
    if domain == "trial":
        return f"https://trial.{region}.maas.aliyuncs.com/compatible-mode/v1"
    shared = {
        "ap-southeast-1": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        "cn-beijing": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "cn-hongkong": "https://cn-hongkong.dashscope.aliyuncs.com/compatible-mode/v1",
        "us-east-1": "https://dashscope-us.aliyuncs.com/compatible-mode/v1",
    }
    if region not in shared:
        raise AIError(code="invalid_request",
                      provider_detail=f"region {region} has no shared DashScope domain; use the workspace domain")
    return shared[region]


class QwenAdapter(OpenAICompatibleAdapter):
    PROVIDER_TYPE = "qwen"
    SUPPORTS_DISCOVERY = False

    def metadata(self):
        from .base import ProviderMetadata
        return ProviderMetadata(
            type_key="qwen", display_name="Alibaba / Qwen",
            docs_url="https://www.alibabacloud.com/help/en/model-studio/",
            native=False, supports_discovery=False,
            note_fa="فهرست مدل‌ها API ندارد و مدل‌ها بسته به منطقه متفاوت‌اند؛ از فهرست راه‌انداز + مدل دستی استفاده کنید.",
        )

    def configuration_schema(self):
        from .base import ConfigField
        return [
            ConfigField("api_key", "کلید API (DASHSCOPE_API_KEY)", type_="password",
                        required=True,
                        help_fa="کلید sk- از کنسول Model Studio؛ به منطقه و پلن صورت‌حساب متصل است."),
            ConfigField("region", "منطقه", type_="enum", required=True,
                        default="ap-southeast-1",
                        options=[(k, v) for k, v in _REGIONS.items()]),
            ConfigField("domain", "نوع دامنه", type_="enum", required=True,
                        default="dashscope",
                        options=[(k, v) for k, v in _DOMAINS.items()]),
            ConfigField("workspace_id", "شناسهٔ Workspace", required=False,
                        help_fa="فقط برای دامنهٔ اختصاصی Workspace الزامی است."),
        ]

    def resolve_base(self, rt: ProviderRuntime) -> str:
        return build_base_url(rt.config)

    def validate_config(self, cfg: dict, trust_class: str = "public") -> dict:
        cleaned = super().validate_config(cfg, trust_class)
        # Cross-field rule: workspace domain requires the id; the final URL is
        # built and SSRF-checked HERE so a broken combination fails at save.
        url = build_base_url(cleaned)
        from .. import endpoint_policy
        try:
            endpoint_policy.validate(url, trust_class)
        except endpoint_policy.EndpointRejected as e:
            raise AIError(code="invalid_request", provider_detail=f"base url: {e.reason}")
        cleaned["resolved_base_url"] = url
        return cleaned

    def reasoning_control(self, model_id: str) -> dict:
        # Non-streaming + thinking is a documented 400; we never stream, so
        # thinking cannot be enabled on this adapter — always disabled.
        return {"can_disable": True, "param": "enable_thinking"}

    def build_body(self, rt, model_id, req) -> dict:
        body = super().build_body(rt, model_id, req)
        # Documented constraint: must be false for non-streaming calls.
        body["enable_thinking"] = False
        return body

    def error_code_from_body(self, status: int, body) -> str:
        err = (body or {}).get("error") if isinstance(body, dict) else None
        if not isinstance(err, dict):
            code = str((body or {}).get("code") or "") if isinstance(body, dict) else ""
        else:
            code = str(err.get("code") or "")
        if code in ("InvalidApiKey", "invalid_api_key"):
            return "authentication_failed"
        if code in ("ModelNotFound", "model_not_found", "WorkSpaceNotFound"):
            return MODEL_NOT_FOUND
        if code.startswith("Throttling") or code in ("LimitRequests", "limit_requests"):
            return "rate_limited"
        if code in ("Arrearage", "CommodityNotPurchased", "PostpaidBillOverdue", "PrepaidBillOverdue"):
            return "quota_exceeded"
        if code in ("DataInspectionFailed", "InvalidParameter.DataInspection", "data_inspection_failed"):
            return CONTENT_REJECTED
        if code in ("InvalidInputLength",):
            return "context_limit_exceeded"
        if code == "ModelUnavailable":
            return "model_unavailable"
        return ""

    async def list_models(self, rt: ProviderRuntime) -> list:
        # NO model-listing endpoint is documented in either mode (research
        # §"Model listing": neither the OpenAI-compatibility pages nor the
        # DashScope reference document one). Inheriting the generic
        # GET {base}/models would fire an undocumented request that 404s and
        # then be reported as a provider outage. Say "unsupported" instead.
        raise AIError(code="invalid_request", provider_type=self.PROVIDER_TYPE,
                      provider_instance_id=rt.instance_id,
                      provider_detail="Qwen/Model Studio documents no model-listing "
                                      "endpoint; use the bootstrap list or enter the "
                                      "model id manually")

    def _probe_model(self, rt: ProviderRuntime) -> str:
        # No discovery endpoint — probe with the operator's configured chat
        # model if the store attached one, else the cheapest bootstrap model.
        return rt.config.get("_probe_model") or "qwen3.6-flash"
