"""Delivers a signed envelope to one webhook, retrying when it's worth it.

Outcome of each attempt:

    2xx                                   delivered
    5xx, 429, timeout, connection failure retried, after an exponential wait with
                                          jitter (or a 429's Retry-After, capped)
    3xx                                   given up: redirects aren't followed, so a
                                          redirect can't send the data elsewhere
    other 4xx (400, 401, 403, 404, 410)   given up: the endpoint is refusing it,
                                          retrying won't help

Retries happen within the run (the waits add up to a couple of minutes); after
the last one the delivery is "gave_up" and can be resent later. Every attempt
is recorded, with the exact URL it went to. The secret and signature never are.
"""

import random
import time
from datetime import datetime, timezone

import httpx

from noti.destinations import DEFAULT_RETRY_DELAYS
from noti.signing import body_bytes, signed_headers, webhook_id

REQUEST_TIMEOUT_SECONDS = 10
MAX_RETRY_AFTER_SECONDS = 60
JITTER = 0.2  # each wait is randomly 20% shorter or longer, so retries don't arrive together
RESPONSE_EXCERPT_CHARS = 200
USER_AGENT = "3lay-webhooks/1"


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def with_jitter(delay, rng):
    return round(delay * rng.uniform(1 - JITTER, 1 + JITTER), 1)


def retry_after(response):
    """The wait a 429 asks for, in seconds, if it says (capped). Only the seconds form."""
    value = response.headers.get("retry-after", "").strip()
    if value.isdigit():
        return min(int(value), MAX_RETRY_AFTER_SECONDS)
    return None


def classify(response):
    """("delivered" | "retry" | "gave_up", reason) for an HTTP response."""
    status = response.status_code
    if 200 <= status < 300:
        return "delivered", None
    if status == 429 or status >= 500:
        return "retry", f"HTTP {status}"
    if 300 <= status < 400:
        return "gave_up", f"HTTP {status}: redirects aren't followed; check the destination URL"
    return "gave_up", f"HTTP {status}: the endpoint refused the delivery"


def post(client, url, message_id, body, secret):
    """One signed POST. Returns (outcome, reason, response or None)."""
    headers = {
        "content-type": "application/json",
        "user-agent": USER_AGENT,
        **signed_headers(secret, message_id, body),  # fresh timestamp on each attempt
    }
    try:
        response = client.post(url, content=body, headers=headers,
                               timeout=REQUEST_TIMEOUT_SECONDS)
    except httpx.TimeoutException:
        return "retry", f"No response within {REQUEST_TIMEOUT_SECONDS} seconds", None
    except httpx.TransportError as exc:  # refused, DNS, TLS, reset...
        return "retry", f"Couldn't connect: {type(exc).__name__}: {exc}", None
    outcome, reason = classify(response)
    return outcome, reason, response


def response_fields(response):
    """What's kept about a response: its status and the start of its body."""
    if response is None:
        return {"http_status": None, "response_excerpt": None}
    return {"http_status": response.status_code,
            "response_excerpt": response.text[:RESPONSE_EXCERPT_CHARS]}


def wait_before_retry(response, attempt, delays, rng):
    """Seconds to wait before the next attempt: a 429's Retry-After if it gave one
    (capped), otherwise the destination's delay for this retry, with jitter."""
    if response is not None and response.status_code == 429:
        asked = retry_after(response)
        if asked is not None:
            return asked
    return with_jitter(delays[attempt - 1], rng)


# sleep and rng are keyword-only test hooks, and the loop keeps a few pieces of
# state per attempt: splitting it further would make the retry logic harder to follow.
def deliver(envelope, destination, secret, client, *, sleep=time.sleep, rng=None):  # pylint: disable=too-many-arguments,too-many-locals
    """Delivers the envelope to one destination. Returns the list of attempt records.

    The last record's status is the delivery's outcome: "delivered" or
    "gave_up". Without a secret nothing is sent: 3lay never sends unsigned data.
    sleep and rng are keyword-only, so tests can skip the waits and fix the jitter.
    """
    message_id = webhook_id(envelope["event_id"], envelope["run"])
    base = {"destination_id": destination["id"], "url": destination["url"],
            "webhook_id": message_id}
    if not secret:
        return [{**base, "attempt": 0, "status": "gave_up", **response_fields(None),
                 "attempted_at": now_iso(), "duration_ms": 0, "retry_in_seconds": None,
                 "error": "No signing secret for this destination: nothing was sent."}]

    body = body_bytes(envelope)  # serialised once: these exact bytes are signed and sent
    delays = list(destination.get("retry_delays", DEFAULT_RETRY_DELAYS))
    rng = rng or random.Random()
    attempts = []
    for attempt in range(1, len(delays) + 2):
        record = {**base, "attempt": attempt, "attempted_at": now_iso()}
        started = time.perf_counter()
        outcome, reason, response = post(client, destination["url"], message_id, body, secret)
        record["duration_ms"] = round((time.perf_counter() - started) * 1000)

        wait = None
        if outcome == "retry" and attempt <= len(delays):
            wait = wait_before_retry(response, attempt, delays, rng)
        elif outcome == "retry":
            outcome, reason = "gave_up", f"{reason}; gave up after {attempt} attempts"

        attempts.append({**record, "status": outcome, **response_fields(response),
                         "retry_in_seconds": wait, "error": reason})
        if wait is None:
            break
        sleep(wait)
    return attempts
