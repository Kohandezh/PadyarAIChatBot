# Moonshot AI / Kimi

Research date: 2026-08-18. All facts below carry the official doc URL they came from.
Anything not found in official docs is marked **UNKNOWN**.

> **Docs moved.** `platform.moonshot.ai/docs/*` now **301-redirects** to `platform.kimi.ai/docs/*`,
> and `platform.moonshot.cn/*` **301-redirects** to `platform.kimi.com/*`.
> The **API hostnames did not change** (`api.moonshot.ai` / `api.moonshot.cn`) — only the docs/console
> domains did. Verified by following the redirects on 2026-08-18.

---

## Platform identity

There are **two separate platforms** with separate consoles, separate accounts, separate billing,
separate currencies, and **non-portable API keys**.

| | International / Global | China mainland |
|---|---|---|
| Console + docs | `https://platform.kimi.ai` (was `platform.moonshot.ai`) | `https://platform.kimi.com` (was `platform.moonshot.cn`) |
| API base URL | `https://api.moonshot.ai/v1` | `https://api.moonshot.cn/v1` |
| Console API keys | `https://platform.kimi.ai/console/api-keys` | `https://platform.kimi.com/console/api-keys` |
| Pricing currency | **USD ($)** | **CNY (¥)** |
| Docs language | English | Chinese |

