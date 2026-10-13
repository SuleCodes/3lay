"""Event records: starting, duplicates, finishing, billing, retention, and a full run.

Uses the in-memory database from conftest.py and the fake model, so nothing
touches Supabase or a real provider.
"""

from datetime import datetime, timedelta, timezone

import openai
import pytest
from sqlalchemy import delete, func, select

from conftest import api_error, pdf_bytes, write_email
from orchy import runner
from store.configurations import save_configuration
from store.db import deliveries, event_sources, event_steps, events, users
from store.events import finish_event, is_billable, start_event

RECEIVED = "2026-10-12T09:30:00.000+00:00"

CONFIG = {
    "document_types": [{
        "name": "income_statement",
        "instructions": "Extract it.",
        "schema": {"$id": "test-statement/1", "type": "object",
                   "required": ["issued_on"],
                   "properties": {"issued_on": {"type": "string", "format": "date"}}},
        "rules": [{"id": "dates_plausible", "type": "date_within", "fields": ["issued_on"],
                   "days_before": 1095, "days_after": 31, "on_fail": "needs_review"}],
    }],
    "destinations": [],
}


def start(database, event_id="evt-1", run=1):
    return start_event(database, event_id=event_id, run=run, user_id="u1", case_ref=event_id,
                       received_at=RECEIVED, config_version=1)


def rows(database, table):
    with database.connect() as connection:
        return connection.execute(select(table)).all()


def count(database, table):
    with database.connect() as connection:
        return connection.execute(select(func.count()).select_from(table)).scalar()


# Starting

def test_start_records_the_run_as_processing(database):
    event_pk = start(database)

    [event] = rows(database, events)
    assert event.id == event_pk
    assert (event.event_id, event.run, event.status, event.user_id) == (
        "evt-1", 1, "processing", "u1")
    assert event.finished_at is None


def test_personal_data_expires_90_days_after_the_event_was_received(database):
    start(database)

    [event] = rows(database, events)
    expires = event.personal_data_expires_at.replace(tzinfo=timezone.utc)
    assert expires == datetime(2026, 10, 12, 9, 30, tzinfo=timezone.utc) + timedelta(days=90)


def test_the_same_event_and_run_is_only_recorded_once(database):
    """A queue message delivered twice is skipped the second time."""
    assert start(database) is not None
    assert start(database) is None
    assert count(database, events) == 1


def test_a_new_run_of_the_same_event_is_a_new_record(database):
    start(database, run=1)

    assert start(database, run=2) is not None
    assert count(database, events) == 2


# Billing

@pytest.mark.parametrize("status, error_code, billable", [
    ("completed", None, True),
    ("failed", "no_documents_found", True),        # caused by the sender
    ("failed", "document_unreadable", True),       # caused by the sender
    ("failed", "processing_error", False),         # caused by 3lay
    ("failed", "processing_timeout", False),       # caused by 3lay
])
def test_billing_policy(status, error_code, billable):
    assert is_billable(status, error_code) is billable


# Finishing

def finished_state():
    step = {"started_at": RECEIVED, "finished_at": RECEIVED, "error": None}
    return {
        "status": "completed", "sender": "actor@example.com", "error_code": None,
        "error_message": None, "envelope": {"event_id": "evt-1", "items": []},
        "documents": [
            {"index": 0, "name": "jan.pdf", "content_type": "application/pdf", "location": "0",
             "forwarded_from": None, "status": "done", "attempts": 2,
             "error_code": None, "error_message": None,
             "checks": [{"name": "matches_schema", "passed": True, "message": None}],
             "extraction": {"issued_on": "2026-01-07"}},
            {"index": 1, "name": "notes.docx", "content_type": "application/msword",
             "location": "1", "forwarded_from": None, "status": "failed", "attempts": 0,
             "error_code": "unsupported_file_type", "error_message": "Not supported.",
             "checks": [], "extraction": None},
        ],
        "steps": [
            {**step, "node": "load_inputs", "outcome": "ok", "attachments": 2},
            {**step, "node": "extract", "outcome": "retry", "document": 0, "model": "m:p",
             "usage": {"input_tokens": 3000, "output_tokens": 0}},
            {**step, "node": "extract", "outcome": "ok", "document": 0, "model": "m:p",
             "usage": {"input_tokens": 3000, "output_tokens": 1000}},
            {**step, "node": "finish", "outcome": "ok"},
        ],
        "deliveries": [
            {"destination_id": "dest_x", "url": "https://hooks.example.com/3lay",
             "webhook_id": "evt-1-r1", "attempt": 1, "status": "delivered", "http_status": 200,
             "response_excerpt": "ok", "error": None, "attempted_at": RECEIVED,
             "duration_ms": 120, "retry_in_seconds": None},
        ],
    }


