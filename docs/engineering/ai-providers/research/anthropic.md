# Anthropic / Claude

Research date: **2026-08-18**. All facts below come from official Anthropic documentation only.

> **Docs domain moved.** `docs.anthropic.com` and `docs.claude.com` now 301-redirect to
> `https://platform.claude.com/docs/...`. Use `platform.claude.com` for canonical URLs.
> The API host itself is unchanged: `https://api.anthropic.com`.

## Sources

All fetched 2026-08-18:

| Topic | URL |
| --- | --- |
| API overview (base URL, auth headers, response headers, size limits) | https://platform.claude.com/docs/en/api/overview |
| Messages API reference | https://platform.claude.com/docs/en/api/messages (redirects to `https://platform.claude.com/docs/en/api/messages/create`) |
| Using the Messages API (system field, prefill, vision, sampling-param removal) | https://platform.claude.com/docs/en/build-with-claude/working-with-messages |
| API versions | https://platform.claude.com/docs/en/api/versioning |
| Errors (statuses, error body, long requests) | https://platform.claude.com/docs/en/api/errors |
| Rate limits (headers, tiers, cache-aware ITPM) | https://platform.claude.com/docs/en/api/rate-limits |
| Models overview (current model IDs) | https://platform.claude.com/docs/en/about-claude/models/overview |
| List Models endpoint | https://platform.claude.com/docs/en/api/models/list |
| Pricing | https://platform.claude.com/docs/en/about-claude/pricing |
| Streaming messages (SSE events, deltas) | https://platform.claude.com/docs/en/build-with-claude/streaming |
| Tool use overview | https://platform.claude.com/docs/en/agents-and-tools/tool-use/overview |
| Structured outputs | https://platform.claude.com/docs/en/build-with-claude/structured-outputs |
| Effort parameter | https://platform.claude.com/docs/en/build-with-claude/effort |
| OpenAI SDK compatibility | https://platform.claude.com/docs/en/api/openai-sdk (canonical: `https://platform.claude.com/docs/en/cli-sdks-libraries/libraries/openai-sdk`) |

---

## Auth

Source: https://platform.claude.com/docs/en/api/overview (§ Authentication)

**It is NOT an OpenAI-style `Authorization: Bearer <key>` header.** The API key goes in a dedicated
`x-api-key` header.

| Header | Value | Required |
| --- | --- | --- |
| `x-api-key` | Your API key from Console | One of `x-api-key` or `Authorization` |
| `Authorization` | `Bearer <token>`, where `<token>` is a short-lived access token obtained from `POST /v1/oauth/token` through Workload Identity Federation | One of `x-api-key` or `Authorization` |
| `anthropic-version` | API version (for example, `2023-06-01`) | **Yes** |
| `content-type` | `application/json` | **Yes** |

Notes:

- A `Authorization: Bearer` header **does** exist, but only for Workload Identity Federation
  short-lived OAuth tokens — not for a plain API key. For an ordinary `sk-ant-…` API key, use
  `x-api-key`. Sending an API key as a bearer token is not the documented path.
- The docs render the header inconsistently as `X-Api-Key` in some curl samples and `x-api-key` in
  others; HTTP headers are case-insensitive, so either works. Prefer lowercase `x-api-key`.
- Optional beta features are opted into with an `anthropic-beta` header (array of beta strings, e.g.
  `output-300k-2026-03-24`, `context-1m-2025-08-07`). Not needed for baseline chat.

### API version

Source: https://platform.claude.com/docs/en/api/versioning

- Current / recommended value: **`2023-06-01`**.
- Only two versions have ever existed: `2023-01-01` (initial release) and `2023-06-01`.
- The header is **mandatory** on every request. Under a given version Anthropic preserves existing
  input/output params but may **add** optional inputs, add output values, and add new enum variants
  (including new streaming event types and new `stop_reason` values) — so parsers must tolerate
  unknown enum values.

---

## Endpoints

Base URL: **`https://api.anthropic.com`**

| Purpose | Method + path | Status |
| --- | --- | --- |
| Create a message (chat) | `POST /v1/messages` | GA |
| Count tokens | `POST /v1/messages/count_tokens` | GA |
| Message batches | `POST /v1/messages/batches` | GA (50% discount) |
| **List models** | `GET /v1/models` | GA |
| Files | `POST /v1/files`, `GET /v1/files` | Beta |
| Skills | `POST /v1/skills`, `GET /v1/skills` | Beta |
| Agents / Sessions / Environments (Managed Agents) | `POST /v1/agents`, `POST /v1/sessions`, `POST /v1/environments` | Beta |
| OpenAI-compatible chat completions | `POST /v1/chat/completions` (base_url `https://api.anthropic.com/v1/`) | Compatibility layer, see verdict below |

Request size limits (413 `request_too_large` if exceeded):
Messages / Token Counting **32 MB**, Batches 256 MB, Files 500 MB.

Response headers on every call: `request-id` (e.g. `req_018EeWyXxfu5pfWkrYcMdjWG`),
`anthropic-organization-id`, `anthropic-workspace-id`.

---

## Request shape

Source: https://platform.claude.com/docs/en/api/messages/create and
https://platform.claude.com/docs/en/build-with-claude/working-with-messages

Canonical minimal request (verbatim from docs):

```bash
curl https://api.anthropic.com/v1/messages \
  -H "x-api-key: $ANTHROPIC_API_KEY" \
  -H "anthropic-version: 2023-06-01" \
  -H "content-type: application/json" \
  -d '{
    "model": "claude-opus-5",
    "max_tokens": 1024,
    "messages": [
      {"role": "user", "content": "Hello, Claude"}
    ]
  }'
```

### Required fields

| Field | Type | Notes |
| --- | --- | --- |
| `model` | string | e.g. `claude-opus-5`, `claude-sonnet-5` |
| `messages` | array | objects with `role` and `content` |
| **`max_tokens`** | number | **REQUIRED.** Model-dependent ceiling. There is no default; omitting it is a 400. |

### Key optional fields

| Field | Type | Default | Notes |
| --- | --- | --- | --- |
| **`system`** | string **or** array of text blocks | — | **Top-level field, not a message role.** This is the system prompt. |
| `temperature` | number | 1.0 | 0.0–1.0. **Rejected (400) on Claude 4.7 and later models.** |
| `top_p` | number | — | **Rejected (400) on Claude 4.7 and later models.** |
| `top_k` | number | — | **Rejected (400) on Claude 4.7 and later models.** |
| `stop_sequences` | array of string | — | |
| `stream` | boolean | false | SSE |
| `tools` | array | — | see Capabilities |
| `tool_choice` | object | — | `{"type": "auto"\|"any"\|"tool"\|"none"}` (+ `disable_parallel_tool_use`) |
| `metadata` | object | — | user id / request metadata |
| `thinking` | object | — | `{"type": "enabled"\|"disabled"\|"adaptive"}`; support varies sharply by model |
| `service_tier` | string | `auto` | `auto` or `standard_only` |
| `output_config` | object | — | Holds `format` (structured outputs), `effort`, `task_budget` |
| `cache_control` | object | — | Top-level automatic prompt caching with ephemeral TTL; can also be placed per content block |
| `container` | string | — | container reuse |
| `inference_geo` | string | `global` | `"us"` applies a 1.1x price multiplier (Claude 4.6+ only) |

