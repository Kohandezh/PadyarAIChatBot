# Google Gemini

Research date: 2026-08-18. Scope: **Gemini Developer API** (API-key based, host
`generativelanguage.googleapis.com`). Vertex / "Gemini Enterprise Agent Platform"
is a *separate* configuration surface — see the note at the end of `## Auth`.

> **Headline finding that reshapes the abstraction:** as of 2026 the Gemini Developer API
> has **two parallel generation surfaces**. The **Interactions API** (`POST /v1beta/interactions`,
> model in the **request body**) is now GA and is what Google's own quickstart shows.
> The classic **`generateContent`** API (model in the **URL path**) is explicitly labelled
> **legacy** — still fully supported, but no new features land there. Any abstraction
> written today must pick one deliberately, and the two differ in *every* structural
> dimension: field names, casing, nesting, token-usage names, and error body shape.

## Sources

All official (`ai.google.dev` / `cloud.google.com`), fetched 2026-08-18.

| Topic | URL |
|---|---|
| API reference index | https://ai.google.dev/api |
| Quickstart (current, Interactions) | https://ai.google.dev/gemini-api/docs/quickstart |
| Interactions API overview | https://ai.google.dev/gemini-api/docs/interactions-overview |
| Interactions API reference | https://ai.google.dev/api/interactions-api |
| Interactions API reference (raw md) | https://ai.google.dev/api/interactions.md.txt |
| Interactions text generation | https://ai.google.dev/gemini-api/docs/interactions/text-generation.md.txt |
| Migrating to Interactions API | https://ai.google.dev/gemini-api/docs/migrate-to-interactions |
| Interactions breaking changes (May 2026) | https://ai.google.dev/gemini-api/docs/interactions-breaking-changes-may-2026 |
| `generateContent` reference (legacy) | https://ai.google.dev/api/generate-content |
| `generateContent` get-started (legacy) | https://ai.google.dev/gemini-api/docs/generate-content/get-started |
| `generateContent` text generation (legacy) | https://ai.google.dev/gemini-api/docs/generate-content/text-generation |
| API versions explained | https://ai.google.dev/gemini-api/docs/api-versions |
| API keys | https://ai.google.dev/gemini-api/docs/api-key |
| Models list | https://ai.google.dev/gemini-api/docs/models |
| Models REST reference | https://ai.google.dev/api/models |
| Structured output | https://ai.google.dev/gemini-api/docs/structured-output |
| Function calling | https://ai.google.dev/gemini-api/docs/function-calling |
| Streaming | https://ai.google.dev/gemini-api/docs/streaming |
| Safety settings | https://ai.google.dev/gemini-api/docs/safety-settings |
| API errors | https://ai.google.dev/gemini-api/docs/api-errors |
| Troubleshooting | https://ai.google.dev/gemini-api/docs/troubleshooting |
| Rate limits | https://ai.google.dev/gemini-api/docs/rate-limits |
| Pricing | https://ai.google.dev/gemini-api/docs/pricing |
| OpenAI compatibility | https://ai.google.dev/gemini-api/docs/openai |
| Release notes / changelog | https://ai.google.dev/gemini-api/docs/changelog |
| Developer API vs Enterprise/Vertex | https://ai.google.dev/gemini-api/docs/migrate-to-cloud |

## Auth

Source: https://ai.google.dev/gemini-api/docs/api-key

- **Header (documented standard):** `x-goog-api-key: YOUR_API_KEY`
  - Not `Authorization: Bearer`. Not `api-key`. This is the single most common
    porting mistake from an OpenAI-shaped client.
- **Also required:** `Content-Type: application/json`
- **Query parameter:** `?key=$GEMINI_API_KEY` still appears in the models REST reference
  (`curl https://generativelanguage.googleapis.com/v1beta/models?key=$GEMINI_API_KEY`,
  https://ai.google.dev/api/models). The api-key guide page does **not** document it and
  all current guide examples use the header. **Use the header** — it keeps the key out of
  URLs, logs, and referrers. Treat `?key=` as legacy-but-working.
- **Environment variables** the official SDKs auto-detect: `GEMINI_API_KEY` or
  `GOOGLE_API_KEY`. Verbatim: *"If both are set, `GOOGLE_API_KEY` takes precedence."*
- **Api-Revision header (Interactions API only):** current guide examples send
  `Api-Revision: 2026-05-20`. See `## Endpoints` — this is a dated schema pin, not auth.
- **Key types:** the docs now distinguish plain API keys from **"Authorization (auth) keys"**
  bound to a Google Cloud service account. Verbatim: *"All new API keys created in Google AI
  Studio are automatically created as auth keys."* Wire format is unchanged (still
  `x-goog-api-key`), so this does not affect client code.

