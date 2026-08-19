# Provider capability matrix

Derived from nine parallel reads of current official documentation, one file per
provider in `research/`. Every claim here traces to a cited URL in those files.
Nothing was written from memory.

**This document is the input to the abstraction, not a description of it.** The
adapter contract in `02-wrapper-contract.md` was written after this matrix, and
where the two disagree, this one is the evidence.

---

## 1. The headline: model IDs written from memory would have been wrong

Before any design conclusions, the reason the research-first rule was correct:

| Provider | What a from-memory implementation would have used | What the docs say today |
|---|---|---|
| DeepSeek | `deepseek-chat`, `deepseek-reasoner` | **Both retired 2026-07-24.** Now `deepseek-v4-flash`, `deepseek-v4-pro` |
| Kimi | `moonshot-v1-8k/32k/128k` | **Legacy, sunsetting.** Now `kimi-k3`, `kimi-k2.6`, `kimi-k2.7-*` |
| xAI | `grok-*-mini`, `grok-*-fast` | **All retired 2026-05-15**, redirect to `grok-4.3` |
| OpenAI | `gpt-4.1`, `gpt-4o-mini` | Current lineup is the `gpt-5.6-*` family |
| Qwen | `qwen-plus`, `qwen-max`, `qwen-turbo` | Filed under **"Legacy Qwen"** |
| Mistral | plain dated snapshots | Mid-transition to `major-minor` form |

Padyar's own config still names `gpt-4.1` / `gpt-5-nano`. That is a live finding,
not a hypothetical — see §7.

---

## 2. Transport and auth

| Provider | Base URL | Auth header | Extra required headers |
|---|---|---|---|
| OpenAI | `https://api.openai.com/v1` | `Authorization: Bearer` | — (optional org/project) |
| Anthropic | `https://api.anthropic.com` | **`x-api-key`** | **`anthropic-version`** |
| Gemini | `https://generativelanguage.googleapis.com` | **`x-goog-api-key`** | `Api-Revision` (pin advised) |
| Z.AI | `https://api.z.ai/api/paas/v4/` | `Authorization: Bearer` | — |
| Kimi | `https://api.moonshot.ai/v1` (intl) | `Authorization: Bearer` | — |
| DeepSeek | `https://api.deepseek.com` (no `/v1`) | `Authorization: Bearer` | — |
| Qwen | **f(region, domain family, workspace)** | `Authorization: Bearer` | — |
| xAI | `https://api.x.ai/v1` | `Authorization: Bearer` | — |
| Mistral | `https://api.mistral.ai/v1` | `Authorization: Bearer` | — |

**Consequence 1 — auth cannot be a shared constant.** Three of nine use a
non-Bearer scheme. `Authorization: Bearer` belongs in a *default*, overridable
per adapter, never hardcoded in the transport.

**Consequence 2 — there is no universal configuration form.** Qwen alone needs
region *and* workspace id *and* domain family; Kimi and Z.AI need a
platform choice (international vs mainland) whose API keys are explicitly
**not portable** across platforms; Gemini needs an API-surface choice. This is
why `configuration_schema()` is per adapter and the Admin form is generated from
it, exactly as the phase spec requires.

---

## 3. Request shape

| Provider | Messages field | System prompt | Model goes in |
|---|---|---|---|
| OpenAI (Responses) | `input` | message | body |
| Anthropic | `messages` | **top-level `system`** | body |
| Gemini (Interactions) | `input` as typed steps | top-level, 3 accepted types | body |
| Gemini (legacy) | `contents[].parts[]` | `systemInstruction` | **URL path**, colon-verb |
| Others | `messages` | message | body |

Three different container names, and the assistant role is `"model"` on Gemini,
never `"assistant"`.

**Anthropic rejects a `role:"system"` message at index 0 with a hard 400.** So
"prepend the system prompt as the first message" — the obvious shared helper —
is wrong for two of nine providers. The neutral request must carry
`system_prompt` as its **own field**, and each adapter decides where it goes.

---

## 4. Sampling parameters — the most dangerous shared assumption

| Provider | `temperature` today |
|---|---|
| Anthropic | **HTTP 400** on Claude 4.7+ (Opus 5, Sonnet 5, Fable 5, Opus 4.8/4.7) |
| Kimi | **Errors** — K-series pins `temperature`/`top_p`/`n` |
| DeepSeek | **Unsupported** while thinking is on — and thinking is on by default |
| xAI | `stop`/`presence_penalty`/`frequency_penalty` **error** on reasoning models |
| Gemini | **Deprecated** (2026-07-21 notes) in favour of `thinking_level` |
| Mistral | Supported; even publishes `default_model_temperature` per model |
| OpenAI, Z.AI, Qwen | Supported |

