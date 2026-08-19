# xAI / Grok

Research date: **2026-08-18**. Every fact below is sourced to an official `docs.x.ai` page.
Nothing here is from memory. Items that could not be verified in official docs are marked **UNKNOWN**.

Primary machine-readable source: xAI publishes the entire documentation site as a single
plaintext bundle at <https://docs.x.ai/llms.txt> (~1.4 MB, all pages, section-delimited by
`===/path===`). This is the highest-fidelity source and was used for the exact JSON field lists below.

> Note on branding: several current doc pages render the vendor name as "SpaceXAI" rather than "xAI".
> The API host, key prefix and docs domain are unchanged (`api.x.ai`, `xai-...`, `docs.x.ai`).

---

## Sources

| Topic | URL |
|---|---|
| Full docs bundle (all pages, plaintext) | <https://docs.x.ai/llms.txt> |
| Overview / base URL | <https://docs.x.ai/overview> |
| Quickstart | <https://docs.x.ai/developers/quickstart> |
| Inference REST API overview (auth + base URL statement) | <https://docs.x.ai/developers/rest-api-reference/inference> |
| Chat + Responses endpoint reference | <https://docs.x.ai/developers/rest-api-reference/inference/chat> |
| Models endpoint reference | <https://docs.x.ai/developers/rest-api-reference/inference/models> |
| Other endpoints (`/v1/api-key`, `/v1/tokenize-text`) | <https://docs.x.ai/developers/rest-api-reference/inference/other> |
| Legacy & deprecated (incl. Anthropic-compatible endpoints) | <https://docs.x.ai/developers/rest-api-reference/inference/legacy> |
| Models list + aliases + caveats | <https://docs.x.ai/developers/models> |
| Grok 4.6 model page | <https://docs.x.ai/developers/grok-4-6> and <https://docs.x.ai/developers/models/grok-4.6> |
| Grok 4.3 model page | <https://docs.x.ai/developers/models/grok-4.3> |
| Pricing | <https://docs.x.ai/developers/pricing> |
| Rate limits | <https://docs.x.ai/developers/rate-limits> |
| Debugging / error status codes | <https://docs.x.ai/developers/debugging> |
| Reasoning (`reasoning_effort`) | <https://docs.x.ai/developers/model-capabilities/text/reasoning> |
| Streaming | <https://docs.x.ai/developers/model-capabilities/text/streaming> |
| Structured outputs | <https://docs.x.ai/developers/model-capabilities/text/structured-outputs> |
| Function calling | <https://docs.x.ai/developers/tools/function-calling> |
| Prompt caching usage & pricing | <https://docs.x.ai/developers/advanced-api-usage/prompt-caching/usage-and-pricing> |
| Responses vs Chat Completions migration | <https://docs.x.ai/developers/model-capabilities/text/comparison> |
| Model retirement (May 15, 2026) | <https://docs.x.ai/developers/migration/may-15-retirement> |
| Legacy Chat Completions guide | <https://docs.x.ai/developers/model-capabilities/legacy/chat-completions> |
| gRPC API reference | <https://docs.x.ai/developers/grpc-api-reference> |
| Status page | <https://status.x.ai> (RSS: <https://status.x.ai/feed.xml>) |

---

## Auth

* Header format: `Authorization: Bearer <XAI_API_KEY>`.
  Stated verbatim on <https://docs.x.ai/developers/rest-api-reference/inference>:
  "For all routes, you have to authenticate with the header `Authorization: Bearer <your xAI API key>`."