**Vertex is a separate configuration surface — yes.** Different host
(`aiplatform.googleapis.com` vs `generativelanguage.googleapis.com`), different auth
(OAuth2 short-lived access tokens from a service account, ~1h lifetime, `cloud-platform`
scope — not a static API key), and it requires project + region config. It is now branded
**"Gemini Enterprise Agent Platform"**. The unified `google-genai` SDK can target either,
but a hand-rolled HTTP client cannot share an auth path between them. If the product ever
needs Vertex, model it as a **distinct provider config**, not a base-URL swap.
(https://ai.google.dev/gemini-api/docs/migrate-to-cloud)

## Endpoints

**Base URL:** `https://generativelanguage.googleapis.com`

### Version segments
Source: https://ai.google.dev/gemini-api/docs/api-versions

- `v1` — verbatim: *"Stable version of the API."* / *"Features in the stable version are
  fully supported over the lifetime of the major version."*
- `v1beta` — verbatim: *"This version includes early features and capabilities that are
  actively being developed."*
- Verbatim: *"The Interactions API and its core features are generally available in `v1`."*
- Verbatim: *"The Gemini API SDKs default to `v1beta`, but you can explicitly specify versions"*
  and *"The GenAI SDKs use `v1beta` by default to enable access to preview features."*

**Which is current, in practice:** `v1beta`. Every curl example across the quickstart,
Interactions reference, legacy generateContent pages, and models reference uses `/v1beta/`.
`v1` exists and is the stable contract, but Google's own docs do not show it. **Recommend
pinning `v1beta`** to match documented behavior, and treating the version segment as a
config value rather than a constant.

### A. Interactions API — current / recommended

```
POST   https://generativelanguage.googleapis.com/v1beta/interactions
GET    https://generativelanguage.googleapis.com/v1beta/interactions/{id}
POST   https://generativelanguage.googleapis.com/v1beta/interactions/{id}/cancel
DELETE https://generativelanguage.googleapis.com/v1beta/interactions/{id}
```

**The model ID is in the request body, not the URL.** Verbatim quickstart example:

```bash
curl -X POST "https://generativelanguage.googleapis.com/v1beta/interactions" \
      -H "x-goog-api-key: $GEMINI_API_KEY" \
      -H 'Content-Type: application/json' \
      -d '{
        "model": "gemini-3.7-flash",
        "input": "Explain how AI works in a few words"
      }'
```

**`Api-Revision` schema pin.** Source:
https://ai.google.dev/gemini-api/docs/interactions-breaking-changes-may-2026
- New schema value: `Api-Revision: 2026-05-20` (current guide examples send this).
- Legacy opt-out value: `Api-Revision: 2026-05-07`.
- Opt-in opened May 7, 2026; new schema became the **default** May 26, 2026; legacy schema
  **removed June 8, 2026**.
- The breaking change replaced the `outputs` array with the **`steps`** array, and
  consolidated `response_mime_type`, `image_config`, and `response_modalities` into a
  single polymorphic **`response_format`**.
- Minimum SDKs that auto-opt-in: Python ≥ 2.0.0, JavaScript ≥ 2.0.0.
- Omitting the header today yields the new (`steps`) schema, since the default flipped.
  **Still send it explicitly** — a dated pin is cheap insurance against the next flip.

### B. `generateContent` — legacy, still fully supported

```
POST https://generativelanguage.googleapis.com/v1beta/{model=models/*}:generateContent
POST https://generativelanguage.googleapis.com/v1beta/{model=models/*}:streamGenerateContent
```

**The model ID is embedded in the URL path**, prefixed with `models/`, and the method is a
**colon-suffixed verb** on the resource. Verbatim legacy example:

```bash
curl "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.7-flash:generateContent" \
      -H "x-goog-api-key: $GEMINI_API_KEY" \
      -H 'Content-Type: application/json' \
      -X POST \
      -d '{
        "contents": [
          {
            "parts": [
              {
                "text": "Explain how AI works in a few words"
              }
            ]
          }
        ]
      }'
```

Every `generateContent` doc page now carries the banner, verbatim: *"The Interactions API
is now generally available. We recommend using this API for access to all the latest
features and models."* The overview states, verbatim: *"While it is now considered legacy,
the original `generateContent` API remains fully supported."* and *"Going forward, all new
models, multimodal capabilities, tools, and agentic features will launch on the
Interactions API."* It is **not formally deprecated**; no shutdown date is published.

## Request shape

### A. Interactions API
Source: https://ai.google.dev/api/interactions-api, `.../interactions/text-generation.md.txt`

**Casing is `snake_case`.** Top-level fields:

| Field | Type | Notes |
|---|---|---|
| `model` | ModelOption | e.g. `"gemini-3.7-flash"`. Mutually alternative with `agent`. |
| `agent` | AgentOption | For managed agents. |
| `input` | string \| array | **Required.** Plain string for one-shot, or an array of step objects for multi-turn. |
| `system_instruction` | **string** | A plain string — not a Content object. |
| `generation_config` | object | See below. |
| `tools` | array | See `## Capabilities`. |
| `response_format` | object | Structured output. See `## Capabilities`. |
| `safety_settings` | array | See `## Safety/content blocking`. |
| `store` | boolean | Default `true` — **server-side conversation persistence**. Set `false` for stateless. |
| `previous_interaction_id` | string | Server-side conversation chaining, alternative to resending history. |
| `background` | boolean | Async/background execution. |
| `stream` | boolean | See `## Capabilities`. |

`generation_config` sub-fields: `max_output_tokens`, `seed`, `thinking_level`
(`minimal` / `low` / `medium` / `high`), `thinking_summaries` (`auto` / `none`),
`tool_choice`, `stop_sequences`.

**`temperature`, `top_p`, `top_k` are DEPRECATED.** Verbatim from the July 21, 2026 release
notes: *"**Deprecated parameters**: The sampling parameters `temperature`, `top_p` and
`top_k` are now deprecated."* Google's guidance for Gemini 3.x is to remove them and use
`thinking_level` instead. They still parse (an example passing `"temperature": 1.0` remains
in the text-generation guide) but **do not build `temperature` into the abstraction as a
required, always-sent Gemini parameter.**

Multi-turn / stateless — `input` becomes an array of typed **steps**, not role/content pairs:

```bash
curl -X POST "https://generativelanguage.googleapis.com/v1beta/interactions" \
  -H "x-goog-api-key: $GEMINI_API_KEY" \
  -H 'Content-Type: application/json' \
  -H "Api-Revision: 2026-05-20" \
  -d '{
    "model": "gemini-3.5-flash",
    "store": false,
    "input": [
      {
        "type": "user_input",
        "content": "I have 2 dogs in my house."
      }
    ]
  }'
```

For subsequent turns you pass the **accumulated steps** in `input`, appending new
`user_input` steps while preserving the model-generated steps verbatim.

System instruction is a bare string:

```json
{
  "model": "gemini-3.5-flash",
  "system_instruction": "You are a cat. Your name is Neko.",
  "input": "Hello there"
}
```

### B. `generateContent` (legacy)
Source: https://ai.google.dev/api/generate-content

**Casing is `camelCase` in the reference**, though `snake_case` aliases are accepted
(the official example literally uses `"system_instruction"` next to `"generationConfig"` —
the API accepts both proto JSON casings).

| Field | Notes |
|---|---|
| `contents[]` | **Required.** Array of `Content`. |
| `contents[].role` | `"user"` / `"model"` — **`"model"`, not `"assistant"`.** |
| `contents[].parts[]` | Array of `Part`: `{"text": ...}`, `inline_data`, `file_data`. **Content is always an array of parts, never a bare string.** |
| `systemInstruction` | A **`Content` object** (`{"parts":[{"text": "..."}]}`), **not** a message in `contents`, and it carries no role. |
| `generationConfig` | `temperature`, `maxOutputTokens`, `responseMimeType`, `responseSchema`, `thinkingConfig`, `candidateCount`, `stopSequences`, `topP`, `topK` |
| `tools[]`, `toolConfig` | Function calling. |
| `safetySettings[]` | `{category, threshold}` pairs. |
| `cachedContent` | Context-cache handle. |
| `serviceTier`, `store` | Newer additions. |

```json
{
  "system_instruction": { "parts": [ { "text": "You are a cat. Your name is Neko." } ] },
  "contents": [ { "parts": [ { "text": "Hello there" } ] } ],
  "generationConfig": { "stopSequences": ["Title"], "maxOutputTokens": 1000 }
}
```

Note the omission of `role` in single-turn examples — it defaults to `user`.

## Response shape

### A. Interactions API

Verbatim example response (from https://ai.google.dev/api/interactions-api):

```json
{
  "created": "2025-11-26T12:25:15Z",
  "id": "v1_ChdPU0F4YWFtNkFwS2kxZThQZ05lbXdROBIXT1NBeGFhbTZBcEtpMWU4UGdOZW13UTg",
  "model": "gemini-3.6-flash",
  "object": "interaction",
  "status": "completed",
  "steps": [
    {
      "type": "model_output",
      "content": [
        { "type": "text", "text": "Hello! I'm functioning perfectly and ready to assist you.\n\nHow are you doing today?" }
      ]
    }
  ],
  "updated": "2025-11-26T12:25:15Z",
  "usage": {
    "input_tokens_by_modality": [ { "modality": "text", "tokens": 7 } ],
    "total_cached_tokens": 0,
    "total_input_tokens": 7,
    "total_output_tokens": 20,
    "total_thought_tokens": 22,
    "total_tokens": 49,
    "total_tool_use_tokens": 0
  }
}
```

- `status` enum: `in_progress`, `requires_action`, `completed`, `failed`, `cancelled`,
  `incomplete`, `budget_exceeded`, `queued`.
  - `incomplete` is documented as what you get when *"hitting max_tokens"*.
  - `requires_action` means the model emitted a tool call and is waiting on you.
- `steps[].type` values seen: `model_output`, `user_input`, `function_call`
  (plus `function_result` sent by the client, and thought/tool-result steps).
- `steps[].content[]` items are typed blocks: `{"type": "text", "text": ...}`.
- `error` — documented as *"Diagnostic faults / platform errors recorded on the
  interaction"*, an array of `{code (URI), message}`. **Per-interaction errors can arrive
  on an HTTP 200.**
- `output_text` — a convenience concatenation of the last model output. Verbatim:
  *"Note: this is added by the SDK."* **It is NOT on the wire.** A hand-rolled HTTP client
  must walk `steps` itself.

There is **no `finishReason`** in this API. The nearest equivalents are the interaction-level
`status` and per-step status.

### B. `generateContent` (legacy)

```
GenerateContentResponse:
  candidates[]        -> [ { content: {parts: [{text}], role}, finishReason, safetyRatings[] } ]
  promptFeedback      -> { blockReason, safetyRatings[] }
  usageMetadata       -> see ## Usage/tokens
  modelVersion, responseId, modelStatus
```

- `candidates[].finishReason` enum: `STOP`, `MAX_TOKENS`, `SAFETY`, `RECITATION`,
  `LANGUAGE_NOT_SUPPORTED`, `OTHER`.
- `promptFeedback.blockReason` enum: `SAFETY`, `OTHER`, `BLOCKLIST`, `PROHIBITED_CONTENT`,
  `IMAGE_SAFETY`.
- Text extraction path: `candidates[0].content.parts[0].text` — and `parts` can hold
  multiple blocks (text + thought + functionCall), so index `[0]` is not safe in general.

## Model listing

Source: https://ai.google.dev/api/models

```
GET https://generativelanguage.googleapis.com/v1beta/models
GET https://generativelanguage.googleapis.com/v1beta/{name=models/*}
```

Query params for list: `pageSize` (default 50, max 1000), `pageToken`.

```json
{
  "models": [
    {
      "name": "models/gemini-3.7-flash",
      "baseModelId": "string",
      "version": "string",
      "displayName": "string",
      "description": "string",
      "inputTokenLimit": 0,
      "outputTokenLimit": 0,
      "supportedGenerationMethods": ["generateContent", "..."],
      "thinking": true,
      "temperature": 0,
      "maxTemperature": 0,
      "topP": 0,
      "topK": 0
    }
  ],
  "nextPageToken": "string"
}
```

Two things that differ from OpenAI's `/v1/models`:
1. `name` is **`models/gemini-3.7-flash`**, not `gemini-3.7-flash`. The `models/` prefix
   must be stripped before use as a request `model` value, and re-added for path-style
   `generateContent` URLs.
2. The response is **paginated** (`nextPageToken`) — OpenAI's is a single `data[]` array.

Note: the docs' own list-models curl uses the query-param key form
(`?key=$GEMINI_API_KEY`); the header works equally.

## Models

Source: https://ai.google.dev/gemini-api/docs/models, cross-checked against
https://ai.google.dev/gemini-api/docs/changelog

**Exact IDs as documented today (2026-08-18):**

Stable text/chat:
- `gemini-3.7-flash` — *"Latest and most capable Flash model for complex coding, agentic
  workflows, and reliable multi-step execution."* Went GA **Aug 13, 2026**. This is the
  default model in Google's own quickstart.
- `gemini-3.6-flash` — previous-generation Flash. GA Jul 21, 2026.
- `gemini-3.5-flash` — *"Legacy Flash model for routine, high-throughput workloads."*
- `gemini-3.5-flash-lite` — *"Fastest, most cost-effective 3.5 model."* GA Jul 21, 2026.
- `gemini-3.1-flash-lite` — *"Frontier-class performance at a fraction of the cost."*
- `gemini-2.5-pro` — *"Most advanced model for complex tasks."*
- `gemini-2.5-flash` — *"Best price-performance model for low-latency, high-volume tasks."*
- `gemini-2.5-flash-lite` — *"Fastest and most budget-friendly multimodal model."*

Preview:
- `gemini-3.1-pro-preview`, `gemini-3-flash-preview`, `gemini-omni-flash`,
  `gemini-3.1-flash-live-preview`, `gemini-3.1-flash-tts-preview`,
  `gemini-3.5-live-translate-preview`

Other families (not relevant here, listed for completeness): `gemini-3.1-flash-image`,
`gemini-3.1-flash-lite-image`, `gemini-3-pro-image`, `gemini-2.5-flash-image`,
`gemini-embedding-2-preview`, `gemini-embedding-001`,
`gemini-2.5-computer-use-preview-10-2025`, `deep-research-preview-04-2026`,
`antigravity-preview-05-2026`, `veo-3.1-generate-preview`, `lyria-3-pro-preview`.

**Recommendations for this project:**
- **(a) General chat:** `gemini-3.7-flash` — current GA workhorse, Google's own default.
  Conservative alternative: `gemini-2.5-flash` (much cheaper, long-established, free tier).
- **(b) Cheap/fast classification:** `gemini-2.5-flash-lite` is the cheapest documented
  option by a wide margin ($0.10 in / $0.40 out per 1M). If newer-generation quality is
  needed, `gemini-3.1-flash-lite` ($0.25 / $1.50) or `gemini-3.5-flash-lite`
  ($0.30 / $2.50). For a routing/classification task, `gemini-2.5-flash-lite` is the
  right default.

**Aliases:** floating aliases exist (example given: `gemini-flash-latest`). Verbatim:
*"Points to the latest release for a specific model variation... This alias will get
hot-swapped with every new release... For breaking changes, a **2-week notice** will be
provided through email before the version behind latest is changed."*
**Do not pin production to a `-latest` alias** — pin an exact ID.

**Shut down / deprecated:** `gemini-2.0-flash` and `gemini-2.0-flash-lite` are shut down.
`gemini-3-pro-preview` shut down Mar 9, 2026 (now aliased to `gemini-3.1-pro-preview`).

## Capabilities

### Streaming
Source: https://ai.google.dev/gemini-api/docs/streaming

- **Interactions API:** `"stream": true` in the JSON body — a body field, not a separate
  endpoint. Transport is SSE with **named events**:
  `interaction.created`, `interaction.status_update`, `step.start`, `step.delta`,
  `step.stop`, `interaction.completed`, `error`.
  Delta payload example: `data: {"index": 0, "delta": {"type": "text", "text": "Hello"}, "event_type": "step.delta"}`
  Terminal sentinel: `event: done` / `data: [DONE]`.
- **`generateContent` (legacy):** a **different endpoint** —
  `:streamGenerateContent` — not a body flag. `?alt=sse` selects SSE framing; without it
  the response is a streamed JSON array.

### Function calling
Source: https://ai.google.dev/gemini-api/docs/function-calling

- **Interactions API:** `tools: [{"type": "function", "name": ..., "description": ...,
  "parameters": {JSON Schema}}]` — flat, OpenAI-ish, **no `functionDeclarations` wrapper**.
  Choice is `generation_config.tool_choice` with values `auto`, `any`, `none`, `validated`.
  Calls arrive as steps of `"type": "function_call"` with `name` and `arguments`;
  you reply with a step of `"type": "function_result"` carrying `name`, `call_id`, `result`.
- **`generateContent` (legacy):** `tools: [{functionDeclarations: [...]}]` (nested wrapper)
  plus `toolConfig.functionCallingConfig.mode` with enum `AUTO` / `ANY` / `NONE` (uppercase).

**The two surfaces use different casing conventions for the same enum** — lowercase
`auto`/`any`/`none` in Interactions, uppercase `AUTO`/`ANY`/`NONE` in generateContent.

### Structured output / JSON mode
Source: https://ai.google.dev/gemini-api/docs/structured-output

- **Interactions API:** a single `response_format` object:
  ```json
  "response_format": { "type": "text", "mime_type": "application/json", "schema": { ... } }
  ```
- **`generateContent` (legacy):** two separate `generationConfig` fields —
  `responseMimeType: "application/json"` and `responseSchema: {...}`.
- Schema support, verbatim: *"Gemini's structured output mode supports a subset of the JSON
  Schema specification."* Supported types: `string`, `number`, `integer`, `boolean`,
  `object`, `array`, `null`. Supported keywords include `properties`, `required`,
  `additionalProperties`, `enum`, `format` (`date-time`/`date`/`time`), `minimum`,
  `maximum`, `items`, `prefixItems`, `minItems`, `maxItems`.
- Limitations, verbatim: *"Not all JSON Schema features are supported"* and *"Very large or
  deeply nested schemas may be rejected."*
- Whether `text/x.enum` and `propertyOrdering` (both present in older Gemini docs) still
  apply is **UNKNOWN** — not documented on the current structured-output page.

### Thinking
`generation_config.thinking_level` (`minimal` / `low` / `medium` / `high`) and
`thinking_summaries` (`auto` / `none`). This is Gemini's replacement for temperature-style
steering on 3.x models. Thinking tokens are **billed as output** and reported separately
(`total_thought_tokens`). Reasoning **cannot be disabled** on Gemini 2.5 Pro or 3.x models.

## Usage/tokens

### Interactions API — `usage` object (snake_case)
Source: https://ai.google.dev/api/interactions.md.txt

```
total_input_tokens
total_output_tokens
total_cached_tokens          <- cached-content tokens
total_thought_tokens         <- reasoning tokens (billed as output)
total_tool_use_tokens
total_tokens
input_tokens_by_modality[]   <- [{modality, tokens}]
output_tokens_by_modality[]
cached_tokens_by_modality[]
tool_use_tokens_by_modality[]
grounding_tool_count
```

> **Conflict flagged:** the migration guide page shows an inline example with
> `usage.prompt_tokens` / `completion_tokens` / `total_tokens` (OpenAI-style names), which
> contradicts the API reference above. The API reference and the raw `.md.txt` agree on the
> `total_*` names, so **treat `total_input_tokens` / `total_output_tokens` /
> `total_tokens` as authoritative** and parse defensively (accept either). This should be
> confirmed against a live response before relying on it.

### `generateContent` — `usageMetadata` object (camelCase)
```
promptTokenCount
cachedContentTokenCount      <- cached-content tokens
candidatesTokenCount
toolUsePromptTokenCount
thoughtsTokenCount
totalTokenCount
promptTokensDetails[] / cacheTokensDetails[] / candidatesTokensDetails[] / toolUsePromptTokensDetails[]
serviceTier
```

**Nothing is named `prompt_tokens` / `completion_tokens` on either surface.** A shared
usage model must map three different vocabularies (OpenAI, Interactions, generateContent).

## Errors

### Interactions API error body
Source: https://ai.google.dev/gemini-api/docs/api-errors

```json
{ "error": { "code": "string (snake_case)", "message": "human-readable" } }
```

`code` is a **snake_case string**, not an integer:

| HTTP | `code` |
|---|---|
| 400 | `invalid_request` |
| 400 | `failed_precondition` (e.g. billing disabled) |
| 400 | `parameter_unknown` |
| 401 | `authentication` (missing/invalid/expired API key) |
| 403 | `permission_denied` |
| 404 | `not_found` |
| 404 | `model_not_found` |
| 409 | `already_exists` |
| 409 | `aborted` |
| 416 | `out_of_range` |
| 429 | `rate_limit_exceeded` (per-minute/second) |
| 429 | `quota_exceeded` (daily) |
| 499 | `cancelled` |
| 500 | `api_error` |
| 501 | `unimplemented` |
| 503 | `service_unavailable` |
| 504 | `deadline_exceeded` |

The same page documents **generation blocked codes** (policy/safety prevented output):
`safety`, `recitation`, `language`, `prohibited_content`, `spii`, `blocklist`,
`image_safety`, `image_prohibited_content`, `image_recitation`, `image_other`,
`content_blocked`

and **generation error codes** (malformed output):
`malformed_function_call`, `malformed_tool_call`, `unexpected_tool_call`, `no_image`,
`too_many_tool_calls`, `missing_thought_signature`

### `generateContent` (legacy) error body
The legacy surface uses the standard **Google API canonical error** envelope:
`{"error": {"code": <int>, "message": <string>, "status": "<UPPER_SNAKE>", "details": [...]}}`
with `status` values like `INVALID_ARGUMENT` (400), `PERMISSION_DENIED` (403),
`NOT_FOUND` (404), `RESOURCE_EXHAUSTED` (429), `INTERNAL` (500), `UNAVAILABLE` (503),
`DEADLINE_EXCEEDED` (504). The troubleshooting page references these status strings
directly. **`error.code` is an integer here and a string in the Interactions API** — a
naive shared parser will break on one of them.

### Retry guidance
Source: https://ai.google.dev/gemini-api/docs/troubleshooting — verbatim:
- *"If you receive an error indicating that you should retry your request (such as a
  `429 RESOURCE_EXHAUSTED` or `503 UNAVAILABLE`), we recommend implementing an exponential
  backoff strategy."*
- *"Use exponential backoff: Wait a short time before the first retry (for example, 1
  second), then increase the delay exponentially (for example, 2s, 4s, 8s)."*
