"""Correlation ids: W3C traceparents, carrying a trace across a queue, and log lines.

These run without ddtrace (DD_TRACE_ENABLED unset), the way local runs and
tests do: the correlation id must work either way. With ddtrace the same
functions make real Datadog spans (checked by hand; there's no agent in tests).
"""

import json
import logging

import pytest

from jobs.ingest import handle, traceparent_of
from observability import (JsonFormatter, configure_logging, continue_trace, current_trace_id,
                           new_traceparent, parse_traceparent)
from orchy.runner import EventRun
from test_ingest_job import JOB, Clients, FakeBlobs, FakeQueue, message

TRACE = "4bf92f3577b34da6a3ce929d0e0e4736"
TRACEPARENT = f"00-{TRACE}-00f067aa0ba902b7-01"


@pytest.fixture(autouse=True)
def no_datadog(monkeypatch):
    monkeypatch.delenv("DD_TRACE_ENABLED", raising=False)


def test_a_new_traceparent_is_valid_w3c():
    trace_id, span_id = parse_traceparent(new_traceparent())
    assert len(trace_id) == 32 and len(span_id) == 16


@pytest.mark.parametrize("value", [
    None, "", "garbage", f"01-{TRACE}-00f067aa0ba902b7-01",           # unknown version
    f"00-{'0' * 32}-00f067aa0ba902b7-01", f"00-{TRACE}-{'0' * 16}-01",  # all-zero ids are invalid
    f"00-{TRACE[:-1]}-00f067aa0ba902b7-01"])
def test_anything_but_a_valid_traceparent_is_ignored(value):
    assert parse_traceparent(value) is None


def test_a_trace_continues_from_its_traceparent_and_ends_cleanly():
    assert current_trace_id() is None
    with continue_trace(TRACEPARENT, "ingest.message") as correlation_id:
        assert correlation_id == TRACE == current_trace_id()
    assert current_trace_id() is None


def test_without_a_traceparent_a_new_trace_starts():
    with continue_trace(None, "ingest.message") as correlation_id:
        assert len(correlation_id) == 32 and correlation_id != TRACE


def test_a_queue_message_carries_its_emails_trace_into_the_run():
    seen = []

    def run(_email_path, **_kwargs):
        seen.append(current_trace_id())  # what start_event records as events.trace_id
        return EventRun({"status": "completed"}, recorded=True)

    clients = Clients(queue=FakeQueue(), poison_queue=FakeQueue(), blobs=FakeBlobs())
    handle(message({**JOB, "traceparent": TRACEPARENT}), clients, "raw-ingest", run=run)

    assert seen == [TRACE]


@pytest.mark.parametrize("content", ["not json", json.dumps({"traceparent": 42}), json.dumps([1])])
def test_an_odd_message_just_has_no_traceparent(content):
    assert traceparent_of(message(text=content)) is None


def test_log_lines_are_json_with_their_level_and_correlation_id():
    record = logging.LogRecord("jobs.ingest", logging.INFO, __file__, 1, "Event %s done", ("e1",), None)
    with continue_trace(TRACEPARENT, "ingest.message"):
        entry = json.loads(JsonFormatter().format(record))

    assert entry["status"] == "info"  # not "error", as plain text on stderr would be
    assert entry["message"] == "Event e1 done" and entry["logger.name"] == "jobs.ingest"
    assert entry["dd.trace_id"] == TRACE


def test_an_exception_is_logged_with_its_stack():
    try:
        raise ValueError("bad message")
    except ValueError:
        import sys  # pylint: disable=import-outside-toplevel
        record = logging.LogRecord("jobs.ingest", logging.ERROR, __file__, 1, "failed", None,
                                   sys.exc_info())
    entry = json.loads(JsonFormatter().format(record))

    assert (entry["status"], entry["error.kind"], entry["error.message"]) == (
        "error", "ValueError", "bad message")
    assert "Traceback" in entry["error.stack"]


def test_logs_go_to_stdout_and_azures_request_chatter_is_quietened(capsys):
    configure_logging()
    logging.getLogger("azure.core.pipeline.policies.http_logging_policy").info("Request URL: ...")
    logging.getLogger("jobs.ingest").info("Processed 1 message(s)")

    out, err = capsys.readouterr()
    assert err == ""
    assert [json.loads(line)["message"] for line in out.splitlines()] == ["Processed 1 message(s)"]


def test_each_message_is_its_own_trace_even_inside_the_executions():
    seen = []

    def run(_email_path, **_kwargs):
        seen.append(current_trace_id())
        return EventRun({"status": "completed"}, recorded=True)

    clients = Clients(queue=FakeQueue(), poison_queue=FakeQueue(), blobs=FakeBlobs())
    with continue_trace(None, "orchy.execution") as execution:
        handle(message({**JOB, "traceparent": TRACEPARENT}), clients, "raw-ingest", run=run)
        handle(message(JOB), clients, "raw-ingest", run=run)  # no traceparent: a trace of its own
        assert current_trace_id() == execution

    assert seen[0] == TRACE and seen[1] not in (TRACE, execution)
