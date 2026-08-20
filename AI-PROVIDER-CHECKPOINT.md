# PadyarAIChatBot — AI Control Plane checkpoint

**Updated:** 2026-08-19
**Repository:** `Kohandezh/PadyarAIChatBot` (branch `main`) — a project separate
from `PadyarVideoChatbot`, which is untouched and continues independently.

**Status:** engineering complete and contract-tested. **No provider has ever
completed a successful live call**, and that is the single most important thing
to know when reading anything below.

---

## 1. Test baseline

```
default suite (SQLite)        891 passed · 0 failed · 125 skipped
PostgreSQL integration        111 passed · 0 failed   (opt-in)
production config checks       22 passed · 0 failed
```

Skips are the two opt-in profiles: 14 live-provider tests (no credentials) and
111 PostgreSQL tests. Runtime on this machine swings from ~76 s to ~400 s under
load with identical results — **treat pass/fail as the signal, never seconds.**

```bash
.venv/bin/python -m pytest -q                                   # default
RUN_POSTGRES_TESTS=1 .venv/bin/python -m pytest tests/postgres -q
```

## 2. What exists

| Area | State |
|---|---|
| Padyar AI Wrapper | authoritative — `padyar_ai.generate/classify` is the only AI entry point |
| Provider registry | 11 types: 3 native (OpenAI Responses, Anthropic Messages, Gemini Interactions), 7 compatible+metadata (incl. SAKOO/Rayen), 1 base |
| Routing | per-task routes (CHAT, CLASSIFICATION), priority, bounded retry, failover, loop protection |
| Circuit breaker | PostgreSQL-shared state, half-open probe lease, auth-failure instant open |
| Model catalog | official refresh where an API exists; manual entry for Z.AI and Qwen, which have none |
| Pricing / usage | time-versioned; cost stored on the usage row so history cannot be rewritten |
| Admin | Providers · Models · Routing · Usage & Costs · RAG Debugger |
| Observability | `applog` only — llm.* events, audit, security, correlation ids |
| Security | CSRF middleware, SSRF endpoint trust model, value-based credential redaction |
| STT | resolves credentials through the Control Plane (see §4) |

**Gate A holds:** the only vendor SDK call outside `adapters/` is Whisper STT
in `app/services/openai.py` — the documented exception.

## 3. Provider status — read this before quoting any of it

**Every provider: IMPLEMENTED + CONTRACT TESTED. Zero LIVE VERIFIED.**

The one configured instance is an `openai_compatible` gateway carrying a
**development placeholder key**. A controlled request through the real wrapper
returns:

```
code       all_routes_failed   (underlying: authentication_failed)
detail     Invalid token (request id: …)
health     degraded
```

That is the **failure** pipeline working correctly end to end — status → normalized
error → no same-provider retry → failover decision → circuit → redacted detail.
The **success** path has never run. Do not report otherwise.

`SAKOO / Rayen` — IMPLEMENTED (2026-08-20) from the supplied Rayen OpenAPI
3.0 contract. Architecture READY · Provider definition READY · Admin
compatibility READY · Routing compatibility READY · Adapter IMPLEMENTED ·
Chat IMPLEMENTED / CONTRACT TESTED · Model discovery IMPLEMENTED / CONTRACT
TESTED · Embeddings IMPLEMENTED / CONTRACT TESTED · Network integration
IMPLEMENTED (hardened shared transport, public trust). **Live verification:
PENDING INSTALLATION** — the service is IP-allowlisted and the development
machine is not authorized; the operator verifies from the whitelisted
deployment environment via Admin → Test Connection / Refresh Models. Not
LIVE VERIFIED and must not be reported as such. Details:
docs/engineering/ai-providers/research/sako-rayen.md; tests:
tests/test_ai_sakoo.py (34, fully mocked).

## 4. Decisions that are settled

1. **OpenAI-first request semantics are prohibited.** `temperature` is
   capability-gated per model: five of nine providers reject or deprecate it
   (Anthropic 400s on Claude 4.7+, Kimi errors, DeepSeek rejects it while
   thinking is on — the default).
2. `retryable` and `failover_eligible` are **separate** flags. Auth failure:
   no retry, does fail over. Context-limit: neither.