- *"Add jitter: Add random 'jitter' to the delay to help prevent all clients from retrying
  at the exact same time."*
- *"Retry on specific errors: Only retry on transient errors (like `429`, `408`, or `5xx`).
  Do not retry on client errors (like `400` or `403`)"*

## Safety/content blocking

**This is a distinct failure class that is not an HTTP error.** A safety block returns
**HTTP 200** with a structurally valid body containing no usable text.

### `generateContent` (legacy)
Source: https://ai.google.dev/gemini-api/docs/safety-settings
- **Prompt blocked:** verbatim — *"if `promptFeedback.blockReason` is set, then the content
  of the prompt was blocked."* `blockReason` enum: `SAFETY`, `OTHER`, `BLOCKLIST`,
  `PROHIBITED_CONTENT`, `IMAGE_SAFETY`.
- **Response blocked:** `candidates[].finishReason == "SAFETY"` (also `RECITATION`,
  `PROHIBITED_CONTENT`) plus `candidates[].safetyRatings[]`. Verbatim: *"the content that
  was blocked is not returned."*
- Whether `candidates` is empty vs present-with-no-parts when blocked is **UNKNOWN** — the
  page does not say. Defensive parsing must handle: missing `candidates`, empty
  `candidates`, candidate with no `content`, and `content` with no `parts`.

