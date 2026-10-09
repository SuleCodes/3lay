"""Unit tests for Orchy's nodes, called directly with a hand-built state: no graph, no model."""

from types import SimpleNamespace

import openai
import pytest

from conftest import USAGE, api_error, pdf_bytes, write_email
from orchy import nodes
from orchy.nodes import extract, finish, load_inputs, validate

SCHEMA = {
    "type": "object",
    "required": ["issued_on", "items"],
    "properties": {
        "issued_on": {"type": "string", "format": "date"},
        "items": {"type": "array"},
    },
}
VALID = {"issued_on": "2026-01-07", "items": []}
INVALID = {"issued_on": "10.12.2025", "items": []}


def document(index=0, **fields):
    return {"index": index, "name": f"doc{index}.pdf", "content_type": "application/pdf",
            "status": "pending", "attempts": 0, "extraction": None, "usage": None,
            "checks": [], "error_code": None, "error_kind": None, "error_message": None,
            **fields}


def state(email_path, *documents, **fields):
    return {"email_path": email_path, "instructions": "Extract it.", "output_schema": SCHEMA,
            "schema_name": "document", "max_attempts": 3, "documents": list(documents),
            **fields}


@pytest.fixture
def one_pdf(tmp_path):
    return write_email(tmp_path / "e.eml", ("doc0.pdf", "application/pdf", pdf_bytes(2)))


# load_inputs

def test_load_inputs_finds_documents_and_counts_pages(tmp_path):
    path = write_email(tmp_path / "e.eml",
                       ("a.pdf", "application/pdf", pdf_bytes(2)),
                       ("b.pdf", "application/pdf", pdf_bytes(1)))

    update = load_inputs({"email_path": path})

    assert update["status"] == "processing"
    assert [(d["name"], d["status"], d["page_count"]) for d in update["documents"]] == [
        ("a.pdf", "pending", 2), ("b.pdf", "pending", 1),
    ]
    assert update["steps"][0]["usable"] == 2


def test_load_inputs_records_unsupported_and_unreadable_attachments(tmp_path):
    path = write_email(tmp_path / "e.eml",
                       ("a.pdf", "application/pdf", pdf_bytes()),
                       ("notes.docx", "application/msword", b"word document"),
                       ("broken.pdf", "application/pdf", b"not really a pdf"))

    docs = load_inputs({"email_path": path})["documents"]

    assert [(d["status"], d["error_code"]) for d in docs] == [
        ("pending", None),
        ("failed", "unsupported_file_type"),
        ("failed", "document_unreadable"),
    ]


def test_load_inputs_fails_the_event_when_nothing_is_usable(tmp_path):
    path = write_email(tmp_path / "e.eml", ("notes.docx", "application/msword", b"x"))

    update = load_inputs({"email_path": path})

    assert update["status"] == "failed"
    assert update["error_code"] == "no_documents_found"


def test_load_inputs_fails_the_event_when_there_are_no_attachments(tmp_path):
    update = load_inputs({"email_path": write_email(tmp_path / "e.eml")})

    assert update["error_code"] == "no_documents_found"
    assert "no attachments" in update["error_message"]


def test_load_inputs_fails_when_the_email_is_missing(tmp_path):
    update = load_inputs({"email_path": str(tmp_path / "missing.eml")})

    assert update["status"] == "failed"
    assert update["error_code"] == "document_unreadable"


# extract

def test_extract_success(one_pdf, fake_model):
    fake_model(VALID)

    update = extract(state(one_pdf, document()))

    [doc] = update["documents"]
    assert (doc["status"], doc["attempts"], doc["extraction"]) == ("extracted", 1, VALID)
    assert doc["usage"] == USAGE
    assert update["steps"][0]["outcome"] == "ok"
    assert update["steps"][0]["document"] == 0


def test_extract_only_processes_documents_waiting_for_it(one_pdf, fake_model):
    calls = fake_model(VALID)
    done = document(1, status="done", extraction=VALID)

    update = extract(state(one_pdf, document(0), done))

    assert len(calls) == 1
    assert update["documents"][1] is done  # left exactly as it was
    assert [s["document"] for s in update["steps"]] == [0]


@pytest.mark.parametrize("error", [
    api_error(openai.RateLimitError, 429),
    api_error(openai.InternalServerError, 503),
])
def test_extract_transient_error_marks_retry_while_attempts_are_left(one_pdf, fake_model, error):
    fake_model(error)

    [doc] = extract(state(one_pdf, document()))["documents"]

    assert (doc["status"], doc["error_kind"]) == ("retry", "transient")


