"""The AI provider registry — ONE place that knows adapter by provider type.

`AI_PROVIDER_REGISTRY` maps provider type keys to adapter classes. Nothing
outside this package imports an adapter by module path, and no `if provider
== "openai"` string-switching exists anywhere in application code.

Registering a future provider = implement an adapter, add one line here.
The wrapper, routing engine, circuit breaker, Admin UI, catalog and usage
dashboard require no changes — that is the extensibility test the phase
demands (SAKOO/Rayen completed 2026-08-20 from the supplied OpenAPI contract).
"""
from .anthropic_adapter import AnthropicAdapter
from .base import BaseAdapter, ConfigField, ProviderMetadata, ProviderRuntime
from .deepseek import DeepSeekAdapter
from .gemini_adapter import GeminiAdapter
from .kimi import KimiAdapter
from .mistral import MistralAdapter
from .openai_adapter import OpenAIAdapter
from .openai_compatible import OpenAICompatibleAdapter
from .qwen import QwenAdapter
from .sakoo import SakooAdapter
from .xai import XAIAdapter
from .zai import ZAIAdapter

AI_PROVIDER_REGISTRY: dict = {
    "openai": OpenAIAdapter,
    "anthropic": AnthropicAdapter,
    "gemini": GeminiAdapter,
    "zai": ZAIAdapter,
    "kimi": KimiAdapter,
    "deepseek": DeepSeekAdapter,
    "qwen": QwenAdapter,
    "xai": XAIAdapter,
    "mistral": MistralAdapter,
    "openai_compatible": OpenAICompatibleAdapter,
    # Architecture slot — no network behaviour exists in this adapter.
    "sakoo": SakooAdapter,
}

_INSTANCES: dict = {}


def adapter_for(provider_type: str) -> BaseAdapter:
    """The (cached) adapter instance for a provider type."""
    key = provider_type or ""
    if key not in _INSTANCES:
        cls = AI_PROVIDER_REGISTRY.get(key)
        if cls is None:
            from ..errors import AIError
            raise AIError(code="invalid_request",
                          provider_detail=f"unknown provider type {key!r}")
        _INSTANCES[key] = cls()
    return _INSTANCES[key]


def provider_types() -> list:
    """Registry entries as admin-facing dicts (metadata + config schema)."""
    out = []
    for key, cls in AI_PROVIDER_REGISTRY.items():
        ad = adapter_for(key)
        meta = ad.metadata()
        out.append({
            "type": key,
            "display_name": meta.display_name,
            "docs_url": meta.docs_url,
            "native": meta.native,
            "supports_discovery": meta.supports_discovery,
            "note": meta.note_fa,
            "config_schema": [f.as_dict() for f in ad.configuration_schema()],
        })
    return out