Request shape:
```json
{ "safetySettings": [ { "category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_LOW_AND_ABOVE" } ] }
```
`HarmCategory` (adjustable): `HARM_CATEGORY_HARASSMENT`, `HARM_CATEGORY_HATE_SPEECH`,
`HARM_CATEGORY_SEXUALLY_EXPLICIT`, `HARM_CATEGORY_DANGEROUS`. Child-safety protections are
built in and **not adjustable**.
`HarmBlockThreshold`: `OFF`, `BLOCK_NONE`, `BLOCK_ONLY_HIGH`, `BLOCK_MEDIUM_AND_ABOVE`,
`BLOCK_LOW_AND_ABOVE`, `HARM_BLOCK_THRESHOLD_UNSPECIFIED`.

### Interactions API
`safety_settings[]` with `type` (harm category), `threshold`, optional `method`
(probability- or severity-based).
Categories (lowercase here): `hate_speech`, `dangerous_content`, `harassment`,
`sexually_explicit`, `image_hate`, `image_dangerous_content`, `image_harassment`,
`image_sexually_explicit`, `jailbreak`.
Thresholds: `block_low_and_above`, `block_medium_and_above`, `block_only_high`,
`block_none`, `off`.

**How a block surfaces here is only partially documented.** The `status` enum has no
dedicated `blocked` value; the blocked-generation codes (`safety`, `prohibited_content`,
`blocklist`, `content_blocked`, …) live in the errors reference and most plausibly appear on
the interaction's `error[].code` and/or a step-level status while `status` reads `failed` or
`incomplete`. **The exact wire representation is UNKNOWN and must be confirmed empirically
against a live blocked request before the abstraction commits to a detection rule.**