**Five of nine reject or deprecate the single parameter every OpenAI-shaped
abstraction treats as universal.**

Padyar sends `temperature=0.66` for chat and `temperature=0.0` for
classification, unconditionally, from `app/services/openai.py`. Pointed at
Anthropic or Kimi, **every request fails** — not degrades, fails.

**Design consequence:** sampling parameters are *requests*, not commands. The
neutral request may express a sampling preference; the adapter decides whether
the target model can accept it and drops or translates it otherwise. No
parameter is sent unconditionally.

---

## 5. Reasoning / thinking — on by default, and a cost trap

| Provider | Default | How to turn it down |
|---|---|---|
| DeepSeek | **on, `high`** | `thinking:{"type":"disabled"}` |
| Z.AI `glm-4.7-flash` | **"thinks compulsorily"** | `thinking:{"type":"disabled"}` |
| Qwen | on | `enable_thinking:false` — **omit it and non-streaming 400s** |
| xAI | `high` | `reasoning.effort`; **cannot disable** on 4.5/4.6 |
| Kimi | on | `thinking:{"type":"disabled"}` |
| Anthropic | — | `output_config.effort` (no `temperature`) |
| Gemini | — | `thinking_level` |

Padyar has already been burned by exactly this. The comment at
`app/services/openai.py:283` records that `gpt-5-nano` with `max_tokens=200`
spent its whole budget on internal reasoning and returned **empty content**, so
every query silently fell through to `out_of_domain`. That was one provider.
Five more behave the same way by default.

**Design consequence:** `reasoning` is a first-class field of the neutral
request, and the CLASSIFICATION task defaults it **off wherever the provider
permits**. Classification is a cheap routing decision; paying for hidden
reasoning tokens on every low-confidence query is a direct cost regression.

---

## 6. Failures that arrive dressed as success

| Provider | The trap |
|---|---|
| Gemini | Safety block = **HTTP 200**, well-formed body, **no text** |
| OpenAI | HTTP 200 with `status:"failed"` / `"incomplete"` + populated `error` |
| Anthropic | In streaming, errors can arrive **after** HTTP 200 |

**Design consequence:** response parsing must never assume text exists, and
error classification must not be HTTP-status-only. `content_rejected` is a
required member of the normalized error taxonomy — it is a distinct outcome
from "provider failed", and it must not trigger failover, because every other
provider will refuse the same content.

---

## 7. Token usage — no two providers agree

| Provider | Input tokens | Cache fields |
|---|---|---|
| OpenAI Responses | `input_tokens` / `output_tokens` | `cached_tokens` |
| OpenAI Chat Compl. | `prompt_tokens` / `completion_tokens` | nested |
| Anthropic | `input_tokens` — **only tokens after the last cache breakpoint** | `cache_read_input_tokens`, `cache_creation_input_tokens`; **no `total_tokens`** |
| DeepSeek | `prompt_cache_hit_tokens` / `prompt_cache_miss_tokens` | (that is the cache reporting) |
| Gemini | `usageMetadata`, three vocabularies | thought + cached tokens |
| Kimi | streaming usage at **`choices[0].usage`**, not top-level | flat `cached_tokens` |
| xAI | + `reasoning_tokens` | `cached_tokens`, plus `cost_in_usd_ticks` |
| Mistral | `prompt_tokens` / `completion_tokens` | — |

Anthropic's is the one that costs money if you get it wrong: true input is
`cache_read + cache_creation + input`, so a naive `usage.input_tokens` read
**under-reports input massively** on any cached conversation.

**Design consequence:** `extract_usage()` is an adapter responsibility, not a
shared parser, and the normalized response carries `tokens_input` as a
*computed* total rather than a copied field.

---

## 8. Model discovery — optional, not assumable

| Provider | Endpoint | Richness |
|---|---|---|
| Mistral | `GET /v1/models` | **Richest.** 14 capability flags, `max_context_length`, `deprecation`, `deprecation_replacement_model`, `default_model_temperature`, `aliases[]`. No pricing. |
| xAI | `GET /v1/language-models` | Very rich: modalities, context, aliases, **per-token pricing**. Root key `models`, not `data`. |
| Kimi | `GET /v1/models` | `context_length` + capability flags |
| OpenAI | `GET /v1/models` | Standard, plus `shutdown_date` |
| Anthropic | `GET /v1/models` | Standard |
| Gemini | `GET /v1beta/models` | Standard |
| **Z.AI** | **none** | Verified absent from the OpenAPI spec |
| **Qwen** | **none** | Absent in both native and compat modes |
| DeepSeek | `GET /models` | Standard |

