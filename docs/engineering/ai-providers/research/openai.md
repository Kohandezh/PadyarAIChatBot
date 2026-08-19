# OpenAI

> Research snapshot. Every fact below is traceable to an official OpenAI source listed under `## Sources`.
> Anything not verifiable from an official page is written as **UNKNOWN**. Nothing here is from memory.
>
> Note on tooling: `platform.openai.com/docs/*` returns HTTP 403 to automated fetches. OpenAI's current
> developer docs are served from **`developers.openai.com`**, and the machine-readable contract is the
> official **`github.com/openai/openai-openapi`** repo (last commit to `openapi.yaml`: **2026-08-15**,
> spec `info.version: 2.3.0`). Both are first-party OpenAI sources.

## Sources

All fetched **2026-08-18**.

- https://developers.openai.com/api/reference/ — auth, base URL, org/project headers
- https://raw.githubusercontent.com/openai/openai-openapi/master/openapi.yaml — official OpenAPI 3.1.0 spec, `info.version: 2.3.0`, commit dated 2026-08-15
- https://developers.openai.com/api/docs/guides/migrate-to-responses — Responses vs Chat Completions recommendation + param mapping
- https://developers.openai.com/api/docs/guides/text — text generation, recommended endpoint, response shape
- https://developers.openai.com/api/docs/models — model catalog
- https://developers.openai.com/api/docs/models/gpt-5.6 — `gpt-5.6` alias behaviour
- https://developers.openai.com/api/docs/models/gpt-5.6-sol — flagship model card
- https://developers.openai.com/api/docs/models/gpt-5.6-terra — mid-tier model card
- https://developers.openai.com/api/docs/models/gpt-5.6-luna — cheap/fast model card
- https://developers.openai.com/api/reference/resources/models/methods/list — `GET /models` response shape
- https://developers.openai.com/api/docs/pricing — pricing table
- https://developers.openai.com/api/docs/guides/error-codes — HTTP status codes + retry guidance
- https://developers.openai.com/api/docs/guides/rate-limits — rate limit headers, dimensions, tiers
- https://developers.openai.com/api/docs/guides/streaming-responses — streaming
- https://developers.openai.com/api/docs/guides/function-calling — tool/function calling
- https://developers.openai.com/api/docs/guides/structured-outputs — JSON schema output
- https://developers.openai.com/api/docs/guides/prompt-caching — cached token fields
- https://developers.openai.com/api/docs/deprecations — lifecycle
- https://developers.openai.com/api/reference/python — SDK timeout/retry defaults
- https://api.github.com/repos/openai/openai-openapi/commits?path=openapi.yaml — spec freshness check

## Auth

| Item | Value | Source |
|---|---|---|
| Scheme | HTTP bearer (`securitySchemes.ApiKeyAuth: {type: http, scheme: bearer}`) | openapi.yaml |
| Header | `Authorization: Bearer OPENAI_API_KEY_OR_ACCESS_TOKEN` | api/reference |
| Org selector | `OpenAI-Organization: $ORGANIZATION_ID` | api/reference |
| Project selector | `OpenAI-Project: $PROJECT_ID` | api/reference |
| Content type | `Content-Type: application/json` | openapi.yaml curl examples |

- Org/project headers are **optional**; documented as needed when a key can access multiple orgs/projects.
- A second scheme `AdminApiKeyAuth` (also HTTP bearer) exists for `/organization/*` admin endpoints. Not needed for a chatbot.
- Docs also mention "workload identity federation for short-lived access tokens" as an alternative to a static key. Details **UNKNOWN** (not fetched).
- No `OpenAI-Beta` header is needed for Responses or Chat Completions. `OpenAI-Beta: assistants=v2` appears only on Assistants endpoints, which are being shut down (see `## Deprecation`).

## Endpoints

