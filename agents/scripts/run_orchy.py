"""Runs Orchy's graph on one email and saves the run to tmp/runs/ for scripts/evaluate.py.

    python scripts/run_orchy.py                      the first .eml in tmp/fixtures/
    python scripts/run_orchy.py tmp/fixtures/x.eml   a specific email

The email is a raw .eml, like the ones the ingest Function stores; make one
from PDFs or photos with scripts/make_email.py.

Settings come from agents/.env: OBED_MODEL, OBED_BASE_URL, HF_TOKEN (read by
the extract node), and LANGSMITH_* if you want tracing.
"""

import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

AGENTS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AGENTS_DIR))  # so the packages import when run as a script

# pylint: disable=wrong-import-position
from noti.destinations import check_destinations  # noqa: E402
from orchy.graph import build_graph  # noqa: E402
from rules import check_rule_definitions  # noqa: E402

FIXTURES_DIR = AGENTS_DIR / "tmp" / "fixtures"
SCHEMA_PATH = AGENTS_DIR / "tmp" / "schema" / "rolepay-income-statement.v1.json"
SCHEMA_NAME = "income_statement"
# The client's rules (data). Checked on load, so a mistyped rule fails here, not mid-run.
RULES_PATH = AGENTS_DIR / "tmp" / "rules" / "rolepay-income-statement.v1.json"
# Where envelopes are delivered (part of the configuration). Set one up with
# scripts/add_destination.py, which also puts its secret in agents/.env.
DESTINATIONS_PATH = AGENTS_DIR / "tmp" / "destinations.json"
RUNS_DIR = AGENTS_DIR / "tmp" / "runs"

# The client's configuration. Later this comes from the database.
CONFIG_VERSION = 1
DOCUMENT_TYPE = "income_statement"
INSTRUCTIONS = "Extract the document into the output schema."
MAX_ATTEMPTS = 3

CHECK_LABELS = {True: "pass", False: "FAIL", None: "skip"}


def email_to_run():
    if len(sys.argv) > 1:
        return Path(sys.argv[1]).resolve()
    emails = sorted(FIXTURES_DIR.glob("*.eml"))
    if not emails:
        sys.exit(f"No .eml in {FIXTURES_DIR}. Make one with: "
                 "python scripts/make_email.py tmp/fixtures/<statement>.pdf")
    return emails[0]


def load_rules():
    """The client's rules, checked before the run. No rules file means no rules."""
    if not RULES_PATH.exists():
        print(f"No rules file at {RULES_PATH}: running with the schema check only.")
        return []
    return check_rule_definitions(json.loads(RULES_PATH.read_text(encoding="utf-8")))


def load_destinations():
    """The client's destinations, checked before the run. No file means no deliveries."""
    if not DESTINATIONS_PATH.exists():
        print(f"No destinations at {DESTINATIONS_PATH}: nothing will be delivered. "
              "Add one with scripts/add_destination.py <url>.")
        return []
    return check_destinations(json.loads(DESTINATIONS_PATH.read_text(encoding="utf-8")))


def total_usage(steps):
    """Token usage added up over every model call, retries included: they all cost money."""
    totals = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    for step in steps:
        for key in totals:
            totals[key] += (step.get("usage") or {}).get(key, 0)
    return totals


def save_run(email_path, final_state, model_name, duration):
    """Saves the run with what's needed to compare runs and see what happened.

    Written to tmp/runs/{email}_{timestamp}.json (no colons, so it works on
    Windows, and names sort in time order). Each document's "output" is its
    extraction, which is what evaluate.py scores.
    """
    timestamp = datetime.now()
    record = {
        "event_id": final_state.get("event_id"),
        "fixture": email_path.name,
        "model": model_name,
        "timestamp": timestamp.isoformat(timespec="seconds"),
        "duration_seconds": round(duration, 2),
        "status": final_state.get("status"),
        "error_code": final_state.get("error_code"),
        "error_message": final_state.get("error_message"),
        "usage": total_usage(final_state.get("steps", [])),
        "documents": [
            {
                "index": d["index"],
                "name": d["name"],
                "content_type": d["content_type"],
                "location": d.get("location"),
                "forwarded_from": d.get("forwarded_from"),
                "status": d["status"],
                "attempts": d["attempts"],
                "error_code": d["error_code"],
                "error_message": d["error_message"],
                "checks": d["checks"],
                "output": d["extraction"],
            }
            for d in final_state.get("documents", [])
        ],
        "steps": final_state.get("steps", []),
        "envelope": final_state.get("envelope"),
        "deliveries": final_state.get("deliveries", []),
    }
    run_path = RUNS_DIR / f"{email_path.stem}_{timestamp:%Y%m%d-%H%M%S}.json"
    run_path.parent.mkdir(parents=True, exist_ok=True)
    run_path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return run_path


def print_summary(final_state):
    print(f"status: {final_state.get('status')}")
    if final_state.get("error_message"):
        print(f"error:  {final_state.get('error_code')}: {final_state['error_message']}")
    print("steps:")
    for step in final_state.get("steps", []):
        document = f"doc {step['document']}" if "document" in step else ""
        print(f"  {step['node']:<12} {document:<6} {step['outcome']:<7} {step.get('error') or ''}")
    for d in final_state.get("documents", []):
        print(f"document {d['index']}: {d['name']}  ({d['status']}, {d['attempts']} attempts)")
        if d["error_message"]:
            print(f"  error: {d['error_code']}: {d['error_message']}")
        for check in d["checks"]:
            print(f"  {CHECK_LABELS[check['passed']]:<5} {check['name']:<28} "
                  f"{check.get('message') or ''}")


def main():
    load_dotenv(AGENTS_DIR / ".env")
    email_path = email_to_run()

    initial_state = {
        "event_id": str(uuid.uuid4()),
        "run": 1,
        "received_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "email_path": str(email_path),
        "config_version": CONFIG_VERSION,
        "document_type": DOCUMENT_TYPE,
        "instructions": INSTRUCTIONS,
        "output_schema": json.loads(SCHEMA_PATH.read_text(encoding="utf-8")),
        "schema_name": SCHEMA_NAME,
        "rules": load_rules(),
        "destinations": load_destinations(),
        "max_attempts": MAX_ATTEMPTS,
        "steps": [],
    }

    started = time.perf_counter()
    final_state = build_graph().invoke(initial_state)
    duration = time.perf_counter() - started

    run_path = save_run(email_path, final_state, os.environ["OBED_MODEL"], duration)
    print(f"Saved run to {run_path}")
    print_summary(final_state)
    print("envelope (what the client receives):")
    print(json.dumps(final_state.get("envelope"), indent=2))
    print("deliveries:")
    for attempt in final_state.get("deliveries", []):
        url = attempt["url"].split("?", 1)[0]  # never show a token in the query string
        print(f"  {attempt['destination_id']} attempt {attempt['attempt']}: {attempt['status']}"
              f" (HTTP {attempt['http_status']}) {url} {attempt.get('error') or ''}")


if __name__ == "__main__":
    main()
