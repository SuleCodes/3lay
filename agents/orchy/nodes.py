"""Orchy's nodes. Each takes the state and returns only the fields it changes.

Nodes never modify the state they're given: they return a dict of updates, and
LangGraph merges it in (see orchy/state.py).
"""
import os
from datetime import datetime, timezone
from functools import cache
from pathlib import Path

import jsonschema
import openai
import pymupdf
from dotenv import load_dotenv

from obed.documents import pdf_to_image_blocks
from obed.extract import create_model, extract as obed_extract
from orchy.state import OrchyState
from rules import run_rules

AGENTS_DIR = Path(__file__).resolve().parent.parent

# Errors worth retrying: the request didn't get through, or the provider was
# busy or broken. Every other API error (bad token, no permission, a request the
# provider rejects, e.g. unsupported structured output) fails the same way
# every time, so it's permanent. APITimeoutError is a kind of APIConnectionError.
TRANSIENT_API_ERRORS = (
    openai.APIConnectionError,
    openai.RateLimitError,
    openai.InternalServerError,
)

DEFAULT_MAX_ATTEMPTS = 3
MAX_SCHEMA_ERRORS_SHOWN = 10


def now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def step_record(node, started_at, outcome, error=None, **details):
    """One entry for the step timeline. outcome is "ok", "retry" or "failed"."""
    return {
        "node": node,
        "started_at": started_at,
        "finished_at": now(),
        "outcome": outcome,
        "error": error,
        **details,
    }


def load_inputs(state: OrchyState) -> dict:
    """Checks the document opens and has pages, so a bad file fails early and cheaply.

    The pages aren't rendered here: extract renders them itself, so the images
    never sit in the state (see the step 2 decisions in tools/Planner.md).
    """
    started_at = now()
    try:
        with pymupdf.open(state["document_path"]) as doc:
            page_count = doc.page_count
    except Exception as exc:  # pylint: disable=broad-exception-caught
        # Missing, corrupt or not a document at all: retrying won't help.
        message = f"Couldn't open the document: {exc}"
        return {
            "status": "failed",
            "error_code": "document_unreadable",
            "error_kind": "permanent",
            "error_message": message,
            "steps": [step_record("load_inputs", started_at, "failed", message)],
        }

    if page_count == 0:
        message = "The document has no pages."
        return {
            "status": "failed",
            "error_code": "document_unreadable",
            "error_kind": "permanent",
            "error_message": message,
            "steps": [step_record("load_inputs", started_at, "failed", message)],
        }

    return {
        "status": "processing",
        "page_count": page_count,
        "steps": [step_record("load_inputs", started_at, "ok", page_count=page_count)],
    }


@cache
def obed_model():
    """Obed's model and its name, from agents/.env. Created once, then reused.

    The model is a live client, not data, so it never goes in the state.
    """
    load_dotenv(AGENTS_DIR / ".env")
    name = os.environ["OBED_MODEL"]
    return create_model(name, os.environ["OBED_BASE_URL"], os.environ["HF_TOKEN"]), name


def extract(state: OrchyState) -> dict:
    """Runs Obed on the document and records the attempt.

    On an error it doesn't decide whether to retry: it reports error_kind
    ("transient" or "permanent") and the routing decides, using attempts and
    max_attempts. It always returns attempts, so the routing can count tries.
    """
    started_at = now()
    attempts = state.get("attempts", 0) + 1
    model, model_name = obed_model()

    def failure(kind, code, message, usage=None):
        return {
            "attempts": attempts,
            "error_kind": kind,
            "error_code": code,
            "error_message": message,
            "steps": [step_record(
                "extract", started_at,
                "retry" if kind == "transient" else "failed", message,
                attempt=attempts, model=model_name, usage=usage,
            )],
        }

    try:
        inputs = pdf_to_image_blocks(state["document_path"])
    except Exception as exc:  # pylint: disable=broad-exception-caught
        # The document opened in load_inputs but a page wouldn't render.
        return failure("permanent", "document_unreadable", f"Couldn't render the document: {exc}")

    try:
        response = obed_extract(
            model, state["instructions"], inputs, state["output_schema"], state["schema_name"]
        )
    except TRANSIENT_API_ERRORS as exc:
        return failure("transient", "processing_error", f"{type(exc).__name__}: {exc}")
    except openai.APIStatusError as exc:
        return failure("permanent", "processing_error", f"{type(exc).__name__}: {exc}")

    usage = response["raw"].usage_metadata
    if response["parsing_error"] is not None:
        # The call worked but no valid JSON came back: worth another try.
        return failure("transient", "processing_error",
                       f"Output couldn't be parsed: {response['parsing_error']}", usage)

    return {
        "attempts": attempts,
        "extraction": response["parsed"],
        "usage": usage,
        "error_kind": None,
        "error_code": None,
        "error_message": None,
        "steps": [step_record(
            "extract", started_at, "ok",
            attempt=attempts, model=model_name, usage=usage,
        )],
    }


