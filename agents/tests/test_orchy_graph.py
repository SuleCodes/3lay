"""Tests for the whole graph: real nodes and routing, with only the model call faked.

Each test is one route through the graph, starting from a raw email. The fake
model gives the next answer on each call, which is how retries are tested
without a real provider. With several documents, calls happen in document order.
"""

import json

import httpx
import jsonschema
import openai
import pytest

from conftest import api_error, email_message, pdf_bytes, png_bytes, write_email
from noti.signing import generate_secret, verify
from orchy import nodes
from orchy.envelope import ENVELOPE_SCHEMA
from orchy.graph import build_graph

SCHEMA = {
    "type": "object",
    "required": ["issued_on"],
    "properties": {"issued_on": {"type": "string", "format": "date"}},
}
VALID = {"issued_on": "2026-01-07"}
INVALID = {"issued_on": "10.12.2025"}  # fails the schema's date format


def run(email_path, **fields):
    """Runs the graph, and checks every envelope it builds matches the client contract."""
    final = build_graph().invoke({
        "event_id": "evt-1",
        "email_path": email_path,
        "instructions": "Extract it.",
        "output_schema": SCHEMA,
        "schema_name": "document",
        "max_attempts": 3,
        "steps": [],
        **fields,
    })
    jsonschema.validate(final["envelope"], ENVELOPE_SCHEMA)
    return final


def route(final_state):
    """The route the event took, e.g. ["load_inputs", "extract", ..., "build_envelope", "deliver"]."""
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
    assert route(final) == ["load_inputs", "extract", "validate", "finish", "build_envelope", "deliver"]
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
        ("validate", 0), ("validate", 1), ("finish", None), ("build_envelope", None), ("deliver", None)]


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
    assert route(final) == ["load_inputs", "finish", "build_envelope", "deliver"]
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
    assert route(final) == ["load_inputs", "extract", "extract", "extract", "finish", "build_envelope", "deliver"]
    assert "Gave up after 3 attempts" in final["error_message"]


def test_permanent_error_fails_without_retrying(one_pdf, fake_model):
    fake_model(api_error(openai.AuthenticationError, 401))

    final = run(one_pdf)

    assert final["status"] == "failed"
    assert final["documents"][0]["attempts"] == 1
    assert route(final) == ["load_inputs", "extract", "finish", "build_envelope", "deliver"]


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
    assert route(final) == ["load_inputs", "extract", "validate", "extract", "validate", "finish", "build_envelope", "deliver"]


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
    assert route(final) == ["load_inputs", "extract", "validate", "finish", "build_envelope", "deliver"]


def test_steps_accumulate_through_the_reducer(one_pdf, fake_model):
    """Each node returns its own step records; the reducer adds them up, not replaces."""
    fake_model(VALID)

    final = run(one_pdf, steps=[{"node": "earlier", "outcome": "ok"}])

    assert route(final) == ["earlier", "load_inputs", "extract", "validate", "finish", "build_envelope", "deliver"]


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


# The envelope: run() above also checks every envelope matches the client contract.

def test_the_envelope_reflects_the_run(two_documents, fake_model):
    rules = [{"id": "dates_plausible", "type": "date_within", "fields": ["issued_on"],
              "days_before": 1095, "days_after": 31, "on_fail": "needs_review"}]
    fake_model(VALID, {"issued_on": "0501-01-06"})

    envelope = run(two_documents, rules=rules)["envelope"]

    assert (envelope["event_type"], envelope["status"]) == ("event.completed", "completed")
    assert envelope["sender"] == "actor@example.com"
    assert envelope["summary"] == {"accepted": 1, "needs_review": 1, "rejected": 0}
    assert [i["verdict"] for i in envelope["items"]] == ["accepted", "needs_review"]
    assert envelope["items"][1]["reasons"][0].startswith("dates_plausible:")


def test_a_failed_event_lists_the_rejected_attachments(tmp_path, fake_model):
    fake_model(VALID)

    envelope = run(write_email(tmp_path / "e.eml",
                               ("notes.docx", "application/msword", b"x")))["envelope"]

    assert envelope["event_type"] == "event.failed"
    assert envelope["error"]["code"] == "no_documents_found"
    [item] = envelope["items"]
    assert (item["verdict"], item["error"]["code"], item["data"]) == (
        "rejected", "unsupported_file_type", None)


# Delivery: a fake server stands in for the client's webhook.

@pytest.fixture
def webhook(monkeypatch):
    """A fake client webhook. Set .answer to change its response; .received has the requests."""

    hook = type("Webhook", (), {})()
    hook.secret = generate_secret()
    hook.answer = httpx.Response(200)
    hook.received = []

    def handler(request):
        hook.received.append(request)
        return hook.answer

    monkeypatch.setenv("NOTI_SECRET_DEST_TEST", hook.secret)
    monkeypatch.setattr(nodes, "http_client", lambda: httpx.Client(
        transport=httpx.MockTransport(handler), follow_redirects=False))
    monkeypatch.setattr(nodes, "wait", lambda seconds: None)
    hook.destinations = [{"id": "dest_test", "type": "webhook",
                          "url": "https://hooks.example.com/3lay",
                          "events": ["event.completed", "event.failed"]}]
    return hook


def test_the_envelope_is_delivered_signed_to_the_clients_webhook(one_pdf, fake_model, webhook):
    fake_model(VALID)

    final = run(one_pdf, destinations=webhook.destinations)

    [request] = webhook.received
    verify(webhook.secret, dict(request.headers), request.content)
    assert request.headers["webhook-id"] == "evt-1-r1"
    assert json.loads(request.content) == final["envelope"]  # exactly what was built
    assert [d["status"] for d in final["deliveries"]] == ["delivered"]
    assert route(final)[-1] == "deliver"


def test_a_failed_delivery_doesnt_change_the_events_status(one_pdf, fake_model, webhook):
    fake_model(VALID)
    webhook.answer = httpx.Response(503)

    final = run(one_pdf, destinations=webhook.destinations)

    assert final["status"] == "completed"  # processing worked; delivery is separate
    assert len(webhook.received) == 4
    assert final["deliveries"][-1]["status"] == "gave_up"
    assert final["steps"][-1]["outcome"] == "failed"


def test_a_destination_only_gets_the_event_types_it_asked_for(tmp_path, fake_model, webhook):
    fake_model(VALID)
    completed_only = [{**webhook.destinations[0], "events": ["event.completed"]}]

    final = run(write_email(tmp_path / "e.eml", ("notes.docx", "application/msword", b"x")),
                destinations=completed_only)  # this event fails

    assert webhook.received == []
    assert final["deliveries"] == []
