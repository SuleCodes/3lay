"""The envelope: verdicts from checks, and the shape clients receive."""

import jsonschema
import pytest

from orchy.envelope import (ENVELOPE_SCHEMA, build_envelope, reasons, schema_reference,
                            verdict)

FINISHED = "2026-10-09T12:00:00.000+00:00"


def check(name, passed, on_fail=None, message=None):
    result = {"name": name, "passed": passed, "message": message}
    if on_fail:
        result["on_fail"] = on_fail
    return result


def document(status="done", checks=(), **fields):
    return {"index": 0, "name": "statement.pdf", "location": "0", "forwarded_from": None,
            "status": status, "checks": list(checks), "extraction": {"gross": 7000.0},
            "error_code": None, "error_message": None, **fields}


# Verdicts

@pytest.mark.parametrize("checks, expected", [
    ([check("matches_schema", True), check("rule", True, "needs_review")], "accepted"),
    ([check("matches_schema", True), check("rule", None, "reject")], "accepted"),  # skipped
    ([check("matches_schema", False, message="bad date")], "needs_review"),
    ([check("matches_schema", True), check("rule", False, "needs_review")], "needs_review"),
    ([check("matches_schema", True), check("rule", False, "reject")], "rejected"),
    ([check("a", False, "needs_review"), check("b", False, "reject")], "rejected"),
])
def test_verdict_from_checks(checks, expected):
    assert verdict(document(checks=checks)) == expected


def test_a_document_that_couldnt_be_processed_is_rejected_with_its_error_as_the_reason():
    failed = document(status="failed", error_code="unsupported_file_type",
                      error_message="application/msword isn't a supported document type.")

    assert verdict(failed) == "rejected"
    assert reasons(failed) == ["application/msword isn't a supported document type."]


def test_reasons_name_each_failed_check():
    doc = document(checks=[check("matches_schema", True),
                           check("dates_plausible", False, "needs_review", "A date is implausible"),
                           check("totals", None, "needs_review", "Skipped: no total")])

    assert reasons(doc) == ["dates_plausible: A date is implausible"]


# Schema reference

def test_schema_reference_comes_from_the_schemas_id():
    assert schema_reference({"$id": "rolepay-income-statement/1"}, "x") == {
        "id": "rolepay-income-statement", "version": 1}
    assert schema_reference({}, "income_statement") == {"id": "income_statement", "version": None}


# Building the envelope

def state(**fields):
    return {"event_id": "evt-1", "received_at": "2026-10-09T11:59:00.000+00:00",
            "sender": "actor@example.com", "schema_name": "income_statement",
            "output_schema": {"$id": "rolepay-income-statement/1"}, "status": "completed",
            "documents": [document(checks=[check("matches_schema", True)])], **fields}


def test_envelope_for_a_completed_event_matches_the_contract():
    envelope = build_envelope(state(), FINISHED)

    jsonschema.validate(envelope, ENVELOPE_SCHEMA)
    assert envelope["event_type"] == "event.completed"
    assert envelope["case_ref"] == "evt-1"  # its own case unless told otherwise
    assert envelope["run"] == 1
    assert envelope["dashboard_url"] == "https://app.3lay.live/events/evt-1"
    [item] = envelope["items"]
    assert item["source"] == {"attachment": "statement.pdf", "location": "0",
                              "forwarded_from": None}
    assert item["schema"] == {"id": "rolepay-income-statement", "version": 1}
    assert (item["verdict"], item["confidence"], item["data"], item["error"]) == (
        "accepted", None, {"gross": 7000.0}, None)


def test_envelope_for_a_failed_event_carries_the_error():
    failed_document = document(status="failed", error_code="processing_error",
                               error_message="Gave up after 3 attempts.")
    envelope = build_envelope(state(status="failed", error_code="processing_error",
                                    error_message="Gave up after 3 attempts.",
                                    documents=[failed_document]), FINISHED)

    jsonschema.validate(envelope, ENVELOPE_SCHEMA)
    assert envelope["event_type"] == "event.failed"
    assert envelope["error"] == {"code": "processing_error",
                                 "message": "Gave up after 3 attempts."}
    assert envelope["summary"] == {"accepted": 0, "needs_review": 0, "rejected": 1}
    assert envelope["items"][0]["data"] is None  # nothing usable came back


def test_the_contract_rejects_an_envelope_with_an_unexpected_field():
    envelope = build_envelope(state(), FINISHED)
    envelope["surprise"] = True

    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(envelope, ENVELOPE_SCHEMA)
