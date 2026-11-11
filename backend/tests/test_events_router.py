"""The events dashboard API: listing, filtering, paging, one event's detail, and Resend.

Events are recorded straight into the test database the way the pipeline
records them; the resend itself (agents/noti/resend.py) is faked here and
tested in the agents' tests.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from app.models import ClientConfiguration, Delivery, Event, EventSource, EventStep
from app.services import boardy

NOW = datetime.now(timezone.utc)
HOOK = {"id": "dest_rolepay_agent_main", "type": "webhook", "url": "https://hooks.example/3lay",
        "events": ["event.completed", "event.failed"]}


def add_event(db, event_id, *, user_id="u1", minutes_ago=0, status="completed", run=1,
              verdicts=("accepted",), deliveries=(("delivered", 200),), expires_in_days=80,
              envelope=True):
    received = NOW - timedelta(minutes=minutes_ago)
    event = Event(id=str(uuid.uuid4()), event_id=event_id, run=run, user_id=user_id,
                  case_ref=event_id, sender="jane@example.com", received_at=received,
                  started_at=received, finished_at=received + timedelta(seconds=30),
                  status=status, config_version=2, trace_id="4bf92f3577b34da6a3ce929d0e0e4736",
                  envelope={"event_type": f"event.{status}", "event_id": event_id} if envelope else None,
                  personal_data_expires_at=NOW + timedelta(days=expires_in_days),
                  model="secret-model:provider", input_tokens=1200)
    db.add(event)
    for i, verdict in enumerate(verdicts):
        db.add(EventSource(event_pk=event.id, position=i, name=f"doc{i}.pdf",
                           content_type="application/pdf", status="done", attempts=1,
                           verdict=verdict, extraction={"total_net": 100 + i},
                           checks=[{"name": "line_totals_add_up", "passed": verdict == "accepted",
                                    "message": "Line totals don't add up"}]))
    for n, (outcome, http_status) in enumerate(deliveries, start=1):
        db.add(Delivery(event_pk=event.id, destination_id=HOOK["id"], url=HOOK["url"],
                        webhook_id=f"{event_id}-r{run}", attempt=n, status=outcome,
                        http_status=http_status, attempted_at=received + timedelta(seconds=20 + n)))
    for i, (node, outcome) in enumerate([("load_inputs", "ok"), ("extract", "retry"),
                                         ("extract", "ok"), ("validate", "ok"), ("deliver", "ok")]):
        db.add(EventStep(event_pk=event.id, sequence=i, node=node, outcome=outcome,
                         started_at=received + timedelta(seconds=i * 5),
                         finished_at=received + timedelta(seconds=i * 5 + 4),
                         details={"model": "secret-model:provider", "usage": {"input_tokens": 600}}))
    db.commit()
    return event


def live_setup(db, user_id="u1", destinations=(HOOK,)):
    db.add(ClientConfiguration(user_id=user_id, version=2, status="active",
                               config={"destinations": list(destinations)}))
    db.commit()


# The list

def test_the_list_is_the_clients_own_events_newest_first(api, db_session):
    add_event(db_session, "older", minutes_ago=10)
    add_event(db_session, "newer", minutes_ago=1, verdicts=("accepted", "needs_review"))
    add_event(db_session, "someone-elses", user_id="u2")

    page = api.sign_in("u1").get("/events").json()

    assert [e["event_id"] for e in page["events"]] == ["newer", "older"]
    newest = page["events"][0]
    assert newest["documents"] == 2
    assert newest["verdicts"] == {"accepted": 1, "needs_review": 1, "rejected": 0}
    assert (newest["delivery"], newest["reference"]) == ("delivered", "4bf92f3577b34da6a3ce929d0e0e4736")
    assert page["next_cursor"] is None


def test_the_list_pages_with_a_cursor(api, db_session):
    for n in range(5):
        add_event(db_session, f"evt-{n}", minutes_ago=n)
    client = api.sign_in("u1")

    first = client.get("/events?limit=2").json()
    second = client.get(f"/events?limit=2&cursor={first['next_cursor']}").json()
    third = client.get(f"/events?limit=2&cursor={second['next_cursor']}").json()

    ids = [e["event_id"] for page in (first, second, third) for e in page["events"]]
    assert ids == ["evt-0", "evt-1", "evt-2", "evt-3", "evt-4"] and third["next_cursor"] is None
    assert client.get("/events?cursor=not-a-cursor").status_code == 400


def test_the_list_can_show_only_what_needs_attention(api, db_session):
    add_event(db_session, "fine")
    add_event(db_session, "review", verdicts=("needs_review",))
    add_event(db_session, "undelivered", deliveries=(("retry", 503), ("gave_up", 503)))
    add_event(db_session, "resent-ok", deliveries=(("gave_up", 404), ("delivered", 200)))
    add_event(db_session, "failed", status="failed", verdicts=())
    client = api.sign_in("u1")

    def ids(query):
        return sorted(e["event_id"] for e in client.get(f"/events?{query}").json()["events"])

    assert ids("needs_review=true") == ["review"]
    assert ids("delivery_failed=true") == ["undelivered"]  # a later success counts as delivered
    assert ids("status=failed") == ["failed"]


# One event

def test_an_event_shows_documents_timeline_and_deliveries_not_our_internals(api, db_session):
    live_setup(db_session)
    add_event(db_session, "evt-1", verdicts=("accepted", "needs_review"),
              deliveries=(("gave_up", 404),))

    event = api.sign_in("u1").get("/events/evt-1").json()

    assert [d["verdict"] for d in event["documents"]] == ["accepted", "needs_review"]
    assert event["documents"][1]["reasons"] == ["line_totals_add_up: Line totals don't add up"]
    assert event["documents"][0]["data"] == {"total_net": 100}
    assert [t["stage"] for t in event["timeline"]] == [
        "received", "read", "extracted", "checked", "delivered"]
    assert event["timeline"][2]["outcome"] == "retried"
    assert event["deliveries"][0]["http_status"] == 404
    assert event["reference"] == "4bf92f3577b34da6a3ce929d0e0e4736"
    assert event["can_resend"] is True
    assert "secret-model" not in str(event) and "input_tokens" not in str(event)


def test_an_event_shows_its_latest_run(api, db_session):
    add_event(db_session, "evt-1", run=1, minutes_ago=5)
    add_event(db_session, "evt-1", run=2)

    assert api.sign_in("u1").get("/events/evt-1").json()["run"] == 2


def test_after_90_days_the_data_is_gone_and_it_cant_be_resent(api, db_session):
    live_setup(db_session)
    add_event(db_session, "evt-1", expires_in_days=-1)

    event = api.sign_in("u1").get("/events/evt-1").json()

    assert event["data_deleted"] is True and event["documents"][0]["data"] is None
    assert event["can_resend"] is False and "90 days" in event["resend_unavailable_reason"]


def test_resend_is_unavailable_without_a_live_webhook_for_it(api, db_session):
    live_setup(db_session, destinations=[{**HOOK, "events": ["event.failed"]}])
    add_event(db_session, "evt-1")

    event = api.sign_in("u1").get("/events/evt-1").json()

    assert event["can_resend"] is False
    assert "No webhook in your live setup receives event.completed" in event["resend_unavailable_reason"]


def test_nobody_else_can_see_or_resend_an_event(api, db_session, monkeypatch):
    add_event(db_session, "evt-1")
    calls = []
    monkeypatch.setattr(boardy, "resend_event", lambda *args: calls.append(args))
    other = api.sign_in("u2")

    assert other.get("/events/evt-1").status_code == 404
    assert other.post("/events/evt-1/resend").status_code == 404
    assert not calls


# Resend

def test_resend_goes_to_the_live_setup_and_returns_the_updated_event(api, db_session, monkeypatch):
    live_setup(db_session)
    event = add_event(db_session, "evt-1", deliveries=(("gave_up", 404),))
    calls = []

    def resend_event(user_id, event_id, live_destinations):
        calls.append((user_id, event_id, live_destinations))
        db_session.add(Delivery(event_pk=event.id, destination_id=HOOK["id"], url=HOOK["url"],
                                webhook_id="evt-1-r1", attempt=2, status="delivered",
                                http_status=200, attempted_at=NOW + timedelta(minutes=1),
                                trigger="manual"))
        db_session.commit()
    monkeypatch.setattr(boardy, "resend_event", resend_event)

    result = api.sign_in("u1").post("/events/evt-1/resend").json()

    assert calls == [("u1", "evt-1", [HOOK])]
    assert [(d["trigger"], d["http_status"]) for d in result["deliveries"]] == [
        ("automatic", 404), ("manual", 200)]


def test_a_refused_resend_explains_why(api, db_session, monkeypatch):
    add_event(db_session, "evt-1")

    def refused(*_args):
        raise HTTPException(status_code=409, detail="It was just resent. Wait 10 seconds between resends.")
    monkeypatch.setattr(boardy, "resend_event", refused)

    response = api.sign_in("u1").post("/events/evt-1/resend")

    assert response.status_code == 409 and "Wait 10 seconds" in response.json()["detail"]


@pytest.fixture(autouse=True)
def _no_real_resend(monkeypatch):
    """Tests that don't fake the resend must never reach the agents' real one."""
    def unexpected(*_args):
        raise AssertionError("the real resend was called")
    monkeypatch.setattr(boardy, "resend_event", unexpected)