def check(name, passed, message=None):
    return {"name": name, "passed": passed, "message": message}


def schema_check(extraction, output_schema):
    """matches_schema: every JSON Schema error, not just the first."""
    validator = jsonschema.Draft202012Validator(
        output_schema, format_checker=jsonschema.FormatChecker()
    )
    errors = sorted(validator.iter_errors(extraction), key=lambda e: list(e.absolute_path))
    if not errors:
        return check("matches_schema", True)
    shown = [
        f"{'.'.join(str(p) for p in e.absolute_path) or '(top level)'}: {e.message}"
        for e in errors[:MAX_SCHEMA_ERRORS_SHOWN]
    ]
    if len(errors) > MAX_SCHEMA_ERRORS_SHOWN:
        shown.append(f"... and {len(errors) - MAX_SCHEMA_ERRORS_SHOWN} more")
    return check("matches_schema", False, "; ".join(shown))


def validate(state: OrchyState) -> dict:
    """Deterministic checks on the extraction. Client-neutral: no client's rules in code.

    First the client's output schema, then the client's rules (state["rules"],
    from its configuration), run by the generic rule engine in rules/. Each
    check's passed is True, False, or None when a rule was skipped because a
    value it needs is missing.

    Only a schema failure is retried (another extract) while attempts are left;
    after the last attempt the event completes anyway, with the failed check
    recorded, so Justice and the verdict flag it as needs_review. Rules never
    cause a retry: they run on whatever was extracted, and their failures are
    information for Justice.
    """
    started_at = now()
    extraction = state.get("extraction")

    if extraction is None:
        checks = [check("matches_schema", False, "There's no extraction to check.")]
    else:
        checks = [
            schema_check(extraction, state["output_schema"]),
            *run_rules(state.get("rules", []), extraction),
        ]

    schema_passed = checks[0]["passed"]
    attempts_left = state.get("attempts", 0) < state.get("max_attempts", DEFAULT_MAX_ATTEMPTS)
    retry = not schema_passed and attempts_left
    failed = [c["name"] for c in checks if c["passed"] is False]
    skipped = sum(1 for c in checks if c["passed"] is None)

    update = {
        "checks": checks,
        "steps": [step_record(
            "validate", started_at, "retry" if retry else "ok",
            "Failed: " + ", ".join(failed) if failed else None,
            checks_passed=len(checks) - len(failed) - skipped,
            checks_failed=len(failed), checks_skipped=skipped,
        )],
    }
    if not retry:
        update["status"] = "completed"
    return update


def fail(state: OrchyState) -> dict:
    """Marks the event failed. Every failure route ends here, so it's recorded one way.

    The node that hit the problem has already set error_code, error_kind and
    error_message; this only makes sure a code is always present (e.g. when
    transient errors ran out of attempts) and adds the final step.
    """
    started_at = now()
    error_code = state.get("error_code") or "processing_error"
    message = state.get("error_message") or "Processing failed."
    if state.get("error_kind") == "transient":
        message = f"Gave up after {state.get('attempts', 0)} attempts. Last error: {message}"
    return {
        "status": "failed",
        "error_code": error_code,
        "error_message": message,
        "steps": [step_record("fail", started_at, "failed", message, error_code=error_code)],
    }
