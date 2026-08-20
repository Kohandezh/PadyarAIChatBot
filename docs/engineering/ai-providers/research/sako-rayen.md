# SAKOO / Rayen — سکوی ملی هوش مصنوعی (National AI Platform)

Status: adapter IMPLEMENTED (2026-08-20). Live verification: PENDING
INSTALLATION — the service is IP-allowlisted and the development machine is
not authorized (verified 2026-08-20: the edge accepts TCP on 443/80, reads the
ClientHello and closes without one byte). The operator verifies from the
whitelisted deployment environment via Admin → AI → Test Connection.

## Contract (supplied Rayen OpenAPI 3.0, API 1.0.0)

| | |
|---|---|
| Base server | `https://rmgpilot.aip.sharif.ir/` |
| OpenAPI | `https://rmgpilot.aip.sharif.ir/openapi.json` |
| Swagger UI | `https://rmgpilot.aip.sharif.ir/docs` |
| Chat | `POST /v1/chat/completions` — `{model, messages[{role,content}], temperature, max_tokens}` |
| Embeddings | `POST /v1/embeddings` — `{model, input}` |
| Models | `GET /v1/models` (authoritative catalog; refresh from Admin) |
| Responses | 200 / 401 / 404 / 500 → normalized via the shared taxonomy |

Documented example models — examples only, never bootstrapped:
`rayen-gemma4-31b` (chat), `rayen-jina-v5` (embeddings).

## Authentication

The documentation confirms auth exists (401 "Authentication required") without
naming the scheme. The adapter uses `Authorization: Bearer <secret>` — the
standing mechanism of the OpenAI-compatible wire protocol Rayen implements and
this project's existing generic-provider representation. The credential is
entered by the operator in Admin → AI, stored via the encrypted provider
secret column, never echoed, value-registered with the log scrubber. If
deployment reveals a different header, only `SakooAdapter.auth_headers`
changes; storage and configuration do not.

## Implementation

`app/services/ai/adapters/sakoo.py` — subclass of the hardened
OpenAI-compatible adapter. Inherits: SSRF endpoint policy (public trust),
DNS pinning, TLS verification, redirect refusal, error normalization, secret
redaction, circuit breaker, routing, usage accounting.

Rayen-specific strictness: only documented fields are sent. `top_p`, `stream`,
`response_format` and reasoning controls are NOT in the documented schema and
are never transmitted; `supports_json_object()` is false. Capabilities
claimed: chat, model discovery, embeddings. NOT claimed (undocumented):
streaming, STT, image, reasoning.

Embeddings: `SakooAdapter.embed()` implements the documented shape and is
contract-tested. No Padyar runtime path consumes provider embeddings today —
retrieval deliberately uses the local model2vec index (offline exhibition
provisioning). The method is real and ready to wire, not a faked integration.

## Deployment checklist (whitelisted environment)

1. Admin → AI → Providers → create **SAKOO / Rayen** (starts disabled).
2. Enter the Rayen token; save (stored encrypted; never displayed again).
3. Test Connection → expects the model list.
4. Refresh Models → catalog populated from `GET /v1/models`.
5. Enable the provider; add it to routing targets.

Tests: `tests/test_ai_sakoo.py` (34, fully mocked; no live network in any
default suite).
