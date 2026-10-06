"""Scores the latest saved run for each gold standard file.

For every tmp/gold_standard/{stem}.json, finds the newest tmp/runs/{stem}_*.json
saved by run_obed.py, and prints its score and the fields that didn't match.
"""

import json
import sys
from pathlib import Path

AGENTS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AGENTS_DIR))  # so the packages import when run as a script

from evaluation.scoring import score  # noqa: E402  pylint: disable=wrong-import-position

GOLD_DIR = AGENTS_DIR / "tmp" / "gold_standard"
RUNS_DIR = AGENTS_DIR / "tmp" / "runs"


def latest_run(stem):
    """The newest run for a fixture; timestamped names sort in time order."""
    runs = sorted(RUNS_DIR.glob(f"{stem}_*.json"))
    return runs[-1] if runs else None


def main():
    gold_files = sorted(GOLD_DIR.glob("*.json"))
    if not gold_files:
        print(f"No gold standard files in {GOLD_DIR}")
        return

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
        print(f"  time:     {run.get('duration_seconds')}s   tokens: {run.get('usage')}")

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
