"""Orchy's nodes. Each takes the state and returns only the fields it changes.

Nodes never modify the state they're given: they return a dict of updates, and
LangGraph merges it in (see orchy/state.py).

An event is one email with one or more documents. load_inputs finds them;
extract and validate work through the documents that need them, one after
another; finish decides the event's outcome from all of them.
"""
import os
from datetime import datetime, timezone
from functools import cache
from pathlib import Path

import jsonschema
import openai
from dotenv import load_dotenv

from obed.documents import SUPPORTED_TYPES, page_count, to_image_blocks
from obed.extract import create_model, extract as obed_extract
from orchy.emails import read_attachment, read_attachments, read_sender
from orchy.envelope import build_envelope as envelope_for
from orchy.state import DocumentState, OrchyState
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


def max_attempts(state):
    return state.get("max_attempts", DEFAULT_MAX_ATTEMPTS)


def failed_document(document, code, message):
    """The document can't be processed and retrying won't help."""
    return {**document, "status": "failed", "error_code": code,
            "error_kind": "permanent", "error_message": message}


# load_inputs

def new_document(attachment) -> DocumentState:
    document = {
        "index": attachment.index,
        "name": attachment.name,
        "content_type": attachment.content_type,
        "location": attachment.location,
        "forwarded_from": attachment.forwarded_from,
        "status": "pending",
        "attempts": 0,
        "extraction": None,
        "usage": None,
        "checks": [],
        "error_code": None,
        "error_kind": None,
        "error_message": None,
    }
    if attachment.content_type not in SUPPORTED_TYPES:
        return failed_document(document, "unsupported_file_type",
                               f"{attachment.content_type} isn't a supported document type.")
    try:
        pages = page_count(attachment.data, attachment.content_type)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        return failed_document(document, "document_unreadable", f"Couldn't open it: {exc}")
    if pages == 0:
        return failed_document(document, "document_unreadable", "It has no pages.")
    return {**document, "page_count": pages}


def load_inputs(state: OrchyState) -> dict:
    """Finds the email's attachments and checks each one opens, so bad files fail early.

    Unsupported or unreadable attachments are recorded as failed documents (the
    client sees why), not dropped. The event fails only if nothing is usable.
    Pages aren't rendered here, and no bytes go into the state: extract reads
    each attachment from the email when it needs it.
    """
    started_at = now()
    try:
        attachments = read_attachments(state["email_path"])
        sender = state.get("sender") or read_sender(state["email_path"])
    except OSError as exc:
        message = f"Couldn't read the email: {exc}"
        return {
            "documents": [],
            "status": "failed",
            "error_code": "document_unreadable",
            "error_message": message,
            "steps": [step_record("load_inputs", started_at, "failed", message)],
        }

    documents = [new_document(attachment) for attachment in attachments]
    usable = sum(1 for d in documents if d["status"] == "pending")

    if usable == 0:
        message = ("The email has no attachments." if not documents else
                   "None of the email's attachments is a supported document (PDF, JPEG or PNG).")
        return {
            "documents": documents,
            "sender": sender,
            "status": "failed",
            "error_code": "no_documents_found",
            "error_message": message,
            "steps": [step_record("load_inputs", started_at, "failed", message,
                                  attachments=len(documents), usable=0)],
        }

    return {
        "documents": documents,
        "sender": sender,
        "status": "processing",
        "steps": [step_record("load_inputs", started_at, "ok",
                              attachments=len(documents), usable=usable)],
    }


# extract

@cache
def obed_model():
    """Obed's model and its name, from agents/.env. Created once, then reused.

    The model is a live client, not data, so it never goes in the state.
    """
    load_dotenv(AGENTS_DIR / ".env")
    name = os.environ["OBED_MODEL"]
    return create_model(name, os.environ["OBED_BASE_URL"], os.environ["HF_TOKEN"]), name