Practical detection rule that works on both surfaces regardless: **HTTP 2xx + zero extracted
text ⇒ treat as a `ContentBlocked` failure**, then enrich with whichever of
`promptFeedback.blockReason` / `finishReason` / `error[].code` is present.

## Rate limits

Source: https://ai.google.dev/gemini-api/docs/rate-limits

- Dimensions: **RPM** (requests/min), **TPM** (tokens/min), **RPD** (requests/day), plus
  IPM (images/min) and TPD for some models.
- Tiers: **Free**, **Tier 1** (billing enabled, $250 cap), **Tier 2** ($100+ spent and 3
  days elapsed, $2,000 cap), **Tier 3** ($1,000+ spent and 30 days elapsed, $20,000+ cap).
- Additional **rolling 10-minute spend caps**: Tier 1 $10 / 10 min, Tier 2 $50 / 10 min,
  Tier 3 $200 / 10 min. Verbatim: *"If you hit a spend-based rate limit, the API returns a
  `429 RESOURCE_EXHAUSTED` error."*
- **Concrete per-model RPM/TPM/RPD numbers: UNKNOWN.** The page no longer publishes them —
  verbatim: *"Rate limits depend on a variety of factors (such as your usage tier) and can
  be viewed in Google AI Studio"*, linking to
  https://aistudio.google.com/rate-limit. Check the account's own dashboard.