### The system prompt — the #1 OpenAI difference

Confirmed at https://platform.claude.com/docs/en/build-with-claude/working-with-messages
(§ System role in messages), verbatim:

> "A `system` message cannot be the first entry in `messages`; use the top-level `system` field for
> instructions that apply from the start."

- `system` is a **top-level request field**, sibling to `messages`. It accepts a plain string or an
  array of text blocks (the array form is what lets you attach `cache_control` to the system prompt).
- On **Claude Fable 5, Claude Mythos 5, Claude Opus 4.8, and Claude Opus 5 only**, you may
  additionally insert `{"role": "system", ...}` entries *after a user turn* for mid-conversation
  instructions. That is an extra capability, not the way to set the initial system prompt, and it is
  explicitly forbidden at index 0.

### Messages array

```json
[
  {"role": "user", "content": "Hello, Claude"},
  {"role": "assistant", "content": "Hi! How can I help?"},
  {"role": "user", "content": [
    {"type": "text", "text": "Explain LLMs", "cache_control": {"type": "ephemeral", "ttl": "5m"}},
    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "..."}}
  ]}
]
```

- Role values: `user`, `assistant`, `system` (the last only mid-conversation, on the models listed above).
- Content is either a plain string or an array of typed blocks.
- Content block types: `text`, `image`, `document`, `tool_use`, `tool_result`, `thinking`,
  `redacted_thinking`, `search_result`.
- Image blocks use `{"type": "image", "source": {"type": "base64"|"url"|"file", "media_type": ..., "data"|"url": ...}}`.
  Supported media types: `image/jpeg`, `image/png`, `image/gif`, `image/webp`.
- The API is **stateless** — always send the whole conversation history.
- **Assistant prefill is not supported on Claude 4.6 and later.** Ending `messages` with an assistant
  turn returns 400 `invalid_request_error`: "This model does not support assistant message prefill.
  The conversation must end with a user message."

---

## Response shape

Source: https://platform.claude.com/docs/en/api/messages/create

Minimal real response (verbatim from working-with-messages):

```json
{
  "id": "msg_01XFDUDYJgAACzvnptvVoYEL",
  "type": "message",
  "role": "assistant",
  "content": [
    {"type": "text", "text": "Hello!"}
  ],
  "model": "claude-opus-5",
  "stop_reason": "end_turn",
  "stop_sequence": null,
  "usage": {"input_tokens": 12, "output_tokens": 6}
}
```

Full documented shape:

```json
{
  "id": "msg_...",
  "type": "message",
  "role": "assistant",
  "model": "claude-opus-5",
  "content": [
    {"type": "text", "text": "Response text", "citations": [ /* char_location objects */ ]},
    {"type": "tool_use", "id": "toolu_...", "name": "tool_name", "input": {}, "caller": {"type": "direct"}}
  ],
  "stop_reason": "end_turn",
  "stop_sequence": null,
  "stop_details": {"type": "refusal", "category": "cyber", "explanation": "..."},
  "usage": { /* see Usage/tokens */ }
}
```

- `content` is **always an array of blocks**, never a bare string. Plain text answers require
  concatenating every block where `type == "text"`.
- `stop_details` is present on refusals (`stop_reason: "refusal"`) on every model.

### `stop_reason` values

| Value | Meaning |
| --- | --- |
| `end_turn` | Model reached a natural stopping point |
| `max_tokens` | Exceeded the `max_tokens` limit |
| `stop_sequence` | A custom stop sequence was generated |
| `tool_use` | Model invoked tools |
| `pause_turn` | Long-running turn paused |
| `refusal` | Policy violation blocked |
| `model_context_window_exceeded` | Context limit exceeded |

Per the versioning policy, **new values may be added** — treat this as an open enum.

---

## Model listing

**Yes, a model listing endpoint exists.** Source: https://platform.claude.com/docs/en/api/models/list

`GET /v1/models` — "More recently released models are listed first."

```bash
curl https://api.anthropic.com/v1/models \
  -H 'anthropic-version: 2023-06-01' \
  -H "X-Api-Key: $ANTHROPIC_API_KEY"
```

Query params: `after_id`, `before_id`, `limit` (default 20, range 1–1000).
There is also a single-model retrieve endpoint, `GET /v1/models/{model_id}`.

Response:

```json
{
  "data": [
    {
      "id": "claude-opus-4-6",
      "type": "model",
      "display_name": "Claude Opus 4.6",
      "created_at": "2026-02-04T00:00:00Z",
      "max_input_tokens": 0,
      "max_tokens": 0,
      "capabilities": {
        "batch": {"supported": true},
        "citations": {"supported": true},
        "code_execution": {"supported": true},
        "context_management": {"supported": true, "clear_thinking_20251015": {"supported": true}, "clear_tool_uses_20250919": {"supported": true}, "compact_20260112": {"supported": true}},
        "effort": {"supported": true, "low": {"supported": true}, "medium": {"supported": true}, "high": {"supported": true}, "max": {"supported": true}, "xhigh": {"supported": true}},
        "image_input": {"supported": true},
        "pdf_input": {"supported": true},
        "structured_outputs": {"supported": true},
        "thinking": {"supported": true, "types": {"adaptive": {"supported": true}, "enabled": {"supported": true}}}
      }
    }
  ],
  "first_id": "first_id",
  "has_more": true,
  "last_id": "last_id"
}
```