* Same bearer header is used for the gRPC surface (`api.x.ai`) — <https://docs.x.ai/developers/grpc-api-reference>.
* Env var convention in every doc example: `XAI_API_KEY`. Keys are issued at
  `https://console.x.ai/team/default/api-keys` (<https://docs.x.ai/developers/quickstart>).
* Key format: redacted example in docs is `"xai-...b14o"` → keys are prefixed `xai-`
  (<https://docs.x.ai/developers/rest-api-reference/inference/other>).
* No secondary header (no org/project header) is documented.
* Optional non-auth header: `x-grok-conv-id` — sticky routing for prompt-cache hits on Chat
  Completions (equivalent to the `prompt_cache_key` body field on `/v1/responses`)
  (<https://docs.x.ai/developers/advanced-api-usage/prompt-caching>, <https://docs.x.ai/developers/grok-4-6>).
* mTLS is available on a separate global host `mtls.api.x.ai`
  (<https://docs.x.ai/developers/advanced-api-usage/mtls>).

---

## Endpoints

Base URL: **`https://api.x.ai/v1`** (docs state "The base for all routes is at `https://api.x.ai`"
and every SDK example sets `base_url="https://api.x.ai/v1"`).
Version segment: **`/v1`**, single version, no dated versions and no version header.
Sources: <https://docs.x.ai/overview>, <https://docs.x.ai/developers/rest-api-reference/inference>.

| Method + path | Purpose | Status |
|---|---|---|
| `POST /v1/responses` | **Recommended** generation endpoint (Responses API) | Current, gets new features first |
| `GET /v1/responses/{response_id}` | Retrieve a stored response (stored 30 days) | Current |
| `DELETE /v1/responses/{response_id}` | Delete a stored response | Current |
| `POST /v1/responses/compact` | Compact a long input window into a canonical shorter one | Current |
| `POST /v1/chat/completions` | Chat Completions | **Legacy** — "New features will come to the Responses API first" |
| `GET /v1/chat/deferred-completion/{request_id}` | Fetch deferred completion (`202` while pending, `200` when done) | Current |
| `GET /v1/models` | Minimal model list | Current |
| `GET /v1/models/{model_id}` | Minimal per-model info | Current |
| `GET /v1/language-models` | **Rich** text/vision model metadata | Current |
| `GET /v1/language-models/{model_id}` | **Rich** per-model metadata | Current |
| `GET /v1/image-generation-models[/{id}]` | Image model metadata | Current |
| `GET /v1/video-generation-models[/{id}]` | Video model metadata | Current |
| `GET /v1/api-key` | Info about the calling API key (ACLs, blocked/disabled flags) | Current |
| `POST /v1/tokenize-text` | Tokenize text for a model | Current |
| `POST /v1/images/generations` | Image generation | Current |
| `POST /v1/batches` (+ `/requests`, `/results`, `:cancel`) | Batch API | Current |
| `POST /v1/files`, `/v1/collections/*` | Files + RAG collections | Current |
| `POST /v1/stt`, TTS, speech-to-speech (WebSocket) | Voice | Current |
| `POST /v1/completions` | Legacy text completion | **Legacy**, not supported by reasoning models |
| `POST /v1/messages` | **Anthropic-compatible** Messages | **Fully deprecated** (see compatibility section) |
| `POST /v1/complete` | **Anthropic-compatible** legacy completion | **Fully deprecated** |

Source for the table: <https://docs.x.ai/developers/rest-api-reference/inference/chat>,
<https://docs.x.ai/developers/rest-api-reference/inference/models>,
<https://docs.x.ai/developers/rest-api-reference/inference/other>,
<https://docs.x.ai/developers/rest-api-reference/inference/legacy>.

There is also a **gRPC** surface at `api.x.ai` used natively by the official Python SDK
(`pip install xai-sdk`), protobufs at <https://github.com/xai-org/xai-proto>
(<https://docs.x.ai/developers/grpc-api-reference>).

Regions: no standalone regions page exists today — `https://docs.x.ai/docs/key-information/regions`
now redirects to a **404**. Region availability is published per model on model detail pages
(e.g. `grok-4.6`: `us-east-1, us-west-2`; `grok-4.3`: `us-east-1, eu-west-1, us-west-2`).
Regional *endpoint hostnames* are **UNKNOWN** (referenced in the mTLS FAQ but never enumerated).

---

## Request shape

### `POST /v1/chat/completions` (OpenAI-shaped, legacy)

```json
{
  "model": "grok-4.6",
  "messages": [
    {"role": "system", "content": "You are a helpful assistant."},
    {"role": "user", "content": "What is 101*3?"}
  ]
}
```

Documented request fields (<https://docs.x.ai/developers/rest-api-reference/inference/chat>):

`messages`, `model`, `stream`, `stream_options.include_usage`, `temperature`, `top_p`,
`max_completion_tokens` (default **128,000** when unset), `max_tokens` (**DEPRECATED**),
`n`, `seed`, `stop`, `user`, `frequency_penalty`, `presence_penalty`, `logit_bias` (**unsupported**),
`logprobs` / `top_logprobs` (silently ignored on `grok-4.20` and newer),
`tools`, `tool_choice`, `parallel_tool_calls`, `response_format`,
`reasoning_effort`, `service_tier` (`"default"` | `"priority"`), `deferred`,
`prompt_cache_key`, `search_parameters{mode,sources,from_date,to_date,max_search_results,return_citations}`,
`web_search_options{search_context_size,user_location,filters}` (OpenAI-compat shims).

### `POST /v1/responses` (recommended)

```json
{
  "model": "grok-4.6",
  "input": "What is the meaning of life?"
}
```

Documented request fields: `input` (string or typed array), `model`, `instructions`
(alt system prompt; cannot combine with `previous_response_id`), `previous_response_id`,
`store` (default `true`), `include` (e.g. `["reasoning.encrypted_content"]`),
`max_output_tokens` (default **128,000**; includes reasoning tokens), `max_turns`, `max_tool_calls`,
`stream`, `temperature`, `top_p`, `top_k`, `min_p`, `tools`, `tool_choice`, `parallel_tool_calls`,
`text.format` (structured outputs), `reasoning{effort,summary,generate_summary}`,
`reasoning_effort` (non-standard convenience alias, only read when `reasoning` is unset),
`service_tier`, `prompt_cache_key`, `search_parameters`, `context_management`, `user`,
`background` (**unsupported**), `metadata` / `truncation` (compat-only, not supported).

Parameter mapping Chat Completions → Responses (<https://docs.x.ai/developers/model-capabilities/text/comparison>):
`messages` → `input`, `max_tokens` → `max_output_tokens`, plus new `previous_response_id`, `store`, `include`.

---

## Response shape

### Chat Completions

```json
{
  "id": "a3d1008e-...",
  "object": "chat.completion",
  "created": 1752854522,
  "model": "latest",
  "choices": [
    {
      "index": 0,
      "message": {"role": "assistant", "content": "101 multiplied by 3 is 303.", "refusal": null},
      "finish_reason": "stop"
    }
  ],
  "usage": { "...": "see Usage/tokens" },
  "system_fingerprint": "fp_3a7881249c"
}
```

Notable extras beyond OpenAI: `choices[].message.reasoning_content` (reasoning trace),
top-level `citations` (live-search pages), `output_files` (code-execution artifacts),
`service_tier`. `finish_reason` values documented: `"stop"`, `"length"`, `"end_turn"`, or `null`
mid-stream. `id` is a bare UUID (no `chatcmpl-` prefix in xAI's own examples).

### Responses API

```json
{
  "id": "ad5663da-...",
  "object": "response",
  "created_at": 1754475266,
  "status": "completed",
  "model": "latest",
  "output": [
    {"type": "message", "role": "assistant", "status": "completed",
     "content": [{"type": "output_text", "text": "101 multiplied by 3 is 303.", "annotations": []}]},
    {"type": "reasoning", "status": "completed",
     "summary": [{"type": "summary_text", "text": "..."}]}
  ],
  "usage": { "...": "see Usage/tokens" },
  "store": true,
  "incomplete_details": null
}
```

`status` ∈ `completed` | `in_progress` | `incomplete`. `error` object present on model failure.
Convenience field `output_text` exists in the OpenAI SDK objects (used in xAI's own examples).

### Streaming

`"stream": true` → SSE, `data:` frames, terminated by `data: [DONE]`
(<https://docs.x.ai/developers/model-capabilities/text/streaming>). Chat Completions chunks are
`object: "chat.completion.chunk"` with `choices[].delta`. Responses API emits typed events, e.g.
`response.reasoning_text.delta` / `response.reasoning_summary_text.delta`
(<https://docs.x.ai/developers/model-capabilities/text/reasoning>).

---

## Model listing

**Yes — xAI documents a richer per-model metadata endpoint than a plain list. This is directly
useful for a model catalog.** Source: <https://docs.x.ai/developers/rest-api-reference/inference/models>.

### `GET /v1/models` (minimal)

```json
{
  "object": "list",
  "data": [
    {
      "id": "grok-420-reasoning",
      "object": "model",
      "owned_by": "xai",
      "created": 1768003200,
      "aliases": [],
      "context_length": 256000,
      "prompt_text_token_price": 20000,
      "cached_prompt_text_token_price": 2000,
      "prompt_image_token_price": 0,
      "completion_text_token_price": 80000,
      "prompt_text_token_price_long_context": 40000,
      "completion_text_token_price_long_context": 160000,
      "long_context_threshold": 128000
    }
  ]
}
```

Note this is already richer than OpenAI's `/v1/models`: it carries `context_length`, `aliases`
and full pricing. Image models instead carry `image_price` and a `pricing[]` array of
`{quality, resolution, price_per_image}`.

### `GET /v1/language-models` (richest — recommended for catalog)

Response root is **`{"models": [...]}`**, not `{"object":"list","data":[...]}`.
Documented as: "Additional information compared to `/v1/models` includes modalities, fingerprint and alias(es)."

Fields per model: `id`, `aliases[]`, `object` (`"model"`), `owned_by`, `created`, `version`,
`fingerprint`, `input_modalities[]` (`"text"`, `"image"`), `output_modalities[]`,
`prompt_text_token_price`, `cached_prompt_text_token_price`, `prompt_image_token_price`,
`completion_text_token_price`, the three `*_long_context` variants,
`long_context_threshold`, `search_price`.

Per-model variants: `GET /v1/models/{model_id}` and `GET /v1/language-models/{model_id}`
(same object, un-wrapped). Parallel families exist for
`/v1/image-generation-models[/{id}]` (adds `max_prompt_length`, `image_price`, `pricing[]`)
and `/v1/video-generation-models[/{id}]`.

**Price unit gotcha:** all `*_token_price` integers are "USD cents per 100 million tokens".
So `20000` → 20000 / 1e8 cents per token → **$2.00 per 1M tokens**. Divide by 10,000 to get
USD per 1M tokens. `image_price` is in USD cents; `pricing[].price_per_image` is in
1/100,000,000ths of a USD cent.

**Caveat for a catalog UI:** xAI's own documented examples show `"id": "latest"` as a returned
model id with the real names in `aliases` (e.g. `aliases: ["grok-4.3-latest"]`, or
`["grok-4","grok-4-latest"]`). Do not assume `id` is the human-facing slug — render `aliases` too.

---

## Models

Exactly as listed on <https://docs.x.ai/developers/models> and <https://docs.x.ai/developers/pricing>
on 2026-08-18. **There is no `-mini` model and no `-fast` model in the current lineup** — those were
retired (see below). Do not assume a "grok-3-mini"-style cheap tier still exists.

### Text models (current)

| Model ID | Context | Notes |
|---|---|---|
| `grok-4.6` | 500k | Frontier model; recommended by xAI for **both chat and code**. Knowledge cutoff **2026-02-01**. Text+image in, text out. Reasoning: `low`/`medium`/`high` (default)/`xhigh`. |
| `grok-4.5` | 500k | Previous frontier. Reasoning `low`/`medium`/`high`; `xhigh` is silently treated as `high`. |
| `grok-4.3` | **1M** | Cheapest general text model. Text+image → text. Function calling + structured outputs + reasoning. Regions `us-east-1, eu-west-1, us-west-2`. |
| `grok-4.20-0309-reasoning` | 1M | Dated reasoning variant. |
| `grok-4.20-0309-non-reasoning` | 1M | Dated non-reasoning variant. |
| `grok-4.20-multi-agent-0309` | 1M | `reasoning.effort` controls **agent count (4 or 16)**, not depth. |
| `grok-build-0.1` | 256k | Agentic coding model (powers Grok Build CLI). |

### Image / video / voice

`grok-imagine-image-2.0`, `grok-imagine-image`, `grok-imagine-image-quality`,
`grok-imagine-video-1.5`, `grok-imagine-video`, `grok-voice-think-fast-2.0`
(`grok-voice-think-fast-1.0` deprecated).

### (a) General chat → `grok-4.6`

xAI's explicit recommendation: "For everything else, including code, use Grok 4.6. It is the most
intelligent and fastest model we've built." Chat: Grok 4.6. (<https://docs.x.ai/developers/models>)

### (b) Cheap / fast classification → `grok-4.3` with low or no reasoning

There is **no dedicated cheap classification model documented today**. The documented cheap/fast
path is `grok-4.3` at a reduced reasoning effort. xAI's own retirement guide makes this explicit:
the retired fast/non-reasoning slugs now resolve to
`grok-4.3` with `low` reasoning effort (reasoning workloads) or
`grok-4.3` with `none` reasoning effort (non-reasoning workloads)
(<https://docs.x.ai/developers/migration/may-15-retirement>).
At $1.25 in / $2.50 out per 1M it is the cheapest general text model in the lineup.

### Retired slugs (redirect, do not use for new work)

Retired **2026-05-15 12:00 PM PT**, per <https://docs.x.ai/developers/migration/may-15-retirement>:
`grok-4-1-fast-reasoning`, `grok-4-1-fast-non-reasoning`, `grok-4-fast-reasoning`,
`grok-4-fast-non-reasoning`, `grok-4-0709`, `grok-code-fast-1`, `grok-3`, `grok-imagine-image-pro`.
The slugs still resolve (redirected to `grok-4.3`, or `grok-build-0.1` for `grok-code-fast-1`,
or `grok-imagine-image-quality`) **but bill at the redirect target's price**. A validation layer that
merely checks "does the request 200?" will not catch a stale slug — check against `/v1/language-models`.

### Alias convention

(<https://docs.x.ai/developers/models>) `<modelname>` → latest stable; `<modelname>-latest` → latest;
`<modelname>-<date>` → pinned immutable release.

---

## Capabilities

### Streaming
`"stream": true` on both `/v1/chat/completions` and `/v1/responses`. SSE, terminated by
`data: [DONE]`. `stream_options: {"include_usage": true}` on Chat Completions adds a final
usage-bearing chunk (other chunks then carry `null` usage). Supported by all text-output models;
**not** supported by image-generation models.
(<https://docs.x.ai/developers/model-capabilities/text/streaming>)

### Tool / function calling
Parameters: `tools` (max **128** tools per request; the tool-schema table separately says
"max 200 tools per request" — the two docs disagree), `tool_choice`, `parallel_tool_calls`.
`tool_choice` values: `"auto"` (default), `"required"`, `"none"`, or
`{"type": "function", "function": {"name": "..."}}` to force one.
Responses API tool objects are **flat**: `{"type":"function","name","description","parameters"}` —
no nested `function` wrapper (that differs from OpenAI Chat Completions style).
Server-side built-in tools are declared the same way: `{"type":"web_search"}`, `{"type":"x_search"}`,
`code_execution`, `image_generation`, `collections_search`, `attachment_search`, remote MCP.
Streaming caveat: "With streaming, the function call is returned in whole in a single chunk, not
streamed across chunks." (<https://docs.x.ai/developers/tools/function-calling>)

### Structured output
Chat Completions: `response_format` with `type` ∈ `"text"` | `"json_object"` | `"json_schema"`
(schema under `response_format.json_schema`).
Responses API: `text.format`.
Tool arguments are always strict-schema-conformant (`strict` is implicitly always `true`).
JSON Schema: Draft 2020-12 preferred, Draft-07 accepted; `additionalProperties` **defaults to `false`**
and must be explicitly set `true`; supported keywords include `anyOf`/`oneOf`/`allOf` (single subschema),
`$ref`/`$defs` (non-circular), `enum`, `const`, `prefixItems`.
Enforced constraint ceilings: `minLength`/`maxLength` 2,048; `minItems`/`maxItems` 256;
`minProperties`/`maxProperties` 64; numeric bounds unlimited.
Rejected with **400**: zero-variant `enum`/`anyOf`, `true`/`false` property schemas,
`maxContains`/`minContains`, `items` as an array.
(<https://docs.x.ai/developers/model-capabilities/text/structured-outputs>)

### Reasoning
* **Is effort a parameter? Yes.** Responses API: `reasoning: {"effort": "..."}`; also a non-standard
  flat `reasoning_effort` alias (only read when `reasoning` is unset). Chat Completions: `reasoning_effort`.
* Levels: `"low"`, `"medium"`, `"high"` (default), `"xhigh"`. `"xhigh"` is `grok-4.6`+ only; on
  `grok-4.5` it is downgraded to `"high"`.
* **Reasoning cannot be disabled on grok-4.6 / grok-4.5.**
* **Documented parameter incompatibilities:** "`presencePenalty`, `frequencyPenalty`, and `stop`
  cannot be used with reasoning models. Requests that include them return an error."
  Also: `logprobs` / `top_logprobs` are silently ignored on `grok-4.20` and newer;
  `logit_bias` is unsupported; `presence_penalty`/`frequency_penalty` are "NOT SUPPORTED in
  Responses API" at all.
* Reasoning trace: `choices[].message.reasoning_content` (Chat Completions);
  `output[]` item of `type: "reasoning"` with `summary[].summary_text` (Responses).
  Encrypted reasoning is opt-in via `include: ["reasoning.encrypted_content"]` and can be replayed
  into a later turn.
* `grok-4.20-multi-agent`: `reasoning.effort` selects **agent count (4 or 16)**, not depth.
* **Doc inconsistency to flag:** the endpoint reference field description for both
  `reasoning_effort` and `reasoning.effort` still reads "Only supported by `grok-4.3`. Possible values
  are `none`, `low` (default), `medium`, `high`" — which contradicts the reasoning guide
  (`grok-4.6`/`grok-4.5`, default `high`, cannot be disabled) and the Grok 4.6 model page
  (`low/medium/high/xhigh`). Treat the reasoning guide + model pages as authoritative for 4.5/4.6,
  and treat `"none"` as **only** documented for `grok-4.3`
  (corroborated by the retirement guide's "grok-4.3 with `none` reasoning effort").

### Other
Prompt caching is **automatic** (set `prompt_cache_key` / `x-grok-conv-id` to maximize hits).
Vision input: jpg/jpeg/png, max 20 MiB per image, no image count limit, `detail` field supported.
Stateful conversations via `previous_response_id` (responses stored 30 days, `store` default `true`).
Deferred completions, Batch API, Priority Processing (`service_tier: "priority"`), WebSocket mode,
context compaction (`POST /v1/responses/compact`) are all documented.

---

## Usage/tokens

### Chat Completions `usage` (exact field names)

```
prompt_tokens
completion_tokens
total_tokens
prompt_tokens_details.text_tokens
prompt_tokens_details.audio_tokens
prompt_tokens_details.image_tokens
prompt_tokens_details.cached_tokens         <- cached tokens
completion_tokens_details.reasoning_tokens  <- reasoning tokens
completion_tokens_details.audio_tokens
completion_tokens_details.accepted_prediction_tokens
completion_tokens_details.rejected_prediction_tokens
num_sources_used                            <- live-search sources
cost_in_usd_ticks                           <- xAI-specific
```

### Responses API `usage` (exact field names)

```
input_tokens
output_tokens
total_tokens
input_tokens_details.cached_tokens          <- cached tokens
output_tokens_details.reasoning_tokens      <- reasoning tokens
num_sources_used
num_server_side_tools_used
server_side_tool_usage_details.{web_search_calls, x_search_calls, code_interpreter_calls,
  file_search_calls, document_search_calls, image_generation_calls, mcp_calls}
context_details.{input_tokens, output_tokens}
cost_in_usd_ticks
cost_in_nano_usd
```

**Cost fields are gold for our cost tracking and have no OpenAI equivalent.**
`cost_in_usd_ticks`: `TICKS_IN_USD_CENT = 100_000_000`, i.e. **10,000,000,000 ticks = $1**.
So `usd = cost_in_usd_ticks / 1e10`. `cost_in_nano_usd` = USD × 1e9 (Responses API only).

Billing/TPM notes: reasoning tokens bill at the **full completion** rate; cached tokens bill at the
reduced cached rate but **still count toward TPM**; long-context pricing is triggered by total prompt
tokens **including** cached tokens.
(<https://docs.x.ai/developers/advanced-api-usage/prompt-caching/usage-and-pricing>, <https://docs.x.ai/developers/rate-limits>)

Anthropic-compat (`/v1/messages`, deprecated) reports instead:
`usage.{input_tokens, output_tokens, cache_read_input_tokens, cache_creation_input_tokens}`
(`cache_creation_input_tokens` is marked **unsupported**).

---

## Errors

Documented HTTP statuses (<https://docs.x.ai/developers/debugging>):

| Status | Meaning | Documented remedy |
|---|---|---|
| `400` Bad Request | malformed request body/URL | check body and URL |
| `401` Unauthorized | missing or invalid auth header | supply `Authorization: Bearer <XAI_API_KEY>` |
| `403` Forbidden | permission | ask team admin |
| `404` Not Found | wrong endpoint / unknown resource | check body + endpoint URL |
| `405` Method Not Allowed | wrong verb | check the reference |
| `415` Unsupported Media Type | bad content type on POST | — |
| `422` Unprocessable Entity | a body field has an invalid format | check body against the reference |
| `429` Too Many Requests | rate limit exceeded | slow down or raise limits |
| `202` Accepted | **deferred completion still processing** (`/v1/chat/deferred-completion/{id}`) | poll again |

5xx statuses are **not enumerated** in the debugging table. Service disruptions are published at
<https://status.x.ai> (RSS <https://status.x.ai/feed.xml>).

### Error body shape

xAI does **not publish a canonical top-level REST error envelope**. What is documented:

* WebSocket-mode errors use an OpenAI-ish envelope
  (<https://docs.x.ai/developers/advanced-api-usage/websocket-mode>):
  ```json
  {"type": "error", "status": 400,
   "error": {"type": "invalid_request_error", "code": "websocket_connection_limit_reached",
             "message": "...", "param": "previous_response_id"}}
  ```
  Documented codes there: `previous_response_not_found`, `websocket_connection_limit_reached`.
* Async **video** jobs return `{"status": "failed", "error": {"code", "message"}}` with documented
  codes: `invalid_argument`, `permission_denied`, `failed_precondition`, `service_unavailable`,
  `internal_error` (<https://docs.x.ai/developers/model-capabilities/video/generation>).
* Responses API objects carry an `error` object field on failure, and `incomplete_details`.

**Observed (not documented):** an unauthenticated `GET https://api.x.ai/v1/models` on 2026-08-18
returned `HTTP/2 401` with body:
```json
{"code":"unauthenticated:no-credentials","error":"No credentials presented."}
```
i.e. a flat `{code, error}` pair where `error` is the human message — **not** the OpenAI
`{"error":{"message","type","code"}}` envelope. Our adapter must tolerate both shapes: read
`body.error.message` if `error` is an object, else `body.error` as a string, with `body.code`
as the machine code. Treat this as empirical, not contractual.

---

## Rate limits

Source: <https://docs.x.ai/developers/rate-limits>.

* Two dimensions, **per model**: **RPS** (requests/second) and **TPM** (tokens/minute).
  RPS is derived as RPM/60 so a minute's budget cannot be burned in one second.
* Tiers by cumulative spend since 2026-01-01, permanent once reached:
  Tier 0 = $0, Tier 1 = $50, Tier 2 = $250, Tier 3 = $1,000, Tier 4 = $5,000, Enterprise on request.
* Example limits: `grok-4.6` / `grok-4.5` — T0: 150 RPS / 50M TPM … T4: 500 RPS / 100M TPM.
  `grok-4.3`, `grok-4.20-*`, `grok-build-0.1` — T0: 37 RPS / 10M TPM … T4: 208 RPS / 85M TPM.
  `grok-4.20-multi-agent-0309` — T0: 9 RPS / 2.5M TPM … T4: 56 RPS / 21M TPM.
* TPM counts prompt + completion + **reasoning** + **cached** tokens.
* Exceeding any limit → **`429 Too Many Requests`**.
* **Retry guidance: exponential backoff.** xAI's own sample uses `wait = 2 ** attempt`, 5 max retries.
* Batch API requests **do not count** toward rate limits.
* **Rate-limit response headers: UNKNOWN.** No `x-ratelimit-*` / `retry-after` header is documented
  anywhere in the docs bundle. Do not build header-driven throttling; use 429 + backoff.

### Timeouts

No server-side request timeout is documented. Client guidance is repeated throughout: **raise the
client timeout for reasoning models** — every official example uses `timeout=3600` seconds
(`httpx.Timeout(3600.0)`, `curl -m 3600`), with an explicit caution that streaming + reasoning can
prematurely close a default-timeout connection
(<https://docs.x.ai/developers/model-capabilities/text/streaming>, <https://docs.x.ai/developers/model-capabilities/text/reasoning>).
The Grok CLI (not the API) documents a 600 s per-chunk SSE idle timeout, which is a reasonable
proxy for how long a stream may idle.

---

## Pricing

Pricing page: <https://docs.x.ai/developers/pricing> (per-model: <https://docs.x.ai/developers/models>).
**Currency: USD** — the page states verbatim "All prices are in USD."
Prices below are **per 1 million tokens**, as of 2026-08-18.

### Text models

| Model | Context | Input | Cached input | Output |
|---|---|---|---|---|
| `grok-4.6` (<200k prompt tokens) | 500k | **$2.00** | $0.50 | **$6.00** |
| `grok-4.6` (≥200k prompt tokens) | 500k | $4.00 | $1.00 | $12.00 |
| `grok-4.5` (<200k) | 500k | $2.00 | $0.30 | $6.00 |
| `grok-4.5` (≥200k) | 500k | $4.00 | $0.60 | $12.00 |
| `grok-4.3` (<200k) | 1M | **$1.25** | $0.20 | **$2.50** |
| `grok-4.3` (≥200k) | 1M | $2.50 | $0.40 | $5.00 |
| `grok-4.20-0309-reasoning` (<200k / ≥200k) | 1M | $1.25 / $2.50 | $0.20 / $0.40 | $2.50 / $5.00 |
| `grok-4.20-0309-non-reasoning` (<200k / ≥200k) | 1M | $1.25 / $2.50 | $0.20 / $0.40 | $2.50 / $5.00 |
| `grok-4.20-multi-agent-0309` (<200k / ≥200k) | 1M | $1.25 / $2.50 | $0.20 / $0.40 | $2.50 / $5.00 |
| `grok-build-0.1` (<200k / ≥200k) | 256k | $1.00 / $2.00 | $0.20 / $0.40 | $2.00 / $4.00 |

**Long-context cliff:** once a request's prompt reaches the threshold (200k for these models),
**all** tokens in that request bill at the higher rate — not just the excess.

### Image / video / voice

`grok-imagine-image-2.0` $0.04/image · `grok-imagine-image` $0.02/image ·
`grok-imagine-image-quality` $0.05/image · `grok-imagine-video-1.5` $0.080/sec ·
`grok-imagine-video` $0.050/sec · Speech-to-Speech (`grok-voice-think-fast-2.0`) $0.08/min audio
($4.80/hr) + $0.004 per text input · Speech-to-Text $0.10/hr REST, $0.20/hr streaming ·
Text-to-Speech $15.00 / 1M chars.

### Server-side tool surcharges (yes, documented)

Billed **in addition to** tokens, per 1,000 tool calls:

| Tool | Tool name | Cost / 1k calls |
|---|---|---|
| Web Search | `web_search` | **$5** |
| X Search | `x_search` | **$5** |
| Code Execution | `code_execution` / `code_interpreter` | **$5** |
| File Attachments | `attachment_search` | **$10** |
| Collections Search (RAG) | `collections_search` / `file_search` | **$2.50** |
| Image Generation | `image_generation` | Imagine API per-image rates |
| Image / X-video understanding | `view_image`, `view_x_video` | no invocation fee — token-based only |
| Remote MCP | set by MCP server | no invocation fee — token-based only |

Image Search is billed as Web Search. Live search sources consumed are reported in
`usage.num_sources_used`; tool call counts in `usage.server_side_tool_usage_details`.

### Other pricing modifiers

* **Batch API**: 20% off standard rates for `grok-4.3`, `grok-4.20-0309-reasoning`,
  `grok-4.20-0309-non-reasoning`, `grok-4.20-multi-agent-0309`. **All other models: no batch discount.**
  Discount applies to input, output, cached and reasoning tokens. Image/video billed at standard rates.
* **Priority Processing**: `service_tier: "priority"` bills at **2×** standard on all token types.
  You are only charged the premium when the response confirms `"service_tier": "priority"`.
  Not available for image gen, video gen, or Batch.
* **Storage**: files $0.025/GiB/day; collections $0.10/GiB/day; downloads $0.20/GiB.
* **Usage guidelines violation fee**: **$0.05 per request** for violations caught pre-generation
  in the Responses API; violating generations are still billed.

---

## Health/test-connection strategy

Recommended, in order of cost:

1. **`GET /v1/api-key`** — zero-token, zero-cost, and the single most informative probe. Returns
   `redacted_api_key`, `name`, `team_id`, `acls[]`, and the three booleans
   `api_key_blocked`, `api_key_disabled`, `team_blocked`. This distinguishes
   *bad key* from *disabled key* from *blocked team* — something a generic 401 cannot.
   (<https://docs.x.ai/developers/rest-api-reference/inference/other>)
2. **`GET /v1/language-models`** — zero-token, and simultaneously refreshes the model catalog with
   ids, aliases, modalities and prices. Use this as the catalog sync call.
   Fall back to `GET /v1/models` if the richer route is unavailable.
3. Avoid a paid `POST /v1/chat/completions` "ping" for health checks — every text model in the
   lineup is a reasoning model with `high` default effort, so a trivial prompt still burns
   reasoning tokens (xAI's own example shows 9 completion tokens alongside **94 reasoning tokens**).
   If a real generation probe is genuinely required, use `grok-4.3` with
   `reasoning_effort: "none"` and a tight `max_completion_tokens`.

Interpretation map for the UI:
`401` → key missing/invalid; `403` → key valid but lacks permission (check `acls`);
`404` → wrong base URL or path (most likely `/v1` omitted); `429` → live but throttled
(**still counts as a healthy connection**); network/DNS failure or `5xx` → check <https://status.x.ai>.

Timeouts: use a short timeout (5–10 s) for the health probe, but a **long** timeout (documented
examples use 3600 s) for real generation calls — do not share one timeout constant.

---

## OpenAI-compatibility verdict

### **OPENAI-COMPATIBLE + PROVIDER-SPECIFIC METADATA**

Justification, from the docs:

**Why OpenAI-compatible is enough for the transport layer:**
* xAI states it outright: "It offers advanced AI capabilities with **full compatibility with the
  OpenAI REST API**" (<https://docs.x.ai/developers/rest-api-reference/inference>).
* Every quickstart example is the stock OpenAI SDK with only `base_url="https://api.x.ai/v1"` and
  the xAI key swapped in — Python and JavaScript alike (<https://docs.x.ai/developers/quickstart>).
* `POST /v1/chat/completions` accepts and returns the exact OpenAI shapes:
  `messages[]`/`model`/`stream`/`temperature`/`top_p`/`tools`/`tool_choice`/`response_format`, and
  returns `object: "chat.completion"` with `choices[].message.content` and `finish_reason`.
* Streaming is standard SSE terminated by `data: [DONE]`, with
  `object: "chat.completion.chunk"` + `choices[].delta`.
* Bearer-token auth, `/v1` prefix, `/v1/models` — all conventional.

**Why a provider-specific metadata layer is still required:**
* **Model catalog.** `GET /v1/language-models` has a **non-OpenAI root key (`models`, not `data`)**
  and is the only source of `input_modalities` / `output_modalities` / `context_length` /
  per-token pricing / `long_context_threshold` / `aliases`. OpenAI's `/v1/models` shape carries none
  of that. Our catalog should read `/v1/language-models` and normalize.
* **Model-id semantics.** xAI's documented responses can return `"id": "latest"` with the real slug
  only in `aliases[]`. A naive OpenAI-shaped catalog would display "latest".
* **Cost tracking.** `usage.cost_in_usd_ticks` / `cost_in_nano_usd` are xAI-only and remove the need
  for a client-side price table — but need the tick conversion (÷ 1e10).
* **Reasoning.** `reasoning_content` (Chat Completions) and the `type: "reasoning"` output item
  with `summary[]` (Responses) are xAI-shaped; `"xhigh"` effort is not an OpenAI value; the
  "`stop` / penalties error out on reasoning models" rule is an xAI-specific validation we must
  enforce before sending.
* **Live search.** `search_parameters`, top-level `citations`, `num_sources_used` and the
  per-1k-call tool surcharges have no OpenAI analogue and directly affect cost.
* **Caching.** The `x-grok-conv-id` header / `prompt_cache_key` is required to get reliable cache
  hits — a plain OpenAI client will not send it and will pay full input price.
* **Errors.** The observed 401 body is `{"code", "error"}` (flat), not OpenAI's nested
  `{"error": {...}}` — our error parser must handle both.
* **Long-context price cliff.** Billing doubles for the *whole request* at 200k prompt tokens; no
  OpenAI-generic cost estimator models this.

**Anthropic compatibility — verified today: effectively gone.**
`POST /v1/messages` (Anthropic Messages) and `POST /v1/complete` still exist in the reference, but
both carry the banner: "**Deprecated**: The Anthropic SDK compatibility is **fully deprecated**.
Please migrate to the Responses API or gRPC."
(<https://docs.x.ai/developers/rest-api-reference/inference/legacy>).
Documented gaps even where it works: `cache_creation_input_tokens` is unsupported, `top_k` is
unsupported, `/v1/complete` streaming is unsupported and it is not supported by reasoning models,
no reasoning/thinking blocks, no server-side tool support. **Do not build on the Anthropic surface.**

**Practical recommendation:** target `POST /v1/chat/completions` through our existing OpenAI-compatible
client for generation (lowest integration cost, and it is the endpoint whose response shape our code
already parses), plus a thin xAI-specific catalog/health module hitting `GET /v1/language-models` and
`GET /v1/api-key`. Note that xAI labels Chat Completions **legacy** and ships new features to
`/v1/responses` first — so treat the Chat Completions path as a deliberate, revisit-later tradeoff,
not a permanent choice. If we later need stateful turns, encrypted reasoning replay, or server-side
tools, that forces a move to `/v1/responses` (also OpenAI-shaped, via `client.responses.create`).

---

## Unknowns

* **Rate-limit response headers — UNKNOWN.** No `x-ratelimit-limit-*`, `x-ratelimit-remaining-*`,
  or `retry-after` header is documented anywhere in the docs bundle. Backoff must be blind.
* **Canonical REST error envelope — UNKNOWN / undocumented.** Only WebSocket and video-job error
  shapes are specified. The flat `{"code","error"}` 401 body above is empirically observed on
  2026-08-18, not a documented contract.
* **5xx status codes — UNKNOWN.** The debugging table stops at 429.
* **Server-side request timeout — UNKNOWN.** Only client-side guidance (3600 s) is given.
* **Regional endpoint hostnames — UNKNOWN.** Regions are named per model
  (`us-east-1`, `us-west-2`, `eu-west-1`) and the mTLS FAQ references "regional endpoints", but the
  regions page (`/docs/key-information/regions`) now redirects to a 404 and no hostnames are listed.
* **Max tools per request — CONFLICTING.** The endpoint reference says 128; the function-calling
  tool-schema table says 200.
* **`reasoning_effort` model support — CONFLICTING** (see Capabilities). Endpoint reference says
  "only `grok-4.3`, default `low`, `none` allowed"; the reasoning guide and model pages say
  `grok-4.6`/`grok-4.5`, default `high`, cannot be disabled.
* **Whether `/v1/models` returns retired-but-redirecting slugs — UNKNOWN.** Not stated; must be
  probed against a live key before relying on the list to validate configured model ids.
* **Exact `finish_reason` enum for tool calls — UNKNOWN.** Docs list `stop`, `length`, `end_turn`,
  `null`; `tool_calls` is not named on the Chat Completions side (it is named `tool_use` only in the
  deprecated Anthropic `stop_reason`).
* **Batch API discount percentages beyond the listed 20% models — N/A** (docs state models not
  listed have no discount), but **per-model batch prices are only visible via a console toggle**,
  not published as a table.
* Nothing here was validated against a live authenticated key — no xAI credential was used. All
  request/response shapes are as documented, except the single unauthenticated 401 probe noted above.
