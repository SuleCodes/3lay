"""Delivering envelopes: retries, give-ups, signing and the records kept.

A fake server (httpx.MockTransport) answers in code, so nothing touches the
network, and the waits between retries are recorded instead of slept.
"""

import json
import random

import httpx
import pytest

from noti.delivery import deliver
from noti.destinations import (check_destinations, destination_secret, secret_variable)
from noti.signing import generate_secret, verify

ENVELOPE = {"event_id": "evt-1", "run": 1, "event_type": "event.completed", "items": []}
DESTINATION = {"id": "dest_test", "type": "webhook", "url": "https://hooks.example.com/3lay",
               "events": ["event.completed"]}
SECRET = generate_secret()


class FakeServer:
    """Answers each request with the next response; records what it received."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        answer = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(answer, Exception):
            raise answer
        return answer


def run(server, destination=DESTINATION, secret=SECRET):
    waits = []
    client = httpx.Client(transport=httpx.MockTransport(server), follow_redirects=False)
    attempts = deliver(ENVELOPE, destination, secret, client, sleep=waits.append,
                       rng=random.Random(1))
    return attempts, waits


def response(status, **kwargs):
    return httpx.Response(status, **kwargs)


def statuses(attempts):
    return [(a["status"], a["http_status"]) for a in attempts]


# Outcomes

def test_2xx_is_delivered_first_time():
    attempts, waits = run(FakeServer(response(200, text="ok")))

    assert statuses(attempts) == [("delivered", 200)]
    assert waits == []
    assert attempts[0]["url"] == DESTINATION["url"]  # the exact URL it went to


def test_5xx_is_retried_with_exponential_waits_then_delivered():
    server = FakeServer(response(503), response(502), response(204))

    attempts, waits = run(server)

    assert statuses(attempts) == [("retry", 503), ("retry", 502), ("delivered", 204)]
    # 15 then 30 seconds, each within 20% jitter
    assert 12 <= waits[0] <= 18 and 24 <= waits[1] <= 36
    assert [a["retry_in_seconds"] for a in attempts] == [*waits, None]


def test_gives_up_after_the_last_retry():
    attempts, waits = run(FakeServer(response(500)))

    assert len(attempts) == 4  # the first try plus 3 retries
    assert len(waits) == 3
    assert attempts[-1]["status"] == "gave_up"
    assert "gave up after 4 attempts" in attempts[-1]["error"]


def test_429_waits_as_long_as_retry_after_says_capped():
    server = FakeServer(response(429, headers={"Retry-After": "7"}),
                        response(429, headers={"Retry-After": "3600"}),
                        response(200))

    _, waits = run(server)

    assert waits == [7, 60]  # an hour is capped to a minute


def test_timeouts_and_connection_failures_are_retried():
    request = httpx.Request("POST", DESTINATION["url"])
    server = FakeServer(httpx.ReadTimeout("slow", request=request),
                        httpx.ConnectError("refused", request=request),
                        response(200))

    attempts, _ = run(server)

    assert [a["status"] for a in attempts] == ["retry", "retry", "delivered"]
    assert attempts[0]["http_status"] is None
    assert "No response within" in attempts[0]["error"]
    assert "Couldn't connect" in attempts[1]["error"]


@pytest.mark.parametrize("status", [400, 401, 403, 404, 410])
def test_other_4xx_gives_up_without_retrying(status):
    attempts, waits = run(FakeServer(response(status, text="nope")))

    assert statuses(attempts) == [("gave_up", status)]
    assert waits == []
    assert attempts[0]["response_excerpt"] == "nope"


def test_a_redirect_is_not_followed():
    server = FakeServer(response(301, headers={"Location": "https://elsewhere.example.com/"}),
                        response(200))

    attempts, _ = run(server)

    assert statuses(attempts) == [("gave_up", 301)]
    assert len(server.requests) == 1  # never went to the other address
    assert "redirects aren't followed" in attempts[0]["error"]


def test_a_client_can_choose_its_own_retry_waits():
    _, waits = run(FakeServer(response(500)), {**DESTINATION, "retry_delays": [1]})

    assert len(waits) == 1 and 0.8 <= waits[0] <= 1.2


def test_without_a_secret_nothing_is_sent():
    server = FakeServer(response(200))

    attempts, _ = run(server, secret=None)

    assert server.requests == []
    assert attempts[0]["status"] == "gave_up"
    assert "No signing secret" in attempts[0]["error"]


# What's sent

def test_each_attempt_is_signed_and_verifiable_with_the_same_id():
    server = FakeServer(response(503), response(200))

    run(server)

    first, second = server.requests
    for request in (first, second):
        verify(SECRET, dict(request.headers), request.content)
        assert json.loads(request.content) == ENVELOPE
        assert request.headers["content-type"] == "application/json"
    assert first.headers["webhook-id"] == second.headers["webhook-id"] == "evt-1-r1"


def test_records_never_contain_the_secret_or_signature():
    attempts, _ = run(FakeServer(response(200)))

    text = json.dumps(attempts)
    assert SECRET not in text and "v1," not in text


# Destination definitions

def test_valid_destinations_are_accepted():
    destinations = [DESTINATION, {**DESTINATION, "id": "dest_local",
                                  "url": "http://localhost:8080/hook",
                                  "events": ["event.completed", "event.failed"],
                                  "retry_delays": [5, 10]}]
    assert check_destinations(destinations) is destinations


@pytest.mark.parametrize("change, problem", [
    ({"url": "http://hooks.example.com/3lay"}, "url"),        # plain http, not local
    ({"events": ["event.deleted"]}, "events"),
    ({"type": "sms"}, "type"),
    ({"id": "rolepay"}, "id"),
    ({"retry_delays": [100, 100, 100, 100]}, "more than 300 seconds"),
    ({"retry_delays": [1, 1, 1, 1, 1, 1]}, "retry_delays"),   # more than 5 retries
    ({"secret": "whsec_x"}, "secret"),                        # secrets never in the config
])
def test_bad_destinations_are_rejected(change, problem):
    with pytest.raises(ValueError, match="Invalid destinations") as error:
        check_destinations([{**DESTINATION, **change}])
    assert problem in str(error.value)


def test_secrets_are_found_by_destination_id(monkeypatch):
    monkeypatch.setenv("NOTI_SECRET_DEST_ROLEPAY_MAIN", SECRET)

    assert secret_variable("dest_rolepay_main") == "NOTI_SECRET_DEST_ROLEPAY_MAIN"
    assert destination_secret("dest_rolepay_main") == SECRET
    assert destination_secret("dest_unknown") is None
