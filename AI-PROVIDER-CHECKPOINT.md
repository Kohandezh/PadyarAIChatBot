# AI Provider Control Plane — checkpoint

**Status:** implementation complete (contract-tested). Awaiting commit
authorization and — separately — live credentials for live verification.
**Date:** 2026-08-19
**Suite:** see §5 for the final counts of this session.

## 1. What exists now

| Step | Phase | State |
|---|---|---|
| 4 | PostgreSQL AI control-plane schema (`migrations/0003_ai_control_plane.sql`, applied) | ✅ |
| 5 | Adapter contract + provider registry (11 types incl. SAKOO slot) | ✅ |
| 6 | Padyar AI Wrapper (routing engine, retry, failover, circuit, usage, pricing) | ✅ |
| 7 | Legacy config import (idempotent, non-destructive) | ✅ |
| 8 | chat/classify runtime re-pointed through the wrapper; STT untouched | ✅ |
| 9–11 | OpenAI (Responses), Anthropic (Messages), Gemini (Interactions) native adapters | ✅ |
| 12 | Architecture quality gate | ✅ passed (see 02-wrapper-contract.md; json_schema deferred) |
| 13–19 | Z.AI, Kimi, DeepSeek, Qwen, xAI, Mistral, OpenAI-compatible + SAKOO slot | ✅ |
| 20–21 | Model catalog + official refresh where it exists + manual models | ✅ |
| 22–24 | Admin UI: Providers / Models / Routing (+ kill switch) | ✅ |
| 25–27 | Retry / failover / circuit breaker (shared PG state, probe lease) | ✅ |
| 28 | Health derivation + Test Connection (per-provider cheapest probe) | ✅ |
| 29–30 | Pricing (time-versioned, history-preserving) + Usage & Costs dashboard | ✅ |
| 31 | Main dashboard AI summary cards | ✅ |
| 32 | RAG debugger (routing picture per wrapper call) | ✅ |
| 33 | llm.* events, admin.ai_* audit events | ✅ |
| 34–36 | Security / architecture / concurrency reviews via dedicated tests | ✅ |
| 37 | Full regression | ✅ see §5 |

Architecture and contracts: **docs/engineering/ai-providers/02-wrapper-contract.md**.

## 2. Key files

```
migrations/0003_ai_control_plane.sql        control-plane tables (applied via the runner)
app/services/ai/request.py                  neutral request/response
app/services/ai/adapters/base.py            adapter contract + shared transport
app/services/ai/adapters/*.py               11 adapters + bootstrap catalog
app/services/ai/store.py                    instances/routes/catalog/pricing/usage (+ SQLite mirror)
app/services/ai/engine.py                   routing, retry, failover
app/services/ai/circuit.py                  shared circuit breaker
app/services/ai/health.py                   derived health + test connection
app/services/ai/catalog.py                  model refresh
app/services/ai/legacy_import.py            one-time config migration
app/services/ai/wrapper.py                  padyar_ai public API
app/routers/admin_ai.py                     admin API (CSRF-protected, audited)
templates/admin/ai_*.html + static/admin/js/ai_*.js   five admin pages
tests/test_ai_{adapters,store,engine,legacy_import,admin_ui,live}.py
```

## 3. Verification honesty

* **All provider implementations are IMPLEMENTED + CONTRACT TESTED.** No live
  credentials existed, so NOTHING is live-verified. Mocked success is not
  success. `tests/test_ai_live.py` is the opt-in live harness
  (`RUN_LIVE_AI_TESTS=1` + per-provider `*_LIVE_API_KEY`).
* Model catalogs and prices come from the 2026-08-18 research files; they are
  bootstrap metadata, not truth. Z.AI and Qwen have no discovery API — manual
  model entry is the documented path there.
* The legacy configured models (gpt-4.1 / gpt-5-nano) were imported as MANUAL
  catalog rows deliberately: whether the customer's gateway still serves them
  is unknown and must not be silently "upgraded".

## 3b. Live validation performed this session (honest record)

The existing provider was reached through the wrapper against the REAL
gateway (api.gapgpt.app). The local `.env` key turned out to be a dev
placeholder, so the provider answered **401 Invalid token** — and the whole
failure pipeline was thereby live-verified: status+body →
`authentication_failed`, no same-provider retry, failover decision,
`all_routes_failed`, shared circuit trip, redacted detail in logs. The
SUCCESS path awaits the operator's real key (Admin → AI → Providers →
ویرایش → کلید API, then Test, then enable). Circuits were reset after the
validation.

This validation also caught three production-only bugs that SQLite tests
could not (all fixed + re-tested):
1. int-for-boolean writes (`1 if x else 0`) → PostgreSQL DatatypeMismatch;
2. `enabled = 1` comparisons against PG booleans → UndefinedFunction;
3. psycopg returns JSONB as dicts — `json.loads(dict)` raised and silently
   wiped provider configs; also TIMESTAMPTZ comes back as datetime, which
   broke ISO-string comparisons in the circuit.

## 4. Known risks / next actions for the operator

