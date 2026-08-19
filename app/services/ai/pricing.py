"""Cost calculation from the time-versioned pricing table.

Cost is computed AT REQUEST TIME from the price row then in effect and stored
ON the usage row — later price changes cannot rewrite history. The pricing
row's effective_from travels with the usage row for auditability.

Unknown pricing → (None, ""): rendered as N/A everywhere, never guessed.
Cached tokens are priced at the cached rate when one is known; when the
provider reports cached tokens but no cached rate exists, they are priced at
the full input rate (the conservative, never-under-reporting choice).

WHAT THIS MODEL CANNOT EXPRESS (so the number is an estimate, not an invoice):

  * One rate triple per model. Prompt-length tiers are invisible: xAI doubles
    the whole request at ≥200k prompt tokens, Gemini 2.5-pro and Qwen have
    their own tiers. The stored rate is the SHORT-prompt one, so long-prompt
    calls UNDER-report.
  * Time-of-day discounts are invisible: DeepSeek's off-peak 50% means the
    stored peak rate OVER-reports off-peak calls.
  * Cache WRITES are invisible. `cached_input_per_million` is a cache-READ
    rate. Anthropic bills cache creation at 1.25x (5m) / 2x (1h) the input
    rate, and the adapter (correctly) counts cache-creation tokens inside
    tokens_in but NOT inside cached_tokens — so they are billed here at the
    plain input rate and a cache write under-reports by 25-100%.
    Fixing this needs a `cache_write_per_million` column, not a change here.
  * `cost` is one number in one currency. A failover chain that spans two
    providers with different currencies sums only what the engine tracked;
    the engine records the last known currency.

Every one of these is a documented limitation, not a bug to hide: the figure
is a good-faith estimate from published list prices, and the provider's own
billing console remains the authority.
"""
from . import store


def estimate(provider_type: str, model_id: str, tokens_in, tokens_out,
             cached_tokens=None) -> tuple:
    """(cost, currency) for one completed provider call. None cost means
    unknown pricing, not free."""
    row = store.lookup_pricing(provider_type, model_id)
    if not row or tokens_in is None or tokens_out is None:
        return None, ""
    cached = min(cached_tokens or 0, tokens_in or 0)
    uncached = (tokens_in or 0) - cached
    in_rate = float(row["input_per_million"])
    out_rate = float(row["output_per_million"])
    cached_rate = (float(row["cached_input_per_million"])
                   if row["cached_input_per_million"] is not None else in_rate)
    cost = (uncached * in_rate + cached * cached_rate
            + (tokens_out or 0) * out_rate) / 1_000_000
    return round(cost, 8), row["currency"] or ""


def pricing_for(instance: dict, model_id: str) -> dict | None:
    row = store.lookup_pricing(instance["provider_type"], model_id)
    if not row:
        return None
    return {
        "currency": row["currency"],
        "input_per_million": row["input_per_million"],
        "cached_input_per_million": row["cached_input_per_million"],
        "output_per_million": row["output_per_million"],
        "effective_from": row["effective_from"],
        "source": row["source"],
    }
