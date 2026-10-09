"""Tests for the whole graph: real nodes and routing, with only the model call faked.

Each test is one route through the graph, starting from a raw email. The fake
model gives the next answer on each call, which is how retries are tested
without a real provider. With several documents, calls happen in document order.
"""

import openai
import pytest

from conftest import api_error, email_message, pdf_bytes, png_bytes, write_email
from orchy.graph import build_graph

SCHEMA = {
    "type": "object",
    "required": ["issued_on"],
    "properties": {"issued_on": {"type": "string", "format": "date"}},
}
VALID = {"issued_on": "2026-01-07"}
INVALID = {"issued_on": "10.12.2025"}  # fails the schema's date format


def run(email_path, **fields):
    return build_graph().invoke({
        "event_id": "evt-1",
        "email_path": email_path,
        "instructions": "Extract it.",
        "output_schema": SCHEMA,
        "schema_name": "document",
        "max_attempts": 3,
        "steps": [],
        **fields,
    })


def route(final_state):
    """The route the event took, e.g. ["load_inputs", "extract", "validate", "finish"]."""
    return [step["node"] for step in final_state["steps"]]


def statuses(final_state):
    return [d["status"] for d in final_state["documents"]]


@pytest.fixture
def one_pdf(tmp_path):
    return write_email(tmp_path / "e.eml", ("statement.pdf", "application/pdf", pdf_bytes()))


@pytest.fixture
def two_documents(tmp_path):
    return write_email(tmp_path / "e.eml",
                       ("jan.pdf", "application/pdf", pdf_bytes()),
                       ("feb-photo.png", "image/png", png_bytes()))


def test_happy_path_one_document(one_pdf, fake_model):
    fake_model(VALID)

    final = run(one_pdf)

    assert final["status"] == "completed"
    assert final["documents"][0]["extraction"] == VALID
    assert route(final) == ["load_inputs", "extract", "validate", "finish"]
    assert final["event_id"] == "evt-1"  # fields no node touches pass through unchanged


def test_each_document_is_extracted_and_checked_on_its_own(two_documents, fake_model):
    calls = fake_model({"issued_on": "2026-01-31"}, {"issued_on": "2026-02-28"})

    final = run(two_documents)

    assert final["status"] == "completed"
    assert [d["extraction"]["issued_on"] for d in final["documents"]] == [
        "2026-01-31", "2026-02-28"]
    assert len(calls) == 2
    # One extract step and one validate step per document, in document order.
    assert [(s["node"], s.get("document")) for s in final["steps"]] == [
        ("load_inputs", None), ("extract", 0), ("extract", 1),
        ("validate", 0), ("validate", 1), ("finish", None)]


def test_an_unsupported_attachment_doesnt_stop_the_others(tmp_path, fake_model):
    path = write_email(tmp_path / "e.eml",
                       ("statement.pdf", "application/pdf", pdf_bytes()),
                       ("notes.docx", "application/msword", b"word document"))
    calls = fake_model(VALID)

    final = run(path)

    assert final["status"] == "completed"
    assert statuses(final) == ["done", "failed"]
    assert final["documents"][1]["error_code"] == "unsupported_file_type"
    assert len(calls) == 1  # the .docx never reaches the model


def test_email_without_usable_attachments_fails_without_calling_the_model(tmp_path, fake_model):
    calls = fake_model(VALID)

    final = run(write_email(tmp_path / "e.eml", ("notes.docx", "application/msword", b"x")))

    assert final["status"] == "failed"
    assert final["error_code"] == "no_documents_found"
    assert route(final) == ["load_inputs", "finish"]
    assert not calls


def test_transient_error_retries_only_the_document_that_needs_it(two_documents, fake_model):
    calls = fake_model(VALID, api_error(openai.RateLimitError, 429), VALID)

    final = run(two_documents)

    assert final["status"] == "completed"
    assert statuses(final) == ["done", "done"]
    assert len(calls) == 3  # document 0 once, document 1 twice
    assert [(s["node"], s.get("document"), s["outcome"]) for s in final["steps"]
            if s["node"] == "extract"] == [
        ("extract", 0, "ok"), ("extract", 1, "retry"), ("extract", 1, "ok")]


def test_transient_errors_every_time_fail_the_event_after_the_last_attempt(one_pdf, fake_model):
    fake_model(api_error(openai.InternalServerError, 503))

    final = run(one_pdf)

    assert final["status"] == "failed"
    assert final["error_code"] == "processing_error"
    assert final["documents"][0]["attempts"] == 3
    assert route(final) == ["load_inputs", "extract", "extract", "extract", "finish"]
    assert "Gave up after 3 attempts" in final["error_message"]


def test_permanent_error_fails_without_retrying(one_pdf, fake_model):
    fake_model(api_error(openai.AuthenticationError, 401))

    final = run(one_pdf)

    assert final["status"] == "failed"
    assert final["documents"][0]["attempts"] == 1
    assert route(final) == ["load_inputs", "extract", "finish"]


def test_one_failed_document_doesnt_fail_the_event(two_documents, fake_model):
    fake_model(VALID, api_error(openai.BadRequestError, 400))

    final = run(two_documents)

    assert final["status"] == "completed"  # something usable came back
    assert statuses(final) == ["done", "failed"]


def test_schema_failure_is_retried_then_succeeds(one_pdf, fake_model):
    fake_model(INVALID, VALID)

    final = run(one_pdf)

    assert final["status"] == "completed"
    assert final["documents"][0]["checks"][0]["passed"]
    assert route(final) == ["load_inputs", "extract", "validate", "extract", "validate", "finish"]


def test_schema_failure_every_time_completes_with_the_failure_recorded(one_pdf, fake_model):
    """After the last attempt the document is done anyway, so Justice flags it needs_review."""
    fake_model(INVALID)

    final = run(one_pdf)

    assert final["status"] == "completed"
    assert final["documents"][0]["checks"][0]["passed"] is False
    assert final["documents"][0]["attempts"] == 3


def test_rules_are_recorded_but_never_cause_a_retry(one_pdf, fake_model):
    fake_model({"issued_on": "0501-01-06"})  # valid date format, implausible date
    rules = [{"id": "dates_plausible", "type": "date_within", "fields": ["issued_on"],
              "days_before": 1095, "days_after": 31, "on_fail": "needs_review"}]

    final = run(one_pdf, rules=rules)

    checks = {c["name"]: c for c in final["documents"][0]["checks"]}
    assert checks["matches_schema"]["passed"] is True
    assert checks["dates_plausible"]["passed"] is False
    assert route(final) == ["load_inputs", "extract", "validate", "finish"]


def test_steps_accumulate_through_the_reducer(one_pdf, fake_model):
    """Each node returns its own step records; the reducer adds them up, not replaces."""
    fake_model(VALID)

    final = run(one_pdf, steps=[{"node": "earlier", "outcome": "ok"}])

    assert route(final) == ["earlier", "load_inputs", "extract", "validate", "finish"]


def test_documents_inside_a_forwarded_email_are_processed(tmp_path, fake_model):
    forwarded = email_message(("statement.pdf", "application/pdf", pdf_bytes()),
                              sender="agency@example.com")
    path = tmp_path / "e.eml"
    path.write_bytes(email_message(forwarded).as_bytes())
    fake_model(VALID)

    final = run(str(path))

    assert final["status"] == "completed"
    [doc] = final["documents"]
    assert (doc["name"], doc["location"], doc["forwarded_from"]) == (
        "statement.pdf", "0 > 0", "agency@example.com")
