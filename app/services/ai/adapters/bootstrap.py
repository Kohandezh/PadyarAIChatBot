"""Bootstrap model catalog + pricing, per provider type.

Source: the research files under docs/engineering/ai-providers/research/
(all fetched 2026-08-18 from official documentation). NOTHING here is from
model memory — every model id and price is copied from a research file.

Bootstrap rows are CACHED INITIAL METADATA, never truth:
  * `Refresh Models` (official discovery) supersedes them where discovery
    exists (OpenAI, Anthropic, Gemini, Kimi, DeepSeek, xAI, Mistral).
  * Z.AI and Qwen have NO discovery endpoint — bootstrap + manual entry is
    the documented answer there.
  * Prices are stored as pricing rows effective 2026-08-18 with
    source "bootstrap: research 2026-08-18" so an operator can tell them
    from verified/refreshed data.
  * UNKNOWN pricing is simply absent — it renders as N/A, never as a guess.
  * UNKNOWN context/max-output is likewise absent (None). A number here means
    the research file states it for that exact model id. Nothing is inferred
    from a sibling model or from the model's generation.

Structure per entry:
    model_id, display_name, status, flags..., context_window,
    max_output_tokens, pricing: {currency, in, cached_in (or None), out}

KNOWN UNMODELLED PRICING (the table has ONE rate triple per model; these are
the documented cases it cannot express — every one of them is a real limit of
the cost figures this system reports, not a rounding detail):

  * DeepSeek off-peak (UTC 16:30–00:30) is 50% off. Stored = documented PEAK
    rate → cost is an UPPER bound; real spend can be up to 2× lower.
  * xAI doubles the WHOLE request at ≥200k prompt tokens (research/xai.md
    §Pricing, field `long_context_threshold`). Stored = the <200k rate →
    cost is a LOWER bound and UNDER-reports by 2× on long prompts. Discovery
    returns the long-context rates; the pricing table cannot hold them yet.
  * Gemini 2.5-pro is likewise tiered at >200k (1.25→2.50 in / 10→15 out).
    Stored = the ≤200k rate → same 2×-ish under-report on long prompts.
  * Gemini 3.7/3.6-flash prices are PROMOTIONAL through 2026-12-31 and
    DOUBLE on 2027-01-01. Stored = the promo rate. Because pricing is
    time-versioned, the correct fix is a second row with
    effective_from = 2027-01-01, not an edit here.
  * Qwen is input-LENGTH tiered (≤256K vs 256K–1M, up to 4× more) and legacy
    `qwen-plus` charges ~3.3× more for output in thinking mode. Stored = the
    cheapest tier, non-thinking → LOWER bound.
  * Anthropic cache WRITES cost 1.25× (5m) / 2× (1h) the base input rate.
    The table has only a cache-READ ("cached_input") rate, so cache-creation
    tokens are billed at the plain input rate → UNDER-reports a cache write
    by 25–100%. See pricing.py.
  * Kimi/Qwen/Z.AI mainland-platform prices differ from the international
    ones stored here; a mainland instance's cost figures will be wrong.
"""
from datetime import datetime, timezone

EFFECTIVE_FROM = datetime(2026, 8, 18, tzinfo=timezone.utc)
SOURCE = "bootstrap: research 2026-08-18"

AVAILABLE = "available"
DEPRECATED = "deprecated"
LEGACY = "legacy"

_B = {  # helper to keep the tables readable
    "id": "", "name": "", "status": AVAILABLE, "reasoning": False,
    "tools": False, "structured": False, "vision": False,
    "ctx": None, "maxout": None, "pricing": None,
}


def _m(model_id, name, status=AVAILABLE, **kw):
    d = dict(_B, id=model_id, name=name, status=status, **kw)
    return d


def _p(currency, in_, cached_in, out):
    return {"currency": currency, "in": in_, "cached_in": cached_in, "out": out}


USD = "USD"

