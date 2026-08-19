# Alibaba Qwen / DashScope

Research date: **2026-08-18**. All facts below come from `alibabacloud.com/help/en/model-studio/*`
(international documentation site) or `help.aliyun.com/en/model-studio/*` (China documentation site,
English). Anything not found on an official page is marked **UNKNOWN**.

---

## Platform and region identity

**The product is called "Alibaba Cloud Model Studio."** In the Chinese market the same product is
branded **Bailian** (百炼) — the international text-generation model page still leaks the phrase
"an equivalent Bailian model". **DashScope is not a separate product.** DashScope is:

1. the **legacy shared domain family** (`dashscope.aliyuncs.com`, `dashscope-intl.aliyuncs.com`, …),
2. the **name of the native (non-OpenAI) wire protocol** (`/api/v1/services/aigc/...`),
3. the **SDK / env-var name** (`DASHSCOPE_API_KEY`, `dashscope` Python package).

So "Qwen", "DashScope" and "Model Studio" are not three products. They are: the model family, the
API/domain, and the platform.

### Three protocols, offered simultaneously on every domain

| Protocol | Path suffix |
|---|---|
| OpenAI-compatible | `/compatible-mode/v1` (chat: `/chat/completions`, responses: `/responses`) |
| Anthropic-compatible | `/apps/anthropic` |
| DashScope native | `/api/v1` (chat: `/services/aigc/text-generation/generation`) |

Source: <https://www.alibabacloud.com/help/en/model-studio/base-url> (Last Updated Jul 01, 2026) and
<https://www.alibabacloud.com/help/en/model-studio/models>

### Three domain *families* per region — this is the part that trips people up

There is not one base URL per region. There are **three kinds of access domain**, and they differ in
timeout, rate limit, auth scope, and SLA:

| Comparison | Workspace-dedicated (**recommended**) | DashScope shared (existing/legacy) | Trial |
|---|---|---|---|
| Domain format | `{WorkspaceId}.{region}.maas.aliyuncs.com` | `dashscope-intl.aliyuncs.com` (Singapore example) | `trial.{region}.maas.aliyuncs.com` |
| Use case | Production | Existing integrations; migration recommended | Quick trial; **not for production** |
| Auth scope | Current workspace only | All workspaces in the region | All workspaces in the region |
| Rate limits | Per model | Per model | RPM 1000; TPM per model |
| **Request timeout** | **3600 s** | **600 s** | **600 s** |
| Protocols | HTTP, SSE, WebSocket, WebRTC | HTTP, SSE, WebSocket | HTTP, SSE |
| SLA | 99.9% | 99.9% | Not provided |

Source: <https://www.alibabacloud.com/help/en/model-studio/regions/> (Last Updated Jun 30, 2026)

Official statement on the shared domain: *"the existing centralized shared domain. It remains
available, but migrating to a workspace-dedicated domain is recommended."* There is **no published
sunset date** for `dashscope-intl.aliyuncs.com` as of this research.

### Region → base URL table (OpenAI-compatible mode)

| Region | Region ID | Workspace-dedicated (recommended) | DashScope shared domain | Trial domain |
|---|---|---|---|---|
| Singapore | `ap-southeast-1` | `https://{WorkspaceId}.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1` | `https://dashscope-intl.aliyuncs.com/compatible-mode/v1` | `https://trial.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1` |
| China (Beijing) | `cn-beijing` | `https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/compatible-mode/v1` | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `https://trial.cn-beijing.maas.aliyuncs.com/compatible-mode/v1` |
| China (Hong Kong) | `cn-hongkong` | `https://{WorkspaceId}.cn-hongkong.maas.aliyuncs.com/compatible-mode/v1` | `https://cn-hongkong.dashscope.aliyuncs.com/compatible-mode/v1` | `https://trial.cn-hongkong.maas.aliyuncs.com/compatible-mode/v1` |
| US (Virginia) | `us-east-1` | Not yet supported | `https://dashscope-us.aliyuncs.com/compatible-mode/v1` | Not yet supported |
| Japan (Tokyo) | `ap-northeast-1` | `https://{WorkspaceId}.ap-northeast-1.maas.aliyuncs.com/compatible-mode/v1` | Not supported | Not yet supported |
| Germany (Frankfurt) | `eu-central-1` | `https://{WorkspaceId}.eu-central-1.maas.aliyuncs.com/compatible-mode/v1` | Not supported | Not yet supported |

Native DashScope mode uses the **same host** with `/api/v1` instead of `/compatible-mode/v1`.
The migration guide states it explicitly: replace `https://dashscope.aliyuncs.com/api/v1` with
`https://llm-xxx.cn-beijing.maas.aliyuncs.com/api/v1`.

Sources: <https://www.alibabacloud.com/help/en/model-studio/base-url>,
<https://www.alibabacloud.com/help/en/model-studio/regions/>

### Region vs "service deployment scope" — a second, separate axis

Region = **where the request lands and data is stored**. Service deployment scope = **where inference
actually executes**. Pricing and rate-limit tables are keyed on the *scope* column, not the region.

