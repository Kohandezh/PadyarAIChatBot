# DeepSeek

Research date: 2026-08-18. All facts below come from official DeepSeek documentation
(`api-docs.deepseek.com`). Anything not confirmed on an official page is marked UNKNOWN.

> **Headline finding — the model IDs in our brief are dead.**
> `deepseek-chat` and `deepseek-reasoner` were **fully retired on 2026-07-24** and are no longer
> accessible. The current model IDs are `deepseek-v4-flash` and `deepseek-v4-pro`. Any code or
> config in this repo still referencing `deepseek-chat` / `deepseek-reasoner` will fail.

## Sources

| Topic | URL |
|---|---|
| Your First API Call (base URL, auth, quickstart) | https://api-docs.deepseek.com/ |
| Chat Completions API reference | https://api-docs.deepseek.com/api/create-chat-completion |
| List Models | https://api-docs.deepseek.com/api/list-models |
| Get User Balance | https://api-docs.deepseek.com/api/get-user-balance |
| Responses API guide | https://api-docs.deepseek.com/guides/responses_api |
| Thinking Mode guide | https://api-docs.deepseek.com/guides/thinking_mode |
| Thinking mode sample (non-streaming) | https://api-docs.deepseek.com/api_samples/thinking_mode_api_example_non_streaming |
| Thinking mode sample (streaming) | https://api-docs.deepseek.com/api_samples/thinking_mode_api_example_streaming |
| Tool / function calling | https://api-docs.deepseek.com/guides/tool_calls |
| JSON output | https://api-docs.deepseek.com/guides/json_mode |
| Context caching (KV cache) | https://api-docs.deepseek.com/guides/kv_cache |
| Chat prefix completion (beta base URL) | https://api-docs.deepseek.com/guides/chat_prefix_completion |
| Pricing | https://api-docs.deepseek.com/quick_start/pricing |
| Rate / concurrency limits | https://api-docs.deepseek.com/quick_start/rate_limit |
| Error codes | https://api-docs.deepseek.com/quick_start/error_codes |
| Token usage | https://api-docs.deepseek.com/quick_start/token_usage |
| Change log | https://api-docs.deepseek.com/updates |
| V4 release / legacy alias deprecation (2026-04-24) | https://api-docs.deepseek.com/news/news260424 |
| V4-Flash GA (2026-07-31) | https://api-docs.deepseek.com/news/news0725 (indexed under updates) |
| V4-Pro GA + peak pricing (2026-08-13) | https://api-docs.deepseek.com/news/news260813 |
| Codex integration (base URL confirmation) | https://api-docs.deepseek.com/quick_start/agent_integrations/codex |
| Sitemap (page enumeration) | https://api-docs.deepseek.com/sitemap.xml |

## Auth

Bearer token in the `Authorization` header. Source: https://api-docs.deepseek.com/

```
Content-Type: application/json
Authorization: Bearer ${DEEPSEEK_API_KEY}
```

No custom header name, no API-version header, no org/project header documented.

## Endpoints

Base URL documented today: **`https://api.deepseek.com`** — *no version segment*.
Source: https://api-docs.deepseek.com/

Documented base URL variants:

| Base URL | Purpose | Source |
|---|---|---|
| `https://api.deepseek.com` | Default, OpenAI-format endpoints | https://api-docs.deepseek.com/ |
| `https://api.deepseek.com/anthropic` | Anthropic Messages API format | https://api-docs.deepseek.com/ |
| `https://api.deepseek.com/beta` | Beta features: chat prefix completion, FIM completion, strict-mode function calling | https://api-docs.deepseek.com/guides/chat_prefix_completion , https://api-docs.deepseek.com/guides/tool_calls |

**On `/v1`:** the brief asked whether DeepSeek documents a `/v1` alternative. **The current docs do
not.** Every current example — the quickstart curl, the Python OpenAI-SDK snippet, and the Codex
integration config (`base_url = "https://api.deepseek.com/"`) — uses the bare host with no version
segment, and the historical note that "`v1` has no relationship with the model version" is **no
longer present** anywhere in the current documentation. Treat `https://api.deepseek.com/v1` as
**UNDOCUMENTED**. It may still resolve for OpenAI-SDK compatibility, but we must not depend on it;
configure the bare host.