- **Timeouts: UNKNOWN.** No recommended client timeout or documented server deadline is
  published. `504 deadline_exceeded` exists as an error code. Pick a client timeout
  empirically; long-thinking Gemini 3.x calls can run well past 60s, so a naive 30s timeout
  will produce false failures. Consider `background: true` + polling for long work.

## Pricing

Source: https://ai.google.dev/gemini-api/docs/pricing — paid tier, **USD per 1M tokens**.

| Model | Input | Output | Context caching | Free tier |
|---|---|---|---|---|
| `gemini-3.7-flash` | $0.75 (thru 2026-12-31; **$1.50** from 2027-01-01) | $3.75 (thru 2026-12-31; **$7.50** from 2027-01-01) | $0.075 → $0.15 | Yes |
| `gemini-3.6-flash` | $0.75 (→ $1.50 on 2027-01-01) | $3.75 (→ $7.50) | $0.075 → $0.15 | Yes |
| `gemini-3.5-flash` | $1.50 | $9.00 | $0.15 | Yes |
| `gemini-3.5-flash-lite` | $0.30 (text/image/video/audio) | $2.50 | $0.03 | Yes |
| `gemini-3.1-flash-lite` | $0.25 (text/image/video); $0.50 (audio) | $1.50 | $0.025; $0.05 audio | Yes |
| `gemini-3.1-pro-preview` | $2.00 (≤200k); $4.00 (>200k) | $12.00 (≤200k); $18.00 (>200k) | $0.20; $0.40 | **No** |
| `gemini-2.5-pro` | $1.25 (≤200k); $2.50 (>200k) | $10.00 (≤200k); $15.00 (>200k) | $0.125; $0.25 | Yes |
| `gemini-2.5-flash` | $0.30 (text/image/video); $1.00 (audio) | $2.50 | $0.03; $0.10 audio | Yes |
| `gemini-2.5-flash-lite` | $0.10 (text/image/video); $0.30 (audio) | $0.40 | $0.01; $0.03 audio | Yes |

Notes that matter for cost modelling:
- **Thinking tokens bill as output tokens** and cannot be disabled on 2.5 Pro / 3.x. Output
  cost is therefore materially higher than a naive token estimate on reasoning models.
- **Prices are prompt-size tiered** on Pro models (≤200k vs >200k). A single flat
  input-rate field in a cost estimator will be wrong for Pro.
- The 3.6/3.7 Flash prices are **promotional and double on 2027-01-01** — do not hard-code.

## Health/test-connection strategy

Recommended, in order:

1. **`GET /v1beta/models` with the API key header.** Cheapest and safest validity probe —
   no tokens billed, no content generated, exercises exactly the auth path
   (`x-goog-api-key`) that generation uses. Distinguishes cleanly:
   - `200` + non-empty `models[]` ⇒ key valid, network reachable.
   - `401 authentication` / `403 permission_denied` ⇒ bad or unauthorized key.
   - `429` ⇒ key valid but throttled — **report as healthy-but-limited, not invalid.**
   - `5xx` ⇒ upstream issue, not a config problem.
2. **Optionally verify the configured model exists** by checking that the configured ID
   appears in the list (remembering to strip the `models/` prefix), and that
   `supportedGenerationMethods` covers the method you intend to call. This catches the
   very common "model was shut down / renamed" failure before a user hits it.