**Design consequence:** `list_models()` is a **capability**, not a contract
method. Two providers have no discovery at all, so the catalog must accept
manually entered model IDs marked `Manual` — and Mistral/xAI's rich responses
must be allowed to populate capability flags automatically rather than being
flattened to the poorest common denominator.

---

## 9. Implementation decision per provider

| Provider | Verdict | Why |
|---|---|---|
| **OpenAI** | **NATIVE** | Responses API is a different wire shape from the "OpenAI-compatible" Chat Completions shape third parties implement — `input` vs `messages`, `output[]` vs `choices[]`, different usage names. One code path cannot serve both. |
| **Anthropic** | **NATIVE** | `x-api-key` + version header, top-level `system`, content-block array, no `choices`, `max_tokens` required, temperature 400s, 529 `overloaded_error`. Official OpenAI-compat exists but the docs call it **non-production** and it silently drops `response_format` and caching. |
| **Gemini** | **NATIVE** | Two parallel surfaces; legacy puts the model in the URL and streaming on a *different URL*; safety block is a 200. |
| Z.AI / GLM | COMPATIBLE + METADATA | Genuine base-URL swap; no model listing; `json_object` only; `tool_choice: "auto"` only |
| Kimi | COMPATIBLE + METADATA | Real compat, but pinned sampling params, streaming usage in a different place, `thinking` has no OpenAI equivalent |
| DeepSeek | COMPATIBLE + METADATA | Own cache-token vocabulary; thinking default-on; `reasoning_content` must be echoed back with tools or 400 |
| Qwen | COMPATIBLE + METADATA | Wire is OpenAI-shaped, but region/workspace config, no `/models`, `enable_thinking:false` mandatory |
| xAI | COMPATIBLE + METADATA | Richer `/v1/language-models`; `reasoning_tokens`; flat non-OpenAI error envelope |
| Mistral | COMPATIBLE + METADATA | Officially documents the stock OpenAI client working; but `additionalProperties:false` **rejects** unknown fields, `finish_reason: "model_length"`, and the rich catalog is worth a native parser |
| Generic OpenAI-compatible | BASE | The existing Padyar gateway. Not an arbitrary HTTP client. |
| **SAKOO** | **ARCHITECTURE READY / REQUIRES DOCUMENTATION** | No docs supplied. No adapter, no guessed URL, no guessed models. |

**Three native adapters, six compatible-plus-metadata, one base.** This is the
finding that prevents the failure mode the phase warned about in both
directions: it is neither nine hand-rolled clients nor nine thin wrappers around
one OpenAI client.

---

## 10. What the abstraction must therefore provide

Each item below is forced by a row above, not by taste.

1. **`system_prompt` as its own field** — §3, Anthropic 400s on a system message.
2. **Sampling params capability-gated, never sent unconditionally** — §4, five of nine reject them.
3. **`reasoning` as a first-class field, defaulting off for CLASSIFICATION** — §5.
4. **`max_tokens` always resolved to a concrete number** — Anthropic requires it; the reasoning budget incident proves a global default is unsafe.
5. **Per-adapter `auth_headers()`** — §2, three non-Bearer schemes.
6. **Per-adapter `configuration_schema()`** — §2, Qwen needs region+workspace, Kimi/Z.AI need platform.
7. **Endpoint as a function of (model, operation)**, not a constant — §3, Gemini legacy.
8. **A parser that never assumes text exists**, and error classification that is not HTTP-status-only — §6.
9. **`content_rejected` in the taxonomy, and NOT failover-eligible** — §6.
10. **`extract_usage()` per adapter, with `tokens_input` computed** — §7, Anthropic's cache arithmetic.
11. **`list_models()` optional + manual model entry** — §8, two providers have none.
12. **Capability flags populated from discovery where the provider is rich enough** — §8, Mistral and xAI.

---

## 11. Honest limits of this research

- **No credentials were used.** Nothing here is live-verified; it is documentation-verified. Any claim of live connectivity will be made only where a real key was actually used.
- Every file carries an explicit `## Unknowns` section. Recurring gaps across providers: **numeric rate limits** (usually dashboard-gated), **timeout values**, and **error-body shapes for uncommon statuses**.
- Two dated risks worth watching: Kimi's docs say the `kimi-k2.5` and `moonshot-v1` lines sunset "August 31" **without stating the year**, and Z.AI's corporate relationship to Zhipu/BigModel could not be officially confirmed (the evidence is strong but circumstantial).
- Pricing was captured where officially published, in the currency published. Where it was not verifiable it is recorded `UNKNOWN` and must render as `N/A`, never as a guess.
