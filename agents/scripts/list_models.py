"""Lists Hugging Face models whose provider supports structured output.

Hugging Face's website can't filter by structured output support, but the
router's model list says, for each model, which providers support it and what
they charge.

  python scripts/list_models.py                    vision models with structured output
  python scripts/list_models.py --all              include text-only models
  python scripts/list_models.py --search qwen      only model ids containing "qwen"
  python scripts/list_models.py --check google/gemma-4-26B-A4B-it:scaleway
"""

import argparse
import json
import sys
import urllib.request

ROUTER_MODELS_URL = "https://router.huggingface.co/v1/models"


def fetch_models():
    with urllib.request.urlopen(ROUTER_MODELS_URL, timeout=30) as response:
        return json.load(response)["data"]


def reads_images(model):
    return "image" in (model.get("architecture") or {}).get("input_modalities", [])


def price(provider):
    pricing = provider.get("pricing")
    if not pricing:
        return "price not listed"
    return f"${pricing['input']:g} in / ${pricing['output']:g} out per 1M tokens"


def check(models, model_and_provider):
    """Says whether one model:provider supports structured output. Exit code 1 if not."""
    model_id, _, provider_name = model_and_provider.partition(":")
    model = next((m for m in models if m["id"] == model_id), None)
    if model is None:
        print(f"{model_id} isn't in the router's model list.")
        return 1
    providers = {p["provider"]: p for p in model.get("providers", [])}
    supported = sorted(name for name, p in providers.items()
                       if p.get("status") == "live" and p.get("supports_structured_output"))
    if not provider_name:
        print(f"{model_id}: no provider given. Structured output: {', '.join(supported) or 'none'}")
        return 0 if supported else 1
    provider = providers.get(provider_name)
    if provider is None:
        print(f"{provider_name} doesn't serve {model_id}. Providers: {', '.join(providers)}")
        return 1
    if provider_name in supported:
        print(f"OK: {model_id} on {provider_name} supports structured output ({price(provider)}).")
        return 0
    print(f"NO: {model_id} on {provider_name} doesn't support structured output. "
          f"Use one of: {', '.join(supported) or 'none'}")
    return 1


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--all", action="store_true", help="include models that can't read images")
    parser.add_argument("--search", help="only model ids containing this text")
    parser.add_argument("--check", metavar="MODEL:PROVIDER", help="check one model and provider")
    args = parser.parse_args()

    models = fetch_models()
    if args.check:
        return check(models, args.check)

    rows = []
    for model in models:
        if not args.all and not reads_images(model):
            continue
        if args.search and args.search.lower() not in model["id"].lower():
            continue
        for provider in model.get("providers", []):
            if provider.get("status") == "live" and provider.get("supports_structured_output"):
                output_price = (provider.get("pricing") or {}).get("output", float("inf"))
                rows.append((output_price, model["id"], provider))

    for _, model_id, provider in sorted(rows, key=lambda r: (r[0], r[1])):
        print(f"{model_id}:{provider['provider']}  ({price(provider)})")
    print(f"\n{len(rows)} model:provider combinations with structured output"
          + ("" if args.all else " that can read images"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
