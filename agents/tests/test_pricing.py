"""Unit tests for cost calculation: made-up prices, no network calls."""

from evaluation.pricing import find_pricing, run_cost

MODELS = [
    {
        "id": "google/gemma-4-26B-A4B-it",
        "providers": [
            {"provider": "scaleway", "pricing": {"input": 0.285, "output": 0.57}},
            {"provider": "featherless-ai"},
        ],
    }
]


def test_finds_the_pinned_providers_price():
    assert find_pricing(MODELS, "google/gemma-4-26B-A4B-it:scaleway") == {"input": 0.285, "output": 0.57}


def test_unknown_model_provider_or_missing_price_gives_none():
    assert find_pricing(MODELS, "someone/else:scaleway") is None
    assert find_pricing(MODELS, "google/gemma-4-26B-A4B-it") is None  # no provider pinned
    assert find_pricing(MODELS, "google/gemma-4-26B-A4B-it:novita") is None
    assert find_pricing(MODELS, "google/gemma-4-26B-A4B-it:featherless-ai") is None


def test_cost_is_tokens_times_price_per_million():
    usage = {"input_tokens": 3000, "output_tokens": 1000}
    cost = run_cost(usage, {"input": 0.285, "output": 0.57})
    assert abs(cost - 0.001425) < 1e-12


def test_cost_is_none_without_usage_or_price():
    assert run_cost(None, {"input": 1, "output": 1}) is None
    assert run_cost({"input_tokens": 10}, None) is None
