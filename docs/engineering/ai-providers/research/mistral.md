# Mistral AI

Research date: 2026-08-18. All facts below come from official Mistral sources
(`docs.mistral.ai`, including the machine-readable OpenAPI spec it publishes).
Anything not verifiable on an official page is marked **UNKNOWN**.

## Sources

| # | What | URL |
|---|------|-----|
| S1 | Official OpenAPI spec (downloaded and inspected directly; `openapi: 3.1.0`, `info.version: 1.0.0`, `title: Mistral AI API`) | https://docs.mistral.ai/openapi.yaml |
| S2 | API reference landing page | https://docs.mistral.ai/api/ |
| S3 | Chat endpoint reference | https://docs.mistral.ai/api/endpoint/chat |
| S4 | Models endpoint reference | https://docs.mistral.ai/api/endpoint/models |
| S5 | Models overview + deprecated/retired table | https://docs.mistral.ai/models |
| S6 | Model lifecycle policy (stages, aliases, notice periods) | https://docs.mistral.ai/inference/model-lifecycle |
| S7 | Pricing | https://docs.mistral.ai/inference/pricing |
| S8 | Error glossary (status codes, error body, retry strategy) | https://docs.mistral.ai/resources/error-glossary |
| S9 | Known limitations (context, rate limits, streaming, JSON mode, tools) | https://docs.mistral.ai/resources/known-limitations |
| S10 | Migration guides — incl. explicit OpenAI-compatible base URL | https://docs.mistral.ai/resources/migration-guides |
| S11 | First API request quickstart (auth header, curl, error table) | https://docs.mistral.ai/getting-started/quickstarts/developer/first-api-request |
| S12 | Chat completions capability guide | https://docs.mistral.ai/studio/conversations/chat-completion |
| S13 | JSON mode | https://docs.mistral.ai/studio/conversations/structured-output/json_mode |
| S14 | Structured output (custom / json_schema) | https://docs.mistral.ai/studio/conversations/structured-output/custom |
| S15 | Function calling | https://docs.mistral.ai/studio/conversations/function-calling |
| S16 | Priority Tier / `service_tier` | https://docs.mistral.ai/inference/priority-tier |
| S17 | Admin: usage and API rate limits | https://docs.mistral.ai/admin/billing-usage/usage-limits |
| S18 | Admin: workspace rate limits (RPS / TPM / tokens-per-month) | https://docs.mistral.ai/admin/workspaces/usage-limits |
| S19 | Model cards (per-model IDs, aliases, context, price) e.g. | https://docs.mistral.ai/models/mistral-medium-3-5-26-04 |

Note on `https://docs.mistral.ai/llms.txt`: it exists and returns 200, but every
`.../docs/....md` link inside it now 404s against the redesigned site. It is
**stale** — do not treat it as a current index.

## Auth

- Header: `Authorization: Bearer $MISTRAL_API_KEY` (S11 curl example, S3).
- OpenAPI (S1) declares a global `security: [{ApiKey: []}]` with
  `ApiKey: {type: http, scheme: bearer}` — i.e. bearer auth applies to every
  endpoint including `GET /v1/models`.
