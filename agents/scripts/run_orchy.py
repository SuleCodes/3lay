"""Runs Orchy's graph on one fixture and saves the run to tmp/runs/ for scripts/evaluate.py.

Settings come from agents/.env: OBED_MODEL, OBED_BASE_URL, HF_TOKEN (read by
the extract node).
"""

import json
import os
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

AGENTS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AGENTS_DIR))  # so the packages import when run as a script

from orchy.graph import build_graph  # noqa: E402  pylint: disable=wrong-import-position

FIXTURE_PATH = AGENTS_DIR / "tmp" / "fixtures" / "1767784629512.pdf"
SCHEMA_PATH = AGENTS_DIR / "tmp" / "schema" / "rolepay-income-statement.v1.json"
SCHEMA_NAME = "income_statement"
RUNS_DIR = AGENTS_DIR / "tmp" / "runs"

# The client's instructions and retry limit. Later these come from the client's
# configuration.
INSTRUCTIONS = "Extract the document into the output schema."
MAX_ATTEMPTS = 3


def total_usage(steps):
    """Token usage added up over every model call, retries included: they all cost money."""
    totals = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    for step in steps:
        for key in totals:
            totals[key] += (step.get("usage") or {}).get(key, 0)
    return totals


def save_run(final_state, model_name, duration):
    """Saves the run with what's needed to compare runs and see what happened.

    Written to tmp/runs/{fixture}_{timestamp}.json (no colons, so it works on
    Windows, and names sort in time order). "output" is the extraction, which is
    what evaluate.py scores.
    """
    timestamp = datetime.now()
    record = {
        "event_id": final_state.get("event_id"),
        "fixture": FIXTURE_PATH.name,
        "model": model_name,
        "timestamp": timestamp.isoformat(timespec="seconds"),
        "duration_seconds": round(duration, 2),
        "status": final_state.get("status"),
        "attempts": final_state.get("attempts"),
        "error_code": final_state.get("error_code"),
        "error_message": final_state.get("error_message"),
        "usage": total_usage(final_state.get("steps", [])),
        "checks": final_state.get("checks", []),
        "steps": final_state.get("steps", []),
        "output": final_state.get("extraction"),
    }
    run_path = RUNS_DIR / f"{FIXTURE_PATH.stem}_{timestamp:%Y%m%d-%H%M%S}.json"
    run_path.parent.mkdir(parents=True, exist_ok=True)
    run_path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return run_path


def main():
    load_dotenv(AGENTS_DIR / ".env")

    initial_state = {
        "event_id": str(uuid.uuid4()),
        "document_path": str(FIXTURE_PATH),
        "instructions": INSTRUCTIONS,
        "output_schema": json.loads(SCHEMA_PATH.read_text(encoding="utf-8")),
        "schema_name": SCHEMA_NAME,
        "max_attempts": MAX_ATTEMPTS,
        "attempts": 0,
        "steps": [],
    }

    started = time.perf_counter()
    final_state = build_graph().invoke(initial_state)
    duration = time.perf_counter() - started

    run_path = save_run(final_state, os.environ["OBED_MODEL"], duration)
    print(f"Saved run to {run_path}")
    print(f"status: {final_state.get('status')}   attempts: {final_state.get('attempts')}")
    for step in final_state.get("steps", []):
        print(f"  {step['node']:<12} {step['outcome']:<7} {step.get('error') or ''}")
    if final_state.get("error_message"):
        print(f"error: {final_state.get('error_code')}: {final_state['error_message']}")


if __name__ == "__main__":
    main()