| Requirement | Region | Deployment scope |
|---|---|---|
| Data must not pass through the Chinese mainland | **Singapore** | **International** (global nodes excluding Chinese mainland) |
| Data must stay within the Chinese mainland | China (Beijing) | Chinese mainland |
| No data-residency restriction, max resource pool | US (Virginia) / Frankfurt / Tokyo / Hong Kong | Global (nodes inside *and* outside China) |
| Data must stay in EU / US / Japan / HK | Frankfurt / US (Virginia) / Tokyo / Hong Kong | EU / United States / Japan / Hong Kong |

Beijing and Singapore each support exactly **one** scope, so no scope selection is needed there.
Frankfurt / Tokyo / Hong Kong use *workspaces* to pick the scope. In US (Virginia), the scope is
selected by **model ID suffix**: `qwen-plus-us` restricts inference to the US; without `-us` it
defaults to Global.

Source: <https://www.alibabacloud.com/help/en/model-studio/regions/>

### Which region should an international developer use?

**Singapore (`ap-southeast-1`), deployment scope "International".** Reasons, all from the docs:

- It is the only non-China region whose deployment scope is documented as *"global nodes excluding
  the Chinese mainland"* — i.e. data provably does not transit mainland China. Every other non-China
  region defaults to "Global (any available node, **including within** … China)" unless you pick a
  narrower scope.
- It is the region every international quickstart and API-reference example uses.
- It has the widest feature support of the international regions: real-time inference, **batch
  inference**, playground, advanced monitoring, alerting, **fine-tuning** — Frankfurt, Tokyo, HK and
  US (Virginia) each lack several of these.
- It supports all three domain families (workspace-dedicated, shared `dashscope-intl`, trial).
  Frankfurt and Tokyo have **no** shared DashScope domain at all; US (Virginia) has **no**
  workspace-dedicated domain.

Use Frankfurt (`eu-central-1`) instead only if EU data residency is a hard requirement — and accept
losing batch inference, fine-tuning, alerting, advanced monitoring, and the shared domain.

### Hard constraints to encode in the adapter

- **"API Keys are independent across regions and cannot be used across regions."**
- **"A Base URL must be used together with an API Key from the same billing plan; otherwise, a 401
  error occurs."**
- **"Each region has its own access domain, API Key, and model list. These cannot be used across
  regions."**
- Workspace-dedicated domains accept **only** an API key belonging to that workspace. Shared and
  trial domains accept any key from the same region.
- **Model availability genuinely differs by region.** Concrete evidence found today: the
  international docs list `qwen3.7-max` / `qwen3.7-plus` / `qwen3.6-flash` as the current Qwen text
  models, while the China docs site lists `qwen3.8-max` / `qwen3.7-plus` / `qwen3.7-flash`. The China
  region ships new models first.

### Two plan-specific endpoints that are NOT for backend services

The docs carve these out explicitly — *"For interactive use in AI coding tools such as Claude Code
and Codex only; not for backend services."* Their keys start with `sk-sp-` and must not be mixed with
general keys/base URLs:

- Coding Plan (Singapore): `https://coding-intl.dashscope.aliyuncs.com/v1`
- Token Plan (Singapore): `https://token-plan.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1`

Our adapter must **not** offer these. Source:
<https://www.alibabacloud.com/help/en/model-studio/base-url>,
<https://www.alibabacloud.com/help/en/model-studio/error-code>

---

## Sources

| Topic | URL | Doc "Last Updated" |
|---|---|---|
| Base URL overview (authoritative region table) | <https://www.alibabacloud.com/help/en/model-studio/base-url> | Jul 01, 2026 |
| Region / deployment scope / access domain | <https://www.alibabacloud.com/help/en/model-studio/regions/> | Jun 30, 2026 |
| What is Model Studio | <https://www.alibabacloud.com/help/en/model-studio/what-is-model-studio> | — |
| First API call to Qwen (quickstart) | <https://www.alibabacloud.com/help/en/model-studio/first-api-call-to-qwen> | — |
| Call Qwen models via OpenAI API | <https://www.alibabacloud.com/help/en/model-studio/compatibility-of-openai-with-dashscope> | — |
| OpenAI compatible — Chat (full API reference) | <https://www.alibabacloud.com/help/en/model-studio/qwen-api-via-openai-chat-completions> | — |
| OpenAI compatible — Responses API | <https://www.alibabacloud.com/help/en/model-studio/qwen-api-via-openai-responses> | — |
| DashScope native API reference | <https://www.alibabacloud.com/help/en/model-studio/qwen-api-via-dashscope> | — |
| Supported models & capabilities overview | <https://www.alibabacloud.com/help/en/model-studio/models> | — |
| Text generation models (context/capability matrix) | <https://www.alibabacloud.com/help/en/model-studio/text-generation-model/> | — |
| Model pricing | <https://www.alibabacloud.com/help/en/model-studio/model-pricing> | — |
| Rate limiting | <https://www.alibabacloud.com/help/en/model-studio/rate-limit> | — |
| Error codes | <https://www.alibabacloud.com/help/en/model-studio/error-code> | Jul 13, 2026 |
| Model decommissioning policy | <https://www.alibabacloud.com/help/en/model-studio/model-depreciation> | Jul 08, 2026 |
| Structured output / JSON (international) | <https://www.alibabacloud.com/help/en/model-studio/qwen-structured-output> | — |
| Structured output / JSON (China site, has JSON Schema) | <https://help.aliyun.com/en/model-studio/qwen-structured-output> | — |
| Models list (China site — newer model IDs) | <https://help.aliyun.com/en/model-studio/models> | — |

