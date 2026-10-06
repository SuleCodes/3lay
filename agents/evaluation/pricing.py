"""Live Hugging Face prices, and what a run cost.

The router's model list gives each provider's current price per million tokens,
so costs are worked out from the tokens a run reported rather than hard-coded
prices, which change.
"""

import json
import urllib.request

ROUTER_MODELS_URL = "https://router.huggingface.co/v1/models"


def fetch_models():
    """Every model on Hugging Face's router, with its providers and their prices."""
    with urllib.request.urlopen(ROUTER_MODELS_URL, timeout=30) as response:
        return json.load(response)["data"]


def find_pricing(models, model_and_provider):
    """{"input": ..., "output": ...} in dollars per million tokens, or None if unknown.

    model_and_provider is the OBED_MODEL form, e.g. "google/gemma-4-26B-A4B-it:scaleway".
    """
    model_id, _, provider_name = model_and_provider.partition(":")
    model = next((m for m in models if m["id"] == model_id), None)
    if model is None or not provider_name:
        return None
    provider = next((p for p in model.get("providers", []) if p["provider"] == provider_name), None)
    return (provider or {}).get("pricing")


def run_cost(usage, pricing):
    """Dollars for one run, from its reported token usage, or None if either is missing."""
    if not usage or not pricing:
        return None
    return (usage.get("input_tokens", 0) * pricing["input"]
            + usage.get("output_tokens", 0) * pricing["output"]) / 1_000_000
