# Performance Hardening for Exhibition Load

**Status:** done (2026-08-24) · **Branch:** `perf/exhibition-load-hardening`
**Trigger:** INOTEX 2026 — ~5000 visitors over 2 days on a single strong server.
Stress tool results and every threshold below were measured on this branch.

## What the load looks like

5000 visitors over 2 days, booth-paced: a visitor asks a question every few
seconds. Peak concurrent ACTIVE chatters are realistically 50–100 (the rest are
watching videos, walking, or idle). The risks were never raw CPU — they were
per-request DB traffic, per-worker incoherence, and unbounded fan-out to the AI
provider.

## Bottlenecks found (ranked)

1. `get_setting()` uncached on the hottest path — 6–10 identical SELECTs per
   `/chat` (kill switch, content policy, twice per applog row for log levels).
2. Search indexes rebuilt only in the worker that handled an admin edit — with
   `WEB_CONCURRENCY=3`, 2 of 3 workers served stale answers until restart.
3. `GET /` built a fresh Jinja2 Environment + template compile + file reads
   per page view, on the event loop.
4. A new `httpx.AsyncClient` per external AI call — full TCP+TLS handshake per
   call, up to 2 calls per Tier-2 question.
5. No cap on simultaneous external AI calls — a burst of ambiguous questions
   fanned out unbounded (only the provider's own 429s pushed back).
6. Rate-limiter swept EVERY known IP on EVERY chat request (O(N) on the loop).
7. Admin bcrypt (~0.5s CPU) ran on the event loop — one login stalled every
   concurrent visitor on that worker.

## Changes

| Change | File | Knob |
| --- | --- | --- |
| Settings TTL cache; writers drop their key; `maintenance_state` never cached; `fresh=True` bypass | `app/db/queries.py` | `SETTINGS_CACHE_TTL` (default 15s, 0=off) |
| Cross-worker index freshness: writers stamp `search_index_version` in `settings`; readers poll at most every few seconds and rebuild in background; per-worker rebuild lock | `app/services/search.py` + writers | `SEARCH_INDEX_REFRESH_SECONDS` (default 5) |
| Rendered theme-shell cache keyed on file mtimes; only the HMAC chat token is spliced per request | `app/services/themes.py` | — |
| One shared `httpx.AsyncClient` for all provider calls (pool 20 keepalive / 200 max), per-request timeouts preserved; closed on shutdown; injectable for tests | `app/services/ai/adapters/base.py` | — |
| Concurrency gate around `adapter.invoke` — excess requests queue instead of fanning out | `app/services/ai/engine.py` | `AI_MAX_CONCURRENCY` (default 16, 0=off) |
| Rate-limit sweep amortised to every 30s; per-bucket filtering unchanged | `app/auth/security.py` | — |
| bcrypt verify/hash moved off the event loop (`asyncio.to_thread`) on login, assistant-save, password change | `app/routers/admin.py` | — |
| `scripts/stress_chat.py` — booth-simulated load test (page + dataset + question mix, 1-in-5 garbled) | new | — |

## Semantics deliberately kept

- Maintenance guard still reads the DB every request (uncached — correctness).
- `set_setting()` invalidates same-worker immediately; other workers converge
  within the TTL. This is the same trade `ai/store.py` runtime cache (20s) made.
- Tests clear the settings cache per test (`tests/conftest.py`) — the TTL is a
  production trade-off and must not change what a test observes.
- Index rebuild is non-blocking: in-flight queries answer from the current
  index; the rebuild happens in the executor.

## Measured (dev laptop, SQLite, single worker)

`scripts/stress_chat.py --users 40 --duration 45`: ~10 rps sustained booth-paced
load, p50 16ms / p99 565ms per chat, zero 5xx crashes; 503s only on the
deliberate garbled-question path with no AI key (expected). Rate limiting
verified separately (429 flood from one IP at default limits).

Production expectation: with 3 workers + PostgreSQL + nginx serving media, this
comfortably covers 100+ concurrent active chatters. The 5000-visitor total is
bounded by disk logs, not the pipeline.

## Runbook additions

- Pre-event smoke: `python scripts/stress_chat.py --url <base> --users 40
  --duration 60` — expect zero 5xx other than deliberate 503s, p99 < 1s.
- If the provider browns out: lower `AI_MAX_CONCURRENCY` (queue visitors
  instead of hammering); the circuit breaker still trips independently.
- If an operator edits content at the booth: takes effect on every worker
  within `SEARCH_INDEX_REFRESH_SECONDS` — no restart needed.