3. **Avoid a generation call as the health check.** It costs money, is subject to safety
   blocking (a blocked probe would read as a failure when the key is fine), and on
   thinking models can take many seconds.

Use a short, separate timeout for the health probe (a few seconds) — distinct from the
generation timeout, which must be generous.

## OpenAI-compatibility verdict

Source: https://ai.google.dev/gemini-api/docs/openai

**Yes, officially.** Base URL:
```
https://generativelanguage.googleapis.com/v1beta/openai/
```
Used with the stock OpenAI SDKs:
```python
from openai import OpenAI
client = OpenAI(
    api_key="GEMINI_API_KEY",
    base_url="https://generativelanguage.googleapis.com/v1beta/openai/"
)
```
Note that in this mode the key travels as `Authorization: Bearer`, **not** `x-goog-api-key`.

Supported OpenAI-relative paths: `/chat/completions`, `/embeddings`, `/models`,
`/images/generations`, `/videos`. Supported features include streaming, function calling
with tools, structured outputs via JSON schema, thinking/reasoning parameters, image and
audio input, batch, and service tiers (`flex`, `priority`).

**Documented limitations:**
- Verbatim: *"Support for the OpenAI libraries is still in beta while we extend feature
  support."*
- Verbatim: *"Batch support for upload and download is currently not supported."*
- Unsupported parameters are *"silently ignored by the compatibility layer"* for image
  generation — **silent** ignoring, which is the dangerous failure mode.
- Grounding with Google Search is restricted to Gemini 3+ / specific image models.
- Reasoning **cannot be disabled** for Gemini 2.5 Pro or 3.x models.
- Verbatim: *"`reasoning_effort` and `thinking_level`/`thinking_budget` overlap
  functionality, so they can't be used at the same time."*
- Video generation is asynchronous and requires polling.

**Verdict for this project.** The compatibility layer is a legitimate shortcut if the goal
is "add Gemini behind an existing OpenAI-shaped client with minimal code." It buys you a
single request/response shape, one usage model, and one error envelope. The costs:
(1) it is still self-described as **beta**; (2) unsupported params are **silently dropped**,
so misconfiguration is invisible rather than loud; (3) safety blocking, thinking budgets,
`thinking_level`, and the whole Interactions/agentic surface are either absent or
awkwardly mapped; (4) Google states plainly that **new capabilities land on the Interactions
API**, so the compat layer is structurally a trailing surface.

Recommendation: **use the native Interactions API** for a first-class Gemini provider, and
treat the OpenAI-compat endpoint as a fallback/prototyping path only. If the abstraction is
deliberately thin and the product only needs plain chat + JSON output, the compat layer is
a defensible time-saver — but write it down as a known ceiling, not as the design.

## Differences from OpenAI that an abstraction must accommodate

Ordered by how badly each one breaks a naive "swap the base URL and model name" design.

1. **Two Gemini surfaces, not one.** Before anything else, the abstraction must choose
   between the Interactions API (current) and `generateContent` (legacy). They differ in
   URL shape, casing, request nesting, response nesting, token-usage names, and error body
   shape. Treating "Gemini" as one provider with one adapter is the first mistake.

2. **Model ID location is not stable across Gemini's own surfaces.**
   - OpenAI: model in the **body** (`{"model": "..."}`), fixed path `/v1/chat/completions`.
   - Gemini `generateContent`: model in the **URL path**, prefixed and verb-suffixed —
     `/v1beta/models/{id}:generateContent`.
   - Gemini Interactions: model back in the **body**.
   Any provider interface that models an endpoint as a constant string and the model as a
   payload field cannot express `generateContent`. The endpoint must be a **function of the
   model and the operation**, e.g. `resolve_url(model, op, stream)`.

3. **The `:method` colon-verb convention.** `generateContent` vs `streamGenerateContent`
   are *different URLs*, whereas OpenAI (and Gemini Interactions) toggle streaming with a
   body flag. A `stream: bool` that only ever touches the payload is insufficient; it must
   be allowed to change the URL.

4. **`messages[]` has no direct equivalent.**
   - OpenAI: `[{role, content: string}]`.
   - `generateContent`: `contents: [{role, parts: [{text}]}]` — content is **always an array
     of typed parts**, never a bare string.
   - Interactions: `input` is either a bare string or an array of **typed steps**
     (`{"type": "user_input", "content": ...}`) where role is encoded in `type`, not a
     `role` key.
   A canonical internal message type must survive round-tripping into all three.

5. **The assistant role is `"model"`, not `"assistant"`.** In `generateContent`,
   `role: "assistant"` is invalid. In Interactions there is no role field at all — the
   assistant turn is a step of `type: "model_output"`. Any hardcoded `"assistant"` string
   in shared history handling will fail or silently mis-attribute turns.

6. **System instruction is a top-level field, not a message.** OpenAI puts it in
   `messages[0]` with `role: "system"`. Gemini has a dedicated slot — a `Content` **object**
   (`systemInstruction.parts[].text`) in `generateContent`, and a **plain string**
   (`system_instruction`) in Interactions. Three different representations. A shared
   "prepend the system prompt to the message list" helper is wrong for Gemini, and a shared
   "system instruction is a string" assumption is wrong for `generateContent`.

7. **Safety blocking is a success-shaped failure.** A blocked request returns **HTTP 200**
   with a well-formed body and no usable text. Nothing in an OpenAI-derived error hierarchy
   models this. The abstraction needs an explicit **`ContentBlocked`** result distinct from
   both `Success` and `TransportError`, populated from `promptFeedback.blockReason` /
   `finishReason == SAFETY` / the Interactions blocked-generation codes. Absent this, the
   app will surface an empty assistant message and look broken. This also means **the
   response parser must never assume text exists** — `candidates` may be absent or empty,
   a candidate may carry no `content`, and `content` may carry no `parts`.