3. `system_prompt` is its own field, never `messages[0]`.
4. `reasoning` defaults **off** for CLASSIFICATION — it is on by default at
   DeepSeek, Z.AI, Qwen, Kimi and xAI, and silently burns tokens.
5. Response parsing never assumes text exists (Gemini signals a safety block
   with HTTP 200 and no text); `content_rejected` must not fail over.
6. `extract_usage()` is per adapter — Anthropic's `input_tokens` counts only
   tokens after the last cache breakpoint.
7. `list_models()` is optional; manual model entry is mandatory.
8. **STT resolves through the Control Plane**: explicit binding
   (`ai_stt_provider_instance_id`) → the single unambiguous eligible instance →
   legacy `ai_api_key` for an install that never migrated. Only `openai` and
   `openai_compatible` are eligible; Anthropic and Gemini do not serve
   `/audio/transcriptions`.
9. Model selection lives in **AI → Routing**. The legacy Settings → AI model
   inputs were removed — they wrote settings the runtime had stopped reading.
10. SMS frozen (Asanak). Public chat UI frozen. SAKOO implemented; live
    verification deferred to the whitelisted deployment environment.

Full evidence: `docs/engineering/ai-providers/01-capability-matrix.md` and the
nine per-provider research files in `research/`. Those outrank memory — five of
nine providers had retired the model IDs recall would have produced.

## 5. Production readiness

**Engineering ~96% · Public launch ~55%.** The gap is not code.

`app/prodcheck.py` runs before anything else at startup. An install marked
production (`COOKIE_SECURE=true`) **refuses to boot** on: SQLite backend,
passwordless/placeholder DSN, empty or `*` origins, `OTP_DELIVERY=dev`, or a
placeholder admin password. Development is never blocked — the same findings
are logged instead. It warns on an unpinned `SECRET_KEY`, a remote DSN without
`sslmode`, an oversized `pool × workers` budget, and **placeholder taxonomy**.

Verified true right now: `SECRET_KEY` is persisted in `settings.app_secret_key`,
so it does **not** rotate on restart; `/api/ready` makes **no** outbound provider
call (a previous version did, unauthenticated).

### Blocking a public launch — none of these are code

| Blocker | Owner |
|---|---|
| Valid AI provider credential (no successful live call yet) | operator |
| Second provider, for live failover proof | operator |
| `data/visit-taxonomy.json` still `"status": "placeholder"` | customer |
| Three open content conflicts — visit hours, organizer list, registration path | customer |
| Human sign-off unsigned | customer |
| `COOKIE_SECURE=true`, real `pg_hba` auth, TLS, PostgreSQL autostart | operator |
| SAKOO/Rayen IP allowlisting of the deployment host + live credential | customer/operator |

### Known open code items — none launch-blocking

- **DNS rebinding.** `endpoint_policy.resolved_ips()` exists so an adapter can
  pin the validated address, and **no adapter calls it**. Validation and connect
  resolve separately, so a hostile DNS answer can change between them. The
  static classifier is otherwise strong (metadata, IPv6-mapped, decimal/octal/
  hex encodings, userinfo tricks and redirects all rejected).
- Alibaba metadata `100.100.100.200` is reachable under the `internal` trust
  class — CGNAT, so the link-local rule misses it.
- Usage P95 slices rows in Python rather than using `percentile_cont`.
- `_RT_CACHE` is 20 s and per-process — irrelevant at one worker.
- Not covered by tests: destructive backup/restore (needs a disposable
  database, not a schema) and pool-exhaustion concurrency.

## 6. Key files

```
app/services/ai/wrapper.py      padyar_ai — the only AI entry point
app/services/ai/engine.py       routing, retry, failover
app/services/ai/circuit.py      shared circuit breaker
app/services/ai/stt.py          transcription credential resolution
app/services/ai/adapters/       11 adapters + bootstrap catalog
app/db/dberrors.py              backend-neutral constraint errors
app/prodcheck.py                startup production-config gate
migrations/0001..0004           applied via scripts/apply_migrations.py
tests/postgres/                 opt-in real-PostgreSQL suite
docs/engineering/ai-providers/  capability matrix + 9 research files
```