---

## Auth

- **Header (both modes):** `Authorization: Bearer $DASHSCOPE_API_KEY`
- **Content type:** `Content-Type: application/json`
- **Key format:** general keys start with `sk-`. Coding Plan / Token Plan Team Edition keys start
  with `sk-sp-` and are bound to their own base URL (mixing produces a 401).
- **Provisioning:** create an Alibaba Cloud account → activate Model Studio and accept the ToS → go
  to the API Key page in the console → *Create API key*. For workspace-dedicated domains you also
  need the **Workspace ID**, obtainable from the popup shown after key creation (copy the *API Host*)
  or from the *API Host* column on the Workspace Management page.
- Environment variable convention used throughout the docs: `DASHSCOPE_API_KEY`.
- Keys are per-region and per-billing-plan; wrong pairing → HTTP 401.

Sources: `first-api-call-to-qwen`, `base-url`, `regions/`, `error-code`.

---

## Endpoints

Let `BASE` be the region's host (see region table above).

| Purpose | Mode | Method + path |
|---|---|---|
| Chat completion | OpenAI-compatible | `POST {BASE}/compatible-mode/v1/chat/completions` |
| Responses API | OpenAI-compatible | `POST {BASE}/compatible-mode/v1/responses` |
| Text generation | DashScope native | `POST {BASE}/api/v1/services/aigc/text-generation/generation` |
| Multimodal generation | DashScope native | `POST {BASE}/api/v1/services/aigc/multimodal-generation/generation` |
| Anthropic-compatible messages | Anthropic-compatible | `POST {BASE}/apps/anthropic` (+ Anthropic path) |

Deprecated Responses path noted in the docs: `/api/v2/apps/protocols/compatible-mode/v1/responses`.

Concrete Singapore examples:

```
POST https://dashscope-intl.aliyuncs.com/compatible-mode/v1/chat/completions
POST https://dashscope-intl.aliyuncs.com/api/v1/services/aigc/text-generation/generation
POST https://{WorkspaceId}.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1/chat/completions
POST https://{WorkspaceId}.ap-southeast-1.maas.aliyuncs.com/api/v1/services/aigc/text-generation/generation
```

---

## Request shape

### OpenAI-compatible mode

Standard OpenAI Chat Completions body. Documented parameters (from `qwen-api-via-openai-chat-completions`):

Core: `model`, `messages`, `stream`, `stream_options`, `temperature`, `top_p`, `top_k`,
`max_tokens`, `max_completion_tokens`, `stop`, `response_format`, `seed`, `n`, `presence_penalty`,
`repetition_penalty`, `logprobs`, `top_logprobs`.

Tooling / reasoning: `tools`, `tool_choice`, `parallel_tool_calls`, `tool_stream`, `enable_thinking`,
`thinking_budget`, `reasoning_effort`, `preserve_thinking`, `thinking`, `clear_thinking`.

Provider extras: `enable_search`, `search_options`, `enable_code_interpreter`, `modalities`, `audio`,
`vl_high_resolution_images`, `min_pixels`, `max_pixels`, `total_pixels`, `translation_options`.

```bash
curl -X POST https://dashscope-intl.aliyuncs.com/compatible-mode/v1/chat/completions \
  -H "Authorization: Bearer $DASHSCOPE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"qwen3.7-plus","messages":[{"role":"system","content":"You are a helpful assistant."},{"role":"user","content":"Who are you?"}]}'
```

> Note: `top_k`, `repetition_penalty`, `vl_high_resolution_images`, `enable_search`, `search_options`
> are **not** OpenAI parameters — when using the official OpenAI Python SDK they must be passed via
> `extra_body`.

### DashScope native mode

Different envelope: a top-level `input` object and a `parameters` object.

```json
{
  "model": "qwen3.7-plus",
  "input": {
    "messages": [
      {"role": "system", "content": "You are a helpful assistant."},
      {"role": "user",   "content": "Who are you?"}
    ]
  },
  "parameters": {
    "result_format": "message",
    "incremental_output": true,
    "enable_thinking": false,
    "thinking_budget": 0,
    "temperature": 0.7,
    "top_p": 0.8,
    "top_k": 50,
    "repetition_penalty": 1.0,
    "presence_penalty": 0.0,
    "max_tokens": 2048,
    "seed": 1234,
    "stream": true,
    "response_format": {"type": "json_object"},
    "tools": [{"type": "function", "function": {"name": "...", "description": "...", "parameters": {}}}],
    "tool_choice": "auto"
  }
}
```

Set `parameters.result_format = "message"` to get an OpenAI-like `output.choices[].message` instead
of the flat `output.text`.

Source: <https://www.alibabacloud.com/help/en/model-studio/qwen-api-via-dashscope>

---

## Response shape

### OpenAI-compatible mode

Standard OpenAI shape: `id`, `model`, `created`, `system_fingerprint`, `choices[]` (each with
`index`, `message` {`role`, `content`, optional `tool_calls`, optional `reasoning_content`},
`finish_reason`), `usage`.

`system_fingerprint` is documented as returning an **empty string** (currently unused).

Streaming: SSE chunks with `choices[].delta`; the final chunk carries `usage` only when
`stream_options: {"include_usage": true}` is set.

