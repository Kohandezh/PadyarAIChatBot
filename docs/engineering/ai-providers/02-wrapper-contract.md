# Padyar AI Wrapper — architecture and contracts

Status: **IMPLEMENTED + CONTRACT TESTED** (2026-08-19). Live verification is
pending real credentials — see the classification table at the end.

## 1. Layering

```
app/routers/chat.py ──> app/services/openai.py (prompt content + STT)
                              │ classify_intent / get_openai_response
                              ▼
                    app/services/ai/wrapper.py      padyar_ai.generate/classify
                              ▼
                    app/services/ai/engine.py       routing, retry, failover
                    app/services/ai/circuit.py      shared circuit breaker
                    app/services/ai/store.py        control-plane tables
                              ▼
                    app/services/ai/adapters/*      provider wire protocol
                              ▼
                    endpoint_policy (SSRF) → httpx (no redirects)
```

Business code never imports a vendor SDK. The only remaining SDK user is
Whisper STT inside `app/services/openai.py:_transcribe_sync` — explicitly out
of routing scope this phase (regression-tested, not migrated). A repo grep
test enforces the boundary (`test_no_vendor_sdk_imports_outside_the_ai_package`).

## 2. The neutral request (`app/services/ai/request.py`)

| Field | Semantics |
|---|---|
| `task` | `chat` \| `classify` — selects the route |
| `messages` | user/assistant roles ONLY; no provider roles leak in |
| `system_prompt` | its own field — never `messages[0]` (Anthropic 400s, Gemini uses a differently-typed field) |
| `max_output_tokens` | always resolved to a concrete number before the adapter sees it (Anthropic requires it; the gpt-5-nano reasoning-budget incident proves a silent default is unsafe) |
| `temperature` / `top_p` | PREFERENCES. The adapter drops/clamps them per model — five of nine providers reject or deprecate them |
| `reasoning` | first-class: off/low/medium/high; classify defaults OFF (five providers think by default and bill it as output) |
| `response_format` | `text` \| `json_object` (json_schema deliberately deferred — no current consumer) |

## 3. The neutral response

`content`, normalized `finish_reason` (stop/length/content_filter/tool_calls/other),
computed usage (`tokens_input` is a COMPUTED total for Anthropic; unknown
stays `None`), `provider_request_id`, correlation ids, and engine-filled
routing facts (`route_priority`, `attempt_count`, `failover_count`, `cost`).

## 4. Adapter contract (`adapters/base.py`)

Every adapter owns: `metadata()`, `configuration_schema()` (the Admin form is
generated from it — there is no universal form), `validate_config()` (SSRF
checks at save time), `sampling_policy(model)`, `reasoning_control(model)`,
`invoke()`, `list_models()` (optional capability — Z.AI and Qwen have none,
documented), `test_connection()`, `extract_usage()`, `error_code_from_body()`.

Three native adapters (OpenAI Responses, Anthropic Messages, Gemini
Interactions). Six compatible providers subclass
`OpenAICompatibleAdapter` and override only their documented divergences.
SAKOO is a registered architecture slot whose `http()` raises — there is no
code path from it to a socket.

## 5. Routing engine (`engine.py`)

Priority order → eligibility (route/target/provider enabled, secret present,
circuit permits, kill switch off) → invoke → per-target bounded retry
(chat 2, classify 1; target overrides) → failover IFF `failover_eligible`
→ `all_routes_failed` with the per-target failure map.

Locked distinctions enforced by tests: auth failure — no same-provider
retry, immediate circuit trip, fail over; context-limit / invalid-request /
content-rejected — never fail over; rate-limit — both; loop protection via
the attempted-target set; no DB transaction ever spans a provider call.

## 6. Circuit breaker (`circuit.py`)

State lives in `ai_circuit_state` (all workers agree). 5 failover-eligible
failures / 120 s trips open; auth failures trip instantly with a 10-minute
cooldown; half-open is a single atomic probe lease (owner + 45 s expiry);
admin reset forces closed. Defaults tunable via `ai_circuit_*` settings.

## 7. Cost & usage

Pricing is time-versioned in `ai_model_pricing`; cost is computed at request
time from the then-effective row and stored ON the usage row with its
effective_from — later price changes cannot rewrite history. One usage row
per wrapper call (tokens/cost summed across attempts; per-attempt detail in
`observability.app_logs` llm.* events). Unknown pricing is `NULL` → «ناموجود».

## 8. Adding a provider (the extensibility test)

1. adapter implementation, 2. metadata, 3. configuration schema,
4. capability mapping, 5. error normalization, 6. model discovery if
supported, 7. contract tests, 8. one registry line. Nothing else changes —
proven twice in this phase (six compatible providers; SAKOO slot).

## 9. Implementation status per provider

| Provider | Status |
|---|---|
| OpenAI / Anthropic / Gemini / Z.AI / Kimi / DeepSeek / Qwen / xAI / Mistral / OpenAI-compatible | **IMPLEMENTED + CONTRACT TESTED** (research-verified wire shapes; no live credentials were available) |
| SAKOO | **ARCHITECTURE READY / REQUIRES DOCUMENTATION** |