Field names for the abstraction: `data[].id`, `data[].display_name`, `data[].max_input_tokens`,
`data[].max_tokens` (= the maximum legal value of the request's `max_tokens`), `data[].capabilities`.
Pagination is `has_more` / `first_id` / `last_id` with `after_id` / `before_id` cursors — **not** the
newer `page` / `next_page` scheme used elsewhere in the API.

`capabilities.structured_outputs.supported` and `capabilities.effort.*` make this endpoint usable as
a runtime capability probe, which is valuable for a provider-neutral layer.

---

## Models

Source: https://platform.claude.com/docs/en/about-claude/models/overview (fetched 2026-08-18)

### Current models

| Model | Claude API ID (exact) | Alias | Context | Max output | Price in/out per MTok |
| --- | --- | --- | --- | --- | --- |
| Claude Fable 5 | `claude-fable-5` | `claude-fable-5` | 1M | 128k | $10 / $50 |
| Claude Opus 5 | `claude-opus-5` | `claude-opus-5` | 1M | 128k | $5 / $25 |
| Claude Sonnet 5 | `claude-sonnet-5` | `claude-sonnet-5` | 1M | 128k | $2 / $10 |
| Claude Haiku 4.5 | `claude-haiku-4-5-20251001` | `claude-haiku-4-5` | 200k | 64k | $1 / $5 |
| Claude Mythos 5 | `claude-mythos-5` | — | 1M | 128k | $10 / $50 — invitation-only (Project Glasswing), not GA |

### Recommendation for this project

- **(a) General chat:** `claude-sonnet-5` — "The best combination of speed and intelligence",
  1M context, 128k output, $2/$10. If maximum quality is wanted instead, `claude-opus-5` ($5/$25).
- **(b) Cheap/fast classification:** `claude-haiku-4-5` (alias) → resolves to the dated snapshot
  `claude-haiku-4-5-20251001`. Documented as "The fastest model with near-frontier intelligence",
  latency "Fastest", $1/$5. **This is the only current Haiku — there is no Haiku 5 as of 2026-08-18.**
  Haiku 3.5 is retired on the first-party API.

### Model ID / snapshot format — important

Verbatim from the Models overview:

> "Every Claude model ID is a pinned snapshot. Models with a date in the ID (for example, `20250929`)
> are fixed to that specific release. **Starting with the Claude 4.6 generation, model IDs use a
> dateless format that is also a pinned snapshot, not an evergreen pointer.** For models before the
> 4.6 generation, entries in the Claude API alias column are convenience pointers that resolve to a
> dated model ID."

So an abstraction must **not** assume `<family>-<version>-<YYYYMMDD>` universally. Two shapes coexist:

- Dateless pinned snapshot (4.6 generation onward): `claude-opus-5`, `claude-sonnet-5`, `claude-fable-5`,
  `claude-opus-4-8`, `claude-opus-4-7`, `claude-opus-4-6`, `claude-sonnet-4-6`.
- Dated snapshot + alias (pre-4.6): `claude-haiku-4-5-20251001` (alias `claude-haiku-4-5`),
  `claude-sonnet-4-5-20250929` (alias `claude-sonnet-4-5`), `claude-opus-4-5-20251101` (alias `claude-opus-4-5`).

### Legacy but still available

`claude-opus-4-8`, `claude-opus-4-7`, `claude-opus-4-6`, `claude-sonnet-4-6`,
`claude-sonnet-4-5-20250929`, `claude-opus-4-5-20251101`.

### Model-dependent behaviour an abstraction must branch on

| Behaviour | Rule |
| --- | --- |
| `temperature` / `top_p` / `top_k` | **Not supported on Claude 4.7 and later** (so: Opus 5, Sonnet 5, Fable 5, Opus 4.8, Opus 4.7). Non-default values return 400. Supported on Sonnet 4.6/4.5, Haiku 4.5, Opus 4.6/4.5. |
| Assistant prefill | Rejected on Claude 4.6 and later |
| `thinking: {"type": "enabled"}` (extended thinking) | Only Haiku 4.5 and Claude 4.5/4.6-era models; 400 on 4.7+ |
| `thinking: {"type": "adaptive"}` | Opus 5 / Sonnet 5 / Fable 5 / Opus 4.6–4.8 / Sonnet 4.6; 400 on 4.5-and-earlier |
| `thinking: {"type": "disabled"}` | 400 on Fable 5 / Mythos 5 (thinking always on) |
| `output_config.effort` | `claude-fable-5`, `claude-mythos-5`, `claude-opus-5`, `claude-opus-4-8`, `claude-opus-4-7`, `claude-opus-4-6`, `claude-sonnet-5`, `claude-sonnet-4-6`, `claude-opus-4-5-20251101`. **Not Haiku 4.5.** |
| Structured outputs | `claude-fable-5`, `claude-opus-5`, `claude-opus-4-8/4-7/4-6`, `claude-sonnet-5`, `claude-sonnet-4-6`, `claude-sonnet-4-5-20250929`, `claude-opus-4-5-20251101`, `claude-haiku-4-5-20251001` |
| Tokenizer | Claude 4.7 and later use a newer tokenizer producing **~30% more tokens for the same text**. Token-count-based cost estimates are not comparable across generations. |

---

## Capabilities

### Streaming

Source: https://platform.claude.com/docs/en/build-with-claude/streaming

Set `"stream": true`. SSE with **named events** (`event: <name>`), and each data payload repeats the
name in its `type` field. There is **no `data: [DONE]` sentinel** (removed in version `2023-06-01`).

Event flow:

1. `message_start` — a `Message` object with empty `content`
2. per content block: `content_block_start` → 0..n `content_block_delta` → `content_block_stop`
   (each carries an `index` matching the final `content` array position)
3. one or more `message_delta` — top-level changes to the final Message
4. `message_stop`
5. `ping` events may appear anywhere

Delta types inside `content_block_delta.delta.type`:

| Delta type | Payload field | Notes |
| --- | --- | --- |
| `text_delta` | `text` | ordinary text |
| `input_json_delta` | `partial_json` | **partial JSON string** fragments for `tool_use.input`; accumulate and parse at `content_block_stop` |
| `thinking_delta` | `thinking` | thinking content |
| `signature_delta` | `signature` | sent just before `content_block_stop` on thinking blocks |

Verbatim basic stream:

```
event: message_start
data: {"type": "message_start", "message": {"id": "msg_...", "type": "message", "role": "assistant", "content": [], "model": "claude-opus-5", "stop_reason": null, "stop_sequence": null, "usage": {"input_tokens": 25, "output_tokens": 1}}}

event: content_block_start
data: {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}

event: ping
data: {"type": "ping"}

event: content_block_delta
data: {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Hello"}}

event: content_block_stop
data: {"type": "content_block_stop", "index": 0}

event: message_delta
data: {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence":null}, "usage": {"output_tokens": 15}}

event: message_stop
data: {"type": "message_stop"}
```

- `stop_reason` arrives in `message_delta.delta.stop_reason`, **not** in `message_stop`.
- **Token counts in `message_delta.usage` are cumulative** (docs warn explicitly).
- Mid-stream errors arrive after an HTTP 200 as an `error` event and do NOT follow normal HTTP error handling:
  ```
  event: error
  data: {"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}}
  ```
- Code must handle unknown event types gracefully (versioning policy).

### Tool use

Source: https://platform.claude.com/docs/en/agents-and-tools/tool-use/overview

Tool definition — note `input_schema`, at the top level of the tool object (no `function` wrapper):

```json
{
  "name": "get_weather",
  "description": "Get the current weather for a given location.",
  "input_schema": {
    "type": "object",
    "properties": {
      "location": {"type": "string", "description": "City and state, e.g. San Francisco, CA"}
    },
    "required": ["location"]
  }
}
```

- `tool_choice`: `{"type": "auto"}` (default), `{"type": "any"}`, `{"type": "tool", "name": ...}`,
  `{"type": "none"}`; plus `disable_parallel_tool_use: true` to cap at one call per turn.
- `strict: true` on a tool definition guarantees schema conformance (Strict tool use).
- The model returns `stop_reason: "tool_use"` and one or more blocks:
  ```json
  {"type": "tool_use", "id": "toolu_01A09q90qw90lq917835lq9", "name": "get_weather", "input": {"location": "New York, NY"}}
  ```
  `input` is an **already-parsed object**, not a JSON string.
- Results are returned as a **`user` message** containing `tool_result` blocks:
  ```json
  {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_...", "content": "15 degrees Celsius, partly cloudy"}]}
  ```
  (`is_error: true` signals a failed tool.) There is no `tool` role.
- Server tools (`web_search`, `web_fetch`, `code_execution`, `tool_search`, `advisor`) execute on
  Anthropic's infrastructure and need no client handler.

### Structured output / JSON

Source: https://platform.claude.com/docs/en/build-with-claude/structured-outputs

```json
{
  "output_config": {
    "format": {
      "type": "json_schema",
      "schema": {
        "type": "object",
        "properties": {"name": {"type": "string"}, "email": {"type": "string"}},
        "required": ["name", "email"],
        "additionalProperties": false
      }
    }
  }
}
```

- Exact parameter path: **`output_config.format.type = "json_schema"`** and
  **`output_config.format.schema = <JSON Schema>`**.
- Migration note in the docs: the parameter moved from a beta top-level `output_format` to
  `output_config.format`; the old beta header `structured-outputs-2025-11-13` and `output_format`
  keep working "for a transition period". Target `output_config.format` in new code.
- The result comes back as **valid JSON inside the normal text content block** — there is no separate
  parsed field. Docs: "Claude's response is valid JSON matching your schema, returned in the
  response's text content block."
- No `response_format` parameter exists on the native API.

### Effort (reasoning budget)

Source: https://platform.claude.com/docs/en/build-with-claude/effort

`output_config.effort` with values `low`, `medium`, `high` (default), `xhigh`, `max`.
Not `reasoning_effort`. `effort: "high"` == omitting the parameter. Changing effort between requests
invalidates prompt-cache prefixes. `adaptive` is a *thinking* mode, never an effort value.

### Prompt caching

`cache_control` either at the top level of the request (automatic breakpoint management) or on
individual content blocks, with `{"type": "ephemeral", "ttl": "5m"}` (or `"1h"`).
There is no OpenAI equivalent — caching there is implicit.

### Other

- Vision: `image` content blocks with `base64`, `url`, or `file` sources.
- Token counting without inference: `POST /v1/messages/count_tokens` → `{"input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"}`.
- Batch: `POST /v1/messages/batches`, 50% discount.

---

## Usage/tokens

Source: https://platform.claude.com/docs/en/api/messages/create and
https://platform.claude.com/docs/en/api/rate-limits

```json
"usage": {
  "input_tokens": 100,
  "output_tokens": 50,
  "cache_creation_input_tokens": 0,
  "cache_read_input_tokens": 0,
  "cache_creation": {
    "ephemeral_5m_input_tokens": 0,
    "ephemeral_1h_input_tokens": 0
  },
  "inference_geo": "global",
  "server_tool_use": {
    "web_search_requests": 0,
    "web_fetch_requests": 0
  },
  "service_tier": "standard",
  "output_tokens_details": {
    "thinking_tokens": 0
  }
}
```

Exact field names: `input_tokens`, `output_tokens`, `cache_creation_input_tokens`,
`cache_read_input_tokens`, `cache_creation.ephemeral_5m_input_tokens`,
`cache_creation.ephemeral_1h_input_tokens`, `server_tool_use.web_search_requests`,
`server_tool_use.web_fetch_requests`, `server_tool_use.code_execution_requests`,
`output_tokens_details.thinking_tokens`, `service_tier`, `inference_geo`.

**There is no `total_tokens` field, and `input_tokens` is NOT total input.** Verbatim from the rate
limits page:

> "The `input_tokens` field only represents tokens that appear **after your last cache breakpoint**,
> not all input tokens in your request."
>
> ```
> total_input_tokens = cache_read_input_tokens + cache_creation_input_tokens + input_tokens
> ```

Any cost/usage accounting layer must compute that sum itself. Example from the docs: a 200k cached
document + a 50-token question reports `input_tokens: 50`.

Streaming: `message_start.message.usage` carries initial input tokens; `message_delta.usage` carries
**cumulative** output tokens.

---

## Errors

Source: https://platform.claude.com/docs/en/api/errors

### Error body (verbatim)

```json
{
  "type": "error",
  "error": {
    "type": "not_found_error",
    "message": "The requested resource could not be found."
  },
  "request_id": "req_011CSHoEeqs5C35K2UUqR7Fy"
}
```

Top level always has `type: "error"`, a nested `error` object with `type` + `message`, and a
`request_id`. The docs note that `type` values "may grow over time" — treat as an open enum.

### Status → error `type`

| HTTP | `error.type` | Meaning |
| --- | --- | --- |
| 400 | `invalid_request_error` | Bad format/content. Also used for other unlisted 4XX. |
| 401 | `authentication_error` | Bad/revoked/expired API key |
| 402 | `billing_error` | Billing or payment problem |
| 403 | `permission_error` | Key lacks permission for the resource |
| 404 | `not_found_error` | Unknown endpoint or resource ID |
| 409 | `conflict_error` | Concurrent modification / uniqueness conflict |
| 413 | `request_too_large` | Over the size cap (32 MB for Messages) |
| 429 | `rate_limit_error` | Rate limit or acceleration limit hit |
| 500 | `api_error` | Internal error — retry with exponential backoff |
| 504 | `timeout_error` | Timed out while processing — use streaming for long requests |
| 529 | `overloaded_error` | API temporarily overloaded (global traffic) |

### Common 400 validation errors worth special-casing

- Prefill not supported (Claude 4.6+): "This model does not support assistant message prefill. The
  conversation must end with a user message."
- Thinking blocks modified: "`thinking` or `redacted_thinking` blocks in the latest assistant message
  cannot be modified..." — thinking blocks must be echoed back byte-identical, including empty ones.
- Extended thinking removed (4.7+): `"thinking.type.enabled" is not supported for this model. Use "thinking.type.adaptive" and "output_config.effort" to control thinking behavior.`
- Adaptive thinking unsupported (4.5 and earlier): "adaptive thinking is not supported on this model"
- Thinking cannot be disabled (Fable 5 / Mythos 5)
- Non-default `temperature` / `top_p` / `top_k` on Claude 4.7+ → 400

### Retry / timeout guidance

- Official SDKs "automatically retry transient failures (such as connection errors, rate limits, and
  5xx server errors) with exponential backoff, **twice by default**, honoring the `retry-after`
  header when present." Max-retries is configurable per client. A hand-rolled client should mirror
  this: retry 408/409-ish transients, 429, 500, 502/503, 504, 529 with exponential backoff and
  respect `retry-after`.
