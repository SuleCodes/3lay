"""Starts a gold standard file for an email from its latest run, for you to correct by hand.

    python scripts/make_gold.py 1767784629512          (the email's file name, without .eml)
    python scripts/make_gold.py 1767784629512 --force  (replace an existing gold file)

A gold standard says what a perfect run of this email looks like:

    {
      "verified": false,            <- set to true once you've checked it against the document
      "status": "completed",        <- the event's expected status
      "error_code": null,
      "route": ["load_inputs", "extract", "validate", "finish"],   <- with no retries
      "documents": [
        {"index": 0, "name": "...", "status": "done", "error_code": null,
         "output": {...},           <- the correct extraction, field by field
         "checks": {"matches_schema": true, "dates_plausible": true, ...}}
      ]
    }

It's started from the model's own output, which is exactly what must NOT be
trusted: correct every field against the document, then set "verified": true.
Outputs already in an existing gold file (either format) are kept, so your
corrections survive a rebuild. Expected checks aren't copied from the run: they're
worked out by running the schema check and the client's rules on the output,
so they say what the checks should report for a correct extraction. Run this
again with --force after correcting the output to recompute them.
"""

import argparse
import json
import sys
from pathlib import Path

AGENTS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AGENTS_DIR))  # so the packages import when run as a script

# pylint: disable=wrong-import-position
from evaluate import GOLD_DIR, latest_runs  # noqa: E402
from run_orchy import RULES_PATH, SCHEMA_PATH  # noqa: E402
from orchy.nodes import schema_check  # noqa: E402
from rules import check_rule_definitions, run_rules  # noqa: E402

HAPPY_ROUTE_PER_DOCUMENT = ["extract", "validate"]


def expected_checks(output, schema, rules):
    if output is None:
        return {}
    checks = [schema_check(output, schema), *run_rules(rules, output)]
    return {c["name"]: c["passed"] for c in checks}


def happy_route(documents):
    """The route with no retries: each usable document extracted once, then validated."""
    usable = sum(1 for d in documents if d["status"] == "done")
    if usable == 0:
        return ["load_inputs", "finish"]
    return ["load_inputs", *["extract"] * usable, *["validate"] * usable, "finish"]


def existing_outputs(gold):
    """{document index: output} from an existing gold file, either format, so
    outputs you've already corrected by hand are kept when it's rebuilt."""
    if gold is None:
        return {}
    if "documents" in gold:
        return {d["index"]: d["output"] for d in gold["documents"]}
    return {0: gold}  # the older format: the file is just document 0's extraction


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("email", help="the email's file name without .eml")
    parser.add_argument("--force", action="store_true", help="replace an existing gold file")
    args = parser.parse_args()

    run_path = latest_runs().get(args.email)
    if run_path is None:
        sys.exit(f"No runs for {args.email}: run scripts/run_orchy.py on it first.")
    run = json.loads(run_path.read_text(encoding="utf-8"))
    if "documents" not in run:
        sys.exit(f"{run_path.name} is from before emails: run scripts/run_orchy.py again.")

    gold_path = GOLD_DIR / f"{args.email}.json"
    existing = json.loads(gold_path.read_text(encoding="utf-8")) if gold_path.exists() else None
    if existing is not None and "documents" in existing and not args.force:
        sys.exit(f"{gold_path} already exists: use --force to rebuild it.")
    kept = existing_outputs(existing)

    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    rules = (check_rule_definitions(json.loads(RULES_PATH.read_text(encoding="utf-8")))
             if RULES_PATH.exists() else [])

    documents = []
    for d in run["documents"]:
        output = kept.get(d["index"], d["output"])
        documents.append({
            "index": d["index"],
            "name": d["name"],
            "status": d["status"],
            "error_code": d["error_code"],
            "output": output,
            "checks": expected_checks(output, schema, rules),
        })

    gold = {
        "verified": False,
        "status": run["status"],
        "error_code": run["error_code"],
        "route": happy_route(documents),
        "documents": documents,
    }
    gold_path.parent.mkdir(parents=True, exist_ok=True)
    gold_path.write_text(json.dumps(gold, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {gold_path} from {run_path.name}"
          + (f" (kept outputs for documents {sorted(kept)} from the existing gold file)"
             if kept else ""))
    print("Check every value against the document, then set \"verified\": true.")


if __name__ == "__main__":
    main()
