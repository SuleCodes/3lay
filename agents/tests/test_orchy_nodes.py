"""Unit tests for Orchy's nodes, called directly with a hand-built state: no graph, no model."""

from types import SimpleNamespace

import httpx
import openai
import pymupdf
import pytest

from orchy import nodes
from orchy.nodes import load_inputs


def make_pdf(path, pages=1):
    doc = pymupdf.open()
    for _ in range(pages):
        doc.new_page()
    doc.save(path)
    doc.close()


def test_load_inputs_counts_pages(tmp_path):
    pdf = tmp_path / "statement.pdf"
    make_pdf(pdf, pages=2)

    update = load_inputs({"document_path": str(pdf)})

    assert update["status"] == "processing"
    assert update["page_count"] == 2
    assert update["steps"][0]["node"] == "load_inputs"
    assert update["steps"][0]["outcome"] == "ok"


def test_load_inputs_fails_permanently_on_a_missing_file(tmp_path):
    update = load_inputs({"document_path": str(tmp_path / "missing.pdf")})

    assert update["status"] == "failed"
    assert update["error_code"] == "document_unreadable"
    assert update["error_kind"] == "permanent"
    assert update["steps"][0]["outcome"] == "failed"


def test_load_inputs_fails_on_a_file_that_isnt_a_document(tmp_path):
    not_a_pdf = tmp_path / "statement.pdf"
    not_a_pdf.write_bytes(b"this is not a pdf")

    update = load_inputs({"document_path": str(not_a_pdf)})

    assert update["error_code"] == "document_unreadable"


# extract: the model call is replaced with a fake, so these never hit the network.

USAGE = {"input_tokens": 3000, "output_tokens": 1000}


def api_error(error_class, status):
    request = httpx.Request("POST", "https://router.huggingface.co/v1/chat/completions")
    return error_class("simulated", response=httpx.Response(status, request=request), body=None)


@pytest.fixture
def extract_state(tmp_path, monkeypatch):
    """A state ready for extract, with a real one-page PDF and a fake model."""
    pdf = tmp_path / "statement.pdf"
    make_pdf(pdf)
    monkeypatch.setattr(nodes, "obed_model", lambda: (object(), "test/model:provider"))
    return {
        "document_path": str(pdf),
        "instructions": "Extract it.",
        "output_schema": {"type": "object"},
        "schema_name": "income_statement",
        "attempts": 1,
    }


def fake_obed(monkeypatch, result=None, raises=None):
    def obed_extract(*_args):
        if raises:
            raise raises
        return result
    monkeypatch.setattr(nodes, "obed_extract", obed_extract)


def test_extract_success_returns_the_extraction(extract_state, monkeypatch):
    fake_obed(monkeypatch, {"parsed": {"gross": 7000.0}, "parsing_error": None,
                            "raw": SimpleNamespace(usage_metadata=USAGE)})

    update = nodes.extract(extract_state)

    assert update["attempts"] == 2
    assert update["extraction"] == {"gross": 7000.0}
    assert update["error_kind"] is None
    assert update["steps"][0]["outcome"] == "ok"
    assert update["steps"][0]["usage"] == USAGE


@pytest.mark.parametrize("error", [
    api_error(openai.RateLimitError, 429),
    api_error(openai.InternalServerError, 503),
    openai.APITimeoutError(httpx.Request("POST", "https://example.test")),
])
def test_extract_busy_or_unreachable_provider_is_transient(extract_state, monkeypatch, error):
    fake_obed(monkeypatch, raises=error)

    update = nodes.extract(extract_state)

    assert update["error_kind"] == "transient"
    assert update["attempts"] == 2
    assert update["steps"][0]["outcome"] == "retry"


@pytest.mark.parametrize("error", [
    api_error(openai.BadRequestError, 400),
    api_error(openai.AuthenticationError, 401),
    api_error(openai.PermissionDeniedError, 403),
])
def test_extract_rejected_request_is_permanent(extract_state, monkeypatch, error):
    fake_obed(monkeypatch, raises=error)

    update = nodes.extract(extract_state)

    assert update["error_kind"] == "permanent"
    assert update["error_code"] == "processing_error"
    assert update["steps"][0]["outcome"] == "failed"


def test_extract_unparseable_output_is_transient(extract_state, monkeypatch):
    fake_obed(monkeypatch, {"parsed": None, "parsing_error": ValueError("Invalid json output"),
                            "raw": SimpleNamespace(usage_metadata=USAGE)})

    update = nodes.extract(extract_state)

    assert update["error_kind"] == "transient"
    assert "couldn't be parsed" in update["error_message"]
    assert update["steps"][0]["usage"] == USAGE  # the failed call still cost tokens


# validate: a made-up, client-neutral schema, because validate knows nothing
# about any client. It only checks the extraction against the schema it's given.

SCHEMA = {
    "type": "object",
    "required": ["issued_on", "items"],
    "properties": {
        "issued_on": {"type": ["string", "null"], "format": "date"},
        "items": {"type": "array", "items": {"type": "object"}},
    },
}


def document(issued_on="2026-01-07"):
    return {"issued_on": issued_on, "items": [{"amount": 10.0}]}


def validate_state(extraction, attempts=1, max_attempts=3):
    return {"extraction": extraction, "output_schema": SCHEMA,
            "attempts": attempts, "max_attempts": max_attempts}


def schema_check_of(update):
    return next(c for c in update["checks"] if c["name"] == "matches_schema")


def test_validate_matching_extraction_passes_and_completes():
    update = nodes.validate(validate_state(document()))

    assert all(c["passed"] for c in update["checks"])
    assert update["status"] == "completed"
    assert update["steps"][0]["outcome"] == "ok"


def test_validate_schema_failure_retries_while_attempts_are_left():
    update = nodes.validate(validate_state(document(issued_on="10.12.2025"), attempts=1))

    check = schema_check_of(update)
    assert not check["passed"]
    assert "issued_on" in check["message"]
    assert update["steps"][0]["outcome"] == "retry"
    assert "status" not in update  # still processing: the routing sends it back to extract


def test_validate_schema_failure_on_last_attempt_completes_with_the_failure_recorded():
    update = nodes.validate(validate_state(document(issued_on="10.12.2025"), attempts=3))

    assert not schema_check_of(update)["passed"]
    assert update["status"] == "completed"
    assert update["steps"][0]["outcome"] == "ok"


def test_validate_reports_every_schema_error_not_just_the_first():
    update = nodes.validate(validate_state({"items": "not a list"}))

    message = schema_check_of(update)["message"]
    assert "issued_on" in message  # missing
    assert "items" in message  # wrong type


def test_validate_with_no_extraction_fails_the_schema_check():
    update = nodes.validate(validate_state(None, attempts=3))

    assert not schema_check_of(update)["passed"]
    assert update["status"] == "completed"
