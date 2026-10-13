"""The ingest job's handling of queue messages, with fake Azure clients.

The run itself is replaced by a fake too: process_event is tested in
test_events.py. These tests are about what happens to the message.
"""

import json
from types import SimpleNamespace

import pytest
from azure.core.exceptions import ResourceNotFoundError

from jobs.ingest import MAX_ATTEMPTS, Clients, handle, username_of
from orchy.runner import EventRun
from store.configurations import ConfigurationNotFound

EMAIL = b"From: actor@example.com\r\nTo: rolepay-agent@in.3lay.live\r\n\r\nhello"
JOB = {"ingest_id": "evt-1", "client": "Rolepay-Agent@in.3lay.live", "origin": "actor@example.com",
       "container": "raw-ingest", "blob_name": "rolepay-agent@in.3lay.live/x/2026/10/13/evt-1",
       "content_type": "message/rfc822", "size_bytes": len(EMAIL),
       "received_at": "2026-10-13T09:00:00+00:00"}


class FakeQueue:
    def __init__(self):
        self.deleted, self.sent = [], []

    def delete_message(self, message):
        self.deleted.append(message.id)

    def send_message(self, content):
        self.sent.append(json.loads(content))


class FakeBlobs:
    def __init__(self):
        self.read = []

    def get_blob_client(self, container, name):
        self.read.append((container, name))
        content = SimpleNamespace(readall=lambda: EMAIL)
        return SimpleNamespace(download_blob=lambda: content)


def message(job=None, attempts=1, text=None):
    return SimpleNamespace(id="m1", dequeue_count=attempts,
                           content=text if text is not None else json.dumps(job or JOB))


@pytest.fixture
def clients():
    return Clients(queue=FakeQueue(), poison_queue=FakeQueue(), blobs=FakeBlobs())


def handled(clients, msg, run):
    return handle(msg, clients, "raw-ingest", run=run)


def completed_run(calls):
    def run(email_path, **kwargs):
        calls.append({"email": email_path.read_bytes(), **kwargs})
        return EventRun({"status": "completed"}, recorded=True)
    return run


def test_a_message_is_run_from_its_blob_and_then_deleted(clients):
    calls = []

    assert handled(clients, message(), completed_run(calls)) == "done"

    assert len(calls) == 1
    call = calls[0]
    assert call["email"] == EMAIL  # the downloaded email
    assert (call["event_id"], call["client"], call["received_at"]) == (
        "evt-1", "rolepay-agent", "2026-10-13T09:00:00+00:00")
    assert clients.blobs.read == [("raw-ingest", JOB["blob_name"])]
    assert clients.queue.deleted == ["m1"]


def test_an_already_recorded_event_is_skipped_and_deleted(clients):
    def already(_email_path, **_kwargs):
        return EventRun(None, recorded=False, skipped_reason="already recorded")

    assert handled(clients, message(), already) == "skipped"
    assert clients.queue.deleted == ["m1"]


def test_a_message_whose_email_was_deleted_is_skipped_and_deleted(clients):
    """Deleting an account deletes its stored emails; queued messages for them are skipped."""
    def gone(*_args):
        raise ResourceNotFoundError("The specified blob does not exist.")
    clients.blobs.get_blob_client = lambda *_args: SimpleNamespace(download_blob=gone)
    calls = []

    assert handled(clients, message(), completed_run(calls)) == "skipped"
    assert not calls
    assert clients.queue.deleted == ["m1"]
    assert clients.poison_queue.sent == []


def test_a_crash_leaves_the_message_to_be_retried(clients):
    def crash(_email_path, **_kwargs):
        raise RuntimeError("database unreachable")

    assert handled(clients, message(), crash) == "retry"
    assert clients.queue.deleted == []  # it reappears after the visibility timeout
    assert clients.poison_queue.sent == []


def test_a_message_that_keeps_failing_goes_to_the_poison_queue(clients):
    calls = []

    outcome = handled(clients, message(attempts=MAX_ATTEMPTS + 1), completed_run(calls))

    assert outcome == "poisoned"
    assert not calls  # not run again
    [kept] = clients.poison_queue.sent
    assert kept["reason"] == f"Failed {MAX_ATTEMPTS} times"
    assert json.loads(kept["message"]) == JOB  # the original message is kept
    assert clients.queue.deleted == ["m1"]


def test_a_client_without_an_active_configuration_goes_to_the_poison_queue(clients):
    def onboarding(_email_path, **_kwargs):
        raise ConfigurationNotFound("rolepay-agent has no active configuration")

    assert handled(clients, message(), onboarding) == "poisoned"
    assert "no active configuration" in clients.poison_queue.sent[0]["reason"]
    assert clients.queue.deleted == ["m1"]


@pytest.mark.parametrize("text, reason", [
    ("not json", "Not a valid ingest message"),
    (json.dumps({"ingest_id": "x"}), "Not a valid ingest message"),
    (json.dumps({**JOB, "container": "something-else"}), "Unexpected container"),
])
def test_an_unusable_message_goes_to_the_poison_queue_without_running(clients, text, reason):
    calls = []

    assert handled(clients, message(text=text), completed_run(calls)) == "poisoned"
    assert not calls
    assert reason in clients.poison_queue.sent[0]["reason"]


def test_the_client_is_the_username_part_of_the_address():
    assert username_of("Rolepay-Agent@in.3lay.live") == "rolepay-agent"
