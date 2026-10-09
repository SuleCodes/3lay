"""Scores the latest saved run for each email against the gold standard, document by document.

For each email (tmp/runs/{email}_*.json, saved by run_orchy.py), takes the
newest run and scores each document's extraction against
tmp/gold_standard/{email}-{index}.json. For the first document,
tmp/gold_standard/{email}.json also works (the single-document naming used
before emails). Prints the run's cost and, per document, the score and the
fields that didn't match. Costs use the provider's current price from
Hugging Face's router.
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


def latest_runs():
    """The newest run per email; names are {email}_{timestamp}.json and sort in time order."""
    latest = {}
    for path in sorted(RUNS_DIR.glob("*_*.json")):
        latest[path.name.rsplit("_", 1)[0]] = path
    return latest


def documents_of(run):
    """Runs list their documents; runs from before emails had a single "output"."""
    if "documents" in run:
        return run["documents"]
    return [{"index": 0, "name": run.get("fixture"), "status": run.get("status"),
             "output": run.get("output"), "error_message": run.get("parsing_error")}]


def gold_for(stem, index):
    candidates = [GOLD_DIR / f"{stem}-{index}.json"]
    if index == 0:
        candidates.append(GOLD_DIR / f"{stem}.json")
    return next((path for path in candidates if path.exists()), None)


def describe_cost(models, run):
    """e.g. "$0.001425 (3000 in, 1000 out at $0.285/$0.57 per 1M tokens)"."""
    if models is None:
        return "unavailable (couldn't fetch prices from Hugging Face)"
    usage = run.get("usage")
    pricing = find_pricing(models, run.get("model") or "")
    cost = run_cost(usage, pricing)
    if cost is None:
        reason = ("no token usage recorded" if not usage
                  else "no price listed for this model:provider")
        return f"unknown ({reason})"
    return (f"${cost:.6f} ({usage.get('input_tokens', 0)} in, {usage.get('output_tokens', 0)} out"
            f" at ${pricing['input']:g}/${pricing['output']:g} per 1M tokens)")


def main():
    runs = latest_runs()
    if not runs:
        print(f"No runs in {RUNS_DIR}: run scripts/run_orchy.py first.")
        return

    try:
        models = fetch_models()
    except OSError:
        models = None

    for stem, run_path in runs.items():
        run = json.loads(run_path.read_text(encoding="utf-8"))
        print(f"{stem}  ({run_path.name})")
        print(f"  model:    {run.get('model')}")
        print(f"  status:   {run.get('status')}   time: {run.get('duration_seconds')}s")
        print(f"  cost:     {describe_cost(models, run)}")

        for document in documents_of(run):
            label = f"  document {document['index']} ({document['name']})"
            gold_path = gold_for(stem, document["index"])
            if gold_path is None:
                print(f"{label}: no gold standard ({stem}-{document['index']}.json)")
                continue
            if document.get("output") is None:
                print(f"{label}: no output: {document.get('error_message')}")
                continue
            gold = json.loads(gold_path.read_text(encoding="utf-8"))
            result = score(gold, document["output"])
            print(f"{label}: {result['percent']}%  ({result['matched']}/{result['total']} fields,"
                  f" against {gold_path.name})")
            for path, expected, got in result["mismatches"]:
                print(f"    {path}: expected {expected!r}, got {got!r}")
        print()


if __name__ == "__main__":
    main()