### DashScope native mode

```json
{
  "status_code": 200,
  "request_id": "…",
  "code": "",
  "message": "",
  "output": {
    "text": null,
    "finish_reason": "stop",
    "choices": [
      {"finish_reason": "stop", "message": {"role": "assistant", "content": "…"}}
    ]
  },
  "usage": {"input_tokens": 26, "output_tokens": 66, "total_tokens": 92}
}
```

`output.text` is populated when `result_format` is `text`; `output.choices` when it is `message`.
`code`/`message` are empty strings on success — the same fields carry the error on failure.

---

## Model listing

**No model-listing endpoint is documented for either mode.** Neither the OpenAI-compatibility pages,
the Chat reference, the Responses reference, nor the DashScope native reference documents
`GET /compatible-mode/v1/models` or any DashScope equivalent. The docs route model discovery to the
**console** ("Available Models" links per region on the regions page) and to the static Models page.

- Does `GET {BASE}/compatible-mode/v1/models` work in practice? **UNKNOWN** — undocumented, untested,
  must not be relied upon.
- Consequence for us: **the Admin form cannot populate a model dropdown from the API.** Ship a
  curated, region-aware model list in config plus a free-text override field.

---

## Models

### Currently recommended Qwen text models (international / Singapore docs)

| Model ID | Context | Thinking | Function calling | Built-in tools | Structured output |
|---|---|---|---|---|---|
| `qwen3.7-max` | 1M | Yes | Yes | Yes | Yes |
| `qwen3.7-plus` | 1M | Yes | Yes | Yes | Yes |
| `qwen3.6-flash` | 1M | Yes | Yes | Yes | Yes |

Official positioning from `text-generation-model/`: *"Start with `qwen3.7-plus` for chatbots, content
generation, summarization, and document processing… To cut costs, switch to `qwen3.6-flash`… For the
strongest reasoning, use `qwen3.7-max`."* The same page maps them to closed-source peers:
`qwen3.7-max` ↔ highest capability tier; `qwen3.7-plus` ↔ balanced tier; `qwen3.6-flash` ↔
lightweight/low-cost tier.

**China region (help.aliyun.com) currently lists `qwen3.8-max`, `qwen3.7-plus`, `qwen3.7-flash`
instead.** Treat model IDs as region-scoped data, never as a global constant.

### Recommendation for our two roles

- **(a) General chat → `qwen3.7-plus`.** Documented as the balanced default; 1M context; full tool
  calling, built-in tools and structured output.
- **(b) Cheap/fast classification → `qwen3.6-flash`.** Documented low-cost/low-latency tier, 1M
  context, still supports structured output and function calling — both of which a classifier wants.
  If cost dominates over capability, the legacy `qwen-flash` is ~5× cheaper on input
  ($0.05 vs $0.25 per M) and still supports structured output — but it is **Legacy** (see below).

### Naming conventions

- **Commercial / closed-source:** `qwen{generation}-{tier}`, tiers `-max` > `-plus` > `-flash`.
  The old `-turbo` tier has been superseded by `-flash`.
- **Legacy un-versioned aliases:** `qwen-max`, `qwen-plus`, `qwen-flash`, `qwen-turbo` — the docs now
  group these under a heading literally called **"Legacy Qwen"**. They still resolve, are still
  priced, and each is *"currently equivalent to"* a dated snapshot (e.g. `qwen-plus` →
  `qwen-plus-2025-12-01`, `qwen-flash` → `qwen-flash-2025-07-28`). Also `qwen-plus-latest`,
  `qwen-max-latest` style aliases exist.
- **Open-source editions:** parameter-count IDs, e.g. `qwen3.5-397b-a17b`, `qwen3.5-122b-a10b`,
  `qwen3.5-35b-a3b`, `qwen3.5-27b`, `qwen3-235b-a22b`, `qwen2.5-*b-instruct`.
- **Snapshots:** `-YYYY-MM-DD` suffix, e.g. `qwen3.7-max-2026-06-08`, `qwen3.7-plus-2026-05-26`.
  Snapshots carry *much* lower default RPM than the mainline alias (60 vs 15,000 in some cases).
- **US-scope variants:** `-us` suffix (`qwen-plus-us`, `qwen3.7-max-us`) restrict inference to the US.

### Legacy models (documented, still callable)

| Model ID | Context | Thinking | Function calling | Built-in tools | Structured output |
|---|---|---|---|---|---|
| `qwen-plus` (+ snapshots) | 1M | Yes | Yes | Yes | Yes |
| `qwen-max` (+ snapshots) | 128k | No | Yes | Yes | Yes |
| `qwen-flash` (+ snapshots) | 1M | Yes | Yes | Yes | Yes |
| `qwen-turbo` (+ snapshots) | 1M | Yes | Yes | Yes | Yes |
| `qwen3.6-max-preview` | 256k | Yes | Yes | No | Yes |
| `qwen3.6-plus` | 1M | Yes | Yes | Yes | Yes |
| `qwen3.5-plus` / `qwen3.5-flash` | 1M | Yes | Yes | Yes | Yes |
| `qwen3-max` (+ snapshots) | 256k | Yes | Yes | Yes | Yes |
| `qwen-long` / `qwen-long-latest` | 10M | No | No | No | Yes |

