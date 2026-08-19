"""Padyar AI Wrapper — the ONE runtime AI interface of the application.

Business code calls `padyar_ai.generate()` / `padyar_ai.classify()` and
receives a provider-neutral AIResponse or a normalized AIError. No module
outside app/services/ai may import a vendor SDK — enforced by review and by
there being no other import path.

Responsibilities owned here (phase spec): task identification, route loading
(via the engine), normalized requests/responses/errors, usage and cost
accounting, logging and correlation. Retry/failover/circuit live in engine +
circuit; provider shape lives in adapters.

Boot sequence (app/main.py lifespan):
    store.ensure_ai_tables()   → SQLite mirror for tests (PG: migrations)
    store.seed_bootstrap_pricing()
    legacy_import.run_import() → one-time, idempotent
"""
from . import engine, store
from .request import AIRequest, AIResponse, AIMessage  # noqa: F401


class PadyarAI:
    """Public wrapper API. Import as `from app.services.ai.wrapper import
    padyar_ai` — one instance, process-wide."""

    # ── Generation ──────────────────────────────────────────────────────

    async def generate(self, messages, system_prompt="", task="chat",
                       max_output_tokens=None, temperature=None, top_p=None,
                       reasoning="default", response_format="text",
                       timeout_s=None, metadata=None) -> AIResponse:
        req = AIRequest(
            task=task,
            messages=[AIMessage(role=m.role, content=m.content)
                      if isinstance(m, AIMessage) else AIMessage(role=m[0], content=m[1])
                      for m in messages],
            system_prompt=system_prompt,
            max_output_tokens=max_output_tokens or 0,
            temperature=temperature, top_p=top_p,
            reasoning=reasoning, response_format=response_format,
            timeout_s=timeout_s, metadata=metadata or {})
        return await engine.execute_request(req)

    async def classify(self, query: str, system_prompt: str,
                       max_output_tokens=1500, temperature=0.0,
                       timeout_s=None) -> AIResponse:
        """CLASSIFICATION task. Reasoning defaults OFF wherever the provider
        permits it (engine resolves "default" per task)."""
        req = AIRequest(
            task="classify",
            messages=[AIMessage(role="user", content=query)],
            system_prompt=system_prompt,
            max_output_tokens=max_output_tokens,
            temperature=temperature,
            reasoning="default",
            timeout_s=timeout_s)
        return await engine.execute_request(req)

    # ── Status for Admin/diagnostics (never secrets) ────────────────────

    def external_ai_enabled(self) -> bool:
        return not engine._kill_switch_on()

    def list_provider_types(self) -> list:
        from .adapters import provider_types
        return provider_types()

    def routes_snapshot(self) -> dict:
        return store.list_routes()


padyar_ai = PadyarAI()
