"""Padyar model-provider abstraction — the runtime seam that keeps business
logic independent of any single AI vendor.

Layers, in the order the chat pipeline consults them:

    1. LocalRetrievalProvider   — curated KB + local embeddings + trained
                                  intent classifier. Zero external calls.
    2. OpenAICompatProvider     — ANY OpenAI-compatible endpoint. The base
                                  URL and key are per-install settings
                                  (admin panel → Settings → AI), so the same
                                  build runs against a commercial proxy, the
                                  national open AI platform, or a self-hosted
                                  gateway (vLLM / Ollama / LiteLLM) without a
                                  code change.

Design rules enforced here:
- No module outside app/services may import the OpenAI SDK directly.
- Data classification: chat messages are the only payload ever sent to the
  external provider; admin credentials, settings and logs never leave the
  host. `classify_data_policy` documents this contract in code.
- Health checks are on-demand (never in the request path) — see
  /api/ready in app/routers/public.py.
"""
import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ProviderStatus:
    name: str
    kind: str                     # "local" | "openai_compatible"
    available: bool
    detail: str = ""
    latency_ms: Optional[float] = None
    checked_at: float = field(default_factory=time.time)

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "kind": self.kind,
            "available": self.available,
            "detail": self.detail,
            "latency_ms": self.latency_ms,
        }


class LocalRetrievalProvider:
    """The proprietary local layer: KB retrieval, embeddings, intent model."""

    name = "padyar-local"
    kind = "local"

    def classify_data_policy(self) -> str:
        return "on_host_only"  # nothing leaves the machine

    def health_check(self) -> ProviderStatus:
        t0 = time.perf_counter()
        try:
            from app.services import search
            ok = bool(search.dataset)
            detail = (
                f"dataset={len(search.dataset)} entries, "
                f"backend={'embedding' if search.dataset_embedding_index is not None else 'tfidf'}, "
                f"intent={'trained' if search.intent_classifier is not None else 'off'}"
            )
            return ProviderStatus(self.name, self.kind, ok, detail,
                                  (time.perf_counter() - t0) * 1000)
        except Exception as e:  # noqa: BLE001
            return ProviderStatus(self.name, self.kind, False, f"{type(e).__name__}: {e}")


class ExternalAIStatus:
    """Public-safe readiness view of the external AI layer.

    This is NOT a provider any more. Since the AI Control Plane landed, the
    provider layer lives in `app/services/ai/` and every real call goes
    Padyar → wrapper → engine → adapter. What remains here is a read-only
    status shim for `/api/ready`, kept because that endpoint predates the
    control plane and orchestrators may already poll it.

    The identifiers below are deliberately vendor-neutral. `/api/ready` needs
    no authentication, and the product rule is that anyone outside the admin
    panel sees PADYAR — never which vendor is answering. The old
    "openai-compatible" name said the quiet part out loud to every caller.
    """

    name = "external-ai"
    kind = "external"

    def classify_data_policy(self) -> str:
        return "chat_messages_only"  # never settings, credentials or logs

    def configured(self) -> bool:
        """True when the control plane has at least one usable instance.

        Reads the control plane rather than the legacy `ai_api_*` settings so
        this cannot claim "configured" on the strength of a stale setting the
        runtime no longer uses.
        """
        try:
            from app.services.ai import store
            return any(i["enabled"] and i["has_secret"]
                       for i in store.list_instances())
        except Exception:  # noqa: BLE001 — readiness must never 500
            return False

    async def health_check(self) -> ProviderStatus:
        """External-AI readiness, DERIVED from recorded state — never a call.

        This used to open an httpx client and GET `{base}/models` with the
        API key attached. Two things were wrong with that, and both were
        reachable by an anonymous visitor through `/api/ready?deep=true`:

        1. It was an authenticated outbound request to an operator-supplied
           URL originating OUTSIDE `app/services/ai/adapters/`. That is the
           one place SSRF validation (`ai/endpoint_policy`), the
           no-auto-redirect rule, the circuit breaker and usage accounting
           all live — so this path had none of them. Anyone on the internet
           could make the server dial its provider, repeatedly, for free.

        2. `detail` was f"HTTP {status} from {base}", publishing the
           configured gateway URL to an unauthenticated caller. The product
           rule is that a visitor sees PADYAR and never the vendor behind it.

        Readiness does not need a live call. `ai/health.instance_health()`
        already derives health from the circuit state and the recorded
        24h usage window, which is *better* evidence than a synthetic probe:
        it reflects what real traffic actually experienced. So this reports
        that, makes no network request, and names no vendor.
        """
        try:
            from app.services.ai import health as ai_health
            rows = ai_health.provider_rows_for_admin()
        except Exception:  # noqa: BLE001 — readiness must never 500
            return ProviderStatus(self.name, self.kind, False, "unavailable")

        if not rows:
            return ProviderStatus(self.name, self.kind, False, "not configured")

        serving = [r for r in rows if r["health"] == "healthy"]
        degraded = [r for r in rows if r["health"] == "degraded"]

        if serving:
            detail = "ready"
        elif degraded:
            detail = "degraded"
        else:
            detail = "unavailable"

        # Counts only. No instance name, no provider type, no URL, no model —
        # this response is public.
        return ProviderStatus(self.name, self.kind, bool(serving), detail)


local_provider = LocalRetrievalProvider()
# Name kept as `external_provider` because `app/routers/public.py` imports it;
# the CLASS was renamed to ExternalAIStatus when its live-probe was removed.
external_provider = ExternalAIStatus()


def provider_chain() -> list:
    """Runtime consultation order. The local layer always comes first; the
    external adapter participates only when configured AND enabled."""
    from app.db.queries import get_setting
    chain = [local_provider]
    if get_setting("openai_enabled", "true") == "true" and external_provider.configured():
        chain.append(external_provider)
    return chain
