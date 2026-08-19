# Z.AI / GLM

Research date: 2026-08-18. All facts below carry the official doc URL they came from.
Anything not confirmed from an official Z.AI / Zhipu source is marked **UNKNOWN**.

---

## Platform identity

**Short answer for a non-China developer: use `https://api.z.ai/api/paas/v4/` with a plain
`Authorization: Bearer <api-key>` header. That is the international platform, its docs are in
English, and its pricing is in USD.**

### What is confirmed

| Thing | Finding | Source |
|---|---|---|
| International platform name | **Z.AI** (docs title "Z.AI DEVELOPER DOCUMENT"; OpenAPI `info.title` = "Z.AI API") | https://docs.z.ai/api-reference/introduction , https://docs.z.ai/openapi.json |
| International docs domain | `docs.z.ai` (English) | https://docs.z.ai/llms.txt |
| International API host | `https://api.z.ai/api` (OpenAPI `servers[0].url`) | https://docs.z.ai/openapi.json |
| International legal operator | **JINGSHENG HENGXING TECHNOLOGY PTE. LTD** — a Singapore entity. Terms governed by **Singapore law**, disputes via SIAC. | https://docs.z.ai/legal-agreement/terms-of-use |
| China-mainland platform name | **智谱 / 智谱开放平台** ("Zhipu Open Platform"), branded **BigModel** | https://docs.bigmodel.cn/llms.txt (title: 智谱AI开放文档 — "Z智谱开放平台开发者文档中心") |
| China docs domain | `docs.bigmodel.cn` — **documentation is in Simplified Chinese only** | https://docs.bigmodel.cn/llms.txt |
| China API host | `https://open.bigmodel.cn/api/paas/v4` | https://docs.bigmodel.cn/cn/guide/develop/openai/introduction.md |
| Model family | **GLM** (plus CogView / CogVideoX / Vidu for image+video) — the same model IDs appear on both platforms | both llms.txt indexes |

### "Are they the same company/product?"

**Not confirmed by an explicit official statement — confirmed by strong structural evidence.**

I could not find any page on `docs.z.ai` that names Zhipu AI or BigModel, and I could not find any
page on `docs.bigmodel.cn` that names Z.AI as its international arm. Neither platform's docs
acknowledge the other. **That linkage is therefore UNVERIFIED from a direct official statement.**

What *is* verifiable from official sources, and is close to conclusive:

1. **Identical URL grammar.** China `https://open.bigmodel.cn/api/paas/v4` ↔ international
   `https://api.z.ai/api/paas/v4`. China Anthropic-compat `https://open.bigmodel.cn/api/anthropic`
   ↔ international `https://api.z.ai/api/anthropic`. Same unusual `/api/paas/v4` segment on both.