Official statement on key separation, from the errors page
(<https://platform.kimi.ai/docs/api/errors>):

> "Keys issued on `platform.kimi.ai` are independent from keys issued on other regional Kimi
> platforms. Mixing keys across platforms returns 401."

Model **IDs and context windows are identical** across both platforms (verified: both
`platform.kimi.ai/docs/models` and `platform.kimi.com/docs/models` list the same 11 model IDs).
Only price and currency differ.

**For this project (Iran / non-China deployment): use the international platform** —
`https://api.moonshot.ai/v1`, key from `platform.kimi.ai`, prices in USD.

---

## Sources

All URLs verified reachable on 2026-08-18. Every page also has a `.md` twin (append `.md`) which is
the AI-readable form — that is what was fetched.

| Topic | URL |
|---|---|
| Docs index (machine-readable) | <https://platform.kimi.ai/docs/llms.txt> |
| Quickstart | <https://platform.kimi.ai/docs/overview> |
| Model list | <https://platform.kimi.ai/docs/models> |
| API overview (base URL, endpoints, auth) | <https://platform.kimi.ai/docs/api/overview> |
| Chat Completion reference | <https://platform.kimi.ai/docs/api/chat> |
| List Models reference | <https://platform.kimi.ai/docs/api/list-models> |
| Model parameter reference | <https://platform.kimi.ai/docs/api/models-overview> |
| Estimate tokens | <https://platform.kimi.ai/docs/api/estimate> |
| Check balance | <https://platform.kimi.ai/docs/api/balance> |
| Common error codes | <https://platform.kimi.ai/docs/api/errors> |
| OpenAPI spec | <https://platform.kimi.ai/docs/openapi.json> |
| Streaming | <https://platform.kimi.ai/docs/guide/utilize-the-streaming-output-feature-of-kimi-api> |
| Tool calls | <https://platform.kimi.ai/docs/guide/use-kimi-api-to-complete-tool-calls> |
| response_format / structured output | <https://platform.kimi.ai/docs/guide/response_format> |
| JSON mode | <https://platform.kimi.ai/docs/guide/use-json-mode-feature-of-kimi-api> |
| Thinking models | <https://platform.kimi.ai/docs/guide/use-thinking-models> |
| Reasoning effort | <https://platform.kimi.ai/docs/guide/use-reasoning-effort> |
| Context caching | <https://platform.kimi.ai/docs/guide/use-context-caching-feature-of-kimi-api> |
| Migrating from OpenAI | <https://platform.kimi.ai/docs/guide/migrating-from-openai-to-kimi> |
| Troubleshooting (retries/timeouts) | <https://platform.kimi.ai/docs/guide/troubleshooting> |
| Rate limits + recharge tiers | <https://platform.kimi.ai/docs/pricing/limits> |
| Pricing index | <https://platform.kimi.ai/docs/pricing/chat> |
| K3 pricing | <https://platform.kimi.ai/docs/pricing/chat-k3> |
| K2.7 Code pricing | <https://platform.kimi.ai/docs/pricing/chat-k27-code> |
| K2.6 pricing | <https://platform.kimi.ai/docs/pricing/chat-k26> |
| K2.5 pricing | <https://platform.kimi.ai/docs/pricing/chat-k25> |
| Moonshot V1 pricing | <https://platform.kimi.ai/docs/pricing/chat-v1> |
| CN platform quickstart | <https://platform.kimi.com/docs/overview> |
| CN platform K3 pricing (CNY) | <https://platform.kimi.com/docs/pricing/chat-k3> |
| CN platform V1 pricing (CNY) | <https://platform.kimi.com/docs/pricing/chat-v1> |

---

## Auth

Source: <https://platform.kimi.ai/docs/api/overview>, <https://platform.kimi.ai/docs/overview>

```
Authorization: Bearer $MOONSHOT_API_KEY
Content-Type: application/json
```

Plain OpenAI-style bearer token. No extra required headers, no version header, no org header
documented. Docs warn: *"Your API Key is sensitive. Do not expose it in client-side code, public
repositories, or logs."*

Keys are **not portable** between `api.moonshot.ai` and `api.moonshot.cn` — cross-use returns
**401** (<https://platform.kimi.ai/docs/api/errors>).

---

## Endpoints

Source: <https://platform.kimi.ai/docs/api/overview> and <https://platform.kimi.ai/docs/openapi.json>
(`servers[]` = `https://api.moonshot.ai`).

| Method | Path | Purpose |
|---|---|---|
| POST | `/v1/chat/completions` | Chat / generation |
| GET | `/v1/models` | List models |
| POST | `/v1/tokenizers/estimate-token-count` | Token counting |
| GET | `/v1/users/me/balance` | Account balance |
| GET, POST | `/v1/files` | List / upload files |
| GET, DELETE | `/v1/files/{file_id}` | Retrieve / delete file |
| GET | `/v1/files/{file_id}/content` | File content |
| GET, POST | `/v1/batches` | List / create batch |
| GET | `/v1/batches/{batch_id}` | Retrieve batch |
| POST | `/v1/batches/{batch_id}/cancel` | Cancel batch |

Full URL for chat: `https://api.moonshot.ai/v1/chat/completions`
(mainland: `https://api.moonshot.cn/v1/chat/completions`).

There is **no `/v1/embeddings`, no `/v1/responses`, no `/v1/audio/*`, no `/v1/images/*`** in the
OpenAPI spec. Kimi is chat + files + batch only.

---

## Request shape

Source: <https://platform.kimi.ai/docs/api/chat>, <https://platform.kimi.ai/docs/api/models-overview>

| Parameter | Type | Notes |
|---|---|---|
| `model` | string | **Required.** |
| `messages` | array | **Required.** Roles: `system`, `user`, `assistant`, `tool`. Content is string or array of `{type: "text"\|"image_url"\|"video_url"}` parts. |
| `stream` | boolean | Optional. SSE output. |
| `stream_options` | object | Optional. `{"include_usage": true}` for token stats in the final chunk. |
| `tools` | array | Optional. Function tool definitions. |
| `tool_choice` | string \| object | `"auto"`, `"none"`, `"required"`, `null`, or a specific function. **`"required"` is K3-only.** |
| `response_format` | object | `{"type":"text"\|"json_object"\|"json_schema"}` |
| `max_completion_tokens` | integer | Optional. Max tokens to generate. |
| `thinking` | object | K2.x only. `{"type": "enabled"\|"disabled", "keep": "all"\|null}` |
| `reasoning_effort` | string | **K3 only.** `"low"` \| `"high"` \| `"max"` (default `"max"`). |
| `temperature` | number | Range **`[0, 1]`** — narrower than OpenAI's `[0, 2]`. **Fixed / non-modifiable on all K-series models** (see Models table). Modifiable only on `moonshot-v1-*` (default `0.0`). |
| `top_p` | number | Fixed `0.95` on all K-series. Modifiable on `moonshot-v1-*` (default `1.0`). |
| `n` | integer | Fixed `1` on K-series. `moonshot-v1-*`: default 1, max 5. *"When `temperature` approaches 0, `n` can only be 1; otherwise the API returns an error."* |
| `presence_penalty` / `frequency_penalty` | number | Fixed `0` on K-series. Modifiable on `moonshot-v1-*`. |
| `partial` | boolean | Provider-specific "Partial Mode" — set on an assistant message to prefill/continue. See <https://platform.kimi.ai/docs/guide/use-partial-mode-feature-of-kimi-api>. |

`max_tokens` vs `max_completion_tokens`: the reference documents **`max_completion_tokens`**.
Whether legacy `max_tokens` is still accepted as an alias is **UNKNOWN** (not stated either way).
The errors page does reference a `max_tokens`-worded error message
(*"prompt tokens + max_tokens exceeds the model specification"*), which implies it is at least parsed,
but that is inference, not documentation.

Minimal request (from <https://platform.kimi.ai/docs/overview>):

```python
client = OpenAI(
    api_key=os.environ["MOONSHOT_API_KEY"],
    base_url="https://api.moonshot.ai/v1",
)
completion = client.chat.completions.create(
    model="kimi-k3",
    messages=[{"role": "user", "content": "Hi, my name is Li Lei. What is 1+1?"}]
)
```

Documented example body (<https://platform.kimi.ai/docs/api/chat>):

```json
{
  "model": "kimi-k2.6",
  "messages": [
    { "role": "user", "content": [
        { "type": "text", "text": "Describe this image" },
        { "type": "image_url", "image_url": { "url": "data:image/jpeg;base64,/9j/4AAQ..." } }
    ]}
  ],
  "stream": false
}
```

---

## Response shape

Source: <https://platform.kimi.ai/docs/api/chat>

```json
{
  "id": "cmpl-04ea926191a14749b7f2c7a48a68abc6",
  "object": "chat.completion",
  "created": 1698999496,
  "model": "kimi-k2.6",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "Hello, Li Lei! 1+1 equals 2. ...",
        "reasoning_content": null
      },
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 19,
    "completion_tokens": 21,
    "total_tokens": 40,
    "cached_tokens": 10
  }
}
```

Deltas from OpenAI:

- `message.reasoning_content` — provider-specific. Chain-of-thought text on thinking models.
  In streaming, *"the reasoning_content field will always appear before the content field."*
  (<https://platform.kimi.ai/docs/guide/use-thinking-models>) The OpenAI SDK does not expose it as a
  typed attribute; docs say use `hasattr()` / `getattr()`.
- `usage.cached_tokens` is **flat**, not nested under `prompt_tokens_details` the way OpenAI does it.
- `finish_reason` values seen in docs: `"stop"`, `"tool_calls"`. A `"length"` value is expected but
  **not explicitly documented** → treat as UNKNOWN-but-likely.

### Streaming shape

Source: <https://platform.kimi.ai/docs/guide/utilize-the-streaming-output-feature-of-kimi-api>

- `Content-Type: text/event-stream`, lines prefixed `data: `.
- Chunk `object` is `"chat.completion.chunk"`, text arrives at `choices[0].delta.content`.
- Terminates with a literal `data: [DONE]`. Docs are explicit:
  *"always use `data: [DONE]` to determine whether the data has been fully transmitted."*
- **Usage placement gotcha:** docs state usage in streaming mode appears at
  **`choices[0].usage`** in the final chunk — *"the `usage` field appears only in the last chunk, not
  at the top level."* This is **different from OpenAI**, which puts `usage` at the top level of a
  final extra chunk. The migration page repeats this: *"Usage information in streaming mode appears
  in end data blocks for each choice, with `completion_tokens` and `total_tokens` varying per choice."*
  A client must read both locations defensively.

---

## Model listing

Source: <https://platform.kimi.ai/docs/api/list-models>, confirmed against
<https://platform.kimi.ai/docs/openapi.json>

```
GET https://api.moonshot.ai/v1/models
Authorization: Bearer $MOONSHOT_API_KEY
```

Response:

```json
{
  "object": "list",
  "data": [
    {
      "id": "string",
      "object": "model",
      "created": 0,
      "owned_by": "string",
      "context_length": 0,
      "supports_image_in": true,
      "supports_video_in": true,
      "supports_reasoning": true
    }
  ]
}
```

This is **richer than the OpenAI `/v1/models` shape** — Kimi adds `context_length`,
`supports_image_in`, `supports_video_in`, `supports_reasoning`. Doc summary:
*"List all currently available models, including model ID, context length, and capability flags."*

Practical consequence: **context window and capabilities are discoverable at runtime.** No hardcoded
model table is needed for those fields. Pricing is **not** in this response.

Documented failure codes on this endpoint: `401` (invalid/missing key), `404` (model does not exist
or account lacks access).

---

## Models

Source: <https://platform.kimi.ai/docs/models>, <https://platform.kimi.ai/docs/api/models-overview>,
pricing pages linked above.

### Currently listed as active

| Model ID | Context | Modality | temperature | Reasoning control | Notes |
|---|---|---|---|---|---|
| `kimi-k3` | 1,048,576 (1M) | text + vision | fixed `1.0` | `reasoning_effort`: `low`/`high`/`max` (default `max`) — always reasoning | Flagship. Only model supporting `tool_choice: "required"`. |
| `kimi-k2.7-code` | 262,144 (256K) | text + vision | fixed `1.0` | `thinking` always on, only `{"type":"enabled","keep":"all"}` accepted | Coding-specialised. Most stable structured output. |
| `kimi-k2.7-code-highspeed` | 262,144 | text + vision | fixed `1.0` | same as above | *"~180 Tokens/s"* output. Costs 2× the base K2.7-code. |
| `kimi-k2.6` | 262,144 | text + vision | fixed `1.0` thinking / `0.6` non-thinking | `thinking`: `enabled` (default) or `disabled` | General chat workhorse. |
| `kimi-k2.5` | 262,144 | text + vision | `1.0` / `0.6` as above | `thinking` enabled by default, can disable | **Sunsetting — see below.** |
| `moonshot-v1-8k` | 8,192 | text only | `0.0`, modifiable | none | **Sunsetting.** Legacy. |
| `moonshot-v1-32k` | 32,768 | text only | `0.0`, modifiable | none | **Sunsetting.** Legacy. |
| `moonshot-v1-128k` | 131,072 | text only | `0.0`, modifiable | none | **Sunsetting.** Legacy. |
| `moonshot-v1-8k-vision-preview` | 8,192 | text + vision | `0.0`, modifiable | none | **Sunsetting.** Legacy. |
| `moonshot-v1-32k-vision-preview` | 32,768 | text + vision | `0.0`, modifiable | none | **Sunsetting.** Legacy. |
| `moonshot-v1-128k-vision-preview` | 131,072 | text + vision | `0.0`, modifiable | none | **Sunsetting.** Legacy. |

### How context sizing works now — **CHANGED**

The old Kimi pattern of *"pick your context window by picking a model ID"* (`moonshot-v1-8k` /
`-32k` / `-128k`) is **legacy only**. Docs state for that family:
*"Moonshot V1 models differ only in maximum context length (input and output included), with no
difference in capability."*

The current **K-series does not differentiate by context length** — every K2.x model is a flat 256K
and K3 is a flat 1M. One model ID, one context size. **Do not build a context-tier model picker.**

### ⚠️ Deprecation status — time-sensitive

From <https://platform.kimi.ai/docs/models>, verbatim:

> "Following the Kimi K3 launch, `kimi-k2.5` and the `moonshot-v1` series are no longer available to
> newly registered users (full platform sunset on August 31)."

Already fully discontinued (documented dates):

| Model | Discontinued |
|---|---|
| `kimi-k2-0711-preview` | May 25, 2026 |
| `kimi-k2-0905-preview` | May 25, 2026 |
| `kimi-k2-turbo-preview` | May 25, 2026 |
| `kimi-k2-thinking` | May 25, 2026 |
| `kimi-k2-thinking-turbo` | May 25, 2026 |
| `kimi-latest` | January 28, 2026 |
| `kimi-thinking-preview` | November 11, 2025 |

**The docs write "August 31" with no year.** Given K3 has shipped and the K2 line was retired
May 2026, the sunset is almost certainly **2026-08-31 — 13 days from today**. But the **year is not
stated in the docs**, so formally: **UNKNOWN**. See Unknowns.

### Answering the two asks

**(a) General chat → `kimi-k2.6`.**
256K context, vision, thinking toggleable, $0.95 / $4.00 per 1M. Current-generation and not on any
sunset list. `kimi-k3` is the better/bigger option (1M context) but costs 3.2× input / 3.75× output
and always reasons, which adds latency and output tokens.

**(b) Cheap/fast classification → `kimi-k2.6` with `thinking: {"type": "disabled"}`.**
This is the correct answer *today*, and it is a deliberate call:

- `kimi-k2.5` is nominally cheaper ($0.60 / $3.00) **but is on the August 31 sunset list and is
  already unavailable to newly registered accounts** — do not build on it.
- `moonshot-v1-8k` has the lowest input price of all ($0.20 / $2.00) **but is on the same sunset
  list**, is text-only, and is legacy. Do not build on it.
- `kimi-k3` with `reasoning_effort: "low"` is a fallback but is the most expensive model and always
  reasons; docs frame `low` as a latency remedy, not a cost tier.

So: the only non-sunsetting cheap path is K2.6 with thinking off. Disabling thinking is documented as
reducing *"latency and token consumption"*
(<https://platform.kimi.ai/docs/guide/use-thinking-models>). Note there is **no separate cheaper
price tier** for thinking-disabled — the saving comes purely from emitting fewer output tokens.

**There is no Kimi equivalent of a `-mini` / `-flash` cheap tier.** The price floor for a supported
model is K2.6 at $0.95/$4.00.

### Max output tokens

- `kimi-k2.6` (and K2.x generally): *"Default maximum is 32k aka 32768"* tokens per response
  (<https://platform.kimi.ai/docs/guide/kimi-k2-6-quickstart>).
- `kimi-k3`: the K3 quickstart reports 1,048,576, which is identical to its context window and
  therefore likely a docs conflation of context vs. output cap. **Treat K3 max output as UNKNOWN**
  and set an explicit `max_completion_tokens`.

---

## Capabilities

Source: as cited per row.

| Capability | Parameter / field | Notes |
|---|---|---|
| Streaming | `stream: true`, `stream_options: {"include_usage": true}` | SSE, `data: [DONE]` sentinel. Usage lands in `choices[0].usage` in the last chunk, not top-level. |
| Tool calling | `tools`, `tool_choice` | OpenAI-shaped. `"required"` is **K3-only**; `kimi-k2.7-code` and `kimi-k2.6` support only `auto` / `none`. The legacy OpenAI `functions` parameter is **not supported** — use `tools`. |
| JSON mode | `response_format: {"type": "json_object"}` | *"Guarantees a valid JSON Object, but does not constrain specific fields."* |
| Structured output | `response_format: {"type": "json_schema", "json_schema": {"name", "strict", "schema"}}` | `strict: true` supported and recommended. K2.7-code most stable (`anyOf`, `oneOf`, `$ref`, `additionalProperties`); K3 reliable for nested objects/arrays/`anyOf`; **K2.6 documented as "occasional instability with complex schemas; prefers simple structures."** |
| Vision | `content: [{type: "image_url", image_url: {url: "data:image/jpeg;base64,..."}}]` | All K-series + `moonshot-v1-*-vision-preview`. |
| Thinking / reasoning | `thinking: {type, keep}` (K2.x) · `reasoning_effort` (K3) | Output at `message.reasoning_content`. |
| Partial / prefill | `partial: true` on an assistant message | Provider-specific, no OpenAI equivalent. |
| Web search | official tool, billed separately | <https://platform.kimi.ai/docs/pricing/tools> |
| Batch | `/v1/batches` | Discounted; see <https://platform.kimi.ai/docs/pricing/batch>. |
| Context caching | automatic | See Usage/tokens. |
| Embeddings | — | **Not offered.** No embeddings endpoint in the OpenAPI spec. |

### Tool-calling wire shapes

Tool definition:

```json
{ "type": "function",
  "function": { "name": "...", "description": "...",
                "parameters": { "type": "object", "required": [...], "properties": {...} } } }
```

Assistant response when `finish_reason == "tool_calls"`:

```json
{ "role": "assistant", "content": "", "tool_calls": [
    { "id": "...", "type": "function",
      "function": { "name": "...", "arguments": "<JSON string>" } } ] }
```

Tool result message back:

```json
{ "role": "tool", "tool_call_id": "...", "name": "...", "content": "<JSON string>" }
```

Byte-identical to OpenAI's function-calling protocol.

---

## Usage/tokens

Source: <https://platform.kimi.ai/docs/api/chat>, <https://platform.kimi.ai/docs/openapi.json>,
<https://platform.kimi.ai/docs/guide/use-context-caching-feature-of-kimi-api>

Non-streaming `usage` object — exactly four fields per the OpenAPI schema:

| Field | Meaning |
|---|---|
| `prompt_tokens` | Input tokens |
| `completion_tokens` | Output tokens |
| `total_tokens` | Sum |
| `cached_tokens` | Portion of `prompt_tokens` served from cache (billed at cache-hit rate) |

Streaming: same fields, but located at `choices[0].usage` in the final chunk (see Response shape).

### Context caching

- **Fully automatic.** *"No manual creation"* required — *"when calling `/v1/chat/completions`,
  simply pass messages in the normal way, and the system will automatically match caches in the
  background."*
- **No headers, no parameters, no cache IDs.** Nothing to opt into.
- Trigger threshold: a prior request must have had **more than 256 prompt tokens**.
- Requires a stable, repeated prefix — system prompts, documents, tool definitions.
- Only `cached_tokens` is exposed. There is **no** `cache_creation_input_tokens` /
  `cache_write_tokens` field (Anthropic-style) and **no separate cache-write charge** documented.
- Practical: put the INOTEX system prompt / knowledge blob **first and byte-stable** in `messages` to
  earn the ~6× input discount (K2.6: $0.16 cached vs $0.95 uncached).

### Token counting endpoint

```
POST /v1/tokenizers/estimate-token-count
{ "model": "...", "messages": [...] }
→ { "data": { "total_tokens": 123 } }
```

Docs: *"If there is no `error` field, you can take `data.total_tokens` as the calculation result."*
Useful for pre-flight context-budget checks without a local tokenizer.

---

## Errors

Source: <https://platform.kimi.ai/docs/api/errors>

Body shape (all errors):

```json
{ "error": { "type": "content_filter",
             "message": "The request was rejected because it was considered high risk" } }
```

Only `type` and `message` are shown on the errors page. The token-estimate endpoint's error schema
additionally shows a `code` field
(`{"error": {"message", "type", "code"}}` — <https://platform.kimi.ai/docs/api/estimate>).
OpenAI's `param` field is **not documented anywhere**. A parser must tolerate `code`/`param` being
absent.

| HTTP | `error.type` | Documented message / cause |
|---|---|---|
| 400 | `content_filter` | "The request was rejected because it was considered high risk" |
| 400 | `invalid_request_error` | Format error / missing required param / invalid param type |
| 400 | `invalid_request_error` | "Input token length too long" |
| 400 | `invalid_request_error` | "prompt tokens + max_tokens exceeds the model specification" |
| 400 | `invalid_request_error` | "Invalid purpose: only 'file-extract' accepted" |
| 400 | `invalid_request_error` | "File size is too large, max file size is 100MB" |
| 400 | `invalid_request_error` | "File size is zero" |
| 400 | `invalid_request_error` | "Too many uploaded files" |
| 401 | `invalid_authentication_error` | "Invalid Authentication" |
| 401 | `incorrect_api_key_error` | "Incorrect API key provided" (incl. wrong-platform key) |
| 403 | `permission_denied_error` | "The API you are accessing is not open" |
| 403 | `permission_denied_error` | "You are not allowed to get other user info" |
| 403 | `permission_denied_error` | "Your IP is not allowed to access this organization" |
| 404 | `resource_not_found_error` | "Model not found, or this account does not have permission to access the model" |
| 429 | `engine_overloaded_error` | "The engine is currently overloaded, please try again later" |
| 429 | `exceeded_current_quota_error` | "Account balance is insufficient or account disabled" |
| 429 | `exceeded_current_quota_error` | "Token quota is insufficient" |
| 429 | `rate_limit_reached_error` | Org-level **concurrency** limit reached |
| 429 | `rate_limit_reached_error` | Org-level **RPM** limit reached |
| 429 | `rate_limit_reached_error` | Org-level **TPM** limit reached |
| 429 | `rate_limit_reached_error` | Org-level **TPD** limit reached |
| 499 | `client_closed_request` | Client disconnected; check KeepAlive/timeout |
| 500 | `server_error` / `unexpected_output` | Retry; contact support with `request_id` |
| 503 | `server_unavailable` | Temporary; retry |
| 504 | (gateway timeout) | "No response for 900 seconds" — docs advise `stream: true` |

**Design note:** 429 is heavily overloaded. It covers rate limiting **and** billing exhaustion
(`exceeded_current_quota_error`). A retry loop must branch on `error.type`, not on the status code —
retrying a `exceeded_current_quota_error` is pointless. Docs note requests interrupted by 429 are
**not charged**.

The 400 `content_filter` type matters for a Persian-language customer bot: Moonshot applies
risk-based content filtering and rejects with 400, not 200. Needs a graceful user-facing fallback.

---

## Rate limits

Source: <https://platform.kimi.ai/docs/pricing/limits>

**Tier-based, driven by cumulative recharge**, and enforced on four axes simultaneously:
Concurrency, RPM, TPM, TPD. Limits are **organization-level**, not per-key.

| Tier | Min cumulative recharge | Concurrency | RPM | TPM | TPD |
|---|---|---|---|---|---|
| Tier 0 | $1 | 1 | 3 | 500K | 1.5M |
| Tier 1 | $10 | 50 | 200 | 2M | Unlimited |
| Tier 2 | $20 | 100 | 500 | 3M | Unlimited |
| Tier 3 | $100 | 200 | 5K | 3M | Unlimited |
| Tier 4 | $1,000 | 400 | 5K | 4M | Unlimited |
| Tier 5 | $3,000 | 1,000 | 10K | 5M | Unlimited |

Tier advancement is automatic on cumulative recharge. Vouchers do **not** count toward the cumulative
total. Docs warn that *"when the cluster load reaches its capacity limit, we may take temporary
measures to adjust the rate limits"*, and that a triggered *"risk-control rate-limiting policy"*
**cannot be lifted**. Higher capacity: `api-service@moonshot.ai`.

**⚠️ Tier 0 is unusable for production: concurrency 1, 3 RPM.** A live chatbot needs Tier 1 ($10
cumulative recharge) minimum. This is the single biggest operational gotcha — a fresh $1 account will
look "broken" under any real traffic.

### Retry / timeout guidance

Source: <https://platform.kimi.ai/docs/guide/troubleshooting>

- **429 `engine_overloaded_error`** → *"Wait as indicated by `Retry-After`, reduce concurrency, and
  retry with exponential backoff."* A `Retry-After` header is therefore emitted on at least this case.
- **429 `rate_limit_reached_error`** → reduce request frequency or upgrade tier. Not a backoff case.
- **429 `exceeded_current_quota_error`** → top up. **Do not retry.**
- **Timeouts:** *"We recommend enabling streaming output with `stream=True` to reduce
  connection-related errors as much as possible."* The gateway hard-cuts at **900 seconds** with a
  504. No recommended client timeout value is given → **UNKNOWN**; pick your own (60–120s for a chat
  turn is sane, with streaming on).
- **SDK default:** OpenAI SDK *"automatically retries 2 times by default, with a short exponential
  backoff"* for connection errors, 408, 429, and 5xx — meaning one logical call can become 3 real
  ones. Docs caution to account for this when reading usage logs.
- There is an official **auto-reconnect** guide for dropped streams:
  <https://platform.kimi.ai/docs/guide/auto-reconnect>.

---

## Pricing

Pricing index: <https://platform.kimi.ai/docs/pricing/chat>

### International platform — **USD**, per 1,000,000 tokens

| Model | Input (cache hit) | Input (cache miss) | Output | Source |
|---|---|---|---|---|
| `kimi-k3` | $0.30 | $3.00 | $15.00 | <https://platform.kimi.ai/docs/pricing/chat-k3> |
| `kimi-k2.7-code` | $0.19 | $0.95 | $4.00 | <https://platform.kimi.ai/docs/pricing/chat-k27-code> |
| `kimi-k2.7-code-highspeed` | $0.38 | $1.90 | $8.00 | same |
| `kimi-k2.6` | $0.16 | $0.95 | $4.00 | <https://platform.kimi.ai/docs/pricing/chat-k26> |
| `kimi-k2.5` *(sunsetting)* | $0.10 | $0.60 | $3.00 | <https://platform.kimi.ai/docs/pricing/chat-k25> |
| `moonshot-v1-8k` *(sunsetting)* | — | $0.20 | $2.00 | <https://platform.kimi.ai/docs/pricing/chat-v1> |
| `moonshot-v1-32k` *(sunsetting)* | — | $1.00 | $3.00 | same |
| `moonshot-v1-128k` *(sunsetting)* | — | $2.00 | $5.00 | same |
| `moonshot-v1-*-vision-preview` | — | same as text sibling | same as text sibling | same |

Docs note: *"1M = 1,000,000. The prices in the table represent the cost per 1M tokens consumed."* and
*"Prices exclude applicable taxes. Specific tax obligations are subject to local tax regulations and
will be calculated at checkout based on your jurisdiction."*

No cache-hit rate is published for the `moonshot-v1` family — those pages list input/output only.

**No separate cache-write / cache-storage charge is documented anywhere.** Caching appears to be a
pure discount with no write premium — notably better economics than Anthropic-style explicit caching.

### China mainland platform — **CNY (¥)**, per 1,000,000 tokens

| Model | Input (cache hit) | Input (cache miss) | Output | Source |
|---|---|---|---|---|
| `kimi-k3` | ¥2.00 | ¥20.00 | ¥100.00 | <https://platform.kimi.com/docs/pricing/chat-k3> |
| `moonshot-v1-8k` | — | ¥2.00 | ¥10.00 | <https://platform.kimi.com/docs/pricing/chat-v1> |
| `moonshot-v1-32k` | — | ¥5.00 | ¥20.00 | same |
| `moonshot-v1-128k` | — | ¥10.00 | ¥30.00 | same |

CNY prices for `kimi-k2.7-code`, `kimi-k2.6`, `kimi-k2.5` on the mainland platform: **UNKNOWN**
(not fetched; pages exist at `https://platform.kimi.com/docs/pricing/chat-k27-code`,
`.../chat-k26`, `.../chat-k25`).

Batch pricing: <https://platform.kimi.ai/docs/pricing/batch> — **UNKNOWN** (not fetched).
Web-search tool pricing: <https://platform.kimi.ai/docs/pricing/tools> — **UNKNOWN** (not fetched).

---

## Health/test-connection strategy

**Recommended primary probe: `GET /v1/models`.**

```
GET https://api.moonshot.ai/v1/models
Authorization: Bearer $MOONSHOT_API_KEY
```

Why it's the right probe:

1. **Free** — no token consumption, no inference charge.
2. **Validates the key** — returns documented `401` on invalid/missing key, and specifically catches
   the wrong-platform-key mistake (`.cn` key against `.ai` host → 401).
3. **Validates the region choice** — a key from the wrong platform fails here loudly instead of
   silently at first chat.
4. **Returns useful metadata for free** — `context_length`, `supports_image_in`,
   `supports_video_in`, `supports_reasoning` per model. The connection test can populate the model
   dropdown and the per-model context budget in one call. No hardcoded model table needed.
5. **Detects deprecation drift** — if `kimi-k2.5` / `moonshot-v1-*` disappear after the August 31
   sunset, a stored model ID will simply stop appearing in `data[]`. Validate the configured model ID
   against this list and surface a clear "model no longer available" message rather than letting the
   user discover it as a `404 resource_not_found_error` mid-conversation.

**Secondary probe (optional): `GET /v1/users/me/balance`.**

```json
{ "code": 0,
  "data": { "available_balance": 0.0, "voucher_balance": 0.0, "cash_balance": 0.0 },
  "scode": "...", "status": true }
```

Docs state that when `available_balance <= 0` the account **cannot call the inference API**. This
catches the "key is valid but you'll get a 429 `exceeded_current_quota_error` on first real request"
failure mode, which `/v1/models` cannot detect. Worth running as a warning-level check.

Note this endpoint's response shape is **non-OpenAI** (`{code, data, scode, status}`) — it needs a
bespoke parser, not the SDK.

**Do not** use a 1-token `chat/completions` call as the health check: it costs money, it is subject
to the Tier-0 concurrency limit of 1, and it gives strictly less information than `/v1/models`.

---

## OpenAI-compatibility verdict

### **OPENAI-COMPATIBLE + PROVIDER-SPECIFIC METADATA**

**Justification — compatibility is officially documented, and it is real.**
<https://platform.kimi.ai/docs/api/overview> states: *"Our API is compatible with the OpenAI Chat
Completions API in request/response format."* The migration guide
(<https://platform.kimi.ai/docs/guide/migrating-from-openai-to-kimi>) says migration is done by
*"replacing the base URL to `https://api.moonshot.ai/v1` and using their Kimi API key"*, and lists
`/v1/chat/completions`, `/v1/files`, `/v1/files/{file_id}`, `/v1/files/{file_id}/content` as
OpenAI-compatible. Every code sample in the official quickstart uses the stock `openai` SDK. Tool
calling is byte-identical to OpenAI's. `response_format` with `json_schema` + `strict` is identical.
So an existing OpenAI-compatible client **works today with only a base-URL and key swap**. A native
adapter is not required.

**But a plain OpenAI client is not sufficient**, because of officially documented divergences:

| Divergence | Doc source |
|---|---|
| `temperature` range is `[0,1]`, not `[0,2]`; **fixed and non-modifiable on every K-series model** (1.0, or 0.6 in K2.6 non-thinking). Sending OpenAI-typical `temperature: 0.7` to a K-series model is an error. | migrating-from-openai, models-overview |
| `n` is locked to 1 on K-series; `temperature≈0` with `n>1` → `invalid_request_error` | migrating-from-openai |
| `tool_choice: "required"` unsupported on `kimi-k2.6` and `kimi-k2.7-code` (K3 only) | migrating-from-openai |
| Legacy `functions` parameter unsupported | migrating-from-openai |
| **Streaming `usage` appears at `choices[].usage` in the final chunk, not top-level** | streaming guide, migrating-from-openai |
| `usage.cached_tokens` is flat, not `prompt_tokens_details.cached_tokens` | api/chat, openapi.json |
| `message.reasoning_content` has no OpenAI equivalent and is not typed by the OpenAI SDK (requires `getattr`) | use-thinking-models |
| `thinking: {type, keep}` — non-OpenAI parameter, required to control cost/latency on K2.x | use-thinking-models |
| `reasoning_effort` on K3 accepts `low`/`high`/`max` — **no `minimal`/`medium`**, unlike OpenAI | models-overview, kimi-k3-quickstart |
| `partial` prefill mode — non-OpenAI | use-partial-mode |
| Error body is `{error:{type,message}}` — `param` never documented, `code` only sometimes | api/errors, api/estimate |
| `/v1/models` returns extra fields (`context_length`, `supports_*`) beyond OpenAI's shape | api/list-models |
| `/v1/users/me/balance` uses a non-OpenAI envelope `{code,data,scode,status}` | api/balance |
| Two non-interchangeable regional hosts with non-portable keys | api/errors |
| No embeddings endpoint | openapi.json |

**Implementation call:** reuse the OpenAI transport/SDK, and add a thin Kimi provider layer that
(1) suppresses/pins `temperature`, `top_p`, `n`, and the penalties for K-series models,
(2) maps `thinking` / `reasoning_effort` from a generic "reasoning" setting,
(3) reads `usage` from both top-level and `choices[0].usage`,
(4) surfaces `cached_tokens` and `reasoning_content`,
(5) branches retry logic on `error.type` rather than HTTP 429 alone,
(6) selects base URL by region and treats keys as region-bound.

---

## Unknowns

1. **The sunset year for `kimi-k2.5` and `moonshot-v1-*`.** Docs say *"full platform sunset on
   August 31"* with **no year**. Contextually 2026-08-31 (13 days from today, 2026-08-18), given K3
   has shipped and K2 retired May 2026 — but this is inference, **not documented**. **Verify with
   Moonshot before depending on either family.** Recommendation stands regardless: do not build on
   `kimi-k2.5` or `moonshot-v1-*`.
2. **`kimi-k3` maximum output tokens.** The K3 quickstart reports 1,048,576, identical to its context
   window — almost certainly a docs conflation. K2.6's 32,768 default is clearly stated; K3's is not
   trustworthy. Set `max_completion_tokens` explicitly.
3. **Whether legacy `max_tokens` is still accepted** as an alias for `max_completion_tokens`. The
   reference documents only `max_completion_tokens`; an errors-page message mentions `max_tokens`.
   Not stated either way.
4. **`finish_reason: "length"`** — expected but never shown in the docs. Only `"stop"` and
   `"tool_calls"` are documented.
5. **Recommended client-side request timeout.** Only the 900s gateway cutoff is documented. No
   suggested client timeout value.
6. **Exact `Retry-After` header semantics** — referenced for `engine_overloaded_error` but its format
   (seconds vs HTTP-date) and whether it is sent on other 429 types is not specified.
7. **CNY prices for `kimi-k2.7-code`, `kimi-k2.6`, `kimi-k2.5`** on the mainland platform — pages
   exist but were not fetched.
8. **Batch API pricing and web-search tool pricing** — pages exist
   (`/docs/pricing/batch`, `/docs/pricing/tools`) but were not fetched.
9. **Kimi K3 launch date.** The platform changelog at
   <https://platform.kimi.ai/docs/platform-changelog> appears **stale** — its most recent entry is
   April 7, 2025 and it contains no K3, K2.6, or K2.7 announcements at all. The changelog is not a
   reliable source for this provider.
10. **Whether cached tokens have any write cost or TTL.** Only the >256-prompt-token trigger
    threshold is documented. Cache lifetime/TTL and eviction behaviour are unstated.
11. **Rate-limit response headers** (e.g. `x-ratelimit-remaining-*`). Not documented; only the tier
    table and error types.
12. **Availability from Iran.** Not addressed in any Moonshot doc. Network reachability and ToS
    eligibility for the deployment region must be tested empirically.
