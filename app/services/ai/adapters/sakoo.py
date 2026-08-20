"""SAKOO / Rayen — سکوی ملی هوش مصنوعی (National AI Platform, Sharif).

Documentation source: the supplied Rayen OpenAPI 3.0 contract (API 1.0.0),
served from https://rmgpilot.aip.sharif.ir/openapi.json. The live service is
IP-allowlisted and NOT reachable from the development machine — verified
2026-08-20: the edge accepts TCP, reads the ClientHello and closes without a
byte, on 443 and 80 alike. That is why nothing in this module, its tests, or
the default suite performs live I/O. Live verification is an Admin-panel step
(Test Connection / Refresh Models) from the whitelisted deployment host.

DOCUMENTED CONTRACT (the only fields this adapter will send)
------------------------------------------------------------
  POST /v1/chat/completions   {model, messages[{role,content}], temperature,
                               max_tokens}
  POST /v1/embeddings         {model, input}
  GET  /v1/models
  Responses: 200 / 401 / 404 / 500 — normalized by the shared taxonomy.

The wire shape is OpenAI Chat Completions, so this subclasses the hardened
compatible transport (SSRF policy, DNS pinning, TLS verification, redirect
refusal, error normalization, secret redaction) and overrides ONLY what the
Rayen documentation actually covers:

  * top_p, stream, response_format, reasoning controls are NOT in the
    documented request schema — they are never sent. Undocumented fields
    discovered by a 400 at an exhibition booth is the failure mode this
    strictness prevents. supports_json_object() is False for the same
    reason: absence of a documented guarantee is absence of the feature.
  * `stream` is omitted entirely (not sent as false): non-streaming is the
    documented default of this wire shape, and the field itself is
    undocumented on Rayen.

AUTHENTICATION
--------------
The documentation confirms auth exists (401 Unauthorized — Authentication
required) but does not name the scheme. `Bearer` is NOT an invention here: it
is the standing credential mechanism of the wire protocol Rayen documents
itself as implementing, and it is this project's existing generic-provider
representation — the secret lives in the provider instance's encrypted secret
column, entered in Admin → AI, never echoed, never logged. If deployment
reveals a different header, the operator escalates and the fix is one method
(`auth_headers`) — configuration and storage are unchanged.

MODELS
------
GET /v1/models is authoritative; the catalog refresh flow (Admin → Refresh
Models) populates it. `rayen-gemma4-31b` / `rayen-jina-v5` appear in the
documentation as EXAMPLES and are deliberately not bootstrapped as a catalog.

EMBEDDINGS
----------
`embed()` implements POST /v1/embeddings per the documented shape. NOTE
HONESTLY: no Padyar runtime path consumes provider embeddings today —
retrieval uses the local model2vec index by design (exhibition machines are
provisioned offline; see app/services/embeddings.py). The method exists so
the capability is real, contract-tested and ready to wire, not to pretend a
RAG integration that does not exist.
"""
from ..errors import AIError, INVALID_RESPONSE
from ..request import AIRequest
from .base import ProviderRuntime
from .openai_compatible import OpenAICompatibleAdapter

BASE = "https://rmgpilot.aip.sharif.ir/v1"
DOCS = "https://rmgpilot.aip.sharif.ir/docs"


class SakooAdapter(OpenAICompatibleAdapter):
    PROVIDER_TYPE = "sakoo"
    DEFAULT_BASE_URL = BASE
    SUPPORTS_DISCOVERY = True

    def metadata(self):
        from .base import ProviderMetadata
        return ProviderMetadata(
            type_key="sakoo", display_name="SAKOO / Rayen",
            docs_url=DOCS,
            native=False, supports_discovery=True,
            note_fa="سکوی ملی هوش مصنوعی (راین) — دسترسی شبکه فقط از محیط "
                    "مجاز (IP-allowlist)؛ پس از استقرار با «آزمون اتصال» "
                    "راستی‌آزمایی کنید.",
        )

    def configuration_schema(self):
        from .base import ConfigField
        return [
            ConfigField("api_key", "کلید/توکن دسترسی راین", type_="password",
                        required=True,
                        help_fa="فقط ذخیره می‌شود؛ هرگز نمایش داده نمی‌شود. "
                                "اعتبار استقرار را اپراتور وارد می‌کند."),
            ConfigField("base_url", "نشانی پایه (اختیاری)", type_="url",
                        default=BASE,
                        help_fa="پیش‌فرض سرویس رسمی راین؛ فقط در صورت تغییر "
                                "رسمی نشانی عوض شود."),
        ]

    # ── Documented request strictness ───────────────────────────────────

    def sampling_policy(self, model_id: str) -> dict:
        # temperature is documented; top_p is not. Never send undocumented.
        return {"temperature": True, "top_p": False}

    def supports_json_object(self, model_id: str) -> bool:
        return False        # response_format is not in the documented schema

    def build_body(self, rt: ProviderRuntime, model_id: str,
                   req: AIRequest) -> dict:
        body = super().build_body(rt, model_id, req)
        # The documented schema is {model, messages, temperature, max_tokens}.
        # `stream` is the base transport's addition, not Rayen's — omit it
        # rather than send an undocumented field as false.
        body.pop("stream", None)
        return body

    # ── Embeddings (documented; no runtime consumer yet — see docstring) ─

    def embeddings_url(self, rt: ProviderRuntime) -> str:
        return f"{self.resolve_base(rt)}/embeddings"

    async def embed(self, rt: ProviderRuntime, model_id: str,
                    text: str) -> dict:
        """One embedding for one input string, per the documented contract.

        Returns {"embedding": [float...], "model": str,
                 "tokens_input": int | None}. Raises normalized AIError on
        any non-200, timeout, connection failure or malformed body.
        """
        status, body, _h = await self.http(
            rt, "POST", self.embeddings_url(rt),
            headers=self.auth_headers(rt),
            body={"model": model_id, "input": text},
            timeout_s=30.0)
        if status != 200:
            raise self.http_error(rt, status, body)

        data = (body or {}).get("data") or []
        vector = (data[0] or {}).get("embedding") if data else None
        if not isinstance(vector, list) or not vector:
            # A 200 with no vector is a broken response, not an empty result.
            raise AIError(code=INVALID_RESPONSE,
                          provider_type=self.PROVIDER_TYPE,
                          provider_instance_id=rt.instance_id,
                          provider_detail="embeddings response carried no vector")
        usage = self.extract_usage(body)
        return {"embedding": vector,
                "model": str((body or {}).get("model") or model_id),
                "tokens_input": usage.get("tokens_in")}