- **Base URL:** `https://api.openai.com/v1` (`servers[0].url` in openapi.yaml; api/reference states `https://api.openai.com/v1/`).
- **API version scheme:** path-prefix `/v1` only. There is **no** date-based version header (unlike Anthropic's `anthropic-version`). The `2.3.0` in the spec is the *spec document* version, not an API version you send.

Endpoints relevant to a text chatbot:

| Purpose | Method + path | Notes |
|---|---|---|
| **Recommended generation** | `POST /v1/responses` | Responses API. Docs: *"While Chat Completions remains supported, Responses is recommended for all new projects."* |
| Legacy/compat generation | `POST /v1/chat/completions` | Still fully supported; explicitly **not** deprecated. |
| Retrieve a stored response | `GET /v1/responses/{response_id}` | Only useful with `store: true`. |
| Cancel a background response | `POST /v1/responses/{response_id}/cancel` | |
| Model listing | `GET /v1/models` | |
| Model retrieve | `GET /v1/models/{model}` | |
| Legacy completions | `POST /v1/completions` | Legacy. Do not use. |

Also present in the spec but out of scope here: `/v1/embeddings`, `/v1/moderations`, `/v1/batches`,
`/v1/conversations/*`, `/v1/responses/input_tokens`, `/v1/responses/compact`, plus `?beta=true` variants of
the responses paths (`operationId: beta_createResponse`). The purpose/stability contract of the `?beta=true`
query variants is **UNKNOWN**.

## Request shape

### `POST /v1/responses` (recommended)

Minimal, verbatim from the spec's curl example:

```bash
curl https://api.openai.com/v1/responses \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $OPENAI_API_KEY" \
  -d '{
    "model": "gpt-5.4",
    "input": "Tell me a three sentence bedtime story about a unicorn."
  }'
```

Required: `model`, `input`. `input` accepts a string **or** an array of typed input items.

Top-level parameters an adapter cares about (names verbatim from `CreateResponse` +
`ResponseProperties` + `ModelResponseProperties` in openapi.yaml):

| Param | Type / notes |
|---|---|
| `model` | string |
| `input` | string or array of input items |
| `instructions` | string — *"A system (or developer) message inserted into the model's context."* Replaces the `system` role message. |
| `stream` | boolean, default `false` |
| `stream_options` | object; currently `{ "include_obfuscation": bool }` |
| `max_output_tokens` | integer, minimum 16; *"including visible output tokens and reasoning tokens"* |
| `temperature` | number |
| `top_p` | number |
| `top_logprobs` | integer |
| `text` | object — carries `text.format` (see `## Capabilities`) |
| `tools` | array of tool defs |
| `tool_choice` | tool selection |
| `parallel_tool_calls` | boolean, default `true` |
| `max_tool_calls` | integer |
| `reasoning` | object (`effort`, `summary`, `context`) |
| `store` | boolean, **default `true`** — stores the response server-side for later retrieval |
| `previous_response_id` | string — server-side conversation chaining |
| `conversation` | associate with a Conversations object |
| `background` | boolean — run asynchronously |
| `include` | array of extra payloads to return (e.g. `reasoning.encrypted_content`, `message.output_text.logprobs`) |
| `context_management` | array — token compaction config |
| `moderation` | object |
| `metadata` | object |
| `service_tier` | enum: `auto`, `default`, `flex`, `scale`, `priority`, `fast`, `ultrafast` (default `auto`) |
| `prompt_cache_key` | string — cache routing key |
| `prompt_cache_retention` | cache lifetime; documented values `"in_memory"` and `"24h"` |
| `safety_identifier` | string |
| `user` | string |
| `truncation` | **deprecated** in the spec; enum `auto` / `disabled`, default `disabled` |

> Privacy note for this project: `store` defaults to **`true`**. If server-side retention is not wanted,
> the adapter must send `"store": false` explicitly.

### `POST /v1/chat/completions` (compat path)

Required: `model`, `messages` (`CreateChatCompletionRequest.required`). Notable: `max_tokens` is
**deprecated** in favour of `max_completion_tokens` — spec text: *"This value is now deprecated in favor of
`max_completion_tokens`, and is not compatible with o-series models."*

Chat Completions ⇄ Responses parameter mapping (from the migration guide):

| Chat Completions | Responses |
|---|---|
| `messages` | `input` (string or array) |
| system/developer message | top-level `instructions` |
| `response_format` | `text.format` |
| `max_tokens` / `max_completion_tokens` | `max_output_tokens` |
| `choices[0].message.content` | `output_text` helper, or walk `output[]` |

## Response shape

### `POST /v1/responses` — the `Response` object

Required top-level fields (`Response.required` in openapi.yaml): `id`, `object`, `created_at`, `error`,
`incomplete_details`, `instructions`, `model`, `tools`, `output`, `parallel_tool_calls`, `metadata`,
`tool_choice`, `temperature`, `top_p`.

Other top-level fields present: `status`, `completed_at`, `output_text`, `usage`, `reasoning`, `store`,
`text`, `truncation`, `service_tier`, `previous_response_id`, `conversation`, `max_output_tokens`,
`prompt_cache_options`, `moderation`, `user`.

- `object` is always the literal `"response"`.
- `status` enum: `completed`, `failed`, `in_progress`, `cancelled`, `queued`, `incomplete`.
- `incomplete_details.reason` enum: `max_output_tokens`, `content_filter`.
- `error` is `null` or `{ "code": <ResponseErrorCode>, "message": string }`.
- `output` is an **array** of items. The text guide warns: *"The `output` array often has more than one item in it!"* — it can hold reasoning items and tool calls alongside the assistant message. A text message item is `{type: "message", id, status, role: "assistant", content: [{type: "output_text", text, annotations: []}]}`.
- `output_text` is a top-level convenience aggregation of all text output. **Caveat:** the text guide describes `output_text` as an SDK convenience property; the spec also lists it on the wire `Response` object. Whether the raw HTTP JSON always populates it is **UNKNOWN** — an adapter should walk `output[]` and treat `output_text` as an optimisation, not a contract.

Verbatim example from the spec (trimmed):

```json
{
  "id": "resp_67ccd3a9da748190baa7f1570fe91ac604becb25c45c1d41",
  "object": "response",
  "created_at": 1741476777,
  "status": "completed",
  "completed_at": 1741476778,
  "error": null,
  "incomplete_details": null,
  "instructions": null,
  "max_output_tokens": null,
  "model": "gpt-4o-2024-08-06",
  "output": [
    { "type": "message", "id": "msg_...", "status": "completed", "role": "assistant",
      "content": [ { "type": "output_text", "text": "…", "annotations": [] } ] }
  ],
  "parallel_tool_calls": true,
  "previous_response_id": null,
  "reasoning": { "effort": null, "summary": null, "context": null },
  "store": true,
  "temperature": 1,
  "text": { "format": { "type": "text" } },
  "tool_choice": "auto",
  "tools": [],
  "top_p": 1,
  "truncation": "disabled",
  "usage": {
    "input_tokens": 328,
    "input_tokens_details": { "cached_tokens": 0, "cache_write_tokens": 0 },
    "output_tokens": 52,
    "output_tokens_details": { "reasoning_tokens": 0 },
    "total_tokens": 380
  },
  "user": null,
  "metadata": {}
}
```

### `POST /v1/chat/completions` — the chat completion object

Top-level fields (`CreateChatCompletionResponse`): `id`, `choices`, `created`, `model`, `metadata`,
`service_tier`, `system_fingerprint`, `object`, `usage`, `moderation`.

## Model listing

- **`GET /v1/models`** (`operationId: listModels`). Auth header only, no body, no query params.

```bash
curl https://api.openai.com/v1/models \
  -H "Authorization: Bearer $OPENAI_API_KEY"
```

Response (`ListModelsResponse`):

```json
{
  "object": "list",
  "data": [
    { "id": "model-id-0", "object": "model", "created": 1686935002,
      "owned_by": "organization-owner", "shutdown_date": null },
    { "id": "model-id-2", "object": "model", "created": 1686935002,
      "owned_by": "openai", "shutdown_date": "2026-10-23" }
  ]
}
```

Model object fields: `id` (string), `object` (always `"model"`), `created` (unix seconds),
`owned_by` (string), `shutdown_date` (string or `null` — *"Shutdown date or null if not announced"*).

`shutdown_date` is directly usable for a lifecycle warning in the admin UI.

There is **no** documented flag on this response telling you which models support the Responses endpoint,
context window size, or pricing — that data lives only on the docs site. Programmatic capability discovery
is therefore **UNKNOWN / not available**; the model list must be filtered client-side.

## Models

Verbatim IDs from https://developers.openai.com/api/docs/models (fetched 2026-08-18).

### Current frontier family (GPT-5.6)

| ID | Positioning (quoted) | Context | Max output | Knowledge cutoff |
|---|---|---|---|---|
| `gpt-5.6-sol` | *"frontier model for complex professional work"* | 1,050,000 (max input 922,000) | 128,000 | Feb 16, 2026 |
| `gpt-5.6-terra` | *"designed for workloads that balance intelligence and cost"*; *"roughly corresponds to the mini model tier used in earlier GPT-5 families"* | 1,050,000 | 128,000 | Feb 16, 2026 |
| `gpt-5.6-luna` | *"cost-sensitive, high-volume workloads"*; *"roughly corresponds to the nano model tier used in earlier GPT-5 families"* | 1,050,000 | 128,000 | Feb 16, 2026 |

All three support **Chat Completions, Responses, and Batch**. None support Realtime, Assistants,
Fine-tuning, Embeddings, image/video generation, speech, transcription, moderation, or legacy Completions.
`gpt-5.6-sol` documents streaming, structured outputs, function calling, file search, image input,
web search, and prompt caching.

**Aliases:** the docs state *"The `gpt-5.6` alias routes requests to GPT-5.6 Sol."* Whether `terra`/`luna`
have their own floating aliases is **UNKNOWN**. Dated snapshot IDs for the 5.6 family are **UNKNOWN** —
each model card lists only the bare ID as its available snapshot.

### Recommendation for this project

- **(a) General chat →** `gpt-5.6-terra`. Balanced tier, 1.05M context, supported on Responses. Use `gpt-5.6-sol` only if quality demands it (2.5× the input price, 2.5× output).
- **(b) Cheap fast classification →** `gpt-5.6-luna`. Explicitly the cost-sensitive/high-volume tier at $0.20 / $1.20 per 1M.
- Note `gpt-5-nano` is still cheaper on paper ($0.05 / $0.40) but belongs to the **deprecated** GPT-5 generation — its dated snapshot `gpt-5-nano-2025-08-07` shuts down **2026-12-11**. Do not build on it.

### Other text models still listed on the pricing page

`gpt-5.5`, `gpt-5.5-pro`, `gpt-5.4`, `gpt-5.4-mini`, `gpt-5.4-nano`, `gpt-5.4-pro`, `gpt-5.2`,
`gpt-5.2-pro`, `gpt-5.1`, `gpt-5`, `gpt-5-mini`, `gpt-5-nano`, `gpt-5-pro`, `gpt-4.1`, `gpt-4.1-mini`,
`gpt-4.1-nano`, `gpt-4o`, `gpt-4o-mini`, `o1`, `o1-pro`, `o3`, `o3-pro`, `o3-mini`, `o4-mini`,
`gpt-4-turbo-2024-04-09`, `gpt-4-0613`, `gpt-3.5-turbo` (+ snapshots), `davinci-002`, `babbage-002`.
Specialised: `chat-latest`, `gpt-5.3-codex`, `gpt-5-search-api`. Embeddings:
`text-embedding-3-small`, `text-embedding-3-large`, `text-embedding-ada-002`. Moderation:
`omni-moderation-latest` (Free).

The exact semantics of the `chat-latest` ID (what it routes to, whether it is stable) are **UNKNOWN**.

## Capabilities

### Streaming

- Parameter: **`stream: true`** on `POST /v1/responses`.
- Transport: *"HTTP streaming (`stream=true`) over server-sent events (SSE)."* The spec confirms the 200 response also has a `text/event-stream` content type bound to `ResponseStreamEvent`.
- Event type names confirmed by the guide for basic text: `response.created`, `response.output_text.delta`, `response.completed`, plus `error`. The full event taxonomy is large (the spec defines dozens of `Response*Event` schemas); the complete list is **UNKNOWN** from the pages fetched — read the spec's `ResponseStreamEvent` union when implementing.
- `stream_options.include_obfuscation` (boolean): *"stream obfuscation adds random characters to an `obfuscation` field on streaming delta events to normalize payload sizes as a mitigation to certain side-channel attacks. These obfuscation fields are included by default"* — set to `false` to shrink the stream.
- Whether `usage` is carried on the final `response.completed` event is **UNKNOWN** (not stated on the streaming guide page fetched). Assume you may need a non-streaming fallback or must parse the terminal event's embedded `response` object.
- Chat Completions streaming uses SSE terminated by a `data: [DONE]` sentinel; not re-verified here — **UNKNOWN** for the current docs.

### Tool / function calling

Responses API tool definition (`tools[]` entry):

```json
{ "type": "function", "name": "...", "description": "...", "parameters": { /* JSON Schema */ }, "strict": true }
```

Note the flat shape — `name`/`parameters` sit at the top level of the tool object, **not** nested under a
`function` key (that is the Chat Completions shape).

Model emits an output item:

```json
{ "type": "function_call", "call_id": "...", "name": "...", "arguments": "<JSON-encoded string>" }
```

You return the result as an input item:

```json
{ "type": "function_call_output", "call_id": "...", "output": "..." }
```

Chat Completions equivalent uses a nested `function` object and correlates with **`tool_call_id`** instead
of `call_id`. Related params: `tool_choice`, `parallel_tool_calls` (default `true`), `max_tool_calls`.

### Structured output / JSON schema

Responses API:

```json
{ "text": { "format": { "type": "json_schema", "name": "schema_name", "schema": { }, "strict": true } } }
```

Chat Completions:

```json
{ "response_format": { "type": "json_schema", "json_schema": { "name": "schema_name", "schema": { }, "strict": true } } }
```

- Docs: *"Structured Outputs is available in our latest large language models, starting with GPT-4o."* For new projects the docs point at `gpt-5.6`.
- Refusals surface as a separate content item `{"type": "refusal", "refusal": "..."}` rather than schema-conformant JSON — the adapter must handle this branch.

### Other

- `reasoning: { effort, summary, context }` for reasoning control.
- Server-side conversation state via `store: true` + `previous_response_id`, or the Conversations API.
- `background: true` for async runs, polled via `GET /v1/responses/{id}`.

## Usage/tokens

### Responses API — `usage` (`ResponseUsage`)

All fields required by the schema:

```
input_tokens                              integer
input_tokens_details.cached_tokens        integer   (required)
input_tokens_details.cache_write_tokens   integer   (required)
output_tokens                             integer
output_tokens_details.reasoning_tokens    integer   (required)
total_tokens                              integer
```

`cached_tokens` = *"The number of tokens that were retrieved from the cache."*
`cache_write_tokens` = *"The number of input tokens that were written to the cache."*

### Chat Completions — `usage` (`CompletionUsage`)

```
prompt_tokens                                        integer
completion_tokens                                    integer
total_tokens                                         integer
prompt_tokens_details.cached_tokens                  integer
prompt_tokens_details.audio_tokens                   integer
completion_tokens_details.reasoning_tokens           integer
completion_tokens_details.audio_tokens               integer
completion_tokens_details.text_tokens                integer
completion_tokens_details.accepted_prediction_tokens integer
completion_tokens_details.rejected_prediction_tokens integer
```

**The two APIs use different names.** `input_tokens`/`output_tokens` (Responses) vs
`prompt_tokens`/`completion_tokens` (Chat Completions). A shared usage-normalising layer must map both.

### Prompt caching

- Automatic: *"Prompt Caching works automatically for eligible requests, with no code changes required."*
- Threshold: *"By default, caching is enabled automatically for prompts that are 1,024 tokens or longer."* For GPT-5.6+ this is strict; earlier models vary 1,024–2,048.
- Billing: *"Cached input tokens are billed at 0.1× the uncached input token rate."* For GPT-5.6+, **new cache writes are billed at 1.25×** — so `cache_write_tokens` is a real cost line, not free.
- `prompt_cache_key` groups requests sharing a prefix; docs suggest roughly **15 requests/minute per key**.
- `prompt_cache_retention`: `"in_memory"` (5–10 min, up to 1 hour) or `"24h"`.

## Errors

### Body shape (official spec, `ErrorResponse` / `Error`)

```json
{ "error": { "type": "...", "message": "...", "param": null, "code": null } }
```

`type` and `message` are strings; `param` and `code` are string-or-null. **All four keys are required**
by the schema, so `param`/`code` are present-but-null rather than absent.

### Documented HTTP statuses (error-codes guide, in page order)

| Status | Condition | Guidance |
|---|---|---|
| 401 | Invalid Authentication | *"Ensure the correct API key and requesting organization are being used"* |
| 401 | Incorrect API key provided | |
| 401 | Must be a member of an organization | |
| 401 | IP not authorized | |
| 403 | Country, region, or territory not supported | |
| 429 | Credit balance exhausted | do **not** retry |
| 429 | Rate limit reached for requests | *"Pace your requests and follow the `Retry-After` header when it's present"* |
| 429 | Organization spend limit reached | do **not** retry |
| 429 | Project spend limit reached | do **not** retry |
| 429 | Organization usage limit reached | do **not** retry |
| 500 | Server error | *"Retry your request after a brief wait"* |
| 503 | Engine overloaded | *"Retry your requests after a brief wait"* |
| 503 | Slow Down | *"Reduce your request rate to its original level, maintain a consistent rate for at least 15 minutes, and then gradually increase it"* |

Documented `code` values on 401/429: `credit_balance_exhausted`, `organization_spend_limit_exceeded`,
`project_spend_limit_exceeded`, `organization_usage_limit_exceeded`.

**Critical for the adapter:** several 429s are *billing* failures, not throughput failures. Retrying
`credit_balance_exhausted` or `*_spend_limit_exceeded` is pointless. Branch on `error.code`, not on the
429 status alone.

**The error-codes page does not document 400, 404, 408, or 413.** Their bodies follow the same
`ErrorResponse` shape per the spec, but their documented `type`/`code` values are **UNKNOWN**.

### In-band (non-HTTP) errors

A `POST /v1/responses` can return HTTP 200 with `status: "failed"` and a populated `error` object.
`ResponseErrorCode` enum: `server_error`, `rate_limit_exceeded`, `invalid_prompt`,
`data_residency_mismatch`, `bio_policy`, `vector_store_timeout`, plus image-specific codes
(`invalid_image`, `invalid_image_format`, `invalid_base64_image`, `invalid_image_url`, `image_too_large`,
`image_too_small`, `image_parse_error`, `image_content_policy_violation`, `invalid_image_mode`,
`image_file_too_large`, `unsupported_image_media_type`, `empty_image_file`, `failed_to_download_image`,
`image_file_not_found`).

Also `status: "incomplete"` with `incomplete_details.reason` of `max_output_tokens` or `content_filter`.
**An adapter that only checks the HTTP status will silently treat these as successes.**

Streaming emits a `ResponseErrorEvent` with `type: "error"`, plus `code` and `message`.

The Python SDK exception taxonomy (useful for naming our own error classes): `APIConnectionError`,
`APITimeoutError`, `AuthenticationError`, `BadRequestError`, `ConflictError`, `InternalServerError`,
`NotFoundError`, `PermissionDeniedError`, `RateLimitError`, `UnprocessableEntityError`.

## Rate limits

### Response headers (exact names, from the rate-limits guide)

```
Retry-After
x-ratelimit-limit-requests
x-ratelimit-limit-tokens
x-ratelimit-remaining-requests
x-ratelimit-remaining-tokens
x-ratelimit-reset-requests
x-ratelimit-reset-tokens
x-ratelimit-limit-project-tokens
x-ratelimit-remaining-project-tokens
x-ratelimit-reset-project-tokens
```

`Retry-After` = *"The minimum number of seconds to wait before retrying a temporary rate-limit error"*.
The spec's `TooManyRequests` response component declares `Retry-After` as an integer ≥ 1 and notes it
*"may be omitted for 429 responses that require user action"* — i.e. absence of `Retry-After` is itself
a signal that retrying will not help.

### Dimensions

RPM (requests/min), RPD (requests/day), TPM (tokens/min), TPD (tokens/day), IPM (images/min), and audio
minutes/min for streaming models.

### Usage tiers

| Tier | Qualification | Monthly limit |
|---|---|---|
| Free | allowed geography | $100 |
| Tier 1 | $5 paid | $100 |
| Tier 2 | $50 paid | $500 |
| Tier 3 | $100 paid | $1,000 |
| Tier 4 | $250 paid | $5,000 |
| Tier 5 | $1,000 paid | $200,000 |

Per-model RPM/TPM numbers per tier are **UNKNOWN** (not captured; they live in a table on the same guide
and change frequently — read them live rather than hardcoding).

### Retry guidance

- *"Use exponential backoff with jitter and limit the number of retries"*.
- Honour `Retry-After` when present; *"add a small random delay so multiple clients don't retry at the same time"*.
- *"avoid retrying quota or billing errors"*.
- Official SDK defaults (Python reference page, mirrored in TS/Java): **`max_retries` defaults to `2`**, with short exponential backoff. Retried by default: connection errors, **408**, **409**, **429**, and **>= 500**.
- **Timeout:** *"By default requests time out after 10 minutes."* (600 s) across the SDKs, configurable per-client and per-request. 10 minutes is far too long for a user-facing chat turn — set an explicit shorter timeout in our adapter.

## Pricing

Source: https://developers.openai.com/api/docs/pricing (fetched 2026-08-18). USD **per 1M tokens**,
standard (non-batch, non-flex, non-fast) tier.

| Model | Input | Cached input | Output |
|---|---|---|---|
| `gpt-5.6-sol` | $5.00 | $0.50 | $30.00 |
| `gpt-5.6-terra` | $2.00 | $0.20 | $12.00 |
| `gpt-5.6-luna` | $0.20 | $0.02 | $1.20 |
| `gpt-5.5` | $5.00 | $0.50 | $30.00 |
| `gpt-5.4` | $2.50 | $0.25 | $15.00 |
| `gpt-5.4-mini` | $0.75 | $0.075 | $4.50 |
| `gpt-5.4-nano` | $0.20 | $0.02 | $1.25 |
| `gpt-5.2` | $1.75 | $0.175 | $14.00 |
| `gpt-5.1` | $1.25 | $0.125 | $10.00 |
| `gpt-5` | $1.25 | $0.125 | $10.00 |
| `gpt-5-mini` | $0.25 | $0.025 | $2.00 |
| `gpt-5-nano` | $0.05 | $0.005 | $0.40 |
| `gpt-4.1` | $2.00 | $0.50 | $8.00 |
| `gpt-4.1-mini` | $0.40 | $0.10 | $1.60 |
| `gpt-4.1-nano` | $0.10 | $0.025 | $0.40 |
| `gpt-4o` | $2.50 | $1.25 | $10.00 |
| `gpt-4o-mini` | $0.15 | $0.075 | $0.60 |
| `o3` | $2.00 | $0.50 | $8.00 |
| `o4-mini` | $1.10 | $0.275 | $4.40 |
| `text-embedding-3-small` | $0.02 | — | — |
| `text-embedding-3-large` | $0.13 | — | — |
| `omni-moderation-latest` | Free | — | — |

The `gpt-5.6-sol` model card independently corroborates $5 / $0.50 / $30, which cross-checks the table.

Other tiers exist with their own tables on the same page — **Batch** (≈50% off), **Flex** (≈50% off),
and **Fast mode** (2× standard, e.g. `gpt-5.6-terra` at $4.00 / $0.40 / $24.00). Selected via
`service_tier`.

**Long-context surcharge:** the `gpt-5.6-sol` model card states requests exceeding **272K input tokens**
incur **2× input and 1.5× output** pricing. Whether the same surcharge applies to `terra` and `luna` is
**UNKNOWN**.

Cache-write billing for GPT-5.6+ is **1.25×** input rate (prompt-caching guide) — not shown in the
pricing table above.

## Health/test-connection strategy

**Use `GET https://api.openai.com/v1/models` with only the `Authorization` header.**

```bash
curl -sS -o /dev/null -w '%{http_code}' \
  https://api.openai.com/v1/models \
  -H "Authorization: Bearer $OPENAI_API_KEY"
```

Why this is the right probe:
- No request body, no model ID to guess, no tokens generated.
- 200 proves the key is valid and the org/project routing works.
- 401 distinguishes bad credentials; the `error.code` field distinguishes billing lockouts
  (`credit_balance_exhausted`, `*_spend_limit_exceeded`) from a plain bad key.
- The same response doubles as the model-picker population for the admin UI, and `shutdown_date`
  gives a free deprecation warning.

Whether OpenAI officially bills or rate-limits `GET /v1/models` is **UNKNOWN** (no statement found).
It generates zero tokens, so there is no token cost; treat request-count rate limits as possible and do
not poll it aggressively.

Optional second-stage probe if you must prove *generation* works (this one does cost money — a few
tokens on the cheapest model):

```bash
curl https://api.openai.com/v1/responses \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $OPENAI_API_KEY" \
  -d '{"model":"gpt-5.6-luna","input":"ping","max_output_tokens":16,"store":false}'
```

## OpenAI-compatibility verdict

**N/A — OpenAI is the reference implementation.**

The shape that third-party providers mean when they advertise "OpenAI-compatible" is almost always
**Chat Completions**, not Responses:

- `POST {base_url}/chat/completions`
- `Authorization: Bearer <key>`
- Request: `{ "model": str, "messages": [{"role": "system"|"user"|"assistant", "content": str}], "stream": bool, "temperature": float, "max_tokens": int, "tools": [...], "response_format": {...} }`
- Response: `{ "id", "object": "chat.completion", "created", "model", "choices": [{"index", "message": {"role","content"}, "finish_reason"}], "usage": {"prompt_tokens","completion_tokens","total_tokens"} }`
- Streaming: SSE with `object: "chat.completion.chunk"` and `choices[].delta`.
- Errors: `{"error": {"type","message","param","code"}}`.

Practical consequence for our provider layer: **the compatibility lingua franca is Chat Completions,
while OpenAI's own recommendation for new work is Responses.** These are different wire shapes
(`messages` vs `input`, `choices[].message.content` vs `output[]`, `prompt_tokens` vs `input_tokens`,
`response_format` vs `text.format`). Pick one internal canonical shape and adapt at the edge; do not
assume a single code path serves both OpenAI-Responses and an "OpenAI-compatible" third party.

Note also: OpenAI's own `max_tokens` is deprecated in favour of `max_completion_tokens`, but most
third-party "OpenAI-compatible" servers still expect `max_tokens`.

## Unknowns

Explicitly **not verified** from an official source:

1. Whether `gpt-5.6-terra` / `gpt-5.6-luna` have floating aliases analogous to `gpt-5.6` → `gpt-5.6-sol`.
2. Dated snapshot IDs for the GPT-5.6 family (each model card lists only the bare ID).
3. What `chat-latest` (pricing page, $5 / $0.50 / $30) routes to and whether it is stable.
4. Whether the long-context surcharge (>272K input → 2× in / 1.5× out) applies to `terra` and `luna`, or only to `sol`.
5. Whether the raw HTTP `Response` JSON always populates top-level `output_text`, or whether it is SDK-only.
6. Whether `usage` is delivered on the final streaming event, and the complete `ResponseStreamEvent` type list.
7. Chat Completions streaming details under the current docs (the `data: [DONE]` sentinel was not re-verified).
8. Documented `type`/`code` values for 400 / 404 / 408 / 413 — the error-codes guide omits these statuses entirely.
9. Per-model, per-tier RPM/TPM numbers.
10. Whether `GET /v1/models` is billed or counted against rate limits.
11. Purpose and stability of the `?beta=true` Responses path variants (`beta_createResponse` et al.) in the spec.
12. Details of "workload identity federation" short-lived access tokens as an auth alternative.
13. Server-side request timeout enforced by OpenAI itself (the 10-minute figure is an **SDK client** default, not a documented server limit).
14. Whether `platform.openai.com/docs/*` and `developers.openai.com/api/docs/*` are guaranteed to stay in sync — the former blocked automated fetching, so all doc facts here come from the latter plus the official OpenAPI repo.