Paths (appended to base URL):

| Method | Path | Purpose |
|---|---|---|
| POST | `/chat/completions` | Chat / generation |
| POST | `/completions` | Legacy text completion (FIM) |
| POST | `/responses` | Responses API (OpenAI-compatible surface) |
| GET | `/models` | Model listing |
| GET | `/user/balance` | Account balance |

## Request shape

Source: https://api-docs.deepseek.com/api/create-chat-completion

`POST https://api.deepseek.com/chat/completions`

```json
{
  "model": "deepseek-v4-pro",
  "messages": [
    {"role": "system", "content": "You are a helpful assistant."},
    {"role": "user", "content": "Hello!"}
  ],
  "thinking": {"type": "enabled"},
  "reasoning_effort": "high",
  "stream": false
}
```

Parameters:

| Name | Type | Notes |
|---|---|---|
| `model` | string, **required** | `deepseek-v4-flash` or `deepseek-v4-pro` |
| `messages` | object[], **required** | min 1 item |
| `stream` | boolean | SSE streaming |
| `stream_options` | object | `{"include_usage": bool}` |
| `temperature` | number | 0–2, default 1. **Ignored/unsupported in thinking mode** |
| `top_p` | number | 0–1, default 1. **Ignored/unsupported in thinking mode** |
| `max_tokens` | integer, nullable | |
| `thinking` | object, nullable | `{"type": "enabled" \| "disabled"}` |
| `reasoning_effort` | string | `low` \| `high` \| `max`. Sent as a **top-level** parameter in the official samples |
| `response_format` | object, nullable | `{"type": "text" \| "json_object"}` |
| `stop` | string \| string[], nullable | up to 16 sequences |
| `tools` | object[], nullable | max 128 |
| `tool_choice` | object, nullable | |
| `logprobs` | boolean, nullable | |
| `top_logprobs` | integer, nullable | 0–20 |
| `user_id` | string, nullable | used for per-user concurrency isolation on expanded-capacity accounts |
| `frequency_penalty` | — | **DEPRECATED — no longer supported** |
| `presence_penalty` | — | **DEPRECATED — no longer supported** |

Note the parameter placement quirk: `thinking` is a request-body object but is not part of the
OpenAI schema, so the official Python sample passes it via `extra_body={"thinking": {"type":
"enabled"}}` while passing `reasoning_effort="high"` as a normal top-level kwarg. A raw-HTTP client
sends both at the top level of the JSON body.

## Response shape

Source: https://api-docs.deepseek.com/api/create-chat-completion

```json
{
  "id": "...",
  "object": "chat.completion",
  "created": 1735000000,
  "model": "deepseek-v4-pro",
  "system_fingerprint": "...",
  "choices": [
    {
      "index": 0,
      "finish_reason": "stop",
      "message": {
        "role": "assistant",
        "content": "...",
        "reasoning_content": "...",
        "tool_calls": [
          {"id": "...", "type": "function",
           "function": {"name": "...", "arguments": "{...}"}}
        ]
      }
    }
  ],
  "usage": {
    "prompt_tokens": 0,
    "completion_tokens": 0,
    "total_tokens": 0,
    "prompt_cache_hit_tokens": 0,
    "prompt_cache_miss_tokens": 0,
    "completion_tokens_details": {"reasoning_tokens": 0}
  }
}
```

Streaming deltas expose the same split: `chunk.choices[0].delta.reasoning_content` and
`chunk.choices[0].delta.content`. Source:
https://api-docs.deepseek.com/api_samples/thinking_mode_api_example_streaming

## Reasoning model handling

This is the part that forces provider-specific handling. Source:
https://api-docs.deepseek.com/guides/thinking_mode