def extract_document(state, document):
    """Runs Obed on one document. Returns (updated document, step record)."""
    started_at = now()
    attempts = document["attempts"] + 1
    model, model_name = obed_model()
    document = {**document, "attempts": attempts}

    def outcome(updated, usage=None):
        step = step_record(
            "extract", started_at,
            {"extracted": "ok", "retry": "retry"}.get(updated["status"], "failed"),
            updated["error_message"],
            document=document["index"], attempt=attempts, model=model_name, usage=usage,
        )
        return updated, step

    def error(kind, code, message, usage=None):
        # A transient error is retried while this document has attempts left.
        retry = kind == "transient" and attempts < max_attempts(state)
        if kind == "transient" and not retry:
            message = f"Gave up after {attempts} attempts. Last error: {message}"
        return outcome({**document, "status": "retry" if retry else "failed",
                        "error_code": code, "error_kind": kind, "error_message": message}, usage)

    try:
        attachment = read_attachment(state["email_path"], document["index"])
        inputs = to_image_blocks(attachment.data, attachment.content_type)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        return error("permanent", "document_unreadable", f"Couldn't render the document: {exc}")

    try:
        response = obed_extract(
            model, state["instructions"], inputs, state["output_schema"], state["schema_name"]
        )
    except TRANSIENT_API_ERRORS as exc:
        return error("transient", "processing_error", f"{type(exc).__name__}: {exc}")
    except openai.APIStatusError as exc:
        return error("permanent", "processing_error", f"{type(exc).__name__}: {exc}")

    usage = response["raw"].usage_metadata
    if response["parsing_error"] is not None:
        # The call worked but no valid JSON came back: worth another try.
        return error("transient", "processing_error",
                     f"Output couldn't be parsed: {response['parsing_error']}", usage)

    return outcome({**document, "status": "extracted", "extraction": response["parsed"],
                    "usage": usage, "error_code": None, "error_kind": None,
                    "error_message": None}, usage)


def extract(state: OrchyState) -> dict:
    """Runs Obed on every document waiting for it ("pending" or "retry"), one after another.

    Documents already extracted, done or failed are left alone, so a retry only
    redoes the documents that need it. Each document gets its own step record.
    """
    documents, steps = [], []
    for document in state["documents"]:
        if document["status"] in ("pending", "retry"):
            document, step = extract_document(state, document)
            steps.append(step)
        documents.append(document)
    return {"documents": documents, "steps": steps}


# validate

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


def validate_document(state, document):
    """Checks one extracted document. Returns (updated document, step record)."""
    started_at = now()
    checks = [
        schema_check(document["extraction"], state["output_schema"]),
        *run_rules(state.get("rules", []), document["extraction"]),
    ]
    retry = not checks[0]["passed"] and document["attempts"] < max_attempts(state)
    failed = [c["name"] for c in checks if c["passed"] is False]
    skipped = sum(1 for c in checks if c["passed"] is None)

    updated = {**document, "checks": checks, "status": "retry" if retry else "done"}
    step = step_record(
        "validate", started_at, "retry" if retry else "ok",
        "Failed: " + ", ".join(failed) if failed else None,
        document=document["index"],
        checks_passed=len(checks) - len(failed) - skipped,
        checks_failed=len(failed), checks_skipped=skipped,
    )
    return updated, step


def validate(state: OrchyState) -> dict:
    """Deterministic checks on each extracted document. Client-neutral: no client's rules in code.

    First the client's output schema, then the client's rules (state["rules"],
    from its configuration), run by the generic rule engine in rules/.

    Only a schema failure is retried (the document goes back to extract) while
    it has attempts left; after its last attempt it's done anyway, with the
    failed check recorded, so Justice and the verdict flag it as needs_review.
    Rules never cause a retry: their failures are information for Justice.
    """
    documents, steps = [], []
    for document in state["documents"]:
        if document["status"] == "extracted":
            document, step = validate_document(state, document)
            steps.append(step)
        documents.append(document)
    return {"documents": documents, "steps": steps}


# finish

def finish(state: OrchyState) -> dict:
    """Decides the event's outcome from all its documents. Every run ends here.

    completed: at least one document is done (others may have failed: each
    document records its own error). failed: nothing usable came back. A
    failure found by load_inputs (no usable attachments) keeps its own error.
    """
    started_at = now()
    documents = state.get("documents", [])
    done = sum(1 for d in documents if d["status"] == "done")
    failed = [d for d in documents if d["status"] == "failed"]
    counts = {"documents_done": done, "documents_failed": len(failed)}

    if done:
        return {"status": "completed",
                "steps": [step_record("finish", started_at, "ok", **counts)]}

    error_code = state.get("error_code")
    message = state.get("error_message")
    if not error_code:
        codes = {d["error_code"] for d in failed}
        error_code = codes.pop() if len(codes) == 1 else "processing_error"
        message = "; ".join(f"{d['name']}: {d['error_message']}" for d in failed) or \
            "No document could be processed."
    return {
        "status": "failed",
        "error_code": error_code,
        "error_message": message,
        "steps": [step_record("finish", started_at, "failed", message,
                              error_code=error_code, **counts)],
    }


# build_envelope

def build_envelope(state: OrchyState) -> dict:
    """Builds what the client receives (orchy/envelope.py), ready for delivery.

    A node rather than part of finish, so it shows in the step timeline and in
    traces, and the next node (Noti's delivery) sends exactly what was built.
    """
    started_at = now()
    envelope = envelope_for(state, finished_at=started_at)
    return {
        "envelope": envelope,
        "steps": [step_record("build_envelope", started_at, "ok", **envelope["summary"])],
    }
