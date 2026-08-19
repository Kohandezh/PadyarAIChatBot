# Phase 0 — Audit of the AI execution path as it exists today

Produced before any code was written for the AI Provider Control Plane phase.
Everything below was read out of the repository, not recalled.

## 1. Complete inventory of AI call sites

A repo-wide search for vendor SDK usage (`from openai`, `import openai`,
`OpenAI(`, `chat.completions`, `anthropic`, `genai.`) returns **zero matches
outside `app/services/openai.py`**.

That is the single most important finding of this audit: the vendor boundary is
already collapsed into one module. Introducing the Padyar AI Wrapper is
therefore a *re-pointing* job, not an excavation.

| Call site | What it invokes | Nature |
|---|---|---|
| `app/routers/chat.py:159` | `classify_intent(match_query)` | **real LLM call** — CLASSIFICATION |
| `app/routers/chat.py:170` | `get_openai_response(user_query, lang)` | **real LLM call** — CHAT |
| `app/routers/voice.py:9` | `_transcribe_sync(...)` | **real API call** — speech-to-text |
| `app/services/providers.py:87,92,103` | `provider_config()` + `GET {base}/models` | reachability probe only |
| `app/services/health.py:158` | `provider_config()` | config read only, no network |
| `app/routers/admin.py:404,426` | `TONE_PRESETS`, `DEFAULT_*` constants | **not an AI call** — prompt content only |

So the true LLM surface is **two tasks** (chat, classification) plus **one
non-LLM API** (Whisper transcription).

`CHAT` and `CLASSIFICATION` are exactly the two tasks the phase specifies. STT
is a third external call that is out of the routing scope for this phase but
**must keep working**; it is recorded here so it is not silently broken.

## 2. Current configuration surface

| Setting key | Read by | Default |
|---|---|---|
| `ai_api_base` | `provider_config()` | env `OPENAI_API_BASE` |
| `ai_api_key` | `provider_config()` | env `OPENAI_API_KEY` |
| `ai_model_chat` | `model_for("chat")` | `gpt-4.1` |
| `ai_model_classify` | `model_for("classify")` | `gpt-5-nano` |
| `openai_enabled` | `chat.py:145`, `public.py:301`, `admin.py:211`, `providers.py:125` | `true` |

Panel settings override env; env keeps a fresh install bootable. This ordering
must survive the migration.

`openai_enabled` is read in four places and already behaves as a coarse
**external-AI kill switch**. The phase asks for a real kill switch; the honest
move is to build on this key rather than introduce a competing one.

## 3. Behaviours the wrapper MUST preserve

These are load-bearing and easy to break. Each was read from the code.

**3.1 `classify_intent` returns a three-tuple, and `None` is not an error.**
`(dataset_entry | None, tokens, cost)`. A `None` entry means the model
answered `out_of_domain`, which sends `chat.py` down a *different, successful*
branch — a full generated answer. A wrapper that collapses "no match" into
"failure" would silently disable that branch. This distinction must be explicit
in the normalized response.

**3.2 `get_openai_response` raises on total failure, and the caller depends on it.**
`chat.py:167` catches, then falls back to a strong local match, else raises
HTTP 503. The wrapper's `all_routes_failed` must therefore still surface as a
raised, catchable error at this call site.

**3.3 Classification uses `max_tokens=1500` for a reasoning model.**
The in-code comment records a real production incident: at 200 tokens
`gpt-5-nano` spent the entire budget on internal reasoning and returned empty
content with `finish_reason=length`, so **every** natural-language query fell
through to `out_of_domain`. This is direct evidence that token budget is a
*capability-dependent* parameter, not a constant — and it is the strongest
in-repo argument for the capability model this phase requires.

**3.4 Retry policy differs per task today.**
Chat: 2 attempts, retried only when the error text looks connection-related,
1.5 s fixed wait. Classification: no retries at all. Per-route retry policy in
the new engine must be able to express both.

**3.5 Transport hardening exists and is deliberate.**
The chat client sets `max_retries=0`, `http2=False`, `max_keepalive_connections=0`
and `local_address="0.0.0.0"`. These look like workarounds for a real network
environment. Adapters must not casually drop them.

## 4. What is wrong today and is in scope to fix

**4.1 Pricing is hardcoded and wrong for every model.**
Both call sites compute:

```
cost = prompt_tokens * 5.0/1_000_000 + completion_tokens * 15.0/1_000_000
```

This single rate is applied no matter which model actually ran. Every cost
figure in the logs today is therefore fiction unless the configured model
happens to cost exactly that. The phase's provider+model+effective-date pricing
table replaces this.

**4.2 Error normalization is substring matching on `str(exc)`.**
`_llm_error_code()` scans the stringified exception for needles like
`"rate limit"`, `"401"`, `"quota"`. It was a reasonable stopgap and its
*taxonomy* is sound — it already yields `rate_limited`, `quota_exceeded`,
`timeout`, `invalid_api_key`, `context_window_exceeded`, `model_not_found`,
`connection_failed`, `provider_internal_error`, `provider_unavailable`.

But matching on `"401"` anywhere in an error string will misfire (a request id
containing `401`, a model named with `401`), and the needle set is OpenAI-shaped.
The new layer must normalize from **HTTP status + parsed provider error body**,
per adapter. The existing code names are a good starting vocabulary and should
be carried forward where they map cleanly, so historical log filters keep working.

**4.3 No failover, no circuit breaker, one provider instance.**
`provider_config()` returns exactly one `(base, key)` pair. There is no concept
of a second provider, so there is nothing to fail over to.

## 5. Infrastructure the phase must reuse, not rebuild

| Need | Existing component | Notes |
|---|---|---|
| Secret storage | `app/services/secure_store.py` | `protect()` / `reveal()`, `enc:` prefix, Fernet-style. Already used for SMS credentials. |
| Logging | `app/services/applog.py` | Already emits `llm.request.completed` / `llm.request.failed` with `provider`, `model`, `tokens_in/out`, `cost`, `duration_ms`, `error_code`. The event names the phase asks for are a superset of what already exists. |
| Audit | `applog.audit(...)` | actor/target/outcome/ip already modelled. |
| Correlation | `applog.set_request_context()`, ContextVars | request_id + correlation_id already flow through middleware. |
| CSRF | `app/auth/csrf.py` middleware | Opt-out by design: new `/admin/*` mutations are protected automatically. |
| DB | `app/db/pg.py` + `migrations/` | Plain versioned SQL, tracked in `app.schema_migrations`. |
| Maintenance mode | `app/services/maintenance.py` | State in `app.settings`, read fresh per request. |
| Health | `app/services/health.py` | 10 probes with per-probe TTL; `ai_provider` probe TTL is 120 s. |

**Reuse consequence:** the phase's instruction "do NOT build a second logging
system" is satisfiable as-is. `applog.record()` already accepts every field the
new LLM events need.

## 6. Design consequences carried into the wrapper contract

1. The wrapper's `classify()` must distinguish **matched**, **out-of-domain**,
   and **failed**. Three outcomes, not two.
2. The wrapper's failure type must be catchable at `chat.py:167` without that
   file learning any vendor vocabulary.
3. Token budget and temperature must be resolvable **per model**, because the
   reasoning-model incident proves a global default is unsafe.
4. Cost must be computed from a pricing table keyed by
   `(provider, model, effective_from)`, and stored **with** the usage row so
   later price changes cannot rewrite history.
5. `openai_enabled` becomes the kill switch rather than a competing new flag.
6. STT stays on its current path this phase, explicitly out of routing scope,
   and is regression-tested rather than migrated.
