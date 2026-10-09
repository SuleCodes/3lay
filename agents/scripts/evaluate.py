"""Scores the latest saved run for each email against its gold standard.

For each email (tmp/runs/{email}_*.json, saved by run_orchy.py), takes the
newest run and compares it with tmp/gold_standard/{email}.json (start one with
scripts/make_gold.py):

- the event's status (and error code)
- per document: its status, its extraction field by field (the score), and
  whether each check came out as expected
- the route through the graph, against the route with no retries. Not scored:
  retries depend on the provider on the day, but they're shown.

Also prints the run's cost, using the provider's current price from Hugging
Face's router. An older gold file (just the extraction) still scores document 0.
"""

import json
import sys
from pathlib import Path

AGENTS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AGENTS_DIR))  # so the packages import when run as a script

# pylint: disable=wrong-import-position
from evaluation.pricing import fetch_models, find_pricing, run_cost  # noqa: E402
from evaluation.checks import compare_checks  # noqa: E402
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
             "output": run.get("output"), "checks": run.get("checks", []),
             "error_message": run.get("parsing_error")}]


def load_gold(stem):
    """The gold standard, in the current format. An older file is just document 0's output."""
    path = GOLD_DIR / f"{stem}.json"
    if not path.exists():
        return None
    gold = json.loads(path.read_text(encoding="utf-8"))
    if "documents" not in gold:
        return {"verified": False, "documents": [{"index": 0, "output": gold}]}
    return gold


def route_of(run):
    return [step["node"] for step in run.get("steps", [])]


def mark(expected, actual):
    return "ok  " if expected == actual else "DIFF"


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


def report_document(document, gold_document):
    print(f"  document {document['index']} ({document['name']})")
    if "status" in gold_document:
        print(f"    {mark(gold_document['status'], document['status'])} status: "
              f"expected {gold_document['status']}, got {document['status']}")

    if gold_document.get("output") is None:
        print("    (no expected output)")
    elif document.get("output") is None:
        print(f"    DIFF data: no output ({document.get('error_message')})")
    else:
        result = score(gold_document["output"], document["output"])
        print(f"    data:   {result['percent']}%  ({result['matched']}/{result['total']} fields)")
        for path, expected, got in result["mismatches"]:
            print(f"      {path}: expected {expected!r}, got {got!r}")

    if "checks" in gold_document:
        matched, total, differences = compare_checks(gold_document["checks"],
                                                     document.get("checks", []))
        print(f"    checks: {matched}/{total} as expected")
        for name, expected, got in differences:
            print(f"      {name}: expected {expected}, got {got}")


def report(stem, run_path, models):
    run = json.loads(run_path.read_text(encoding="utf-8"))
    gold = load_gold(stem)
    print(f"{stem}  ({run_path.name})")
    print(f"  model:  {run.get('model')}   time: {run.get('duration_seconds')}s")
    print(f"  cost:   {describe_cost(models, run)}")

    if gold is None:
        print(f"  no gold standard: start one with scripts/make_gold.py {stem}\n")
        return
    if not gold.get("verified"):
        print("  NOTE: gold standard not verified yet; scores are only as good as it is")

    if "status" in gold:
        print(f"  {mark(gold['status'], run.get('status'))} status: expected {gold['status']}"
              f" ({gold.get('error_code')}), got {run.get('status')} ({run.get('error_code')})")
    if "route" in gold:
        route = route_of(run)
        retries = len(route) - len(gold["route"])
        note = "as expected" if route == gold["route"] else (
            f"{retries} extra step(s), e.g. retries" if retries > 0 else "different")
        print(f"  route:  {note}: {' -> '.join(route)}")

    documents = {d["index"]: d for d in documents_of(run)}
    for gold_document in gold["documents"]:
        document = documents.get(gold_document["index"])
        if document is None:
            print(f"  document {gold_document['index']}: DIFF missing from the run")
            continue
        report_document(document, gold_document)
    for index in sorted(set(documents) - {d["index"] for d in gold["documents"]}):
        print(f"  document {index}: DIFF in the run but not in the gold standard")
    print()


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
        report(stem, run_path, models)


if __name__ == "__main__":
    main()