Also documented: `qwen-mt-*` (translation, 16k, no tools), `qwen-*-character` (role-play), `qwq-plus`,
`qvq-max`, `qwen-omni-turbo`.

### Scheduled retirements

**October 10, 2026, 00:00:00** — mainline: `qwen3.6-max-preview`, `qwen3-max-preview`, `qwen3-max`,
`qwen3-vl-flash`, `qwen3-coder-plus`. Snapshots: `qwen3-max-2026-01-23`, `qwen3-max-2025-09-23`,
several `qwen3-vl-*`, `qwen3-coder-*`, and open-source `qwen3-8b` / `qwen3-14b` / `qwen3-30b-a3b`.
Replacements are `qwen3.7-max`, `qwen3.7-plus`, `qwen3.6-flash`.

Notice policy: **30 days** before sunset for snapshot models, **3 months** for mainline models.
Rate limits are throttled down starting at the notice date. `qwen-turbo` is **not** on the
international retirement list as of 2026-08-18.

---

## Capabilities

| Feature | OpenAI-compatible mode | DashScope native mode |
|---|---|---|
| Streaming | `stream: true`; usage in last chunk via `stream_options: {"include_usage": true}` | HTTP header `X-DashScope-SSE: enable` **and** `parameters.incremental_output: true` |
| Tool calling | `tools`, `tool_choice`, `parallel_tool_calls`, `tool_stream`; result in `choices[].message.tool_calls` | `parameters.tools`, `parameters.tool_choice` |
| JSON object mode | `response_format: {"type": "json_object"}` — **prompt must contain the word "json"** (case-insensitive) or the API errors | `parameters.response_format: {"type": "json_object"}` |
| JSON Schema mode | `response_format: {"type": "json_schema", "json_schema": {…, "strict": true}}` — no "json" keyword required | Not documented for native mode |
| Thinking / reasoning | `enable_thinking`, `thinking_budget`, `reasoning_effort`, `preserve_thinking` | `parameters.enable_thinking`, `parameters.thinking_budget` |
| Web search | `enable_search`, `search_options` (via `extra_body` in the OpenAI SDK) | `parameters.enable_search` |

**Documented behavioural constraints (all are footguns for a generic OpenAI adapter):**

- *"`tools` parameter cannot be used with `stream=True`"* (stated on `compatibility-of-openai-with-dashscope`).
- `n` is supported only by `qwen-plus` and is forced to 1 when `tools` is passed.
- `presence_penalty` is supported only by commercial Qwen models and open-source models from
  qwen1.5 onward.
- Thinking-mode models: `enable_thinking` **must be false for non-streaming calls**, otherwise 400.
- Native mode: when `enable_thinking` is true, `incremental_output` **must** be true.
- Do not set a system message for QwQ models; system messages have no effect on QVQ models.
- Structured output (both JSON modes) is documented as supported only in **non-thinking mode** for
  the Max/Plus/Flash/Turbo families.

**JSON Schema availability caveat:** the JSON Schema mode is documented on the **China** docs site
(`help.aliyun.com/en/model-studio/qwen-structured-output`) but the corresponding **international**
page (`alibabacloud.com/help/en/model-studio/qwen-structured-output`) contains no occurrence of
`json_schema` — only `json_object`. Whether `json_schema` is actually accepted on
`dashscope-intl.aliyuncs.com` is **UNKNOWN** and must be probed before we depend on it. Plan for
`json_object` + prompt-side schema as the portable path.

Parameter ranges enforced (from `error-code`): `temperature` ∈ [0.0, 2.0), `top_p` ∈ (0.0, 1.0],
`top_k` ≥ 0, `repetition_penalty` > 0.0, `presence_penalty` ∈ [-2.0, 2.0], `n` ∈ [1, 4],
`seed` ∈ [0, 9223372036854775807] (native mode).

---

## Usage/tokens

| Mode | Fields |
|---|---|
| OpenAI-compatible | `usage.prompt_tokens`, `usage.completion_tokens`, `usage.total_tokens` |
| OpenAI-compatible (detail) | `usage.prompt_tokens_details.cached_tokens`; output breakdowns include `reasoning_tokens`, `audio_tokens`; input breakdowns include `text_tokens`, `image_tokens`, `video_tokens` |
| DashScope native | `usage.input_tokens`, `usage.output_tokens`, `usage.total_tokens` |

The two modes use **different names for the same quantities** — normalise at the adapter boundary.
In streaming, `usage` is only emitted when `stream_options.include_usage` is true (OpenAI mode).

---

## Errors

### Body shapes

OpenAI-compatible mode returns the OpenAI error envelope:

```json
{"error": {"message": "…", "type": "…", "param": null, "code": "…"}}
```

DashScope native mode returns the flat envelope (same fields as the success envelope, now populated):

```json
{"code": "InvalidApiKey", "message": "Invalid API-key provided.", "request_id": "…"}
```

The native shape is directly corroborated by the native reference, which shows `status_code`,
`request_id`, `code`, `message` at the top level (empty on success). The OpenAI-mode envelope shape
is reported by the error-code page; treat the exact `type`/`param` fields as **medium confidence**
and parse defensively.

### Status codes and error codes

The error-code page names codes in a **dual form**: `DashScopeCode/openai_code`.

