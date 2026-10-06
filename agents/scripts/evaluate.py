"""Scores the latest saved run for each gold standard file.

For every tmp/gold_standard/{stem}.json, finds the newest tmp/runs/{stem}_*.json
saved by run_obed.py, and prints its score, cost and the fields that didn't match.
Costs use the provider's current price from Hugging Face's router.
"""

import json
import sys
from pathlib import Path

AGENTS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AGENTS_DIR))  # so the packages import when run as a script

# pylint: disable=wrong-import-position
from evaluation.pricing import fetch_models, find_pricing, run_cost  # noqa: E402
from evaluation.scoring import score  # noqa: E402

GOLD_DIR = AGENTS_DIR / "tmp" / "gold_standard"
RUNS_DIR = AGENTS_DIR / "tmp" / "runs"


def latest_run(stem):
    """The newest run for a fixture; timestamped names sort in time order."""
    runs = sorted(RUNS_DIR.glob(f"{stem}_*.json"))
    return runs[-1] if runs else None


def describe_cost(models, run):
    """e.g. "$0.001425 (3000 in, 1000 out at $0.285/$0.57 per 1M tokens)"."""
    if models is None:
        return "unavailable (couldn't fetch prices from Hugging Face)"
    usage = run.get("usage")
    pricing = find_pricing(models, run.get("model") or "")
    cost = run_cost(usage, pricing)
    if cost is None:
        reason = "no token usage recorded" if not usage else "no price listed for this model:provider"
        return f"unknown ({reason})"
    return (f"${cost:.6f} ({usage.get('input_tokens', 0)} in, {usage.get('output_tokens', 0)} out"
            f" at ${pricing['input']:g}/${pricing['output']:g} per 1M tokens)")


def main():
    gold_files = sorted(GOLD_DIR.glob("*.json"))
    if not gold_files:
        print(f"No gold standard files in {GOLD_DIR}")
        return

    try:
        models = fetch_models()
    except OSError:
        models = None

    for gold_path in gold_files:
        stem = gold_path.stem
        run_path = latest_run(stem)
        if run_path is None:
            print(f"{stem}: no runs yet (run scripts/run_obed.py for this fixture first)\n")
            continue

        gold = json.loads(gold_path.read_text(encoding="utf-8"))
        run = json.loads(run_path.read_text(encoding="utf-8"))

        print(f"{stem}  ({run_path.name})")
        print(f"  model:    {run.get('model')}")
        print(f"  time:     {run.get('duration_seconds')}s")
        print(f"  cost:     {describe_cost(models, run)}")

        if run.get("output") is None:
            print(f"  no output: {run.get('parsing_error')}\n")
            continue

        result = score(gold, run["output"])
        print(f"  score:    {result['percent']}%  ({result['matched']}/{result['total']} fields)")
        for path, expected, got in result["mismatches"]:
            print(f"    {path}: expected {expected!r}, got {got!r}")
        print()


if __name__ == "__main__":
    main()