- Do **not** retry 400/401/402/403/404/413.
- **Long requests:** docs warn to use streaming or the Batch API for anything over ~10 minutes.
  The SDKs "validate that your non-streaming Messages API requests are not expected to exceed a
  **10-minute timeout**" and set TCP keep-alive. Avoid large `max_tokens` on non-streaming calls;
  idle connections get dropped by intermediate networks.
- `retry-after` is in **seconds**; "Earlier retries will fail."

---

## Rate limits

Source: https://platform.claude.com/docs/en/api/rate-limits

Limits are per organization, per model, enforced with a **token bucket** (continuous replenishment,
not fixed-window resets), across three axes: RPM, ITPM (input tokens/min), OTPM (output tokens/min).

### Response headers (exact names)

| Header | Meaning |
| --- | --- |
| `retry-after` | Seconds to wait before retrying |
| `anthropic-ratelimit-requests-limit` | Max requests per period |
| `anthropic-ratelimit-requests-remaining` | Requests left |
| `anthropic-ratelimit-requests-reset` | RFC 3339 timestamp of full replenishment |
| `anthropic-ratelimit-tokens-limit` | Max tokens per period (most restrictive limit in effect) |
| `anthropic-ratelimit-tokens-remaining` | Tokens left, rounded to nearest thousand |
| `anthropic-ratelimit-tokens-reset` | RFC 3339 |
| `anthropic-ratelimit-input-tokens-limit` / `-remaining` / `-reset` | Input-token bucket |
| `anthropic-ratelimit-output-tokens-limit` / `-remaining` / `-reset` | Output-token bucket |
| `anthropic-priority-input-tokens-*` / `anthropic-priority-output-tokens-*` | Priority Tier only |
| `anthropic-fast-*` | Fast mode only |