- **There is no separate reasoning model any more.** Both `deepseek-v4-flash` and
  `deepseek-v4-pro` support thinking mode; it is a per-request switch, not a model choice.
- **Thinking is ENABLED BY DEFAULT, with default effort `high`.** This is a cost trap: a naive
  request with no `thinking` field will generate chain-of-thought tokens billed at output rates.
  For cheap/fast work we must explicitly send `{"thinking": {"type": "disabled"}}`.
- Reasoning is returned in a **separate field**, `message.reasoning_content`, sibling to
  `message.content` (delta equivalent when streaming).
- **Unsupported parameters are explicitly documented:** "Thinking mode does not support the
  `temperature`, `top_p`, `presence_penalty`, or `frequency_penalty` parameters." (`presence_penalty`
  and `frequency_penalty` are additionally deprecated API-wide.) Docs do not state that passing them
  errors — treat as ignored — but our abstraction should strip them when thinking is enabled.
- **Multi-turn rules differ depending on tools:**
  - *Without* tool calls: prior `reasoning_content` need not be re-sent; if sent, "it will be
    ignored."
  - *With* tool calls (`tools` present): `reasoning_content` **must be passed back** in all
    subsequent turns. "If your code does not correctly pass back `reasoning_content`, the API will
    return a 400 error."

That last rule is the single strongest argument against a plain OpenAI client: an OpenAI-shaped
message serializer will drop the non-standard `reasoning_content` key and break tool-calling
conversations with a 400.

## Model listing

`GET https://api.deepseek.com/models` with the same `Authorization: Bearer` header.
Source: https://api-docs.deepseek.com/api/list-models

```json
{
  "object": "list",
  "data": [
    {"id": "deepseek-v4-flash", "object": "model", "owned_by": "deepseek"},
    {"id": "deepseek-v4-pro",   "object": "model", "owned_by": "deepseek"}
  ]
}
```

Object shape is OpenAI-compatible but minimal: `id`, `object`, `owned_by` only. No `created`, no
context-window, no pricing, no capability flags. Context length / max output / pricing must come
from the pricing page, not the API.

## Models