def test_finish_records_the_outcome_and_everything_the_run_produced(database):
    event_pk = start(database)

    finish_event(database, event_pk, finished_state())

    [event] = rows(database, events)
    assert (event.status, event.sender, event.model, event.billable) == (
        "completed", "actor@example.com", "m:p", True)
    assert (event.input_tokens, event.output_tokens) == (6000, 1000)  # retries count too
    assert event.envelope == {"event_id": "evt-1", "items": []}

    sources = sorted(rows(database, event_sources), key=lambda r: r.position)
    assert [(s.name, s.status, s.verdict, s.error_code) for s in sources] == [
        ("jan.pdf", "done", "accepted", None),
        ("notes.docx", "failed", "rejected", "unsupported_file_type")]
    assert sources[0].extraction == {"issued_on": "2026-01-07"}

    steps = sorted(rows(database, event_steps), key=lambda r: r.sequence)
    assert [(s.node, s.source_position, s.outcome) for s in steps] == [
        ("load_inputs", None, "ok"), ("extract", 0, "retry"), ("extract", 0, "ok"),
        ("finish", None, "ok")]
    assert steps[0].details == {"attachments": 2}
    assert steps[2].details["usage"]["output_tokens"] == 1000

    [delivery] = rows(database, deliveries)
    assert (delivery.destination_id, delivery.url, delivery.status, delivery.http_status) == (
        "dest_x", "https://hooks.example.com/3lay", "delivered", 200)


def test_deleting_a_client_deletes_its_events_and_everything_under_them(database):
    finish_event(database, start(database), finished_state())

    with database.begin() as connection:
        connection.execute(delete(users).where(users.c.id == "u1"))

    for table in (events, event_sources, event_steps, deliveries):
        assert count(database, table) == 0


# A full run through the runner: configuration, record, graph, record

@pytest.fixture
def rolepay(database, monkeypatch):
    save_configuration(database, "rolepay-agent", CONFIG)
    monkeypatch.setattr(runner, "engine", lambda: database)
    return database


@pytest.fixture
def email_to_rolepay(tmp_path):
    path = write_email(tmp_path / "e.eml", ("jan.pdf", "application/pdf", pdf_bytes()))
    # write_email addresses it to client@in.example.com; this one goes to Rolepay.
    text = (tmp_path / "e.eml").read_bytes().replace(b"client@in.example.com",
                                                     b"rolepay-agent@in.3lay.live")
    (tmp_path / "e.eml").write_bytes(text)
    return path


def test_a_full_run_is_recorded_with_the_clients_configuration(rolepay, email_to_rolepay,
                                                              fake_model):
    fake_model({"issued_on": "0501-01-06"})  # implausible: needs review

    result = runner.process_event(email_to_rolepay, event_id="evt-full", received_at=RECEIVED)

    assert result.recorded
    [event] = rows(rolepay, events)
    assert (event.event_id, event.status, event.config_version, event.billable) == (
        "evt-full", "completed", 1, True)
    assert event.envelope["summary"] == {"accepted": 0, "needs_review": 1, "rejected": 0}
    [source] = rows(rolepay, event_sources)
    assert source.verdict == "needs_review"
    assert [c["name"] for c in source.checks] == ["matches_schema", "dates_plausible"]
    assert count(rolepay, event_steps) == len(result.final_state["steps"])


def test_a_duplicate_queue_message_is_skipped_without_running_the_graph(
        rolepay, email_to_rolepay, fake_model):
    calls = fake_model({"issued_on": "2026-01-07"})
    runner.process_event(email_to_rolepay, event_id="evt-dup", received_at=RECEIVED)

    again = runner.process_event(email_to_rolepay, event_id="evt-dup", received_at=RECEIVED)

    assert again.final_state is None and "already been recorded" in again.skipped_reason
    assert len(calls) == 1  # the model wasn't called a second time
    assert count(rolepay, events) == 1


def test_a_3lay_failure_is_recorded_as_not_billable(rolepay, email_to_rolepay, fake_model):
    fake_model(api_error(openai.InternalServerError, 503))

    runner.process_event(email_to_rolepay, event_id="evt-503", received_at=RECEIVED)

    [event] = rows(rolepay, events)
    assert (event.status, event.error_code, event.billable) == (
        "failed", "processing_error", False)


def test_a_crash_leaves_the_record_processing_for_the_watchdog(rolepay, email_to_rolepay,
                                                              fake_model, monkeypatch):
    fake_model({"issued_on": "2026-01-07"})

    class CrashingGraph:
        @staticmethod
        def invoke(_state):
            raise RuntimeError("the job died")

    monkeypatch.setattr(runner, "build_graph", CrashingGraph)

    with pytest.raises(RuntimeError):
        runner.process_event(email_to_rolepay, event_id="evt-crash", received_at=RECEIVED)
    [event] = rows(rolepay, events)
    assert event.status == "processing"


def test_offline_runs_are_not_recorded(email_to_rolepay, fake_model):
    fake_model({"issued_on": "2026-01-07"})
    settings = {"document_type": "income_statement", "instructions": "x",
                "output_schema": CONFIG["document_types"][0]["schema"],
                "schema_name": "income_statement", "rules": [], "destinations": [],
                "max_attempts": 3, "config_version": 0}

    result = runner.process_event(email_to_rolepay, event_id="evt-off", settings=settings)

    assert result.final_state["status"] == "completed"
    assert not result.recorded