def test_extract_transient_error_on_the_last_attempt_fails_the_document(one_pdf, fake_model):
    fake_model(api_error(openai.RateLimitError, 429))

    [doc] = extract(state(one_pdf, document(status="retry", attempts=2)))["documents"]

    assert doc["status"] == "failed"
    assert doc["error_message"].startswith("Gave up after 3 attempts")


@pytest.mark.parametrize("error", [
    api_error(openai.BadRequestError, 400),
    api_error(openai.AuthenticationError, 401),
])
def test_extract_permanent_error_fails_the_document(one_pdf, fake_model, error):
    fake_model(error)

    [doc] = extract(state(one_pdf, document()))["documents"]

    assert (doc["status"], doc["error_code"], doc["error_kind"]) == (
        "failed", "processing_error", "permanent")


def test_extract_unparseable_output_is_transient_and_still_records_usage(
        one_pdf, monkeypatch, fake_model):
    fake_model(VALID)  # fakes obed_model; obed_extract is replaced again below
    monkeypatch.setattr(nodes, "obed_extract", lambda *_args: {
        "parsed": None, "parsing_error": ValueError("Invalid json output"),
        "raw": SimpleNamespace(usage_metadata=USAGE)})

    update = extract(state(one_pdf, document()))

    [doc] = update["documents"]
    assert doc["status"] == "retry"
    assert "couldn't be parsed" in doc["error_message"]
    assert update["steps"][0]["usage"] == USAGE  # the failed call still cost tokens


# validate

def extracted(extraction, attempts=1):
    return document(status="extracted", attempts=attempts, extraction=extraction)


def test_validate_passes_and_marks_done():
    [doc] = validate(state("unused.eml", extracted(VALID)))["documents"]

    assert doc["status"] == "done"
    assert doc["checks"][0] == {"name": "matches_schema", "passed": True, "message": None}


def test_validate_schema_failure_retries_while_attempts_are_left():
    [doc] = validate(state("unused.eml", extracted(INVALID)))["documents"]

    assert doc["status"] == "retry"
    assert "issued_on" in doc["checks"][0]["message"]


def test_validate_schema_failure_on_the_last_attempt_is_done_with_the_failure_recorded():
    [doc] = validate(state("unused.eml", extracted(INVALID, attempts=3)))["documents"]

    assert doc["status"] == "done"
    assert doc["checks"][0]["passed"] is False


def test_validate_reports_every_schema_error_not_just_the_first():
    [doc] = validate(state("unused.eml", extracted({"items": "not a list"})))["documents"]

    message = doc["checks"][0]["message"]
    assert "issued_on" in message  # missing
    assert "items" in message  # wrong type


def test_validate_runs_the_clients_rules_per_document():
    rules = [{"id": "dates_plausible", "type": "date_within", "fields": ["issued_on"],
              "days_before": 1095, "days_after": 31, "on_fail": "needs_review"}]
    implausible = extracted({"issued_on": "0501-01-06", "items": []})

    [doc] = validate(state("unused.eml", implausible, rules=rules))["documents"]

    assert doc["checks"][1]["name"] == "dates_plausible"
    assert doc["checks"][1]["passed"] is False
    assert doc["status"] == "done"  # rules never cause a retry


def test_validate_leaves_other_documents_alone():
    pending = document(1)

    update = validate(state("unused.eml", extracted(VALID), pending))

    assert update["documents"][1] is pending
    assert [s["document"] for s in update["steps"]] == [0]


# finish

def test_finish_completes_when_any_document_is_done():
    update = finish(state("e.eml", document(0, status="done"),
                          document(1, status="failed", error_code="unsupported_file_type",
                                   error_message="no")))

    assert update["status"] == "completed"
    assert update["steps"][0]["documents_done"] == 1


def test_finish_fails_with_the_documents_shared_error_code():
    update = finish(state("e.eml",
                          document(0, status="failed", error_code="document_unreadable",
                                   error_message="corrupt")))

    assert update["status"] == "failed"
    assert update["error_code"] == "document_unreadable"
    assert "doc0.pdf: corrupt" in update["error_message"]


def test_finish_with_different_document_errors_uses_processing_error():
    update = finish(state("e.eml",
                          document(0, status="failed", error_code="document_unreadable",
                                   error_message="corrupt"),
                          document(1, status="failed", error_code="processing_error",
                                   error_message="401")))

    assert update["error_code"] == "processing_error"


def test_finish_keeps_an_error_found_by_load_inputs():
    update = finish({"documents": [], "status": "failed",
                     "error_code": "no_documents_found", "error_message": "No attachments."})

    assert (update["error_code"], update["error_message"]) == (
        "no_documents_found", "No attachments.")