Current documented model IDs (source: https://api-docs.deepseek.com/api/list-models and
https://api-docs.deepseek.com/quick_start/pricing):

| Model ID | Underlying version | Context | Max output | Notes |
|---|---|---|---|---|
| `deepseek-v4-flash` | DeepSeek-V4-Flash-0731 | 1M tokens | 384K tokens | 284B total params / 13B active |
| `deepseek-v4-pro` | DeepSeek-V4-Pro-0813 | 1M tokens | 384K tokens | 1.6T total params / 49B active |

Both are **aliases that float** to the latest dated build: "The calling method remains unchanged —
simply use `deepseek-v4-flash` or `deepseek-v4-pro` to access the latest version."
(https://api-docs.deepseek.com/). No pinned dated IDs (`...-0731`, `...-0813`) are documented as
callable model strings — the dated names appear only as release descriptions. **We cannot pin a
version.** Expect silent model upgrades under a stable ID.

Retired aliases (source: https://api-docs.deepseek.com/news/news260424):
> "deepseek-chat & deepseek-reasoner will be fully retired and inaccessible after Jul 24th, 2026,
> 15:59 (UTC Time). (Currently routing to deepseek-v4-flash non-thinking/thinking)."

Recommendation for our two use cases:

- **(a) General chat** → `deepseek-v4-pro`, thinking enabled, `reasoning_effort` `low` or `high`
  depending on latency budget. Or `deepseek-v4-flash` if cost matters more than depth — Flash is
  3x cheaper on every axis.
- **(b) Cheap/fast classification** → `deepseek-v4-flash` with `{"thinking": {"type": "disabled"}}`
  explicitly set, plus `response_format: {"type": "json_object"}` and a small `max_tokens`. Leaving
  thinking at its default would silently 3x–10x the cost of a one-label classification.

## Capabilities

| Feature | Parameter | Caveats (from docs) |
|---|---|---|
| Streaming | `stream: true`, `stream_options: {"include_usage": true}` | Server emits SSE keep-alive comments (`: keep-alive`) during long waits; parsers must tolerate them |
| Tool calling | `tools` (max 128), `tool_choice` | Model does not execute functions. `reasoning_content` must be echoed back or the API 400s. Strict schema validation requires the **`/beta` base URL** |
| JSON output | `response_format: {"type": "json_object"}` | Prompt **must contain the word "json"**; docs advise including a format example; set `max_tokens` high enough to avoid truncation; documented failure mode: "The API may occasionally return empty content" |
| Structured outputs (`json_schema`) | — | **NOT documented.** Only `text` and `json_object` are listed for `response_format` |
| Context caching | automatic | Enabled by default, no code change, no storage fee documented; "does not guarantee a 100% cache hit rate", best-effort; caches auto-clear "usually within a few hours to a few days" |
| Prefix completion | `prefix: true` on last assistant message | Requires `https://api.deepseek.com/beta` |
| FIM completion | `POST /completions` | Beta |
| Anthropic Messages format | base URL `/anthropic` | |
| Responses API | `POST /responses` | See gaps below |
| Vision / image input | — | **NOT supported** on the Responses API (images "replaced with a placeholder text"). No vision support documented for chat completions either |

## Usage/tokens

Exact field names (source: https://api-docs.deepseek.com/api/create-chat-completion and
https://api-docs.deepseek.com/guides/kv_cache):

- `usage.prompt_tokens` — documented as `prompt_cache_hit_tokens + prompt_cache_miss_tokens`
- `usage.completion_tokens`
- `usage.total_tokens`
- `usage.prompt_cache_hit_tokens` — prompt tokens served from cache (billed at the far cheaper
  cache-hit rate)
- `usage.prompt_cache_miss_tokens` — prompt tokens processed fresh
- `usage.completion_tokens_details.reasoning_tokens` — CoT tokens

This cache-hit/miss split is DeepSeek-specific and does **not** exist in the OpenAI schema (OpenAI
uses `prompt_tokens_details.cached_tokens`). Our cost accounting must read DeepSeek's names or it
will price every request at the cache-miss rate. `prompt_tokens_details` is **not** documented for
DeepSeek — UNKNOWN whether it is present.

Rough sizing (https://api-docs.deepseek.com/quick_start/token_usage): English ≈ 0.3 tokens per
character, Chinese ≈ 0.6 tokens per character; an offline tokenizer is downloadable. No Persian
ratio is documented — for our RTL Persian workload, do not trust these ratios; read `usage` from
the response.

## Errors

Source: https://api-docs.deepseek.com/quick_start/error_codes

| HTTP | Meaning | Documented remedy | Our handling |
|---|---|---|---|
| 400 | Invalid Format | Fix request body per the error message | Non-retryable. Also the code returned when `reasoning_content` is not echoed back with `tools` |
| 401 | Authentication Fails | Check API key | Non-retryable — surface to admin |
| 402 | Insufficient Balance | Top up | Non-retryable — surface to admin, distinct from auth failure |
| 422 | Invalid Parameters | Fix parameters | Non-retryable |
| 429 | Rate Limit Reached | "pace your requests reasonably" | Retryable with backoff |
| 500 | Server Error | "retry your request after a brief wait" | Retryable |
| 503 | Server Overloaded | "retry your request after a brief wait" | Retryable |

**Error response body shape: UNKNOWN.** The error-codes page documents statuses and prose remedies
only — no JSON schema, no `error.type` / `error.code` enumeration is published. Parse defensively:
assume an OpenAI-ish `{"error": {"message": ..., "type": ..., "code": ...}}` but never require any
field, and always fall back to the HTTP status.

The 402 status is unusual and worth a dedicated branch — a billing problem presenting as a hard
API failure is exactly the case where a generic "provider error" message wastes an operator's time.

## Rate limits

Source: https://api-docs.deepseek.com/quick_start/rate_limit

DeepSeek's policy is **concurrency-based, not request-rate-based**. There is no documented
RPM/TPM quota. Instead:

| Model | Concurrency limit |
|---|---|
| `deepseek-v4-flash` | 2500 |
| `deepseek-v4-pro` | 500 |

"A request counts as one concurrent connection from the time it is sent until the model response is
complete." Exceeding it returns **HTTP 429**. Accounts with expanded capacity get both an
account-level cap and a per-`user_id` cap.

(Note: this is a change from DeepSeek's long-standing historical position of "we do not constrain
your rate limit" — the concurrency caps are now explicit.)

Keep-alive / timeout behavior, documented and load-bearing for our HTTP client:

- Non-streaming requests: the server "continuously return[s] empty lines" while waiting.
- Streaming requests: the server continuously returns SSE keep-alive comments (`: keep-alive`).
- "These contents do not affect the parsing of the JSON body" — but a strict JSON/SSE parser can
  choke on them. Our client must skip blank lines and `:`-prefixed SSE comments.
- Server-side timeout: "If the request has not started inference after 10 minutes, the server will
  close the connection." So client read timeouts must be generous (well above typical defaults),
  and we should prefer streaming so keep-alives hold the connection.

**No retry guidance (backoff strategy, Retry-After header, max attempts) is documented — UNKNOWN.**
Implement our own: exponential backoff with jitter on 429/500/503 only.

## Pricing

Source: https://api-docs.deepseek.com/quick_start/pricing

**Currency: USD, per 1M tokens.** Both models: 1M context, 384K max output.

Peak (standard) rates:

| Model | Input (cache hit) | Input (cache miss) | Output |
|---|---|---|---|
| `deepseek-v4-flash` | $0.014 | $0.44 | $1.32 |
| `deepseek-v4-pro` | $0.044 | $1.32 | $3.96 |

Off-peak (discount) rates — exactly half of peak:

| Model | Input (cache hit) | Input (cache miss) | Output |
|---|---|---|---|
| `deepseek-v4-flash` | $0.007 | $0.22 | $0.66 |
| `deepseek-v4-pro` | $0.022 | $0.66 | $1.98 |

**Time-based discount is documented and material:** "Peak hours are 01:00 - 04:00 and 06:00 - 10:00
UTC (all other hours are off-peak)." Off-peak is 50% off. This pricing structure took effect
2026-08-16 16:00 UTC (https://api-docs.deepseek.com/news/news260813).

Two consequences for our cost model:
1. The cache-hit vs cache-miss gap is ~31x on input. Stable system prompts / long RAG prefixes are
   worth deliberately structuring for prefix-cache reuse.
2. Cost per request is **time-of-day dependent**. Any stored per-token price constant will be wrong
   half the time. Either store both tiers and pick by UTC clock, or store the peak rate and treat
   it as an upper bound.

Billing formula: "The expense = number of tokens × price," deducted from granted balance first,
then topped-up balance.

UNKNOWN: whether `completion_tokens` already includes `reasoning_tokens` for billing purposes. The
pricing page does not state it. Assume it does (CoT is billed as output) but verify empirically
before relying on the number.

## Health/test-connection strategy

Recommended, in order of cost:

1. **`GET /models`** — cheapest liveness + auth check. Zero token cost. Returns 401 on a bad key.
   Also lets us verify that the configured model ID is actually in `data[].id`, which would have
   caught the `deepseek-chat` retirement automatically.
2. **`GET /user/balance`** — catches the 402 case *before* it fails a user-facing request. Returns
   `{"is_available": bool, "balance_infos": [{"currency": "CNY"|"USD", "total_balance",
   "granted_balance", "topped_up_balance"}]}`. `is_available` is a direct "can this key still make
   calls" boolean. This is a genuinely useful admin-panel signal and has no OpenAI equivalent.
   Source: https://api-docs.deepseek.com/api/get-user-balance
3. **Minimal `POST /chat/completions`** — only when a true end-to-end check is needed. Use
   `deepseek-v4-flash`, `{"thinking": {"type": "disabled"}}`, `max_tokens: 1`. Without disabling
   thinking, a "test connection" button burns CoT tokens at output rates.

Proposed health check: `/models` for the pass/fail, `/user/balance` for a warning badge when
`is_available` is false or the balance is low.

## OpenAI-compatibility verdict

**OPENAI-COMPATIBLE + PROVIDER-SPECIFIC METADATA**

Justification from the docs:

*What is compatible.* DeepSeek officially documents the OpenAI Python SDK as the client, with only
`base_url` swapped (https://api-docs.deepseek.com/). Path shapes (`/chat/completions`, `/models`),
auth (`Authorization: Bearer`), the `choices[].message` envelope, `tools`/`tool_choice`,
`stream`/`stream_options.include_usage`, `stop`, `logprobs`, and `response_format: json_object` all
follow the OpenAI schema. The Responses API guide even states that "unsupported parameters are
silently ignored and do not cause errors, so existing Responses API clients can connect without
modification." A generic OpenAI client will work for basic chat.

*Why it is not sufficient alone.* Four documented divergences carry real information or real
failure modes, and all four sit outside the OpenAI schema:

1. `message.reasoning_content` / `delta.reasoning_content` — a non-OpenAI field. A strict OpenAI
   client drops it, and with `tools` present, failing to echo it back is a documented **400 error**.
2. `thinking` object + `reasoning_effort` — must be sent via `extra_body` through the OpenAI SDK;
   thinking defaults to **enabled at `high` effort**, so ignoring it is a silent cost multiplier.
3. `usage.prompt_cache_hit_tokens` / `prompt_cache_miss_tokens` — DeepSeek's own field names, not
   OpenAI's `prompt_tokens_details.cached_tokens`. Ignoring them mis-prices every cached request by
   up to 31x.
4. `GET /user/balance` and the 402 status — no OpenAI analogue, and the most operationally useful
   error we can surface to an admin.

Plus: `temperature`/`top_p` are silently unsupported in thinking mode, and
`frequency_penalty`/`presence_penalty` are deprecated outright — an OpenAI-shaped request builder
that always sends them is sending dead parameters.

So: reuse the OpenAI transport/wire format, but wrap it in a DeepSeek adapter that (a) injects
`thinking`/`reasoning_effort`, (b) preserves and re-sends `reasoning_content` when tools are in
play, (c) strips sampling params under thinking mode, and (d) maps the cache-hit/miss usage fields
into our cost model. A full hand-rolled native HTTP client is not required.

## Unknowns

- **`https://api.deepseek.com/v1`** — not documented anywhere in the current docs; the historical
  "v1 has no relation to model version" note is gone. May still work for SDK compatibility, but
  UNDOCUMENTED — do not configure it.
- **Error response body JSON schema** — UNKNOWN. Statuses documented, body shape and machine-readable
  error codes are not.
- **Retry guidance / `Retry-After` header / backoff policy** — UNKNOWN, not documented.
- **Pinned dated model IDs** (`deepseek-v4-pro-0813` etc. as callable strings) — UNKNOWN; only
  floating aliases are documented.
- **`json_schema` / strict structured outputs** — not documented for `response_format`; only `text`
  and `json_object`. Strict *function* schema validation exists but requires the `/beta` base URL.
- **`usage.prompt_tokens_details`** — not documented; presence UNKNOWN.
- **Whether `completion_tokens` includes `reasoning_tokens` for billing** — UNKNOWN.
- **Whether passing `temperature`/`top_p` in thinking mode errors or is silently ignored** —
  UNKNOWN; docs only say "does not support".
- **Persian/Farsi token-per-character ratio** — not documented (only English and Chinese).
- **Cache TTL as a precise number** — only "a few hours to a few days".
- **Parallel tool calls on `/chat/completions`** — not documented (the Responses API guide says
  parallel tool calling is always on there).
- **Vision/image input on `/chat/completions`** — not documented; explicitly unsupported on
  `/responses`.
- **Data retention / training-on-inputs policy** — not covered on the API docs pages reviewed.