2. **Z.AI's own OpenAPI spec serves example assets from `cdn.bigmodel.cn`** — e.g. the vision
   examples reference `https://cdn.bigmodel.cn/static/logo/register.png` and
   `https://cdn.bigmodel.cn/agent-demos/lark/113123.mov`. Z.AI is serving Zhipu/BigModel CDN
   content in its official machine-readable spec. (https://docs.z.ai/openapi.json)
3. **Identical proprietary error-code numbering** (1000/1001/1003/1113/1210…1321) on both.
4. **Identical model IDs and the same doc information architecture** (Mintlify, mirrored page
   trees, mirrored `llms.txt` structure).
5. **The China docs ship an SDK package named `zai-sdk`** whose client class is `ZhipuAiClient`,
   while the international docs ship `zai-sdk` with client class `ZaiClient`.
   (https://docs.bigmodel.cn/cn/guide/start/quick-start , https://docs.z.ai/guides/overview/quick-start)

**Working conclusion:** Z.AI and Zhipu/BigModel are two regional front-ends onto the same GLM
platform — Z.AI (Singapore entity, English, USD) for international, BigModel (Chinese, CNY) for
mainland China. Treat this as "extremely likely, not officially stated."

### They are not feature-identical — this matters

The China platform documents **more model families** than the international one: embeddings
(`embedding-3`, `embedding-2`), TTS (`glm-tts`, `glm-tts-clone`), realtime (`glm-realtime`),
voice (`glm-4-voice`), and character models (`charglm-4`, `emohaa`) appear on `docs.bigmodel.cn`
but **not** in the `docs.z.ai` index. Do not assume a model documented on BigModel exists on Z.AI.

### Things I actively disproved

A blog result claimed the international OpenAI-compatible base URL is
`https://api.z.ai/api/openai/v1`. **This is false.** A live probe returns
`{"code":500,"msg":"404 NOT_FOUND","success":false}`. No official Z.AI page documents that path.
The OpenAI-compatible base URL is the *same* as the native one: `https://api.z.ai/api/paas/v4/`.

### Region gating

Z.AI's terms require the user confirm they are **not** located in Iran, North Korea, Cuba, Crimea,
Donetsk, or Zaporizhzhia. (https://docs.z.ai/legal-agreement/terms-of-use) Relevant if this
project has Iran-adjacent deployment; **verify before committing to this provider.**

---

## Sources

Official Z.AI (English):
- Docs index (machine-readable): https://docs.z.ai/llms.txt
- **OpenAPI spec (authoritative): https://docs.z.ai/openapi.json**
- API reference introduction: https://docs.z.ai/api-reference/introduction
- Chat Completion reference: https://docs.z.ai/api-reference/llm/chat-completion
- Errors: https://docs.z.ai/api-reference/api-code
- Quick start: https://docs.z.ai/guides/overview/quick-start
- HTTP API guide (auth incl. JWT): https://docs.z.ai/guides/develop/http/introduction
- OpenAI SDK guide: https://docs.z.ai/guides/develop/openai/python
- Pricing: https://docs.z.ai/guides/overview/pricing
- Streaming: https://docs.z.ai/guides/capabilities/streaming
- Function calling: https://docs.z.ai/guides/capabilities/function-calling
- Structured output: https://docs.z.ai/guides/capabilities/struct-output
- Thinking mode: https://docs.z.ai/guides/capabilities/thinking-mode
- GLM-4.7 family: https://docs.z.ai/guides/llm/glm-4.7
- Model overview: https://docs.z.ai/guides/overview/overview
- Terms of use: https://docs.z.ai/legal-agreement/terms-of-use
- Coding-plan tool endpoints: https://docs.z.ai/devpack/tool/others
- Rate limits (redirects to console, JS-only): https://docs.z.ai/api-reference/rate-limit

Official Zhipu / BigModel (**Simplified Chinese only**):
- Docs index: https://docs.bigmodel.cn/llms.txt
- Quick start: https://docs.bigmodel.cn/cn/guide/start/quick-start
- OpenAI compat: https://docs.bigmodel.cn/cn/guide/develop/openai/introduction
- Claude/Anthropic compat: https://docs.bigmodel.cn/cn/guide/develop/claude/introduction
- GLM-5: https://docs.bigmodel.cn/cn/guide/models/text/glm-5

Live probes I ran (not documentation, but evidence):
- `GET https://api.z.ai/api/openai/v1/models` → `{"code":500,"msg":"404 NOT_FOUND","success":false}`
- `GET https://api.z.ai/api/paas/v4/models` (no auth) → 401 `{"error":{"code":"1001","message":"Authentication parameter not received in Header, unable to authenticate"}}`
- `GET https://api.z.ai/api/paas/v4/definitely-not-a-real-endpoint` (no auth) → **the same 401** → auth is checked at the gateway *before* routing, so a 401 on `/models` proves nothing about whether it exists.

---

## Auth

**Plain API key in a Bearer header. NOT JWT-signed by default.** I verified this rather than
assuming it.

```
Authorization: Bearer <YOUR_API_KEY>
Content-Type: application/json
Accept-Language: en-US,en      # used in official curl examples; optional
```

Source: https://docs.z.ai/guides/develop/http/introduction , and the OpenAPI security scheme:

```json
"securitySchemes": {
  "bearerAuth": {
    "type": "http", "scheme": "bearer",
    "description": "Use the following format for authentication: Bearer <your api key>"
  }
}
```
with a global `"security": [{"bearerAuth": []}]`. (https://docs.z.ai/openapi.json)

**JWT is an optional alternative, not a requirement.** The HTTP guide documents a second method:
the API key is formatted `id.secret`; you split it and sign a time-limited JWT whose payload
carries the key id, an expiry timestamp and a creation timestamp (PyJWT example, 1-hour validity),
described as "suitable for scenarios requiring higher security."
(https://docs.z.ai/guides/develop/http/introduction)

**Practical implication:** send the raw API key as a Bearer token. Do not build a JWT signer.
The `id.secret` shape does mean the key contains a dot — do not write validation that rejects that.

API key management console: https://z.ai/manage-apikey/apikey-list

---

## Endpoints

| Purpose | Base URL | Notes |
|---|---|---|
| **Native + OpenAI-compatible (use this)** | `https://api.z.ai/api/paas/v4/` | Same URL for both. The OpenAI SDK is officially documented against this exact base. |
| OpenAPI `servers[0]` | `https://api.z.ai/api` | paths are then `/paas/v4/...` |
| Anthropic Messages compatible | `https://api.z.ai/api/anthropic` | Documented **only** under the GLM Coding Plan / DevPack tooling page, not the general API reference. |
| OpenAI Chat Completions (Coding Plan) | `https://api.z.ai/api/coding/paas/v4` | GLM Coding Plan subscription only — **not** the pay-as-you-go API. |
| OpenAI Responses protocol (Coding Plan) | `https://api.z.ai/api/v1` | Coding Plan only. |
| China mainland native | `https://open.bigmodel.cn/api/paas/v4` | Separate platform, separate account, CNY. |
| China mainland Anthropic-compat | `https://open.bigmodel.cn/api/anthropic/v1/messages` | |

Sources: https://docs.z.ai/openapi.json , https://docs.z.ai/guides/develop/http/introduction ,
https://docs.z.ai/guides/develop/openai/python , https://docs.z.ai/devpack/tool/others ,
https://docs.bigmodel.cn/cn/guide/develop/claude/introduction

**Chat endpoint:** `POST https://api.z.ai/api/paas/v4/chat/completions`

Other documented paths on the same base (from https://docs.z.ai/openapi.json):
`/paas/v4/videos/generations`, `/paas/v4/async-result/{id}`, `/paas/v4/images/generations`,
`/paas/v4/async/images/generations`, `/paas/v4/audio/transcriptions`, `/paas/v4/tokenizer`,
`/paas/v4/layout_parsing`, `/paas/v4/web_search`, `/paas/v4/reader`, `/paas/v4/files`,
`/v1/agents`, `/v1/agents/async-result`, `/v1/agents/conversation`.

There is a **`/paas/v4/tokenizer`** endpoint — useful if you need server-side token counting.

---

## Request shape

`POST /api/paas/v4/chat/completions`

Required: `model`, `messages`. (https://docs.z.ai/openapi.json → `ChatCompletionTextRequest`)

```bash
curl --location 'https://api.z.ai/api/paas/v4/chat/completions' \
  --header 'Authorization: Bearer YOUR_API_KEY' \
  --header 'Content-Type: application/json' \
  --data '{
    "model": "glm-5.3",
    "messages": [{"role": "user", "content": "Hello"}],
    "temperature": 1.0,
    "max_tokens": 1024
  }'
```
(https://docs.z.ai/guides/develop/http/introduction)

Full documented parameter list, from the OpenAPI schema (authoritative):

| Param | Type | Default | Notes |
|---|---|---|---|
| `model` | string | `glm-5.3` | enum-constrained; see Models |
| `messages` | array | — | roles: `system`, `user`, `assistant`, `tool`. Docs: "The input must not consist of system messages or assistant messages only." |
| `do_sample` | boolean | `true` | **Z.AI-specific.** `do_sample=false` (greedy) is documented as *not* usable via the OpenAI SDK path. |
| `stream` | boolean | `false` | SSE; terminates with `data: [DONE]` |
| `thinking` | object | `{"type":"enabled"}` | **Z.AI-specific.** `{"type":"enabled"\|"disabled", "clear_thinking": bool}` |
| `reasoning_effort` | string | `max` | **Z.AI-specific.** enum: `max`, `xhigh`, `high`, `medium`, `low`, `minimal`, `none` |
| `temperature` | number | `1.0` | range `[0.0, 1.0]` — **not** OpenAI's `[0,2]` |
| `top_p` | number | `0.95` | range `[0.01, 1.0]` |
| `max_tokens` | integer | — | `[1, 131072]`; per-family caps below |
| `tools` | array | — | max 128 functions; item types `function`, `retrieval`, `web_search` |
| `tool_choice` | string | — | **`"auto"` is the only accepted value** |
| `tool_stream` | boolean | `false` | **Z.AI-specific.** streams function-call args |
| `stop` | array | — | `maxItems: 4` in schema, but the description says **"Currently, only one stop word is supported"** |
| `response_format` | object | `{"type":"text"}` | enum `text` \| `json_object` — **no `json_schema`** |
| `request_id` | string | — | **Z.AI-specific.** 6–64 chars, client-supplied idempotency/trace id |
| `user_id` | string | — | **Z.AI-specific.** 6–128 chars end-user id |

`max_tokens` caps by family (from the schema description): GLM-5.3/5.2/5.1/5/4.7/4.6 → 128K;
GLM-4.5 series → 96K; GLM-4.6V series → 32K; GLM-4.5V → 16K; GLM-4-32B-0414-128K → 16K.

**`thinking` semantics (important, from the `ChatThinking` schema):**
- `glm-5.3` — thinking **can only be enabled**; depth is controlled by `reasoning_effort`.
- `glm-5.2`, `glm-5.1`, `glm-5`, `glm-5-turbo`, `glm-5v-turbo`, `glm-4.6`, `glm-4.5` — when enabled,
  the model *decides for itself* whether to think.
- **`glm-4.7` and `glm-4.5v` — "will think compulsorily" when enabled.** For latency-sensitive
  classification on the 4.7 family you must pass `"thinking": {"type": "disabled"}`.
- `clear_thinking` (default `true`) strips prior-turn `reasoning_content` from context.

Tool definition shape is OpenAI-identical:
```json
{"type":"function","function":{"name":"...","description":"...","parameters":{"type":"object","properties":{},"required":[]}}}
```
Tool result message is OpenAI-identical: `{"role":"tool","content":"<json string>","tool_call_id":"<id>"}`.
(https://docs.z.ai/guides/capabilities/function-calling)

---

## Response shape

Non-streaming (`ChatCompletionResponse`, https://docs.z.ai/openapi.json):

```json
{
  "id": "string",
  "request_id": "string",
  "created": 1677652288,
  "model": "glm-5.3",
  "choices": [{
    "index": 0,
    "message": {
      "role": "assistant",
      "content": "string",
      "reasoning_content": "string",
      "tool_calls": [{
        "id": "string",
        "type": "function",
        "function": {"name": "string", "arguments": "<JSON string>"}
      }]
    },
    "finish_reason": "stop"
  }],
  "usage": {
    "prompt_tokens": 100,
    "completion_tokens": 50,
    "prompt_tokens_details": {"cached_tokens": 0},
    "total_tokens": 150
  },
  "web_search": []
}
```

Deltas from OpenAI:
- extra top-level **`request_id`** and **`web_search`**
- extra message field **`reasoning_content`** (thinking trace)
- **no `object`, no `system_fingerprint`, no `service_tier`, no `logprobs`**
- `finish_reason` includes non-OpenAI values: `stop`, `tool_calls`, `length`, **`sensitive`**,
  **`model_context_window_exceeded`**, **`network_error`**
- `content` is `null` when a tool call fires
- GLM-4.5V may emit raw `<think></think>` and `<|begin_of_box|><|end_of_box|>` markers inside
  `content` — strip them if you ever use that model.

**Streaming** (https://docs.z.ai/guides/capabilities/streaming): standard SSE.
```
data: {"id":"1","created":1677652288,"model":"glm-5","choices":[{"index":0,"delta":{"content":"Spring"},"finish_reason":null}]}
...
data: [DONE]
```
- incremental text: `choices[0].delta.content`
- incremental thinking: `choices[0].delta.reasoning_content`
- `finish_reason` appears only in the terminal chunk
- **`usage` is included in the final chunk** (no `stream_options.include_usage` needed)

---

## Model listing

**No model-listing endpoint is documented. Do not build against one.**

- `https://docs.z.ai/openapi.json` contains **no `/models` path** — the complete path list is in
  the Endpoints section above.
- `https://docs.z.ai/llms.txt` has **no "List Models" API reference page.**
- Live probe of `/api/paas/v4/models` returns 401, but so does a deliberately nonsense path, so the
  401 is a gateway auth interceptor and is **not** evidence the endpoint exists.

**Treat the model list as a static, hand-maintained constant in our code.** The authoritative
machine-readable source of valid IDs is the `model` enum in
`https://docs.z.ai/openapi.json` → `components.schemas.ChatCompletionTextRequest.properties.model.enum`.
Re-check that enum when refreshing model support rather than calling an API.

---

## Models

From the OpenAPI `model` enum (https://docs.z.ai/openapi.json) cross-checked against
https://docs.z.ai/guides/overview/overview and https://docs.z.ai/guides/overview/pricing.

**Text models (exact IDs, as documented today 2026-08-18):**
`glm-5.3`, `glm-5.2`, `glm-5.1`, `glm-5-turbo`, `glm-5`, `glm-4.7`, `glm-4.7-flash`,
`glm-4.7-flashx`, `glm-4.6`, `glm-4.5`, `glm-4.5-air`, `glm-4.5-x`, `glm-4.5-airx`,
`glm-4.5-flash`, `glm-4-32b-0414-128k`

**Vision models:** `glm-5v-turbo`, `glm-4.6v`, `glm-4.6v-flash`, `glm-4.6v-flashx`, `glm-4.5v`,
`autoglm-phone-multilingual`

### (a) Recommended for general chat

**`glm-5.3`** — current flagship and the schema default. 1M context. Documented as
"Claude Fable 5-level coding and agent capabilities." Thinking cannot be disabled; control cost and
latency via `reasoning_effort` (`minimal` / `none` at the low end).
(https://docs.z.ai/guides/overview/overview , https://docs.z.ai/guides/llm/glm-5.3)

Cheaper general-chat alternative: **`glm-4.7`** — 200K context, 128K max output, ~2.3× cheaper.
(https://docs.z.ai/guides/llm/glm-4.7)

### (b) Recommended for cheap/fast classification

**`glm-4.7-flash` — free, 200K context, 128K max output.** Positioned as "Lightweight, Completely
Free," a 30B-class model. This is the obvious default for intent classification / routing.
(https://docs.z.ai/guides/llm/glm-4.7 , https://docs.z.ai/guides/overview/pricing)

**`glm-4.7-flashx`** — the paid, higher-throughput sibling ($0.07 in / $0.40 out per 1M). Use if the
free tier's concurrency limit becomes the bottleneck. Also `glm-4.5-flash` (free, legacy) and
`glm-4.5-air` ($0.20 / $1.10, 128K).

**Critical caveat for classification:** GLM-4.7 "will think compulsorily" when `thinking` is
enabled, and `thinking` defaults to enabled. **Always send `"thinking": {"type": "disabled"}` on
classification calls** or you pay latency and output tokens for a reasoning trace you discard.
Pair with `"response_format": {"type": "json_object"}` and a small `max_tokens`.

---

## Capabilities

| Capability | Parameter | Verdict | Source |
|---|---|---|---|
| Streaming | `stream: true` | SSE, `data: [DONE]` sentinel, `delta.content` / `delta.reasoning_content`, usage in final chunk | https://docs.z.ai/guides/capabilities/streaming |
| Tool calling | `tools[]`, `tool_choice` | OpenAI-shaped tool defs and `role:"tool"` + `tool_call_id` results. **`tool_choice` accepts only `"auto"`** — no `"none"`, no `"required"`, no forced-function object. Max 128 functions. | https://docs.z.ai/guides/capabilities/function-calling , https://docs.z.ai/openapi.json |
| Streaming tool calls | `tool_stream: true` | Z.AI-specific opt-in. Supported on GLM-5.3/5.2/5.1/5/5-Turbo/4.7/4.6 only. | https://docs.z.ai/guides/tools/stream-tool |
| Structured output | `response_format: {"type":"json_object"}` | **JSON mode only. `json_schema` is NOT supported.** Docs list glm-5, glm-4.7, glm-4.6, glm-4.5 as supporting it. If you need a guaranteed schema you must validate client-side and retry. | https://docs.z.ai/guides/capabilities/struct-output , https://docs.z.ai/openapi.json |
| Reasoning / thinking | `thinking: {...}`, `reasoning_effort` | Z.AI-specific. See Request shape. | https://docs.z.ai/guides/capabilities/thinking-mode |
| Prompt caching | automatic | Surfaced as `usage.prompt_tokens_details.cached_tokens`; discounted "cached input" pricing. Pricing page notes cached-input storage is "Limited-time Free." | https://docs.z.ai/guides/capabilities/cache |
| Built-in web search | `tools: [{"type":"web_search",...}]` | Provider-side tool, billed $0.01/use | https://docs.z.ai/guides/tools/web-search |
| Vision / multimodal | `content[]` with `image_url` / `video_url` / `file_url` | Vision models only | https://docs.z.ai/openapi.json |
| Embeddings | — | **Not offered on Z.AI international.** Only on the China BigModel platform (`embedding-3`, `embedding-2`). | https://docs.bigmodel.cn/llms.txt |
| Logprobs / seed / n / presence_penalty / frequency_penalty | — | **Not documented.** Assume unsupported. | https://docs.z.ai/openapi.json |

---

## Usage/tokens

Field names (OpenAI-compatible plus one nested extra):

```
usage.prompt_tokens                          // integer, input tokens
usage.completion_tokens                      // integer, output tokens
usage.total_tokens                           // integer
usage.prompt_tokens_details.cached_tokens    // integer, cache-hit input tokens
```

Notes:
- There is **no `completion_tokens_details`** and therefore **no separate reasoning-token count**,
  even though `reasoning_content` is returned. Reasoning tokens appear to be folded into
  `completion_tokens`. (UNKNOWN whether that is exactly the billing behaviour — not documented.)
- Streaming includes `usage` in the final chunk without needing `stream_options`.
- Server-side token counting is available at `POST /api/paas/v4/tokenizer`.

Source: https://docs.z.ai/openapi.json (`ChatCompletionResponse.usage`),
https://docs.z.ai/api-reference/llm/chat-completion

---

## Errors

**HTTP statuses used:** 400, 401, 403, 429, 500.
(https://docs.z.ai/api-reference/api-code)

**Error body — note the documented shape and the spec shape disagree.**

Documented on the errors page, and confirmed by live probe:
```json
{"error": {"code": "1001", "message": "Authentication parameter not received in Header, unable to authenticate"}}
```
`code` is a **string** here.

But the OpenAPI `Error` schema is *flat* and types `code` as an **integer**:
```json
{"required": ["code","message"], "properties": {"code": {"type":"integer","format":"int32"}, "message": {"type":"string"}}}
```

I also observed a *third* shape from a non-existent route:
`{"code":500,"msg":"404 NOT_FOUND","success":false}` — flat, with `msg` not `message`, returned
with **HTTP 200**.

**Implication for our adapter: parse defensively.** Read `error.code` or `code`; coerce to string;
fall back to `error.message` or `message` or `msg`. Do not trust HTTP status alone — check for an
error body even on 200.

**Documented error codes** (https://docs.z.ai/api-reference/api-code):

| Code | HTTP | Meaning | Retry? |
|---|---|---|---|
| — | 500 | Internal error | yes, backoff |
| 1000 | 401 | Authentication failed | **no** |
| 1001 | 401 | Auth parameter not received in header | **no** |
| 1003 | 401 | Auth token expired, regenerate | **no** |
| 1005 | 401 | Two-factor authentication needed | **no** |
| 1113 | 429 | Insufficient balance / resource package | **no** — surface to operator |
| 1200 | 500 | API call error | yes |
| 1210 | 400 | Invalid API parameter | **no** |
| 1211 | 400 | Unknown model | **no** |
| 1212 | 400 | Model does not support this call method | **no** |
| 1213 | 400 | Required parameter missing | **no** |
| 1214 | 400 | Parameter invalid | **no** |
| 1215 | 400 | Conflicting parameters set simultaneously | **no** |
| 1220 | 403 | Permission denied | **no** |
| 1221 | 400 | API taken offline | **no** |
| 1222 | 400 | API does not exist | **no** |
| 1230 | 500 | API call process error | yes |
| 1234 | 500 | Network error (error id provided) | yes |
| 1261 | 400 | Prompt exceeds length limit | **no** |
| 1301 | 400 | Unsafe / sensitive content detected | **no** — content moderation |
| 1302 | 429 | Rate limit exceeded | **yes, backoff** |
| 1305 | 429 | Service temporarily overloaded | **yes, backoff** |
| 1308 | 429 | Usage limit reached (reset time provided) | no — wait for reset |
| 1309 | 429 | GLM Coding Plan package expired | **no** |
| 1310 | 429 | Weekly/monthly limit exhausted | **no** |
| 1311 | 429 | Subscription does not include this model | **no** |
| 1313 | 429 | Fair Usage Policy violation, frequency limited | backoff |
| 1314 | 429 | Enterprise package expired | **no** |
| 1315 | 429 | API key limited to enterprise coding scenarios | **no** |
| 1316–1321 | 429 | Various usage-limit and spending-cap scenarios | **no** |

**Design note:** 429 is heavily overloaded here. Only **1302** and **1305** (and arguably 1313) are
genuinely retryable. Codes 1113 and 1308–1321 are billing/quota exhaustion and must **not** be
retried — a blind "retry all 429s" policy will hammer the API pointlessly. **Branch on the numeric
code, not the HTTP status.**

Content moderation (**1301**) is a real operational consideration for a Persian-language chatbot —
GLM applies safety filtering and can also terminate a generation with
`finish_reason: "sensitive"`. Handle both paths.

---

## Rate limits

**Largely UNKNOWN — the documentation does not publish numbers.**

`https://docs.z.ai/api-reference/rate-limit` issues a **307 redirect to
`https://z.ai/manage-apikey/rate-limits`**, which is a client-side-rendered console page behind
login. Fetching it returns only an Ant Design SPA shell with no rate-limit content. So:

- **Concurrency limits: UNKNOWN** (per-key, configured in the console; the console is where the
  actual number lives)
- **RPM: UNKNOWN**
- **TPM: UNKNOWN**
- **Tier structure: UNKNOWN**
- **Per-model limits: UNKNOWN**
- **Rate-limit response headers: UNKNOWN** — no `x-ratelimit-*` headers are documented anywhere
- **Request timeout: UNKNOWN** — no documented server-side timeout

What *is* documented:
- Rate limiting is enforced primarily as a **concurrency** cap per API key, configured per-key in
  the console (the redirect target is literally the key-management rate-limits page).
- Free models (`glm-4.7-flash`, `glm-4.5-flash`) are constrained by concurrency rather than by a
  token quota — see the free-model entries in https://docs.z.ai/guides/overview/pricing.
- **Retry guidance:** the HTTP guide's best-practices section explicitly recommends
  "implementing retry logic with **exponential backoff**." That is the only official retry
  guidance found. (https://docs.z.ai/guides/develop/http/introduction)

**Recommendation:** treat concurrency as the binding constraint. Implement a client-side
concurrency semaphore (start conservative, e.g. 5) plus exponential backoff on codes 1302/1305,
and set our own client timeout (suggest 60s non-streaming, longer for `reasoning_effort: max`
which on `glm-5.3` can run long). Read the real concurrency number off the console before load
testing.

---

## Pricing

Page: **https://docs.z.ai/guides/overview/pricing**

### CURRENCY: **USD** on the Z.AI international platform.

This is explicit on the international pricing page. The China BigModel platform prices in **CNY
(元)** — do not mix the two tables. I could not load a BigModel pricing page (the obvious URL
404s and it is not listed in `docs.bigmodel.cn/llms.txt`), so **exact CNY figures are UNKNOWN**;
they are shown in the BigModel console.

Text models, **USD per 1M tokens**, as of 2026-08-18:

| Model | Input | Cached input | Output |
|---|---|---|---|
| GLM-5.3 | $1.40 | $0.26 | $4.40 |
| GLM-5.2 | $1.40 | $0.26 | $4.40 |
| GLM-5.1 | $1.40 | $0.26 | $4.40 |
| GLM-5 | $1.00 | $0.20 | $3.20 |
| GLM-5-Turbo | $1.20 | $0.24 | $4.00 |
| GLM-4.7 | $0.60 | $0.11 | $2.20 |
| **GLM-4.7-FlashX** | **$0.07** | $0.01 | **$0.40** |
| GLM-4.6 | $0.60 | $0.11 | $2.20 |
| GLM-4.5 | $0.60 | $0.11 | $2.20 |
| GLM-4.5-X | $2.20 | $0.45 | $8.90 |
| GLM-4.5-Air | $0.20 | $0.03 | $1.10 |
| GLM-4.5-AirX | $1.10 | $0.22 | $4.50 |
| GLM-4-32B-0414-128K | $0.10 | — | $0.10 |
| **GLM-4.7-Flash** | **Free** | Free | **Free** |
| **GLM-4.5-Flash** | **Free** | Free | **Free** |

Vision (USD / 1M tokens): GLM-5V-Turbo $1.20/$4.00 · GLM-4.6V $0.30/$0.90 · GLM-4.6V-FlashX
$0.04/$0.40 · GLM-4.5V $0.60/$1.80 · GLM-OCR $0.03/$0.03 · GLM-4.6V-Flash **Free**.

Other: Web Search $0.01/use · GLM-ASR-2512 $0.03 per 1M tokens · Translation Agent $3.00 per 1M
tokens · GLM-Image $0.015/image · CogView-4 $0.01/image · CogVideoX-3 $0.20/video.

The pricing page notes cached-input **storage** is currently "Limited-time Free" — i.e. the cached
rates above may be promotional. **Re-verify before basing a budget on them.**

---

## Health/test-connection strategy

**There is no `/models` endpoint, so the usual "list models" health check is unavailable.**

Recommended connection test — a minimal, near-zero-cost chat completion:

```bash
curl -sS -X POST 'https://api.z.ai/api/paas/v4/chat/completions' \
  -H 'Authorization: Bearer $ZAI_API_KEY' \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "glm-4.7-flash",
    "messages": [{"role":"user","content":"ping"}],
    "thinking": {"type": "disabled"},
    "max_tokens": 1,
    "stream": false
  }'
```

Why this shape:
- **`glm-4.7-flash` is free**, so health checks cost nothing.
- `thinking: disabled` keeps it fast (4.7 thinks compulsorily otherwise).
- `max_tokens: 1` bounds the response.

Interpreting the result:
- **HTTP 200 with `choices[]`** → key valid, network fine, quota fine.
- **HTTP 200 with a body containing `code`/`msg`** → routing/path error. Always inspect the body.
- **401 + code 1000/1001/1003/1005** → bad or missing key. Surface as "invalid credentials",
  never retry.
- **403 + code 1220** → key lacks permission.
- **429 + code 1113 / 1308–1321** → billing or quota problem, **not** a transient rate limit.
  Surface distinctly to the operator.
- **429 + code 1302/1305** → transient; the service is up. A health check should arguably report
  *healthy-but-throttled*.
- **400 + code 1211** → the model ID we hardcoded no longer exists. **This is our early-warning
  signal that the static model list has drifted**, since there is no listing endpoint to diff
  against. Log it loudly.

Also worth wiring: send a `request_id` on every call (6–64 chars) so our logs correlate with
Z.AI-side support requests.

---

## Unknowns

Explicitly **not** verified from official sources:

1. **Official confirmation that Z.AI and Zhipu AI/BigModel are the same company.** Strong
   structural evidence (shared CDN, identical URL grammar, identical error codes, identical model
   IDs), but no page on either site states the relationship. See Platform identity.
2. **All rate-limit numbers** — concurrency, RPM, TPM, tiers, per-model caps. The docs page
   redirects to a login-gated JS console. Must be read from the console.
3. **Rate-limit response headers** — no `x-ratelimit-*` headers documented; existence unknown.
4. **Server-side request timeout** — not documented.
5. **CNY prices on the BigModel/China platform** — the pricing page is not in the BigModel docs
   index and the obvious URL 404s. Currency is CNY; figures unknown.
6. **Whether reasoning tokens are billed as output tokens.** `reasoning_content` is returned but
   there is no `completion_tokens_details.reasoning_tokens` field; the accounting is unstated.
7. **Whether `/paas/v4/models` exists undocumented.** The 401 probe is inconclusive because the
   auth gateway 401s unknown paths identically. Not in the OpenAPI spec; treat as absent.
8. **The authoritative error body shape.** The errors page (`{"error":{"code":"<string>",...}}`)
   and the OpenAPI `Error` schema (`{"code":<int>,"message":...}`) disagree. Live probes match the
   errors page for the auth path; other paths returned a third shape.
9. **The full list of "differences" from OpenAI.** The compat page says only: "In some scenarios,
   there are still differences between Z.AI and OpenAI interfaces, but this does not affect overall
   compatibility." No enumeration is published. The gaps in the verdict below are ones I derived
   from the schema, not ones Z.AI documented.
10. **Whether the Anthropic-compatible endpoint (`https://api.z.ai/api/anthropic`) works with
    pay-as-you-go API keys or only Coding Plan subscriptions.** It is documented only in the
    DevPack/Coding Plan section.
11. **SLA / uptime guarantees** — not found.
12. **Data residency for the international platform** — Singapore entity under Singapore law, but
    where inference actually runs is not stated. Relevant if this project has data-residency
    requirements.

---

## OpenAI-compatibility verdict

# **OPENAI-COMPATIBLE + PROVIDER-SPECIFIC METADATA**

**Justification.**

Officially documented compatibility is real and first-class, not a bolt-on: Z.AI publishes guides
for the OpenAI Python, Node.js and Java SDKs, and — critically — the OpenAI-compatible base URL is
**the same URL as the native one** (`https://api.z.ai/api/paas/v4/`). There is no separate
compatibility shim to route around. The native wire format *is* the OpenAI chat-completions format.
(https://docs.z.ai/guides/develop/openai/python , https://docs.z.ai/api-reference/introduction)

Everything a standard OpenAI client needs is present and identically named: `model`, `messages`
(with `system`/`user`/`assistant`/`tool` roles), `stream`, `temperature`, `top_p`, `max_tokens`,
`stop`, `tools` with the exact OpenAI function schema, `tool_choice`, `response_format`,
`tool_call_id` on tool results, `choices[].message.tool_calls[].function.arguments` as a JSON
string, SSE streaming with `choices[0].delta.content` and a `data: [DONE]` sentinel, and
`usage.{prompt_tokens,completion_tokens,total_tokens}`. A generic OpenAI client will work.

So **NATIVE ADAPTER REQUIRED is wrong** — writing a bespoke HTTP client would duplicate the OpenAI
SDK for no benefit.

But **OPENAI-COMPATIBLE SUFFICIENT is also wrong**, because a plain OpenAI client leaves real
capability on the table and will mis-handle real failures. The provider-specific surface that
actually matters to us:

*Request-side extras with no OpenAI equivalent* — `thinking` (and its `clear_thinking`),
`reasoning_effort`, `do_sample`, `tool_stream`, `request_id`, `user_id`. `thinking` is not optional
polish: it defaults to **enabled**, and on the GLM-4.7 family the model then **thinks
compulsorily**. A naive OpenAI-shaped classification call silently pays latency and output tokens
for a discarded reasoning trace. We must be able to pass `thinking` through.

*Response-side extras* — `reasoning_content` (per-message and per-delta), top-level `request_id`
and `web_search`, `usage.prompt_tokens_details.cached_tokens`. Typed OpenAI SDK models will drop
these silently.

*Documented behavioural narrowings* — `temperature` is `[0.0, 1.0]`, not OpenAI's `[0, 2]`;
`tool_choice` accepts **only `"auto"`** (no `none`, `required`, or forced-function);
`response_format` supports `json_object` but **not `json_schema`**, so no schema-guaranteed
structured output; `stop` is effectively one string despite `maxItems: 4`; `do_sample: false`
(greedy) is documented as not usable through the OpenAI SDK path.

*Missing surface* — **no `/v1/models` listing endpoint**, so model discovery must be a static
constant; no `logprobs`, `seed`, `n`, or penalty parameters; no embeddings on the international
platform.

*A genuinely divergent error model* — the error body is `{"error":{"code":"1302","message":"..."}}`
with **numeric GLM codes, not OpenAI `type`/`param` fields**, and the 429 status is overloaded
across transient throttling (1302, 1305) and hard billing exhaustion (1113, 1308–1321). An OpenAI
client's built-in "retry on 429" will retry unretryable quota errors forever. `finish_reason` also
carries non-OpenAI values (`sensitive`, `model_context_window_exceeded`, `network_error`) that our
moderation and truncation handling must recognise.

**Practical recommendation:** use an OpenAI-compatible transport pointed at
`https://api.z.ai/api/paas/v4/`, wrapped in a thin Z.AI provider layer that (1) injects
`thinking`/`reasoning_effort`/`request_id`, (2) reads `reasoning_content` and
`prompt_tokens_details.cached_tokens` off the raw response, (3) classifies errors by the numeric
GLM code rather than HTTP status, and (4) holds the model ID list as a maintained constant.