- Two other schemes exist in the spec for admin surfaces only:
  `AdminApiKey` (bearer, admin-scoped; "Standard workspace/inference API keys are
  rejected") and `DashboardUserContextAuth` (`x-api-key` header). Not needed for
  chat/models.
- No org/project header is documented for inference calls. Rate limits and
  billing are attached to the workspace that owns the key (S18).

## Endpoints

- Base URL / server (S1 `servers`): `https://api.mistral.ai`
- Version segment: `/v1` for chat, models, embeddings, classifiers, moderation,
  files, batch, OCR, audio, agents, conversations. (A `/v2` segment exists but
  only for beta Prompts.)
- OpenAI-compatible base URL as documented in S10: `https://api.mistral.ai/v1`

Relevant paths:

| Purpose | Method + path |
|---|---|
| Chat completion | `POST /v1/chat/completions` |
| Chat completion (streaming) | same path, `stream: true`, `Accept: text/event-stream` |
| List models | `GET /v1/models` |
| Retrieve model | `GET /v1/models/{model_id}` |
| Delete fine-tuned model | `DELETE /v1/models/{model_id}` |
| Embeddings | `POST /v1/embeddings` |
| Classification (Classifier Factory / classifier models) | `POST /v1/classifications` |
| Chat-shaped classification | `POST /v1/chat/classifications` |
| Moderation (raw text) | `POST /v1/moderations` |
| Moderation (conversation) | `POST /v1/chat/moderations` |
| FIM completion | `POST /v1/fim/completions` |
| Batch jobs | `POST /v1/batch/jobs` |

`GET /v1/models` accepts two optional query params (S1): `provider` and `model`
(both `string|null`).

## Request shape

`ChatCompletionRequest` (S1). `required: [messages, model]`,
**`additionalProperties: false`** — unknown fields are rejected.

| Param | Type | Default / constraint |
|---|---|---|
| `model` | string | required. Example in spec: `mistral-large-latest` |
| `messages` | array of `SystemMessage \| UserMessage \| AssistantMessage \| ToolMessage`, discriminated on `role` | required |
| `temperature` | number \| null | 0 – 1.5; **default varies by model — spec says call `/models` to get it** (`default_model_temperature`) |
| `top_p` | number \| null | (0, 1] |
| `max_tokens` | integer \| null | ≥ 0; prompt + `max_tokens` ≤ context length |
| `stream` | boolean | `false` |
| `stop` | string \| string[] \| null | — |
| `random_seed` | integer \| null | ≥ 0, deterministic sampling |
| `response_format` | `ResponseFormat` object | `{"type": "text"}` |
| `tools` | array \| null | `Tool` (`type: function`) plus built-ins: `web_search`, `web_search_premium`, `code_interpreter`, `image_generation`, `document_library`, `connector` |
| `tool_choice` | `ToolChoice` object \| enum | default `auto`; enum = `auto \| none \| any \| required` |
| `parallel_tool_calls` | boolean | `true` |
| `presence_penalty` | number \| null | -2 … 2 |
| `frequency_penalty` | number \| null | -2 … 2 |
| `n` | integer \| null | ≥ 1; "input tokens are only billed once" |
| `prediction` | `{type: "content", content: string}` | default `{type: content, content: ""}` |
| `reasoning_effort` | enum \| null | `none \| minimal \| low \| medium \| high \| xhigh` |
| `prompt_mode` | enum \| null | only value: `reasoning` |
| `safe_prompt` | boolean | `false` — injects a safety system prompt |
| `prompt_cache_key` | string \| null | prompt-caching key |
| `service_tier` | enum \| null | `auto \| standard_only`; **omitted ⇒ `standard_only`** (S16) |
| `guardrails` | `GuardrailConfig[]` \| null | — |
| `metadata` | object \| null | free-form |

Message shapes:

- `UserMessage`: `{role: "user", content: string | ContentChunk[] | null}`
- `SystemMessage`: `{role: "system", content: ...}`
- `AssistantMessage`: `{role, content, tool_calls, prefix}` — `prefix: boolean`
  (default `false`) forces the model to *begin* its reply with that content.
  This is a Mistral-only field (S12).
- `ToolMessage`: `{role: "tool", ...}` with `tool_call_id`.
- `ContentChunk` is a union: `TextChunk`, `ImageURLChunk`, `DocumentURLChunk`,
  `AudioChunk` (`input_audio`), etc.

`response_format`:
```json
{"type": "text"}
{"type": "json_object"}
{"type": "json_schema",
 "json_schema": {"name": "book", "schema": {...}, "strict": true, "description": "..."}}
```
(`ResponseFormats` enum = `text | json_object | json_schema`; `JsonSchema`
requires `name` + `schema`, `strict` defaults `false`.)

Tool definition:
```json
{"type": "function",
 "function": {"name": "...", "description": "", "strict": false, "parameters": {...}}}
```

## Response shape

`ChatCompletionResponse` = `ResponseBase` + `created` + `choices`
(`required: [id, object, data, model, usage, created, choices]` — note `data` in
the required list appears to be spec sloppiness inherited from `ResponseBase`'s
shared use with embeddings; the chat body itself carries `choices`).

```json
{
  "id": "cmpl-e5cc70bb28c444948073e77776eb30ef",
  "object": "chat.completion",
  "created": 1702256327,
  "model": "mistral-small-latest",
  "choices": [
    {
      "index": 0,
      "message": {"role": "assistant", "content": "...", "tool_calls": null, "prefix": false},
      "finish_reason": "stop"
    }
  ],
  "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
}
```

- `finish_reason` enum (non-streaming): `stop | length | model_length | error | tool_calls`.
  Note `model_length` — **not an OpenAI value**.
- `choices[].messages` (plural) also exists in the schema as an array of
  `DeltaMessage`, used when a single choice yields multiple interleaved messages
  (tool calls, citations, handoffs). S12 explicitly warns `content` may be a
  string *or* a list of typed chunks.
- `ToolCall`: `{id, type: "function", function: {name, arguments}, index}`.
  **`arguments` is `object | string`** in the schema — OpenAI always returns a
  JSON string, so a parser must handle both.

Streaming (`text/event-stream`): SSE where each event's `data` is a
`CompletionChunk`:
```json
{"id": "...", "object": "...", "created": 0, "model": "...",
 "usage": {...},
 "choices": [{"index": 0,
              "delta": {"role": null, "content": "...", "tool_calls": null, "index": 0},
              "finish_reason": null}]}
```
Stream terminates with `data: [DONE]` (S1 `stream` description).
Streaming `finish_reason` enum: `stop | length | error | tool_calls | null`
(no `model_length`).

## Model listing

**This is the strongest part of Mistral's API for a model catalog.**

`GET /v1/models` → `ModelList`:
```json
{"object": "list", "data": [ BaseModelCard | FTModelCard ]}
```
Discriminated on `type`: `base` → `BaseModelCard`, `fine-tuned` → `FTModelCard`.

`BaseModelCard` fields (S1, verbatim from the schema):

| Field | Type | Default | Notes |
|---|---|---|---|
| `id` | string | — | **required** |
| `object` | string | `"model"` | |
| `created` | integer | — | unix ts |
| `owned_by` | string | `"mistralai"` | |
| `capabilities` | `ModelCapabilities` | — | **required**, see below |
| `name` | string \| null | — | human-readable display name |
| `description` | string \| null | — | human-readable description |
| `max_context_length` | integer | `32768` | **context window, per model** |
| `aliases` | string[] | `[]` | e.g. `mistral-medium-latest` |
| `deprecation` | date-time \| null | — | **deprecation date** |
| `deprecation_replacement_model` | string \| null | — | **recommended successor id** |
| `default_model_temperature` | number \| null | — | the per-model default temperature the chat spec tells you to read from here |
| `internal` | boolean | `false` | |
| `type` | const `"base"` | `base` | discriminator |

`FTModelCard` = all of the above with `type: "fine-tuned"` plus `job` (string,
required), `root` (string, required — base model id), `archived` (boolean,
default `false`).

`ModelCapabilities` — **14 boolean flags**, all default `false`:

`completion_chat`, `function_calling`, `reasoning`, `completion_fim`,
`fine_tuning`, `vision`, `ocr`, `classification`, `moderation`, `audio`,
`audio_transcription`, `audio_transcription_realtime`, `audio_speech`,
`unified_resources`.

(The schema carries an internal note: "This is populated by Harmattan, but some
fields have a name that we don't want to expose in the API." — i.e. these flags
are generated from Mistral's internal model registry, so they track reality.)

Example payload published in S1/S4 (fine-tuned model):
```json
{"id": "<model_id>",
 "capabilities": {"completion_chat": true, "completion_fim": false,
                  "function_calling": false, "fine_tuning": false,
                  "vision": false, "classification": false},
 "job": "<job_id>", "root": "open-mistral-7b", "object": "model",
 "created": 1756746619, "owned_by": "<owner_id>",
 "max_context_length": 32768, "aliases": [], "TYPE": "fine-tuned",
 "archived": false}
```
(The example prints the discriminator as `TYPE`; the schema property is `type`.
Treat the example's casing as a docs artifact and read `type` — but a defensive
parser should accept either.)

### Verdict for our catalog

`GET /v1/models` is rich enough to **auto-populate** the catalog: id, display
name, description, context window, alias list, per-capability flags, default
temperature, and deprecation date + replacement model all come back in one call.

What it does **not** return: **pricing** (no price fields anywhere in
`BaseModelCard`/`FTModelCard`) and no lifecycle *stage* string (Labs / Public
Preview / GA). Those two must come from the pricing page and the lifecycle
convention (`labs-` prefix) respectively.

## Models

Naming is in transition. S6 states the *new* convention is
`model-name-major-minor` (e.g. `mistral-medium-3-5`), but most currently-GA
models still expose date-stamped snapshot ids (`mistral-large-2512`). Both forms
are live today. The `names` arrays below are read directly from each official
model card page (S19 pattern) — first entry is the canonical id, the rest are
aliases.

### (a) General chat

| Model | Canonical id | Aliases | Context | Price USD in/out per 1M |
|---|---|---|---|---|
| Mistral Medium 3.5 (v26.04, GA) | `mistral-medium-3-5` | `mistral-medium-3`, `mistral-medium-latest` | 256k | $1.5 / $7.5 (cached in $0.15) |
| Mistral Large 3 (v25.12, GA) | `mistral-large-2512` | `mistral-large-latest` | 256k | $0.5 / $1.5 (cached in $0.05) |
| Mistral Small 4 (v26.03, GA) | `mistral-small-2603` | `mistral-small-latest` | 256k | $0.15 / $0.6 (cached in $0.015) |
| Ministral 3 14B (v25.12) | `ministral-14b-2512` | `ministral-14b-latest` | 256k | $0.2 / $0.2 |
| Ministral 3 8B (v25.12) | `ministral-8b-2512` | `ministral-8b-latest` | 256k | $0.15 / $0.15 |
| Ministral 3 3B (v25.12) | `ministral-3b-2512` | `ministral-3b-latest` | 256k | $0.1 / $0.1 |
| Z.ai GLM 5.2 (third-party, hosted by Mistral) | `zai-glm-5-2` | none | 1M | $1.4 / $4.4 (cached in $0.14) |

Note the Ministral 3 ids drop the family "3": the card titled "Ministral 3 3B"
serves id `ministral-3b-2512`, not `ministral-3-3b-2512`.

### (b) Cheap / fast classification

There is no dedicated cheap "classifier LLM" other than these paths:

1. **`ministral-3b-2512` / `ministral-3b-latest`** — cheapest general model,
   $0.1 in / $0.1 out per 1M USD, 256k context, supports Structured Outputs and
   Function Calling per its card. This is the natural "cheap classifier" for
   prompt-based labelling.
2. **`mistral-small-2603` / `mistral-small-latest`** — $0.15 / $0.6, if 3B
   quality is insufficient.
3. **`mistral-moderation-2603`** (Mistral Moderation 2, v26.03, 128k context) —
   safety/moderation classification only, via `POST /v1/moderations` or
   `POST /v1/chat/moderations`. Listed as **Free** on the pricing page (S7).
   It has **no `-latest` alias** on its card.
4. **`shieldstral-1-0`** (Shieldstral 1.0, Apache 2.0) — compact multimodal
   text+image moderation classifier. Its card exposes no alias list and it does
   not appear on the pricing page. Pricing: **UNKNOWN**.
5. **Classifier Factory** — fine-tuned classifiers served at
   `POST /v1/classifications`, response
   `{"id", "model", "results": [{"<target>": {"scores": {"<label>": number}}}]}`.

### Dated snapshot vs `-latest` (documented difference, S6)

- `model-name-latest` → "Latest General Availability version, **across
  generations**".
- `model-name-major` (e.g. `mistral-medium-3`) → latest minor within that major.
- `model-name-major-minor` (e.g. `mistral-medium-3-5`) / dated snapshot → fixed
  version.
- Only **GA** models are eligible for `-latest` and `-major`. Labs models are
  prefixed `labs-` and get no aliases; Public Preview models use major-minor but
  get no `-latest`.
- Explicit warning in S6: "Aliases automatically switch to newer models once they
  reach General Availability. As a result, using an alias may expose you to
  silent updates in model behavior **and pricing**. For precise control, pin your
  deployment to a specific major.minor version identifier."

Other current models (non-chat, for completeness): `codestral-2508` /
`codestral-latest`; `mistral-embed-2312` / `mistral-embed`;
`codestral-embed-2505`; `mistral-ocr-4.1`, `mistral-ocr-4.0`, `mistral-ocr-3`;
`voxtral-mini-tts-2603` / `voxtral-mini-tts-latest`; `voxtral-mini-2602`;
`voxtral-mini-realtime-2602`; `voxtral-small-2507`; `leanstral-1.5`.

## Model lifecycle/deprecation

Five stages (S6): **Labs** → **Public Preview** → **General Availability** →
**Deprecated** → **Retired**. Silent updates: allowed in Labs and Public
Preview, **not** in GA/Deprecated.

Notice periods (S6):

| Stage | Notice period |
|---|---|
| Labs | 1 month |
| Public Preview | 1 month |
| General Availability | **6 months** |
| Third-party models | 1 month |

- "Deprecation is announced as soon as a replacement model is available."
- "During the deprecation period, the model remains accessible. Once retired,
  requests to its identifiers fail with a **404** error." — so a retired model is
  a 404, not a 400.

How lifecycle status is exposed:

1. **API (authoritative, machine-readable):** `deprecation` (RFC3339 date-time or
   null) and `deprecation_replacement_model` (string or null) on every model card
   from `GET /v1/models`. A non-null `deprecation` = deprecation announced.
2. **Stage** is only inferable: `labs-` id prefix = Labs; presence of `-latest` /
   `-major` aliases implies GA. There is no `stage` field in the API. Reading the
   stage programmatically: **UNKNOWN / not exposed**.
3. **Docs table (human-readable):** S5 publishes a "Deprecated & retired models"
   table with columns Model / Version / API id / Deprecation date / Retirement
   date / Alternative. Sample of the currently-listed rows (dates as printed,
   M/D/YYYY):

| Model | API id | Deprecated | Retired | Replacement |
|---|---|---|---|---|
| Mistral Medium 3.1 | `mistral-medium-2508` | 5/22/2026 | 8/31/2026 | Mistral Medium 3.5 |
| Mistral Medium 3 | `mistral-medium-2505` | 5/22/2026 | 8/31/2026 | Mistral Medium 3.5 |
| Mistral Small 3.2 | `mistral-small-2506` | 4/30/2026 | 7/31/2026 | Mistral Small 4 |
| Devstral 2 | `devstral-2512` | 5/22/2026 | 7/31/2026 | Mistral Medium 3.5 |
| Magistral Medium 1.2 | `magistral-medium-2509` | 5/22/2026 | 7/31/2026 | Mistral Medium 3.5 |
| Magistral Small 1.2 | `magistral-small-2509` | 4/30/2026 | 7/31/2026 | Mistral Small 4 |
| Mistral Nemo 12B | `open-mistral-nemo-2407` | 5/22/2026 | 7/31/2026 | Ministral 3 8B |
| Mistral Large 2.1 | `mistral-large-2411` | 2/27/2026 | 5/31/2026 | Mistral Medium 3.5 |
| Mistral Moderation | `mistral-moderation-2411` | 3/31/2026 | 6/30/2026 | Mistral Moderation 2 |
| Leanstral (labs) | `labs-leanstral-2603` | 5/22/2026 | 6/30/2026 | Leanstral 1.5 |
| Codestral Mamba 7B | `open-codestral-mamba` | 6/6/2025 | 6/6/2025 | Codestral |

(~45 rows total; the table also carries Ministral 3B/8B `-2410`, Pixtral,
Mixtral, Mistral 7B, older Codestral, Saba, etc.)

**Catalog recommendation:** poll `GET /v1/models` and store `deprecation` +
`deprecation_replacement_model` per model; do not hardcode the docs table.

## Capabilities

| Capability | Parameter names |
|---|---|
| Streaming | request `stream: true` (boolean, default `false`); SSE `text/event-stream`; per-event body is `CompletionChunk` with `choices[].delta`; terminated by `data: [DONE]` |
| Streaming usage | S9 says `stream_options.include_usage` "must be explicitly set to receive token usage in stream events". **Conflict:** `stream_options` does **not** exist in `ChatCompletionRequest`, which is `additionalProperties: false`. Either the spec lags the docs or the docs describe the OpenAI-compat path. Treat as **UNVERIFIED — test before relying on it.** `CompletionChunk.usage` *is* in the schema. |
| Tool calling | `tools[]` (`{"type":"function","function":{name, description, strict, parameters}}`), `tool_choice` (`auto`/`none`/`any`/`required` or `{"type":"function","function":{"name":"..."}}`), `parallel_tool_calls` (default `true`). Response: `choices[].message.tool_calls[] = {id, type, function:{name, arguments}, index}`. Follow-up turn uses `role: "tool"` + `tool_call_id`. Max **128 tools per request** (S9). |
| JSON mode | `response_format: {"type": "json_object"}`. S9: model always returns valid JSON, **you must include the word "JSON" in the system or user prompt or the model may emit an infinite whitespace stream**; JSON mode does **not** guarantee schema adherence |
| Structured output (schema) | `response_format: {"type": "json_schema", "json_schema": {"name", "schema", "strict", "description"}}` |
| Reasoning | `reasoning_effort`: `none\|minimal\|low\|medium\|high\|xhigh`; `prompt_mode: "reasoning"` |
| Prompt caching | `prompt_cache_key` (string). Cached input priced separately (S7) |
| Predicted outputs | `prediction: {"type":"content","content":"..."}` |
| Assistant prefix | `AssistantMessage.prefix: true` — forces the reply to start with that text |
| Safety prompt | `safe_prompt: true` |
| Priority routing | `service_tier: "auto" | "standard_only"` (default `standard_only`) |
| Vision | image content chunks; max 20 MB/image; PNG, JPG, JPEG, GIF, WEBP (S9) |

## Usage/tokens

`UsageInfo` (S1), `additionalProperties: false`,
`required: [prompt_tokens, completion_tokens, total_tokens]`:

| Field | Type | Default |
|---|---|---|
| `prompt_tokens` | integer | 0 |
| `completion_tokens` | integer | 0 |
| `total_tokens` | integer | 0 |
| `prompt_audio_seconds` | integer \| null | — |
| `service_tier` | string \| null | — | "The service tier at which the request was processed: standard or priority." |

Names match OpenAI's *legacy* chat-completions naming
(`prompt_tokens`/`completion_tokens`), not the Responses API naming.
There is **no** `cached_tokens` / `prompt_tokens_details` field in the schema
even though cached input is priced separately — so cache-hit accounting from the
response body is **UNKNOWN**.

Token counting client-side: official tokenizer is `mistral-common`
(`pip install mistral-common`, `MistralTokenizer`) per S10.

## Errors

Documented status codes (S8): **400, 401, 403, 404, 422, 429** and
**500, 502, 503, 504**. S11's quickstart error table additionally documents
**402 Payment Required** ("No payment method on account").

Error body (S8, verbatim):
```json
{
  "object": "error",
  "message": "A human-readable description of the error.",
  "type": "invalid_request_error",
  "param": "model",
  "code": "unknown_model"
}
```

| Field | Meaning |
|---|---|
| `message` | Human-readable description |
| `type` | Category: `invalid_request_error`, `authentication_error`, `rate_limit_error`, `server_error` |
| `param` | Offending parameter, if applicable |
| `code` | Machine-readable code, if applicable (example given: `unknown_model`) |

A full enumerated list of `code` values is **UNKNOWN** — S8 documents the four
`type` categories and one sample `code` only.

Separate from that, the OpenAPI spec defines **422 → `HTTPValidationError`**:
```json
{"detail": [{"loc": ["body", "model"], "msg": "...", "type": "..."}]}
```
This is FastAPI-style and is a **different shape** from the `object: "error"`
body. A client must handle both.

Other documented semantics:
- Retired model id → **404** (S6).
- Request exceeding the context window → **400 Bad Request** (S9).
- Rate limit exceeded → **429** (S8, S9).

## Rate limits

- Enforced **per organization** (S9) and **per workspace**, shared across all API
  keys in that workspace (S18).
- Dimensions (S17, S18): **requests per second (RPS)**, **tokens per minute
  (TPM)**, **tokens per month**. "Requests per second and tokens per minute are
  enforced independently." Additional dimensions exist for audio (audio
  seconds/min, /month) and OCR (pages/minute).
- **Actual numeric limits are not published in the docs.** They vary by
  subscription tier and model and are shown only in
  Admin Panel › API › Limits. Numeric values: **UNKNOWN**.
- Batch processing does **not** count against real-time rate limits (S9).

Headers:
- `X-RateLimit-Remaining` — S9: "Check the `X-RateLimit-Remaining` response
  header to monitor your usage before hitting the limit." This is the only
  rate-limit header named in the official docs.
- `Retry-After` — S8 (429 resolution): "Implement exponential backoff. Check the
  `Retry-After` response header."
- Any `X-RateLimit-Limit` / `X-RateLimit-Reset` equivalents: **UNKNOWN**.

Retry guidance (S8): for transient errors **429, 500, 502, 503, 504** use
exponential backoff; the published sample is `wait = 2**attempt + random(0,1)`,
`max_retries=5`. Docs also note the official Python and TypeScript SDKs ship
built-in retry with exponential backoff.

Timeouts:
- Streaming connections "time out after **10 minutes of inactivity**" (S9).
- A request timeout for non-streaming calls is **UNKNOWN** — S1's `stream`
  description only says the server "will hold the request open until the timeout
  or until completion" without naming a value.

## Pricing

Page: **https://docs.mistral.ai/inference/pricing** (S7). The page has a
USD/EUR toggle; **USD is the default and the figures below are USD**. EUR
figures are available via the toggle but were not captured — EUR values:
**UNKNOWN**. There are also Standard / Batch / Priority / Regional-inference
toggles; figures below are the **Standard** tab.

Per 1M tokens, USD:

| Model | Input | Cached input | Output |
|---|---|---|---|
| Mistral Large 3 | $0.50 | $0.05 | $1.50 |
| Mistral Medium 3.5 | $1.50 | $0.15 | $7.50 |
| Mistral Small 4 | $0.15 | $0.015 | $0.60 |
| Ministral 3 14B | $0.20 | $0.02 | $0.20 |
| Ministral 3 8B | $0.15 | $0.015 | $0.15 |
| Ministral 3 3B | $0.10 | $0.01 | $0.10 |
| Z.ai GLM 5.2 (third-party) | $1.40 | $0.14 | $4.40 |
| Codestral | $0.30 | $0.03 | $0.90 |
| Codestral Embed | $0.15 | $0.015 | — |

Non-token-priced (USD):

| Model | Price |
|---|---|
| OCR 4.1 / OCR 4.0 | $4 per 1000 pages (cached $0.4 per 1000 pages) |
| Voxtral Mini Transcribe 2 | $0.003 / minute (cached $0.0003 / min) |
| Voxtral TTS | $0 in / **$16 per 1M characters** out |
| Mistral Moderation 2 | Free |
| Leanstral 1.5 (Labs) | Free |

Not listed on the pricing page: `mistral-embed`, Shieldstral 1.0 →
price **UNKNOWN**.

Billing plan model (S17): "Free mode" with included monthly usage (no credit
card), plus pay-as-you-go beyond that; org- and workspace-level monthly spending
caps that suspend API access when hit.

## Health/test-connection strategy

Recommended, in order:

1. **`GET https://api.mistral.ai/v1/models`** with
   `Authorization: Bearer <key>`. Cheapest possible check: no tokens billed, no
   model choice needed, and it doubles as the catalog refresh. Success = `200`
   with `{"object":"list","data":[...]}`. Map failures:
   - `401` → bad/expired key (`type: authentication_error`)
   - `403` → key lacks permission
   - `429` → rate limited (surface `Retry-After`)
   - `5xx` → provider outage; retry with backoff
2. If a generation-path smoke test is wanted, a minimal
   `POST /v1/chat/completions` with `model: "ministral-3b-latest"`,
   one short user message, `max_tokens: 1` — the cheapest chat model at
   $0.1/$0.1 per 1M USD.
3. Do **not** treat a 404 from a chat call as "provider down": per S6 a retired
   model id returns 404, which is a catalog-staleness signal, and the correct
   remediation is to read `deprecation_replacement_model` from `/v1/models`.
4. Because the error body has two shapes (`{object:"error",...}` and
   `{detail:[...]}` for 422), the health check's error parser must handle both.

## OpenAI-compatibility verdict

**OPENAI-COMPATIBLE + PROVIDER-SPECIFIC METADATA**

Justification from the docs:

*Why OpenAI-compatible is genuinely sufficient for the chat path:* S10 (official
migration guide) states "The Mistral Chat Completions API follows the same
request structure as OpenAI" and publishes a working example that instantiates
the stock `openai` client with `base_url="https://api.mistral.ai/v1"` and no
other changes, explicitly for "LangChain, LlamaIndex, or any other third-party
library… changing only the base URL and model name. No library swap required."
The wire shapes line up: `POST /v1/chat/completions`, bearer auth,
`model`/`messages`/`temperature`/`top_p`/`max_tokens`/`stream`/`stop`/`n`/
`presence_penalty`/`frequency_penalty`/`tools`/`tool_choice`/
`parallel_tool_calls`/`response_format`, and a response with
`id`/`object`/`created`/`model`/`choices[].message`/`finish_reason` plus
`usage.{prompt,completion,total}_tokens`. SSE deltas and `data: [DONE]` match.

*Why provider-specific metadata is still required:*
1. **`GET /v1/models` is far richer than OpenAI's** — `max_context_length`,
   `aliases[]`, `deprecation`, `deprecation_replacement_model`,
   `default_model_temperature`, and a 14-flag `capabilities` object. An
   OpenAI-shaped model parser (`id`/`object`/`created`/`owned_by`) throws all of
   that away, and it is precisely the data our catalog wants.
2. **Divergent enums and fields**: `finish_reason: "model_length"`;
   `tool_choice: "any"`; `reasoning_effort: "xhigh"`; `random_seed` instead of
   `seed`; `safe_prompt`; `prefix` on assistant messages; `prompt_mode`;
   `service_tier: standard_only`; `guardrails`; `prompt_cache_key`.
3. **`ChatCompletionRequest` is `additionalProperties: false`** — passing
   OpenAI-only fields (`seed`, `logprobs`, `user`, `stop_sequences`,
   `stream_options`, `max_completion_tokens`) will be rejected rather than
   ignored. This is the sharpest practical difference.
4. **`ToolCall.function.arguments` may be an object or a string**, where OpenAI
   guarantees a string.
5. **Two error body shapes** (`{object:"error", type, param, code}` for most
   codes, FastAPI `{detail:[...]}` for 422) vs OpenAI's single `{error:{...}}`
   envelope.

So: reuse an OpenAI-compatible transport for chat, but add a Mistral-specific
model-catalog adapter, a Mistral error normaliser, and a request-field allowlist.

## Unknowns

- **Numeric rate limits** (RPS/TPM/tokens-per-month per tier and model) — not
  published; only visible in Admin Panel › API › Limits.
- **`X-RateLimit-Limit` / `X-RateLimit-Reset`** or any header beyond
  `X-RateLimit-Remaining` and `Retry-After`.
- **Non-streaming request timeout value.**
- **`stream_options.include_usage`** — documented in Known limitations but absent
  from the OpenAPI request schema, which forbids unknown properties. Must be
  empirically tested.
- **Full enumeration of error `code` values** — only `unknown_model` is shown.
- **Cached-token accounting in the response** — cached input is priced but
  `UsageInfo` has no cached-token field.
- **EUR prices** — the pricing page has a EUR toggle but the EUR figures were not
  captured; all figures recorded here are USD.
- **Pricing for `mistral-embed` and Shieldstral 1.0** — absent from the pricing
  page.
- **Programmatic lifecycle *stage*** (Labs / Public Preview / GA) — not a field
  in the model card; only inferable from the `labs-` prefix and alias presence.
- **Whether `GET /v1/models` returns rows for already-retired models** (and
  therefore whether the deprecation fields are ever populated for models we can
  still see) — not stated in the docs; needs a live call with a real key.
- **Whether a `-latest` alias appears as its own row in `/v1/models`** or only
  inside another row's `aliases[]` — not stated; needs a live call.