| HTTP | Code(s) | Meaning |
|---|---|---|
| 400 | `InvalidParameter` | Bad/missing parameter (huge sub-family: range violations, `enable_thinking` misuse, `incremental_output` misuse, input-length overflow) |
| 400 | `InvalidParameter.DataInspection`, `DataInspectionFailed` / `data_inspection_failed` | Content-safety block on input or output |
| 400 | `InvalidInputLength` | Input exceeds model max |
| 400 | `InvalidSchema` | Schema invalid |
| 400 | `Arrearage` | Account overdue |
| 400 | `APIConnectionError`, `ClientDisconnect`, `UnsupportedOperation` | Transport / unsupported call |
| 401 | `InvalidApiKey` / `invalid_api_key` | Key wrong, key/base-URL plan mismatch, or key from another region |
| 403 | `AccessDenied`, `AllocationQuota.*` | Not authorized / model needs approval / service not activated |
| 404 | `ModelNotFound` / `model_not_found` | Model does not exist, or not available on this region/endpoint |
| 404 | `WorkSpaceNotFound` | Bad WorkspaceId |
| 429 | `Throttling.RateQuota` / `LimitRequests` / `limit_requests` | RPM/RPS exceeded |
| 429 | `Throttling.AllocationQuota` | TPM/TPS quota exceeded |
| 429 | `CommodityNotPurchased`, `PostpaidBillOverdue`, `PrepaidBillOverdue` | Billing state |
| 500 | `InternalError` (and `AppProcessFailed`, `InvokePluginFailed`, …) | Server-side |
| 503 | `ModelUnavailable` | Model temporarily unavailable — *"Retry later."* |

Notable message strings worth matching on: `'messages' must contain the word 'json' in some form, to
use 'response_format' of type 'json_object'`, `parameter.enable_thinking must be set to false for
non-streaming calls`, `The incremental_output parameter must be "true" when enable_thinking is true`.

### Retry guidance