Reset values are **RFC 3339 timestamps**, not seconds-until-reset.

### Cache-aware ITPM (important for cost/throughput design)

- `input_tokens` ✓ counts toward ITPM
- `cache_creation_input_tokens` ✓ counts toward ITPM
- `cache_read_input_tokens` ✗ does **not** count toward ITPM on all current models
  (Haiku 3.5, retired first-party, is the only exception)

So prompt caching multiplies effective throughput, not just cuts cost.
`max_tokens` does **not** factor into OTPM — "there is no rate limit downside to setting a higher
`max_tokens` value."

### Tier table (standard limits)

| Tier | Model | RPM | ITPM | OTPM |
| --- | --- | --- | --- | --- |
| Start | Opus 5 / Sonnet 5 / Haiku 4.5 | 1,000 | 2,000,000 | 400,000 |
| Start | Fable 5 | 1,000 | 500,000 | 100,000 |
| Build | Opus 5 / Sonnet 5 / Haiku 4.5 | 5,000 | 5,000,000 | 1,000,000 |
| Build | Fable 5 | 2,000 | 1,500,000 | 300,000 |
| Scale | Opus 5 / Sonnet 5 / Haiku 4.5 | 10,000 | 10,000,000 | 2,000,000 |
| Scale | Fable 5 | 4,000 | 4,000,000 | 800,000 |

Monthly spend caps: Start $500, Build $1,000, Scale $200,000, Custom uncapped.
New orgs may start in an "Evaluation tier" with limits **below** these while history is established.
429s can also come from **acceleration limits** on sharp traffic increases — ramp gradually.

---

## Pricing