BOOTSTRAP_MODELS = {
    "openai": [
        _m("gpt-5.6-sol", "GPT-5.6 Sol", ctx=1_050_000, maxout=128_000,
           reasoning=True, tools=True, structured=True,
           pricing=_p(USD, 5.00, 0.50, 30.00)),
        _m("gpt-5.6-terra", "GPT-5.6 Terra", ctx=1_050_000, maxout=128_000,
           reasoning=True, tools=True, structured=True,
           pricing=_p(USD, 2.00, 0.20, 12.00)),
        _m("gpt-5.6-luna", "GPT-5.6 Luna", ctx=1_050_000, maxout=128_000,
           reasoning=True, tools=True, structured=True,
           pricing=_p(USD, 0.20, 0.02, 1.20)),
        # Deprecated GPT-5 generation; snapshot gpt-5-nano-2025-08-07 shuts
        # down 2026-12-11 (research/openai.md). Context/max-output not stated
        # on the pricing page → left unknown.
        _m("gpt-5-nano", "GPT-5 Nano", DEPRECATED,
           pricing=_p(USD, 0.05, 0.005, 0.40)),
        _m("gpt-4.1", "GPT-4.1", LEGACY,
           pricing=_p(USD, 2.00, 0.50, 8.00)),
    ],
    "anthropic": [
        _m("claude-fable-5", "Claude Fable 5", ctx=1_000_000, maxout=128_000,
           reasoning=True, structured=True,
           pricing=_p(USD, 10.00, 1.00, 50.00)),
        _m("claude-opus-5", "Claude Opus 5", ctx=1_000_000, maxout=128_000,
           reasoning=True, structured=True,
           pricing=_p(USD, 5.00, 0.50, 25.00)),
        _m("claude-sonnet-5", "Claude Sonnet 5", ctx=1_000_000, maxout=128_000,
           reasoning=True, structured=True,
           pricing=_p(USD, 2.00, 0.20, 10.00)),
        _m("claude-haiku-4-5-20251001", "Claude Haiku 4.5", ctx=200_000,
           maxout=64_000, structured=True,
           pricing=_p(USD, 1.00, 0.10, 5.00)),
        # "Legacy but still available" (research/anthropic.md). The models
        # overview publishes context/max-output only for the CURRENT four —
        # so context stays unknown here rather than being assumed to be 200k.
        _m("claude-opus-4-8", "Claude Opus 4.8", LEGACY,
           pricing=_p(USD, 5.00, 0.50, 25.00)),
        _m("claude-sonnet-4-6", "Claude Sonnet 4.6", LEGACY,
           pricing=_p(USD, 3.00, 0.30, 15.00)),
    ],
    # research/gemini.md lists the exact model ids and their prices but does
    # NOT publish a context window for any of them, so every ctx here is
    # None. `Refresh Models` (GET /v1beta/models) fills it in from the
    # provider — a guessed 1M would have been model memory, not research.
    # Only `gemini-3.5-flash` is documented as "Legacy Flash model"; the
    # flash-lite variants are current GA and are marked available.
    "gemini": [
        # 3.7/3.6 prices are promotional and double on 2027-01-01 (research).
        _m("gemini-3.7-flash", "Gemini 3.7 Flash",
           reasoning=True, structured=True,
           pricing=_p(USD, 0.75, 0.075, 3.75)),
        _m("gemini-3.6-flash", "Gemini 3.6 Flash",
           reasoning=True, structured=True,
           pricing=_p(USD, 0.75, 0.075, 3.75)),
        _m("gemini-3.5-flash", "Gemini 3.5 Flash", LEGACY,
           reasoning=True, structured=True,
           pricing=_p(USD, 1.50, 0.15, 9.00)),
        _m("gemini-3.5-flash-lite", "Gemini 3.5 Flash Lite",
           structured=True, pricing=_p(USD, 0.30, 0.03, 2.50)),
        _m("gemini-3.1-flash-lite", "Gemini 3.1 Flash Lite",
           structured=True, pricing=_p(USD, 0.25, 0.025, 1.50)),
        # ≤200k tier; >200k is $2.50 in / $15.00 out — see the docstring.
        _m("gemini-2.5-pro", "Gemini 2.5 Pro", reasoning=True,
           structured=True,
           pricing=_p(USD, 1.25, 0.125, 10.00)),
        _m("gemini-2.5-flash", "Gemini 2.5 Flash",
           structured=True, pricing=_p(USD, 0.30, 0.03, 2.50)),
        _m("gemini-2.5-flash-lite", "Gemini 2.5 Flash Lite",
           structured=True, pricing=_p(USD, 0.10, 0.01, 0.40)),
    ],
    "zai": [
        # No discovery endpoint — this list IS the documented catalog.
        # 1M context is documented for glm-5.3 only; its max-output is not
        # published (128K max output is documented for the 4.7 line).
        _m("glm-5.3", "GLM-5.3", ctx=1_000_000,
           reasoning=True, tools=True, structured=True,
           pricing=_p(USD, 1.40, 0.26, 4.40)),
        _m("glm-5.2", "GLM-5.2", pricing=_p(USD, 1.40, 0.26, 4.40)),
        _m("glm-5.1", "GLM-5.1", pricing=_p(USD, 1.40, 0.26, 4.40)),
        _m("glm-5", "GLM-5", pricing=_p(USD, 1.00, 0.20, 3.20)),
        _m("glm-5-turbo", "GLM-5-Turbo", pricing=_p(USD, 1.20, 0.24, 4.00)),
        _m("glm-4.7", "GLM-4.7", ctx=200_000, maxout=131_072, tools=True,
           structured=True, pricing=_p(USD, 0.60, 0.11, 2.20)),
        _m("glm-4.7-flash", "GLM-4.7-Flash (رایگان)", ctx=200_000,
           maxout=131_072, tools=True, structured=True,
           pricing=_p(USD, 0.0, 0.0, 0.0)),
        _m("glm-4.7-flashx", "GLM-4.7-FlashX", pricing=_p(USD, 0.07, 0.01, 0.40)),
        _m("glm-4.6", "GLM-4.6", LEGACY, pricing=_p(USD, 0.60, 0.11, 2.20)),
        _m("glm-4.5", "GLM-4.5", LEGACY, pricing=_p(USD, 0.60, 0.11, 2.20)),
        _m("glm-4.5-air", "GLM-4.5-Air", LEGACY,
           pricing=_p(USD, 0.20, 0.03, 1.10)),
    ],
    "kimi": [
        _m("kimi-k3", "Kimi K3", ctx=1_048_576, reasoning=True, tools=True,
           structured=True, vision=True,
           pricing=_p(USD, 3.00, 0.30, 15.00)),
        _m("kimi-k2.7-code", "Kimi K2.7 Code", ctx=262_144, reasoning=True,
           tools=True, structured=True, vision=True,
           pricing=_p(USD, 0.95, 0.19, 4.00)),
        _m("kimi-k2.7-code-highspeed", "Kimi K2.7 Code Highspeed", ctx=262_144,
           reasoning=True, tools=True, structured=True, vision=True,
           pricing=_p(USD, 1.90, 0.38, 8.00)),
        _m("kimi-k2.6", "Kimi K2.6", ctx=262_144, reasoning=True, tools=True,
           structured=True, vision=True,
           pricing=_p(USD, 0.95, 0.16, 4.00)),
        # Sunsetting "August 31" (year unstated in docs — see research Unknowns).
        _m("kimi-k2.5", "Kimi K2.5", DEPRECATED, ctx=262_144,
           pricing=_p(USD, 0.60, 0.10, 3.00)),
        # moonshot-v1 context comes from the model id itself (8k/32k/128k),
        # which is the only place the docs state it for these three.
        _m("moonshot-v1-8k", "Moonshot v1 8K", DEPRECATED, ctx=8_192,
           pricing=_p(USD, 0.20, None, 2.00)),
        _m("moonshot-v1-32k", "Moonshot v1 32K", DEPRECATED, ctx=32_768,
           pricing=_p(USD, 1.00, None, 3.00)),
        _m("moonshot-v1-128k", "Moonshot v1 128K", DEPRECATED, ctx=131_072,
           pricing=_p(USD, 2.00, None, 5.00)),
    ],
    "deepseek": [
        # Peak rates; documented off-peak (UTC 01-04, 06-10) is 50% off.
        _m("deepseek-v4-flash", "DeepSeek V4 Flash", ctx=1_000_000,
           maxout=384_000, reasoning=True, tools=True, structured=True,
           pricing=_p(USD, 0.44, 0.014, 1.32)),
        _m("deepseek-v4-pro", "DeepSeek V4 Pro", ctx=1_000_000,
           maxout=384_000, reasoning=True, tools=True, structured=True,
           pricing=_p(USD, 1.32, 0.044, 3.96)),
    ],
    # International (Singapore) list prices; Beijing is ~2-4x cheaper and
    # input-length tiered — see research/qwen.md §Pricing.
    "qwen": [
        _m("qwen3.7-max", "Qwen 3.7 Max", ctx=1_000_000, reasoning=True,
           tools=True, structured=True,
           pricing=_p(USD, 2.50, None, 7.50)),
        _m("qwen3.7-plus", "Qwen 3.7 Plus", ctx=1_000_000, reasoning=True,
           tools=True, structured=True,
           pricing=_p(USD, 0.40, None, 1.60)),
        _m("qwen3.6-flash", "Qwen 3.6 Flash", ctx=1_000_000, reasoning=True,
           tools=True, structured=True,
           pricing=_p(USD, 0.25, None, 1.50)),
        _m("qwen3.5-flash", "Qwen 3.5 Flash", ctx=1_000_000, reasoning=True,
           tools=True, structured=True,
           pricing=_p(USD, 0.10, None, 0.40)),
        _m("qwen-plus", "Qwen Plus (Legacy)", LEGACY, ctx=1_000_000,
           pricing=_p(USD, 0.40, None, 1.20)),
        _m("qwen-max", "Qwen Max (Legacy)", LEGACY, ctx=128_000,
           pricing=_p(USD, 1.60, None, 6.40)),
        _m("qwen-flash", "Qwen Flash (Legacy)", LEGACY, ctx=1_000_000,
           pricing=_p(USD, 0.05, None, 0.40)),
    ],
    "xai": [
        # <200k-prompt rates; the whole request doubles at ≥200k prompt tokens.
        _m("grok-4.6", "Grok 4.6", ctx=500_000, reasoning=True, tools=True,
           structured=True, vision=True,
           pricing=_p(USD, 2.00, 0.50, 6.00)),
        _m("grok-4.5", "Grok 4.5", LEGACY, ctx=500_000, reasoning=True,
           tools=True, structured=True, vision=True,
           pricing=_p(USD, 2.00, 0.30, 6.00)),
        _m("grok-4.3", "Grok 4.3", ctx=1_000_000, reasoning=True, tools=True,
           structured=True, vision=True,
           pricing=_p(USD, 1.25, 0.20, 2.50)),
        _m("grok-4.20-0309-reasoning", "Grok 4.20 Reasoning", ctx=1_000_000,
           reasoning=True, pricing=_p(USD, 1.25, 0.20, 2.50)),
        _m("grok-4.20-0309-non-reasoning", "Grok 4.20 Non-Reasoning",
           ctx=1_000_000, pricing=_p(USD, 1.25, 0.20, 2.50)),
    ],
    "mistral": [
        _m("mistral-large-2512", "Mistral Large 3", ctx=256_000, tools=True,
           structured=True, pricing=_p(USD, 0.50, 0.05, 1.50)),
        _m("mistral-medium-3-5", "Mistral Medium 3.5", ctx=256_000,
           reasoning=True, tools=True, structured=True,
           pricing=_p(USD, 1.50, 0.15, 7.50)),
        _m("mistral-small-2603", "Mistral Small 4", ctx=256_000, tools=True,
           structured=True, pricing=_p(USD, 0.15, 0.015, 0.60)),
        _m("ministral-14b-2512", "Ministral 3 14B", ctx=256_000,
           pricing=_p(USD, 0.20, 0.02, 0.20)),
        _m("ministral-8b-2512", "Ministral 3 8B", ctx=256_000,
           pricing=_p(USD, 0.15, 0.015, 0.15)),
        _m("ministral-3b-2512", "Ministral 3 3B", ctx=256_000,
           pricing=_p(USD, 0.10, 0.01, 0.10)),
    ],
    # openai_compatible: the endpoint's own model list is unknown by
    # definition — discovery or manual entry, never a hardcoded catalog.
    "openai_compatible": [],
    # sakoo: GET /v1/models is documented and authoritative; the catalog is
    # populated by Admin → Refresh Models from the whitelisted environment.
    # rayen-gemma4-31b / rayen-jina-v5 appear in the docs only as EXAMPLES
    # and are deliberately not seeded here.
    "sakoo": [],
}


def pricing_rows() -> list:
    """Flatten bootstrap pricing into (provider_type, model_id, currency,
    input, cached_input, output) tuples for seeding ai_model_pricing."""
    rows = []
    for ptype, models in BOOTSTRAP_MODELS.items():
        for m in models:
            p = m.get("pricing")
            if not p:
                continue
            rows.append((ptype, m["id"], p["currency"], p["in"],
                         p.get("cached_in"), p["out"]))
    return rows
