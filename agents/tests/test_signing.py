"""Webhook signing: our implementation, checked against the official Standard Webhooks library.

If the library verifies what we sign, clients can verify 3lay's deliveries with
off-the-shelf code in their own language.
"""

import json
import time

import pytest
from standardwebhooks.webhooks import Webhook, WebhookVerificationError

from noti.signing import (SignatureError, body_bytes, generate_secret, secret_key,
                          signed_headers, verify, webhook_id)

ENVELOPE = {"event_id": "3f6c1a2e", "run": 1, "items": [{"data": {"recipient": "Zoë Smith"}}]}


def delivery(secret_or_secrets, envelope=ENVELOPE, timestamp=None):
    """A signed delivery: (body, headers). Pass a list of secrets to sign with each,
    as during a rotation; signed_headers accepts one secret or a list."""
    body = body_bytes(envelope)
    return body, signed_headers(secret_or_secrets, webhook_id("3f6c1a2e", 1), body, timestamp)


# The official library accepts what we sign

def test_the_official_library_verifies_our_signature():
    secret = generate_secret()
    body, headers = delivery(secret)

    assert Webhook(secret).verify(body, headers) == ENVELOPE


def test_the_official_library_rejects_a_changed_body():
    secret = generate_secret()
    body, headers = delivery(secret)

    with pytest.raises(WebhookVerificationError):
        Webhook(secret).verify(body.replace(b"Smith", b"Smyth"), headers)


def test_the_official_library_accepts_either_secret_during_a_rotation():
    old, new = generate_secret(), generate_secret()
    body, headers = delivery([new, old])

    assert len(headers["webhook-signature"].split(" ")) == 2
    Webhook(old).verify(body, headers)
    Webhook(new).verify(body, headers)


# Our own verify (for the test receiver) agrees

def test_verify_accepts_a_genuine_delivery():
    secret = generate_secret()
    body, headers = delivery(secret)

    verify(secret, headers, body)


@pytest.mark.parametrize("tamper", [
    lambda body, headers: (body + b" ", headers),                                  # body changed
    lambda body, headers: (body, {**headers, "webhook-id": "someone-else-r1"}),    # id changed
    lambda body, headers: (body, {**headers, "webhook-signature": "v1,AAAA"}),     # forged
    lambda body, headers: (body, {k: v for k, v in headers.items()
                                  if k != "webhook-signature"}),                   # missing
])
def test_verify_rejects_tampering(tamper):
    secret = generate_secret()
    body, headers = tamper(*delivery(secret))

    with pytest.raises(SignatureError):
        verify(secret, headers, body)


def test_verify_rejects_the_wrong_secret():
    body, headers = delivery(generate_secret())

    with pytest.raises(SignatureError, match="No matching signature"):
        verify(generate_secret(), headers, body)


def test_verify_rejects_a_replay_after_the_tolerance():
    secret = generate_secret()
    body, headers = delivery(secret, timestamp=time.time() - 600)  # sent 10 minutes ago

    with pytest.raises(SignatureError, match="Too old"):
        verify(secret, headers, body)
    with pytest.raises(WebhookVerificationError, match="too old"):
        Webhook(secret).verify(body, headers)


def test_verify_allows_small_clock_differences():
    secret = generate_secret()
    body, headers = delivery(secret, timestamp=time.time() + 60)  # our clock a minute ahead

    verify(secret, headers, body)


# The pieces

def test_webhook_id_is_the_same_for_every_retry_and_new_for_a_new_run():
    assert webhook_id("3f6c1a2e", 1) == "3f6c1a2e-r1"
    assert webhook_id("3f6c1a2e", 1) == webhook_id("3f6c1a2e", 1)
    assert webhook_id("3f6c1a2e", 2) != webhook_id("3f6c1a2e", 1)


def test_generated_secrets_are_prefixed_random_and_32_bytes():
    first, second = generate_secret(), generate_secret()

    assert first.startswith("whsec_") and first != second
    assert len(secret_key(first)) == 32


def test_a_secret_without_the_prefix_is_refused():
    with pytest.raises(ValueError, match="whsec_"):
        secret_key("MfKQ9r8GKYqrTwjUPD8ILPZIo2LaLaSw")


def test_the_body_is_compact_utf8_json_that_round_trips():
    body = body_bytes(ENVELOPE)

    assert b"\n" not in body and b": " not in body
    assert "Zoë".encode("utf-8") in body  # not escaped as ë
    assert json.loads(body) == ENVELOPE
