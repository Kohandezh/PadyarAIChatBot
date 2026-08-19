"""SAKOO — architecture slot ONLY. Awaiting official documentation.

State (locked in AI-PROVIDER-CHECKPOINT.md §3.17):
    Architecture: READY
    Provider definition: READY
    Admin compatibility: READY
    Routing compatibility: READY
    Adapter: NOT IMPLEMENTED
    Network integration: NOT IMPLEMENTED
    Reason: Awaiting official SAKOO documentation

The customer will supply official SAKOO API documentation later. Until then
this adapter exists so the registry, the Admin UI, the routing schema and the
catalog all understand the provider type — but it physically cannot perform
network I/O: `http()` is overridden to raise, so there is no code path from
this class to a socket. No endpoint, auth scheme, header, request shape or
model ID has been guessed.
"""
from ..errors import AIError
from ..request import AIRequest, AIResponse
from .base import BaseAdapter, ProviderMetadata, ProviderRuntime

STATUS_NOT_IMPLEMENTED = "REQUIRES DOCUMENTATION"
REASON_FA = "در انتظار مستندات رسمی SAKOO — آداپتر پیاده‌سازی نشده است."


class SakooAdapter(BaseAdapter):
    PROVIDER_TYPE = "sakoo"

    def metadata(self) -> ProviderMetadata:
        return ProviderMetadata(
            type_key="sakoo", display_name="SAKOO",
            docs_url="",
            native=True,
            supports_discovery=False,
            note_fa=REASON_FA,
        )

    def configuration_schema(self) -> list:
        from .base import ConfigField
        # Only identity fields exist. NO endpoint, NO auth scheme — guessing
        # them from memory is exactly what the research-first rule forbids.
        return [
            ConfigField("notes", "یادداشت (در انتظار مستندات)", required=False),
        ]

    def endpoint_url(self, rt: ProviderRuntime) -> str:
        return ""            # unknown by design

    async def invoke(self, rt: ProviderRuntime, model_id: str,
                     req: AIRequest) -> AIResponse:
        raise AIError(code="invalid_request", provider_type=self.PROVIDER_TYPE,
                      provider_instance_id=rt.instance_id,
                      provider_detail=REASON_FA)

    async def list_models(self, rt: ProviderRuntime) -> list:
        # Fail loudly rather than return an empty list: an empty list is a
        # valid discovery result and would let a caller conclude "SAKOO has
        # no models" instead of "SAKOO is not implemented".
        raise AIError(code="invalid_request", provider_type=self.PROVIDER_TYPE,
                      provider_instance_id=rt.instance_id,
                      provider_detail=REASON_FA)

    async def test_connection(self, rt: ProviderRuntime) -> dict:
        return self.test_result(False, "requires_documentation", REASON_FA, 0)

    async def http(self, rt: ProviderRuntime, method: str, url: str,
                   **_kw) -> tuple:
        # The hard guarantee: no SAKOO request can ever leave the process.
        raise AIError(code="invalid_request", provider_type=self.PROVIDER_TYPE,
                      provider_instance_id=rt.instance_id,
                      provider_detail="SAKOO network access is not implemented")
