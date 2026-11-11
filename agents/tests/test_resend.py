"""Resending an event's webhook from the dashboard (noti/resend.py).

The test database from conftest.py, an event recorded the way the pipeline
records one, and a fake server (httpx.MockTransport), so nothing leaves the
machine.
"""

import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import select
from standardwebhooks.webhooks import Webhook

from noti.resend import ResendNotFound, ResendRefused, resend
from noti.signing import webhook_id
from store.db import deliveries, events
from store.secrets import create_or_rotate_secret

# Real time: a delivery records when its attempt really happened.
NOW = datetime.now(timezone.utc)
ENVELOPE = {"envelope_version": 1, "event_type": "event.completed", "event_id": "evt-1",
            "run": 1, "items": [{"verdict": "accepted", "data": {"total_net": 1240.5}}]}
OLD_URL = "https://old.example.com/hook"
NEW_URL = "https://new.example.com/hook"
LIVE = [{"id": "dest_rolepay_agent_main", "type": "webhook", "url": NEW_URL,
         "events": ["event.completed", "event.failed"]}]


def record_event(database, envelope=ENVELOPE, expires=NOW + timedelta(days=80), user_id="u1"):
    """An event as the pipeline leaves it, with one automatic delivery that got a 404."""
    pk = str(uuid.uuid4())
    with database.begin() as connection:
        connection.execute(events.insert().values(
            id=pk, event_id="evt-1", run=1, user_id=user_id, case_ref="evt-1",
            received_at=NOW - timedelta(days=1), started_at=NOW - timedelta(days=1),
            status="completed", envelope=envelope, personal_data_expires_at=expires,
            created_at=NOW - timedelta(days=1)))
        connection.execute(deliveries.insert().values(
            id=str(uuid.uuid4()), event_pk=pk, destination_id="dest_rolepay_agent_main",
            url=OLD_URL, webhook_id="evt-1-r1", attempt=1, status="gave_up", http_status=404,
            attempted_at=NOW - timedelta(days=1)))
    return pk


def server(status=200, seen=None):
    def handle(request):
        if seen is not None:
            seen.append(request)
        return httpx.Response(status, text="ok")
    return httpx.Client(transport=httpx.MockTransport(handle), follow_redirects=False)


def attempts_of(database, pk):
    with database.connect() as connection:
        return connection.execute(select(deliveries).where(deliveries.c.event_pk == pk)
                                  .order_by(deliveries.c.attempt)).all()


def test_a_resend_is_the_same_message_signed_and_sent_to_the_live_url(database):
    pk = record_event(database)
    secret = create_or_rotate_secret(database, "u1", "dest_rolepay_agent_main")
    seen = []

    [attempt] = resend(database, "u1", "evt-1", LIVE, server(200, seen), now=NOW)

    [request] = seen
    assert str(request.url) == NEW_URL  # the live setup's URL, not the original
    assert request.headers["webhook-id"] == webhook_id("evt-1", 1)  # same message
    assert Webhook(secret).verify(request.content, dict(request.headers)) == ENVELOPE
    assert (attempt["status"], attempt["trigger"], attempt["attempt"]) == ("delivered", "manual", 2)

    old, new = attempts_of(database, pk)
    assert (old.trigger, old.url) == ("automatic", OLD_URL)
    assert (new.trigger, new.url, new.attempt, new.http_status) == ("manual", NEW_URL, 2, 200)


def test_a_failed_resend_is_one_attempt_recorded_not_retried(database):
    pk = record_event(database)
    create_or_rotate_secret(database, "u1", "dest_rolepay_agent_main")
    seen = []

    [attempt] = resend(database, "u1", "evt-1", LIVE, server(503, seen), now=NOW)

    assert len(seen) == 1
    assert (attempt["status"], attempt["http_status"]) == ("gave_up", 503)
    assert attempts_of(database, pk)[-1].status == "gave_up"


def test_resending_again_straight_away_is_refused(database):
    record_event(database)
    create_or_rotate_secret(database, "u1", "dest_rolepay_agent_main")
    start = datetime.now(timezone.utc)  # the attempt below is recorded at the real time
    resend(database, "u1", "evt-1", LIVE, server(), now=start)

    with pytest.raises(ResendRefused, match="Wait 10 seconds"):
        resend(database, "u1", "evt-1", LIVE, server(), now=start + timedelta(seconds=5))
    resend(database, "u1", "evt-1", LIVE, server(), now=start + timedelta(seconds=11))  # fine


@pytest.mark.parametrize("setup, live, message", [
    ({"user_id": "u2"}, LIVE, "Event not found"),                       # another client's
    ({"envelope": None}, LIVE, "hasn't finished processing"),
    ({"expires": NOW - timedelta(minutes=1)}, LIVE, "older than 90 days"),
    ({}, [{**LIVE[0], "events": ["event.failed"]}], "No webhook in your live setup"),
])
def test_a_resend_that_cant_happen_says_why(database, setup, live, message):
    record_event(database, **setup)
    seen = []

    with pytest.raises(ResendRefused, match=message) as refused:
        resend(database, "u1", "evt-1", live, server(200, seen), now=NOW)

    assert not seen
    assert isinstance(refused.value, ResendNotFound) == (message == "Event not found")
