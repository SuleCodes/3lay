"""Runs Obed on one fixture and saves the run to tmp/runs/ for scripts/evaluate.py.

Settings come from agents/.env: OBED_MODEL, OBED_BASE_URL, HF_TOKEN.
"""

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

AGENTS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AGENTS_DIR))  # so the packages import when run as a script

# pylint: disable=wrong-import-position
from obed.documents import pdf_to_image_blocks  # noqa: E402
from obed.extract import create_model, extract  # noqa: E402

FIXTURE_PATH = AGENTS_DIR / "tmp" / "fixtures" / "1767784629512.pdf"
SCHEMA_PATH = AGENTS_DIR / "tmp" / "schema" / "rolepay-income-statement.v1.json"
SCHEMA_NAME = "income_statement"
RUNS_DIR = AGENTS_DIR / "tmp" / "runs"

# The client's instructions. Later these come from the client's configuration.
INSTRUCTIONS = "Extract the document into the output schema."


def save_run(fixture_path, model_name, response, duration):
    """Saves the extraction with what's needed to compare runs: model, time, tokens.

    Written to tmp/runs/{fixture}_{timestamp}.json. The timestamp has no colons
    (not allowed in Windows file names) and sorts in time order, so the latest
    run is the last file by name. When parsing fails, the model's raw reply is
    kept so you can see why.
    """
    timestamp = datetime.now()
    failed = response["parsing_error"] is not None
    record = {
        "fixture": fixture_path.name,
        "model": model_name,
        "timestamp": timestamp.isoformat(timespec="seconds"),
        "duration_seconds": round(duration, 2),
        "usage": response["raw"].usage_metadata,
        "parsing_error": str(response["parsing_error"]) if failed else None,
        "raw_content": response["raw"].content if failed else None,
        "output": response["parsed"],
    }
    run_path = RUNS_DIR / f"{fixture_path.stem}_{timestamp:%Y%m%d-%H%M%S}.json"
    run_path.parent.mkdir(parents=True, exist_ok=True)
    run_path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return run_path


def main():
    load_dotenv(AGENTS_DIR / ".env")
    model_name = os.environ["OBED_MODEL"]
    model = create_model(model_name, os.environ["OBED_BASE_URL"], os.environ["HF_TOKEN"])

    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    inputs = pdf_to_image_blocks(FIXTURE_PATH)

    started = time.perf_counter()
    response = extract(model, INSTRUCTIONS, inputs, schema, SCHEMA_NAME)
    duration = time.perf_counter() - started

    run_path = save_run(FIXTURE_PATH, model_name, response, duration)
    print(f"Saved run to {run_path}")
    if response["parsing_error"] is not None:
        print(f"Parsing failed: {response['parsing_error']}")
        print("Check that this model's provider supports structured output: "
              "scripts/list_models.py --check MODEL:PROVIDER")
    else:
        print(json.dumps(response["parsed"], indent=2))


if __name__ == "__main__":
    main()
