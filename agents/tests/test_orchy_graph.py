"""Tests for the whole graph: real nodes and routing, with only the model call faked.

Each test is one route through the graph. The fake model can give a different
answer on each attempt, which is how retries are tested without a real provider.
"""

from types import SimpleNamespace

import httpx
import openai
import pymupdf
import pytest

from orchy import nodes
from orchy.graph import build_graph

SCHEMA = {
    "type": "object",
    "required": ["issued_on"],
    "properties": {"issued_on": {"type": "string", "format": "date"}},
}
USAGE = {"input_tokens": 3000, "output_tokens": 1000, "total_tokens": 4000}
VALID = {"issued_on": "2026-01-07"}
INVALID = {"issued_on": "10.12.2025"}  # fails the schema's date format


def api_error(error_class, status):
    request = httpx.Request("POST", "https://router.huggingface.co/v1/chat/completions")
    return error_class("simulated", response=httpx.Response(status, request=request), body=None)


@pytest.fixture
def initial_state(tmp_path, monkeypatch):
    pdf = tmp_path / "document.pdf"
    doc = pymupdf.open()
    doc.new_page()
    doc.save(pdf)
    doc.close()
    monkeypatch.setattr(nodes, "obed_model", lambda: (object(), "test/model:provider"))
    return {
        "event_id": "evt-1",
        "document_path": str(pdf),
        "instructions": "Extract it.",
        "output_schema": SCHEMA,
        "schema_name": "document",
        "max_attempts": 3,
        "attempts": 0,
        "steps": [],
    }


def fake_obed(monkeypatch, *answers):
    """Each call to the model gets the next answer: a dict to return, or an error to raise.

    The last answer repeats if the graph asks more times than there are answers.
    """
    remaining = list(answers)

    def obed_extract(*_args):
        answer = remaining.pop(0) if len(remaining) > 1 else remaining[0]
        if isinstance(answer, Exception):
            raise answer
        return {"parsed": answer, "parsing_error": None,
                "raw": SimpleNamespace(usage_metadata=USAGE)}

    monkeypatch.setattr(nodes, "obed_extract", obed_extract)


def route(final_state):
    """The route the event took, e.g. ["load_inputs", "extract", "validate"]."""
    return [step["node"] for step in final_state["steps"]]


def test_happy_path_completes_first_time(initial_state, monkeypatch):
    fake_obed(monkeypatch, VALID)

    final = build_graph().invoke(initial_state)

    assert final["status"] == "completed"
    assert final["extraction"] == VALID
    assert final["attempts"] == 1
    assert route(final) == ["load_inputs", "extract", "validate"]
    assert final["event_id"] == "evt-1"  # fields no node touches pass through unchanged


def test_steps_accumulate_through_the_reducer(initial_state, monkeypatch):
    """Each node returns a one-item steps list; the reducer adds them up, not replaces."""
    fake_obed(monkeypatch, VALID)
    initial_state["steps"] = [{"node": "earlier", "outcome": "ok"}]

    final = build_graph().invoke(initial_state)

    assert route(final) == ["earlier", "load_inputs", "extract", "validate"]


def test_unreadable_document_fails_without_calling_the_model(initial_state, monkeypatch):
    fake_obed(monkeypatch, VALID)
    initial_state["document_path"] = "does-not-exist.pdf"

    final = build_graph().invoke(initial_state)

    assert final["status"] == "failed"
    assert final["error_code"] == "document_unreadable"
    assert route(final) == ["load_inputs", "fail"]


def test_transient_error_is_retried_then_succeeds(initial_state, monkeypatch):
    fake_obed(monkeypatch, api_error(openai.RateLimitError, 429), VALID)

    final = build_graph().invoke(initial_state)

    assert final["status"] == "completed"
    assert final["attempts"] == 2
    assert route(final) == ["load_inputs", "extract", "extract", "validate"]
    assert [s["outcome"] for s in final["steps"] if s["node"] == "extract"] == ["retry", "ok"]


def test_transient_errors_every_time_fail_after_the_last_attempt(initial_state, monkeypatch):
    fake_obed(monkeypatch, api_error(openai.InternalServerError, 503))

    final = build_graph().invoke(initial_state)

    assert final["status"] == "failed"
    assert final["error_code"] == "processing_error"
    assert final["attempts"] == 3
    assert route(final) == ["load_inputs", "extract", "extract", "extract", "fail"]
    assert "Gave up after 3 attempts" in final["error_message"]


def test_permanent_error_fails_without_retrying(initial_state, monkeypatch):
    fake_obed(monkeypatch, api_error(openai.AuthenticationError, 401))

    final = build_graph().invoke(initial_state)

    assert final["status"] == "failed"
    assert final["error_code"] == "processing_error"
    assert final["attempts"] == 1
    assert route(final) == ["load_inputs", "extract", "fail"]


def test_schema_failure_is_retried_then_succeeds(initial_state, monkeypatch):
    fake_obed(monkeypatch, INVALID, VALID)

    final = build_graph().invoke(initial_state)

    assert final["status"] == "completed"
    assert final["checks"][0]["passed"]
    assert route(final) == ["load_inputs", "extract", "validate", "extract", "validate"]


def test_schema_failure_every_time_completes_with_the_failure_recorded(initial_state, monkeypatch):
    """Your decision: after the last attempt, complete anyway so Justice flags it needs_review."""
    fake_obed(monkeypatch, INVALID)

    final = build_graph().invoke(initial_state)

    assert final["status"] == "completed"
    assert not final["checks"][0]["passed"]
    assert final["attempts"] == 3
    assert route(final) == [
        "load_inputs", "extract", "validate", "extract", "validate", "extract", "validate",
    ]