8. **Text extraction is a tree walk, not an index.** OpenAI: `choices[0].message.content`.
   Gemini `generateContent`: `candidates[0].content.parts[]` where parts may interleave
   text, thought, and `functionCall` blocks. Gemini Interactions: walk `steps[]` for
   `type == "model_output"`, then its `content[]` for `type == "text"`. The SDK-only
   `output_text` convenience field **does not exist on the wire** — a hand-rolled HTTP
   client must implement the walk itself.

9. **Token usage has three incompatible vocabularies.** OpenAI's
   `prompt_tokens`/`completion_tokens`/`total_tokens` maps to
   `promptTokenCount`/`candidatesTokenCount`/`totalTokenCount` (generateContent) and
   `total_input_tokens`/`total_output_tokens`/`total_tokens` (Interactions). Gemini also
   reports dimensions OpenAI has no field for: `total_thought_tokens` /`thoughtsTokenCount`
   (reasoning, **billed as output**), `total_cached_tokens`/`cachedContentTokenCount`, and
   per-modality breakdowns. A usage record with only three integer fields will under-report
   real cost on every thinking model.

10. **Two different error envelopes, with `error.code` changing type.** Interactions:
    `error.code` is a **snake_case string** (`rate_limit_exceeded`). `generateContent`:
    `error.code` is an **integer** alongside `error.status` as an **UPPER_SNAKE string**
    (`RESOURCE_EXHAUSTED`). OpenAI: `error.type` / `error.code`. Error classification must
    key off **HTTP status first**, with the body used only for enrichment.

11. **Structured output has a different parameter name on each surface.** OpenAI:
    `response_format: {type: "json_schema", json_schema: {...}}`. Interactions:
    `response_format: {type, mime_type, schema}` — *same key name, different inner shape*,
    which is worse than a rename because it will pass a shallow type check. `generateContent`:
    two separate fields, `responseMimeType` + `responseSchema`. And Gemini accepts only a
    **subset of JSON Schema**, so a schema that works with OpenAI may be rejected.

12. **`temperature` is deprecated on Gemini 3.x.** Verbatim from the July 21, 2026 release
    notes: *"The sampling parameters `temperature`, `top_p` and `top_k` are now
    deprecated."* Google directs developers to `thinking_level` instead. An abstraction
    whose canonical request always carries `temperature` is sending a deprecated parameter,
    and has no field for the knob that actually matters. Make sampling params **optional and
    per-provider**, and add a provider-specific `thinking_level` passthrough.

13. **Model listing is prefixed and paginated.** `name` is `models/gemini-3.7-flash`, not
    `gemini-3.7-flash`, and results page via `nextPageToken` rather than a flat `data[]`.
    A shared "list models" implementation must strip the prefix and follow pages.

14. **Function-calling enums differ in casing between Gemini's own surfaces** — lowercase
    `auto`/`any`/`none`/`validated` (Interactions) vs uppercase `AUTO`/`ANY`/`NONE`
    (generateContent) — and the legacy surface nests declarations inside a
    `functionDeclarations` wrapper that the new one drops.

15. **Statefulness is a Gemini-native concept.** `store` (default **`true`**) persists the
    conversation server-side, and `previous_interaction_id` chains turns without resending
    history. OpenAI's chat completions are stateless by default. **The default is the
    surprising direction**: unless the abstraction explicitly sends `store: false`,
    conversations are being retained server-side. That is a privacy/retention decision the
    abstraction should make deliberately, not inherit.

16. **A dated `Api-Revision` header governs the response schema.** Nothing in the OpenAI
    world corresponds to this. Google flipped the Interactions schema under this header in
    May–June 2026 (`outputs` → `steps`). The abstraction should **send an explicit pin**
    (`Api-Revision: 2026-05-20`) rather than inherit whatever the default becomes next.

17. **Streaming wire formats differ.** OpenAI: unnamed SSE `data:` frames terminated by
    `data: [DONE]`. Gemini Interactions: **named SSE events**
    (`step.delta`, `interaction.completed`, …) terminated by `event: done` / `data: [DONE]`.
    Gemini legacy: a separate `:streamGenerateContent` endpoint that returns a streamed JSON
    array unless `?alt=sse` is set. A single SSE parser will not cover all three.

18. **Auth header name.** `x-goog-api-key`, not `Authorization: Bearer` — except in the
    OpenAI-compat mode, where it *is* Bearer. Auth must be per-adapter, not global.

## Unknowns

- **Concrete RPM / TPM / RPD numbers** for any model or tier — no longer published;
  the docs redirect to the per-account AI Studio dashboard. **UNKNOWN.**
- **Recommended or enforced request timeout / server deadline** — not documented.
  `504 deadline_exceeded` exists but no duration is stated. **UNKNOWN.**
- **Exact wire representation of a safety block in the Interactions API** — no `blocked`
  status enum value is documented; the blocked-generation codes live in the errors
  reference without a worked example. Must be confirmed empirically. **UNKNOWN.**
- **Whether `candidates` is absent or empty on a blocked `generateContent` response** —
  the safety page says blocked content "is not returned" but does not specify the shape.
  **UNKNOWN.**
- **Interactions `usage` field names** — the API reference and raw `.md.txt` say
  `total_input_tokens` / `total_output_tokens` / `total_tokens`; the migration guide's
  inline example says `prompt_tokens` / `completion_tokens` / `total_tokens`. Docs
  disagree. **Confirm against a live response.**
- **Whether `text/x.enum` responseMimeType and `propertyOrdering`** (present in older
  Gemini structured-output docs) are still supported — not mentioned on the current page.
  **UNKNOWN.**
- **Whether `generateContent` has any announced shutdown date** — labelled "legacy" and
  "fully supported", no sunset published. **UNKNOWN.**
- **Full `ModelOption` enum** accepted by the Interactions API `model` field — the
  reference lists examples rather than an exhaustive set. Use `GET /v1beta/models` at
  runtime rather than hard-coding. **UNKNOWN.**
- **Free-tier rate limits specifically** (as opposed to availability, which is documented
  per model on the pricing page). **UNKNOWN.**