1. **The work is still not in git** (~250+ uncommitted files including this
   phase). Commit authorization is still pending.
2. Run the live harness with real keys before trusting failover behaviour in
   production; watch the RAG debugger page for the first real traffic.
3. `openai_enabled` is the single kill switch (routing page + legacy settings
   both toggle it).
4. DeepSeek off-peak 50% pricing is NOT modelled (peak rate stored = upper
   bound); xAI long-context 2× cliff not modelled; Gemini 3.6/3.7 promo
   prices double 2027-01-01. All are recorded as pricing rows that can be
   superseded by new effective-dated rows.
5. `json_schema` structured output is deferred (no current consumer; only
   `json_object` is normalized).

## 5. Test counts

Baseline entering this session: **609 passed**. This session adds:

```
tests/test_ai_adapters.py       46   (provider contracts, wire shapes, SAKOO)
tests/test_ai_store.py          30   (secrets, catalog merge, pricing, circuit)
tests/test_ai_engine.py         23   (routing/retry/failover/loop/kill/concurrency)
tests/test_ai_legacy_import.py  10   (migration idempotency + wrapper compatibility)
tests/test_ai_admin_ui.py       27   (HTML pages, CSRF, XSS, refresh, SAKOO proof)
tests/test_ai_live.py           14 skipped (opt-in; no credentials)
```

Final full-suite numbers are in the session report — run on an idle machine
before comparing runtimes (the 213 s contamination lesson).

## 6. Git state at handoff

HEAD `c69dab4`, branch `main-noor`, level with `origin/main-noor`. The 11
pre-existing staged renames are untouched. No commit, no push, no destructive
commands were run — see the session report for the full inventory.

## 7. Multi-agent verification pass — 2026-08-19

16 Opus specialists audited this phase in parallel (architecture, PostgreSQL
adversarial, routing, circuit, 3× provider-contract, catalog/pricing, 2× admin
UI, security red-team, concurrency, test-quality, observability, legacy/Gate-H,
freeze audit). Every finding was re-verified by the orchestrator before being
accepted — two agent claims were rejected as wrong (see below).

**Suite: 745 -> 825 passed, 0 failed, 14 skipped (intentional live-provider
opt-ins), 112 s on an idle machine.**

### P0 defects found and fixed

1. **OTP/registration was 100% dead on PostgreSQL.** `otp.ensure_table()`
   caught only `sqlite3.OperationalError`, so psycopg's `DuplicateColumn`
   escaped from the first statement of all six OTP entry points. Separately
   `SET used = 1` / `AND used = 1` hit a real BOOLEAN column. Net effect: a
   visitor entering the CORRECT code got a 500 *after* validation, leaving the
   challenge unconsumed and the code replayable. Invisible to CI because the
   suite pins `DB_BACKEND=sqlite`. Fixed and proven against live PostgreSQL.
2. **Circuit breaker did not trip under concurrency.** `record_failure` read
   the counter into Python and wrote it back; 5 concurrent failures recorded
   as 2 and the circuit stayed CLOSED — failing at exactly the load it exists
   for. Now a DB-side `CASE` increment under row lock. Mutation-verified.
3. **Credential redaction was shape-based and leaked.** `xai-...`, Mistral's
   bare-alphanumeric and `gw_live_...` keys survived scrubbing and were written
   verbatim into `audit_logs`, which is exempt from retention pruning.
   Redaction is now VALUE-based: `applog.register_secret()` is called wherever
   a secret is decrypted, and `scrub_text` removes the exact value whatever
   shape it has. Regexes remain as the second line.
4. **Legacy import could freeze a half-migrated control plane forever.** A
   fault after `create_instance` left an instance with zero route targets, and
   the next boot's guard then set the migration marker permanently. Now rolled
   back so the next boot retries.
5. **Discovery silently overwrote MANUAL model rows.** `gpt-4.1`/`gpt-5-nano`
   are manual precisely because nobody knows if the gateway still serves them;
   one Refresh converted that into a confident claim. Now `preserved_manual`.

### P1 also fixed
`applog.record()` leaked a pooled connection carrying an aborted transaction
when a log write failed — held across the provider HTTP call. `/api/ready?deep`
made an unauthenticated-triggerable authenticated outbound call from outside
the adapter layer (no SSRF policy, no circuit, no accounting) and published the
gateway URL publicly; it now derives health from recorded state. `AIError`'s
exception message carried unscrubbed provider text into tracebacks.

### Rejected agent claims (verification matters)
* "OpenAI reasoning models reject `temperature`" — contradicted by
  `01-capability-matrix.md` (OpenAI listed **Supported**) and by
  `research/openai.md`. Not applied.
* "openai_adapter bypasses the sampling gate" — already fixed on disk before
  the claim was filed.

### Known-open (documented, not fixed)
DNS rebinding is still possible against the `public` trust class because
`resolved_ips()` has no callers — no IP pinning at connect time. Alibaba
metadata `100.100.100.200` is reachable under `internal`. Usage `P95` slices
in Python rather than using `percentile_cont`. `_RT_CACHE` is 20 s per-process
(irrelevant at 1 worker, real at >1).