Pricing page: **https://platform.claude.com/docs/en/about-claude/pricing**
(marketing mirror: https://claude.com/pricing). Figures below fetched 2026-08-18, USD per million tokens.

| Model | Base input | 5m cache write | 1h cache write | Cache hits & refreshes | Output |
| --- | --- | --- | --- | --- | --- |
| Claude Fable 5 | $10 | $12.50 | $20 | $1 | $50 |
| Claude Mythos 5 (limited availability) | $10 | $12.50 | $20 | $1 | $50 |
| Claude Opus 5 | $5 | $6.25 | $10 | $0.50 | $25 |
| Claude Opus 4.8 / 4.7 / 4.6 / 4.5 | $5 | $6.25 | $10 | $0.50 | $25 |
| **Claude Sonnet 5** | **$2** | $2.50 | $4 | $0.20 | **$10** |
| Claude Sonnet 4.6 / 4.5 | $3 | $3.75 | $6 | $0.30 | $15 |
| **Claude Haiku 4.5** | **$1** | $1.25 | $2 | $0.10 | **$5** |
| Claude Haiku 3.5 (retired first-party) | $0.80 | $1 | $1.60 | $0.08 | $4 |

Cache multipliers relative to base input: 5m write **1.25x**, 1h write **2x**, cache read **0.1x**.

Batch API: **50% off both input and output.** Sonnet 5 batch $1 / $5; Haiku 4.5 batch $0.50 / $2.50;
Opus 5 batch $2.50 / $12.50.

Other modifiers:
- `inference_geo: "us"` → **1.1x** on every token category (Claude 4.6+ only).
- Fast mode (research preview, Opus 5 / Opus 4.8 only): $10 in / $50 out.
- Web search server tool: **$10 per 1,000 searches** plus tokens. Web fetch: no extra charge.
- Code execution: 1,550 free container-hours/month per org, then $0.05/hour/container; free when
  paired with web search or web fetch.
- Long context: Claude 4.6+ include the full 1M window at standard rates (no long-context surcharge).
- Tool use adds a hidden system prompt: Sonnet 5 = 354 tokens (`auto`/`none`) or 474 (`any`/`tool`);
  Haiku 4.5 = 496 / 588; Opus 5 = 286 / 406.

Sonnet 5 note (verbatim): the $2/$10 introductory price "is now the standard price"; the previously
scheduled increase to $3/$15 on 2026-09-01 "will not occur."

---

## Health/test-connection strategy

Anthropic does **not** document a dedicated health or ping endpoint. Recommended approach:

1. **Primary connection test: `GET /v1/models?limit=1`.**
   - Costs **zero tokens** and zero dollars (no model inference).
   - Exercises exactly the failure modes we care about: DNS/TLS reachability to
     `api.anthropic.com`, a valid `x-api-key`, a valid `anthropic-version` header, org/workspace
     permission, and billing state.
   - Distinguishable failures: 401 `authentication_error` = bad key; 403 `permission_error` =
     key lacks workspace access; 402 `billing_error` = payment problem; 429 = rate limited but
     credentials fine; 5xx/529 = Anthropic-side.
   - Bonus: use the response to validate that a configured model ID actually exists and to read
     `max_tokens` / `capabilities.structured_outputs` before sending a real request.

2. **Optional deeper probe (validates the model, not just the key):**
   `POST /v1/messages/count_tokens` with the configured model and a one-word message. Also free,
   but it *does* validate that the model ID is accepted for message-shaped requests.

3. **Smoke test (only when a real inference path must be proven):**
   `POST /v1/messages` with `max_tokens: 1` and a one-token user message. Expect
   `stop_reason: "max_tokens"`. Cost is negligible but non-zero. Do not run this on every health tick.

4. **Always record the `request-id` response header** on failures — it is what Anthropic support asks for.

5. Treat 429 and 529 as **degraded, not down**, and honour `retry-after`.

---

## OpenAI-compatibility verdict

**Yes, Anthropic officially ships an OpenAI-compatible endpoint — but the docs explicitly tell you not
to build production on it.**

Source: https://platform.claude.com/docs/en/api/openai-sdk
(canonical https://platform.claude.com/docs/en/cli-sdks-libraries/libraries/openai-sdk)

Setup: point the OpenAI SDK at `base_url = "https://api.anthropic.com/v1/"`, use an Anthropic API key
as `api_key`, and use a Claude model name. It serves `POST /v1/chat/completions`.

Verbatim caveat from the docs:

> "This compatibility layer is primarily intended to test and compare model capabilities, and is not
> considered a long-term or production-ready solution for most use cases. While it is intended to
> remain fully functional and not have breaking changes, the priority is the reliability and
> effectiveness of the Claude API."

Documented limitations:

- `strict` for function calling is **ignored** — tool JSON is not guaranteed to match the schema.
- Audio input ignored and stripped.
- **Prompt caching not supported** through the compat layer.
- System/developer messages are **hoisted and concatenated** (joined with `\n`) into a single leading
  system message, because Anthropic supports only one initial system message.
- `response_format` is **ignored** — no JSON mode. Structured outputs require the native API.
- Ignored fields: `logprobs`, `metadata`, `prediction`, `presence_penalty`, `frequency_penalty`,
  `seed`, `service_tier`, `audio`, `logit_bias`, `store`, `user`, `modalities`, `top_logprobs`,
  `reasoning_effort`. "Most unsupported fields are silently ignored rather than producing errors."
- `n` must be exactly 1; `choices[]` always length 1.
- `temperature` clamped to 0–1 (values > 1 capped at 1).
- Always-empty response fields: `usage.completion_tokens_details`, `usage.prompt_tokens_details`,
  `choices[].message.refusal`, `choices[].message.audio`, `logprobs`, `service_tier`,
  `system_fingerprint`.
- Error format is OpenAI-shaped but "the detailed error messages will not be equivalent. Only use the
  error messages for logging and debugging."
- Headers are remapped to OpenAI names: `x-ratelimit-limit-requests`, `x-ratelimit-limit-tokens`,
  `x-ratelimit-remaining-*`, `x-ratelimit-reset-*`, plus `retry-after` and `request-id`.
  `openai-version` always `2020-10-01`; `openai-processing-ms` always empty.
- Rate limits are the same as `/v1/messages`.
- Image input works via `image_url`; `detail` is ignored; `input_audio` and `file` parts ignored.

**Verdict for this project:** do not route production traffic through the compat shim. Silent field
dropping (no JSON mode, no caching, no strict tools, ignored `reasoning_effort`) means we would lose
cost control and output reliability while getting no error telling us why. Write a native
`/v1/messages` adapter behind the provider-neutral interface. The compat endpoint is only worth
keeping in mind as a quick A/B harness for comparing Claude against an OpenAI model with identical
client code.

---

## Differences from OpenAI that an abstraction must accommodate

Ordered roughly by how much they force structure into the provider-neutral layer.

### 1. Auth shape (affects the transport layer)
- Anthropic: `x-api-key: <key>` + **mandatory** `anthropic-version: 2023-06-01` + `content-type: application/json`.
- OpenAI: `Authorization: Bearer <key>`, no version header.
- **Consequence:** the provider interface needs a `build_headers()` hook, not a shared "set bearer
  token" helper. A missing `anthropic-version` is a hard failure, so the version string must be a
  first-class provider constant, not a request option.

### 2. `max_tokens` is required (affects the request contract)
- Anthropic **rejects** a Messages request without `max_tokens`. OpenAI treats `max_tokens` /
  `max_completion_tokens` as optional and defaults to "until the model stops."
- **Consequence:** the neutral request object must carry `max_tokens` as a **required, always-populated**
  field with a sane per-model default (e.g. 1024 for chat replies, 64–256 for classification),
  resolved at the adapter boundary. Do not model it as `Optional[int]` — the OpenAI adapter can drop
  it, the Anthropic adapter cannot invent it.
- Related: `max_tokens` is a **hard cap on thinking + visible output combined** on thinking models,
  and it has a per-model ceiling (128k on Opus/Sonnet/Fable 5, 64k on Haiku 4.5) readable from
  `GET /v1/models` → `data[].max_tokens`.

### 3. System prompt placement (the single biggest structural difference)
- Anthropic: top-level `system` field, sibling to `messages`, `string | TextBlock[]`.
  A `{"role": "system"}` entry **cannot be first in `messages`** and only exists at all on
  Fable 5 / Mythos 5 / Opus 4.8 / Opus 5 as a *mid-conversation* instruction.
- OpenAI: `messages[0] = {"role": "system"|"developer", ...}`.
- **Consequence:** the neutral conversation model should store the system prompt as a **separate
  field** (`system: str | None`) rather than as message index 0. Round-tripping through an
  OpenAI-shaped message list and hoisting at the last moment is how you end up with a 400 or a
  silently-mangled prompt. Note also that only **one** initial system prompt exists — if the neutral
  layer allows multiple system messages, the Anthropic adapter must concatenate them (the compat
  layer joins with `\n`; mirror that).
- The array form of `system` is what enables `cache_control` on the system prompt. If we ever want
  prompt caching for the big Persian instruction block, the neutral layer must be able to express
  "system prompt as blocks", not just "system prompt as string."

### 4. Response content is a block array, not a string
- Anthropic: `content: [{type: "text", text: ...}, {type: "tool_use", ...}, {type: "thinking", ...}]`.
- OpenAI: `choices[0].message.content` (string) + a separate `tool_calls` array.
- **Consequence:** the neutral response needs `text` (joined from all `type == "text"` blocks) and
  `tool_calls` as *derived* views over a block list. Assuming `content[0].text` breaks the moment a
  thinking block or a tool_use block is emitted first. Also: `choices` does not exist — Anthropic
  returns exactly one candidate, so an `n`/candidates concept cannot be modelled portably.

### 5. Finish-reason vocabulary
- Anthropic `stop_reason`: `end_turn`, `max_tokens`, `stop_sequence`, `tool_use`, `pause_turn`,
  `refusal`, `model_context_window_exceeded` (open enum, may grow).
- OpenAI `finish_reason`: `stop`, `length`, `tool_calls`, `content_filter`, `function_call`.
- **Consequence:** define a neutral enum with an explicit `UNKNOWN` fallback and a mapping table.
  `end_turn`→stop, `max_tokens`→length, `tool_use`→tool_calls, `refusal`→content_filter,
  `stop_sequence`→stop; `pause_turn` and `model_context_window_exceeded` have **no OpenAI analogue**
  and need first-class handling (pause_turn means "call again to continue"; the context-window one
  is effectively a recoverable input-size error).
- Anthropic also attaches `stop_details: {type, category, explanation}` on refusals — richer than
  OpenAI's bare `content_filter`. Worth surfacing.

### 6. Usage accounting — names AND semantics differ
- Anthropic: `input_tokens`, `output_tokens`, `cache_creation_input_tokens`,
  `cache_read_input_tokens`, `output_tokens_details.thinking_tokens`. **No `total_tokens`.**
- OpenAI: `prompt_tokens`, `completion_tokens`, `total_tokens`,
  `prompt_tokens_details.cached_tokens`.
- **The trap:** Anthropic's `input_tokens` only counts tokens **after the last cache breakpoint**.
  True input is `cache_read_input_tokens + cache_creation_input_tokens + input_tokens`. Mapping
  `input_tokens → prompt_tokens` naively under-reports cost and usage by orders of magnitude on
  cached workloads.
- **Consequence:** the neutral usage struct must have separate `cached_input_tokens`,
  `cache_write_tokens`, and `uncached_input_tokens`, with `total_input_tokens` computed —
  not a two-field prompt/completion pair. And cost must be computed from three different unit
  prices (base, cache write at 1.25x/2x, cache read at 0.1x), not one input price.

### 7. Streaming protocol is structurally different
- Anthropic: named SSE events (`message_start` / `content_block_start` / `content_block_delta` /
  `content_block_stop` / `message_delta` / `message_stop` / `ping` / `error`), indexed content
  blocks, **no `[DONE]` sentinel**, `stop_reason` in `message_delta`, **cumulative** usage in
  `message_delta`.
- OpenAI: uniform `chat.completion.chunk` objects with `choices[0].delta`, terminated by
  `data: [DONE]`, `finish_reason` on the last chunk, usage only if `stream_options` requests it.
- **Consequence:** the neutral streaming layer must be an **event-based state machine** (start /
  text-delta / tool-call-delta / block-end / finish) that each adapter feeds, not a "yield
  `delta.content`" loop. Termination cannot be detected by a sentinel — the Anthropic adapter ends on
  `message_stop`. Tool arguments stream as `input_json_delta.partial_json` fragments that must be
  accumulated per block `index` and parsed at `content_block_stop`, analogous to but not
  interchangeable with OpenAI's `tool_calls[].function.arguments` fragments.
- Anthropic can emit an `error` event **after a 200 OK**, so streaming error handling cannot rely on
  HTTP status alone.

### 8. Tool calling — three separate incompatibilities
- Schema key: Anthropic `tools[].input_schema` at the tool's top level; OpenAI
  `tools[].function.parameters` under a `function` wrapper with `type: "function"`.
- Call payload: Anthropic returns `tool_use` block with `input` as a **parsed object**; OpenAI returns
  `tool_calls[].function.arguments` as a **JSON string** that must be `json.loads`'d.
- Result submission: Anthropic sends results as a **`user` message containing `tool_result` blocks**
  keyed by `tool_use_id`; OpenAI sends a **`role: "tool"` message** keyed by `tool_call_id`.
  Anthropic has no `tool` role at all.
- `tool_choice`: Anthropic `{"type": "auto"|"any"|"tool"|"none"}` (+ `disable_parallel_tool_use`);
  OpenAI `"auto"|"none"|"required"|{"type":"function","function":{"name":...}}` (+ `parallel_tool_calls`).
- **Consequence:** the neutral layer needs its own tool-definition, tool-call, and tool-result types
  with adapter-side translation in both directions. This is the second-largest structural cost after
  the system prompt.

### 9. Structured output
- Anthropic: `output_config.format = {"type": "json_schema", "schema": {...}}`; result is JSON text
  in a normal text block. Strict tool schemas via `strict: true` on the tool.
- OpenAI: `response_format = {"type": "json_schema", "json_schema": {"name": ..., "strict": true, "schema": {...}}}`
  or `{"type": "json_object"}`.
- Differences: Anthropic has **no schema `name`**, no separate `strict` flag on the format (it is
  always strict), and no `json_object` freeform-JSON mode. The nesting depth differs by one level.
- **Consequence:** neutral API should be `json_schema: dict | None` (+ optional `schema_name` that
  the Anthropic adapter drops). Support varies by model — gate it on
  `GET /v1/models` → `capabilities.structured_outputs.supported`.

### 10. Sampling parameters are being removed, not just renamed
- **`temperature`, `top_p`, and `top_k` are unsupported on Claude 4.7 and later** (Opus 5, Sonnet 5,
  Fable 5, Opus 4.8, Opus 4.7). Setting them to a non-default value returns **400**.
- **Consequence:** this is the most dangerous silent assumption to port from an OpenAI integration.
  A shared `temperature=0.3` default applied to every provider will hard-fail against every current
  Claude model. The neutral layer must either (a) treat sampling params as an
  optional, per-model-gated capability, or (b) have the Anthropic adapter drop them for 4.7+ models.
  Determinism/"low creativity" must instead be expressed as `output_config.effort` or via prompting.
- Anthropic also has **no** `presence_penalty`, `frequency_penalty`, `logit_bias`, `seed`,
  `logprobs`, or `n`. Any of these in the neutral request must be droppable without error.
- Conversely, Anthropic's `output_config.effort` (`low`/`medium`/`high`/`xhigh`/`max`) has no clean
  OpenAI counterpart (`reasoning_effort` is close but not equivalent, and is *ignored* by the compat
  layer). For classification workloads, `effort: "low"` is the cost lever — but **Haiku 4.5 does not
  support effort at all**, so the adapter must gate it per model.

### 11. Prompt caching is explicit, not automatic
- Anthropic requires `cache_control` markers (top-level or per block) with `ttl` `5m`/`1h`, and bills
  cache writes at 1.25x/2x and reads at 0.1x. OpenAI caches automatically with no request-side control.
- Cached reads also **do not count toward ITPM rate limits** on Anthropic — a throughput lever with
  no OpenAI equivalent.
- **Consequence:** if we care about cost on a long fixed Persian system prompt, the neutral layer
  needs a "cacheable prefix" concept that the OpenAI adapter ignores and the Anthropic adapter turns
  into `cache_control`. Skipping this leaves ~90% of the input cost on the table.

### 12. Error envelope and status codes
- Anthropic: `{"type": "error", "error": {"type", "message"}, "request_id"}`.
- OpenAI: `{"error": {"message", "type", "param", "code"}}` — no `param`/`code` on Anthropic, and
  Anthropic adds a top-level `type` and `request_id`.
- Statuses unique-ish to Anthropic: **529 `overloaded_error`** (OpenAI has no 529), **413
  `request_too_large`** (32 MB), **402 `billing_error`**, **409 `conflict_error`**,
  **504 `timeout_error`**.
- **Consequence:** the retry classifier must include **529** or we will treat routine Anthropic
  overload as a permanent failure. The neutral error type should carry `provider_error_type` +
  `request_id` (the header `request-id` / body `request_id`) for support tickets.

### 13. Rate-limit header names and reset format
- Anthropic: `anthropic-ratelimit-{requests,tokens,input-tokens,output-tokens}-{limit,remaining,reset}`,
  where **reset is an RFC 3339 timestamp**. `retry-after` in seconds.
- OpenAI: `x-ratelimit-limit-requests` etc. with reset expressed as a **duration string** (`"6m0s"`).
- **Consequence:** a shared rate-limit parser must normalise both to an absolute deadline. Anthropic
  also splits input and output token budgets into separate buckets — a single "tokens remaining"
  gauge loses information.

### 14. Model identity and listing
- Both expose `GET /v1/models`, but the response shapes differ: Anthropic paginates with
  `after_id`/`before_id` + `has_more`/`first_id`/`last_id`, and each entry carries `display_name`,
  `created_at`, `max_input_tokens`, `max_tokens`, and a rich `capabilities` object. OpenAI returns a
  flat `{object: "list", data: [{id, object, created, owned_by}]}`.
- Model ID conventions differ and are **internally inconsistent within Anthropic**: dateless pinned
  snapshots for 4.6+ (`claude-opus-5`) versus dated snapshot + alias for older
  (`claude-haiku-4-5-20251001` / `claude-haiku-4-5`).
- **Consequence:** never regex-parse a model ID for a version or date. Treat model IDs as opaque
  strings, keep a config-driven registry, and use `capabilities` from `GET /v1/models` when a runtime
  capability check is needed. Anthropic's `capabilities` block is strictly richer than anything
  OpenAI exposes — worth surfacing in the neutral model-info type even though other providers will
  return nulls.

### 15. Assistant prefill / partial-completion is gone
- The OpenAI-world trick of seeding the assistant turn (`{"role": "assistant", "content": "The answer is ("}`)
  to force a format returns **400 on Claude 4.6+**. Conversation must end with a user message.
- **Consequence:** any "force the model to start its answer with X" logic must be reimplemented as
  structured outputs or system-prompt instruction. Do not expose prefill in the neutral API.

### 16. Thinking blocks must round-trip byte-identical
- If the last assistant message contains `thinking` / `redacted_thinking` blocks, they must be sent
  back **exactly as received**, including blocks with an empty `thinking` field. Filtering content
  blocks by type before resending is an explicit documented cause of 400s.
- **Consequence:** the neutral conversation store cannot normalise or lossily re-serialise assistant
  turns. It must retain the raw provider block list alongside any derived plain-text view.

### 17. Long-request semantics
- Anthropic docs: use streaming or the Batch API for anything over ~10 minutes; SDKs actively
  validate that a non-streaming request is not expected to exceed a 10-minute timeout, and set TCP
  keep-alive. 504 `timeout_error` is a documented status.
- **Consequence:** the neutral client should force streaming above a `max_tokens` threshold on the
  Anthropic path, and set a client timeout below 600s with keep-alive enabled.

---

## Unknowns

- **Exact HTTP request timeout enforced server-side.** The docs describe a 10-minute expectation
  validated *client-side by the SDKs* and a 504 `timeout_error` status, but do not state a hard
  server-side deadline in seconds. → **UNKNOWN**
- **Whether `GET /v1/models` returns real `max_input_tokens` / `max_tokens` values.** The documented
  example response shows `"max_input_tokens": 0, "max_tokens": 0` (clearly placeholders in the docs
  sample). Actual live values are unverified without an API key. → **UNKNOWN**
- **Whether `GET /v1/models` lists a model the caller's org cannot access** (i.e. whether it is
  key-scoped or a global catalogue). Docs say "available models" but do not define scope. → **UNKNOWN**
- **Number of `anthropic-ratelimit-*` headers actually returned per response.** The table lists all
  possible headers; which subset appears on a given response is not specified. → **UNKNOWN**
- **Retry-after presence on 529.** Docs guarantee `retry-after` on 429 and say SDKs honour it "when
  present"; whether 529 always includes it is not stated. → **UNKNOWN**
- **Anthropic's own recommended backoff schedule** beyond "exponential backoff, twice by default" in
  the SDKs. No documented base delay or jitter policy. → **UNKNOWN**
- **Whether the OpenAI-compatible `/v1/chat/completions` path accepts `x-api-key`** in addition to
  `Authorization: Bearer`. The compat header table marks `authorization` "Fully supported"; the
  `x-api-key` case is not documented for that path. → **UNKNOWN**
- **Per-request price of `pause_turn` continuations** (whether the resumed turn re-bills the full
  input). Not documented. → **UNKNOWN**
- **Whether a Claude Haiku 5 is planned.** As of 2026-08-18 the docs list Haiku 4.5 as the only
  current Haiku; no successor is announced. → **UNKNOWN (none exists today)**