Documented behaviour: **retry** 429 (*"Wait a few minutes and retry. The rate limit will be lifted
automatically"*; *"Recovery usually occurs within one minute"*), 500, 503, and connection timeouts.
**Do not retry** 400 / 401 / 403 / 404 without changing the request. Recommended pacing: *"use uniform
scheduling, exponential backoff, or a request queue to distribute requests evenly and avoid sudden
peaks."*

---

## Rate limits

Defined per model **and per deployment scope**, as RPM (requests/min) and TPM (tokens/min, input +
output combined). *"The service may also enforce limits based on requests per second (RPS = RPM/60)
and tokens per second (TPS = TPM/60)"* — so a burst inside one minute can 429 even when the
per-minute total is legal. Batch API calls are exempt from rate limiting.

**Singapore / "International" deployment scope defaults:**

| Model | RPM | TPM |
|---|---|---|
| `qwen3.7-max` | 600 | 1,000,000 |
| `qwen3.7-plus` | 15,000 | 5,000,000 |
| `qwen3.6-flash` | 15,000 | 5,000,000 |
| `qwen3.5-plus` | 15,000 | 5,000,000 |
| `qwen-max` | 600 | 1,000,000 |
| `qwen-plus` | 600 | 1,000,000 |
| `qwen-flash` | 600 | 5,000,000 |
| `qwen-turbo` | 600 | 5,000,000 |
| dated snapshots (typical) | 60–600 | 1,000,000 |

Global-scope numbers run higher (e.g. `qwen3.7-max` Global = 30,000 RPM / 5,000,000 TPM). Trial
domain is capped at RPM 1000. Temporary TPM increases can be requested in the console (effective
immediately, valid 30 days).

**Timeouts (from `regions/`):** workspace-dedicated **3600 s**, DashScope shared **600 s**, trial
**600 s**. Separately, the OpenAI-compat Chat reference states streaming calls have no 300-second
timeout, while a non-streaming call interrupted at 300 s returns partial content rather than an error
— so set our client timeout ≤ the domain limit and prefer streaming for long generations.

---

## Pricing

**Currency: USD** on the international pricing page (`alibabacloud.com/help/en/model-studio/model-pricing`).
Prices are **per 1 million tokens**, keyed by **deployment scope** and **tiered by input length**.
Several rows carry "Limited-time" discounts (Singapore up to 50%; other regions 20–80%, some with
night 22:00–08:00 UTC+8 vs daytime rates) — the figures below are **list prices**.

### Singapore — deployment scope "International" (USD / 1M tokens)

| Model | Input tier | Input | Output | Free quota |
|---|---|---|---|---|
| `qwen3.7-max` | 0 < T ≤ 1M | 2.50 | 7.50 | 1M tokens |
| `qwen3.7-plus` | 0 < T ≤ 256K | 0.40 | 1.60 | 1M tokens |
| `qwen3.7-plus` | 256K < T ≤ 1M | 1.20 | 4.80 | — |
| `qwen3.6-flash` | 0 < T ≤ 256K | 0.25 | 1.50 | 1M tokens |
| `qwen3.6-flash` | 256K < T ≤ 1M | 1.00 | 4.00 | — |
| `qwen3.5-flash` | 0 < T ≤ 1M | 0.10 | 0.40 | 1M tokens |
| `qwen-max` (legacy) | no tiering | 1.60 | 6.40 | 1M tokens (90 days) |
| `qwen-plus` (legacy) | 0 < T ≤ 256K | 0.40 | 1.20 non-thinking / 4.00 thinking | 1M tokens |
| `qwen-plus` (legacy) | 256K < T ≤ 1M | 1.20 | 3.60 / 12.00 thinking | — |
| `qwen-flash` (legacy) | 0 < T ≤ 256K | 0.05 | 0.40 | 1M tokens |
| `qwen-flash` (legacy) | 256K < T ≤ 1M | 0.25 | 2.00 | — |
| `qwen-turbo` (legacy) | no tiering | 0.05 | 0.20 non-thinking / 0.50 thinking | 1M tokens |

### China (Beijing) — deployment scope "Chinese mainland" (USD / 1M tokens)

| Model | Input tier | Input | Output |
|---|---|---|---|
| `qwen3.7-max` | 0 < T ≤ 1M | 1.650 | 4.951 |
| `qwen3.7-plus` | 0 < T ≤ 256K | 0.276 | 1.101 |
| `qwen3.7-plus` | 256K < T ≤ 1M | 0.826 | 3.301 |
| `qwen3.6-flash` | 0 < T ≤ 256K | 0.165 | 0.990 |
| `qwen3.6-flash` | 256K < T ≤ 1M | 0.660 | 3.961 |
| `qwen3.5-flash` | 0 < T ≤ 128K | 0.029 | 0.287 |
| `qwen-max` (legacy) | no tiering | 0.345 | 1.377 |
| `qwen-plus` (legacy) | 0 < T ≤ 128K | 0.115 | 0.287 non-thinking / 1.147 thinking |
| `qwen-plus` (legacy) | 256K < T ≤ 1M | 0.689 | 6.881 / 9.175 thinking |
| `qwen-flash` (legacy) | 0 < T ≤ 128K | 0.022 | 0.216 |
| `qwen-turbo` (legacy) | no tiering | 0.044 | 0.087 non-thinking / 0.431 thinking |

**Beijing is roughly 2–4× cheaper than Singapore for the same model.** Beijing rows carry **no free
quota**. Note also the tier boundaries differ by region: Singapore tiers at 256K, Beijing tiers at
128K / 256K.

Discounts documented: 50% batch-inference discount on several models; a context-caching discount;
limited-time promotional rates. **UNKNOWN:** the expiry dates of the limited-time discounts, and
whether context-cache hits are billed at a separate published rate.

---

## Health/test-connection strategy

Because there is **no documented model-listing endpoint**, a "Test connection" button cannot do a
cheap `GET /models`. Proposed strategy:

1. **Build the URL** from `region` + `domain_type` (+ `workspace_id` when workspace-dedicated) and
   append `/compatible-mode/v1/chat/completions`.
2. **Send a minimal non-streaming chat completion**: the configured chat model, a single user message
   ("ping"), `max_tokens: 1`, `enable_thinking: false` (required — thinking models reject
   non-streaming calls otherwise), no `tools`, no `response_format`. Client timeout 15 s.
3. **Interpret:**
   - `200` → connection OK. Surface `model`, `usage.total_tokens`, and latency.
   - `401` `InvalidApiKey`/`invalid_api_key` → wrong key, **or key from a different region**, **or
     key/base-URL plan mismatch**. The message must say all three, because region mismatch is the
     single most likely misconfiguration here.
   - `403` `AccessDenied` / `AccessDenied.Unpurchased` → Model Studio not activated, or the model
     needs approval.
   - `404` `ModelNotFound`/`model_not_found` → model not available **in this region** (not
     necessarily a typo). `404 WorkSpaceNotFound` → bad Workspace ID.
   - `429` → credentials are valid; report "connected, rate limited" as a *pass* with a warning.
   - `400 Arrearage` / `429 *BillOverdue` → billing problem; credentials fine.
   - `5xx` → provider-side; retry once then report degraded.
4. **Optionally** run the same probe against the classification model so the admin learns immediately
   if only one of the two model IDs is valid in the chosen region.
5. Do **not** probe with `response_format: {"type":"json_schema"}` — availability on the
   international endpoint is unverified (see Capabilities).

---

## OpenAI-compatibility verdict

### **OPENAI-COMPATIBLE + PROVIDER-SPECIFIC METADATA**

Justification, from the docs:

**Why OpenAI-compatible is sufficient for the wire protocol.** Alibaba publishes a first-class,
explicitly-named OpenAI compatibility mode (`/compatible-mode/v1/chat/completions`) with its own full
API reference, documents the standard OpenAI SDK as the supported client, and returns the standard
`choices[]` / `message` / `finish_reason` / `usage.prompt_tokens|completion_tokens|total_tokens`
shape. Streaming, `tools`/`tool_choice`/`parallel_tool_calls`, `response_format`, `seed`, `stop`,
`stream_options.include_usage` are all present under their OpenAI names. There is also an
OpenAI **Responses** API endpoint. A native DashScope adapter would buy us nothing: the native mode
uses a *different* envelope (`input`/`parameters`/`output`, `input_tokens`/`output_tokens`) with no
capability the compatible mode lacks for chat.

**Why plain OpenAI config is nevertheless not enough.** Every one of these is a documented deviation
that a generic "base URL + API key + model" provider record cannot express:

1. **Base URL is computed, not typed.** It is a function of `region` × `domain_type` ×
   `workspace_id`. Five of six regions have a workspace-scoped host; two have no shared host at all;
   one has no workspace-scoped host.
2. **API keys are region-scoped** — *"cannot be used across regions"* — and additionally plan-scoped
   (`sk-` vs `sk-sp-`). A key/URL mismatch surfaces as a bare 401, which is indistinguishable from a
   typo unless we carry the region as first-class metadata.
3. **Model IDs are region-scoped.** International lists `qwen3.7-max`/`qwen3.6-flash`; China lists
   `qwen3.8-max`/`qwen3.7-flash`. There is no `/models` endpoint to reconcile this at runtime.
4. **No model-listing endpoint** in either mode — model choice must be config-driven.
5. **Non-OpenAI parameters are required for correct behaviour**: `enable_thinking` must be explicitly
   `false` for non-streaming calls to thinking-capable models, or the request 400s. `thinking_budget`,
   `enable_search`, `top_k`, `repetition_penalty` must go through `extra_body`.
6. **Documented capability gaps**: `tools` cannot be combined with `stream=true`; `n` is
   single-model and clamps to 1 with tools; `system_fingerprint` is always empty; structured output
   is non-thinking-mode only; JSON Schema mode is not documented on the international site.
7. **Dual error-code vocabulary** (`Throttling.RateQuota/LimitRequests/limit_requests`) plus
   provider-only codes (`DataInspectionFailed` content blocking, `Arrearage`, `WorkSpaceNotFound`)
   that our error mapper must recognise.
8. **Region-dependent pricing, rate limits, timeouts and free quota** — cost accounting cannot be a
   single per-model constant.

So: reuse the OpenAI HTTP client, wrap it in a Qwen provider that owns region → URL construction,
region → model catalogue, parameter injection, and error-code translation.

---

## Required configuration fields

Fields the Admin form must ask for:

| Field | Type | Required | Notes |
|---|---|---|---|
| `region` | enum | **yes** | `ap-southeast-1` (Singapore) · `cn-beijing` · `cn-hongkong` · `ap-northeast-1` (Tokyo) · `eu-central-1` (Frankfurt) · `us-east-1` (US Virginia). **Default `ap-southeast-1`.** Drives base URL, model list, pricing, rate limits. |
| `domain_type` | enum | **yes** | `workspace` (recommended, 3600 s timeout, prod) · `dashscope_shared` (legacy, 600 s) · `trial` (600 s, RPM 1000, non-prod). **Default `dashscope_shared`** for the simplest setup; recommend `workspace` for production. Must be filtered by region — Tokyo/Frankfurt have no shared domain; US Virginia has no workspace domain. |
| `workspace_id` | string | conditional | Required iff `domain_type = workspace` or `trial` is not used and region requires it. Shown as "API Host" in the console. Hide unless applicable. |
| `api_key` | secret | **yes** | Starts with `sk-`. Reject `sk-sp-` with a message explaining Coding/Token Plan keys are for interactive coding tools only. |
| `base_url_override` | string | no | Escape hatch; when set, bypasses region/domain construction. Show the computed URL read-only when empty. |
| `chat_model` | string (with suggestions) | **yes** | Default `qwen3.7-plus`. Suggestions filtered by region; free text allowed since no `/models` endpoint exists. |
| `classifier_model` | string (with suggestions) | **yes** | Default `qwen3.6-flash`. |
| `api_mode` | enum | no | `openai_compatible` (default) · `dashscope_native`. Only expose if we ever implement native. |
| `enable_thinking` | bool | no | Default `false`. Must be sent explicitly as `false` for non-streaming calls. |
| `request_timeout_s` | int | no | Default 60. Cap at 600 for shared/trial domains, 3600 for workspace domains. |

Derived / display-only (not asked, but shown): computed base URL, per-model list price for the chosen
region, documented RPM/TPM for the chosen model+region.

---

## Unknowns

- **Does `GET {BASE}/compatible-mode/v1/models` exist?** Not documented anywhere. Behaviour UNKNOWN.
- **Is `response_format: {"type":"json_schema"}` accepted on the international (Singapore) endpoint?**
  Documented on the China docs site only; the international page shows `json_object` exclusively.
  UNKNOWN — probe before depending on it.
- **Sunset date for `dashscope-intl.aliyuncs.com`.** Docs say the shared domain "remains available"
  and recommend migration, but publish **no date**. UNKNOWN.
- **`qwen-turbo` retirement.** Not on the international decommissioning list as of 2026-08-18.
  Whether it is scheduled on the China/Bailian list is UNKNOWN (not verified on an official page).
- **Expiry dates of the "Limited-time" pricing discounts**, and the exact billed rate for
  context-cache hits. UNKNOWN.
- **Whether `qwen3.8-max` / `qwen3.7-flash` (listed on the China docs site) are available in
  Singapore.** The international models page does not list them. UNKNOWN — assume no.
- **Exact `type` and `param` field values in the OpenAI-mode error envelope.** The shape is reported
  by the error-code page but was not verified field-by-field against a live response.
- **Max output tokens per model.** The docs reference a "Maximum Output Tokens" column in the model
  list but it was not extractable from the rendered pages during this research. UNKNOWN.
- **Whether the Anthropic-compatible endpoint (`/apps/anthropic`) offers anything the OpenAI mode
  lacks.** Not investigated — out of scope.
