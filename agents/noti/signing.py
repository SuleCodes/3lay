"""Signs webhook deliveries following Standard Webhooks (symmetric, HMAC-SHA256).

Spec: https://github.com/standard-webhooks/standard-webhooks/blob/main/spec/standard-webhooks.md

Every delivery carries three headers:

    webhook-id:        <event_id>-r<run>   the same on every retry of this delivery
    webhook-timestamp: <unix seconds>      when it was sent
    webhook-signature: v1,<base64 HMAC>    one per secret, space-separated

The signature is HMAC-SHA256, keyed with the destination's secret, over
"<webhook-id>.<webhook-timestamp>.<body>". The client recomputes it with its
copy of the secret (the official libraries do this, plus the timestamp and
duplicate checks), which proves the delivery came from 3lay and wasn't changed.

The body is signed as exact bytes: serialise the envelope once (body_bytes),
sign those bytes, and send those same bytes. Re-serialising after signing, here
or in the client's framework, would break verification.
"""

import base64
import hashlib
import hmac
import json
import secrets
import time

SECRET_PREFIX = "whsec_"
SECRET_BYTES = 32  # the spec allows 24 to 64
TIMESTAMP_TOLERANCE_SECONDS = 5 * 60  # what receivers allow either way, for clock differences


class SignatureError(Exception):
    """A delivery that doesn't verify: missing headers, too old or new, or no matching signature."""


def generate_secret():
    """A new destination secret, e.g. "whsec_MfKQ9r8GKYqrTwjUPD8ILPZIo2LaLaSw".

    Shown to the client once when the destination is set up, then stored as a
    secret (Key Vault in production), never in the configuration or logs. The
    whsec_ prefix makes an accidental leak easy to spot.
    """
    return SECRET_PREFIX + base64.b64encode(secrets.token_bytes(SECRET_BYTES)).decode("ascii")


def secret_key(secret):
    """The raw key bytes from a "whsec_..." secret."""
    if not secret.startswith(SECRET_PREFIX):
        raise ValueError("A webhook secret starts with whsec_")
    key = base64.b64decode(secret[len(SECRET_PREFIX):] + "==")  # tolerate missing padding
    if not key:
        raise ValueError("The webhook secret is empty")
    return key


def webhook_id(event_id, run):
    """The delivery's ID: the same on every retry, so the client can spot duplicates.

    A reprocessed event (run 2, 3, ...) gets a new ID: it's a new result the
    client should process.
    """
    return f"{event_id}-r{run}"


def body_bytes(envelope):
    """The envelope as the exact bytes to sign and send (compact UTF-8 JSON)."""
    return json.dumps(envelope, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def signature(secret, message_id, timestamp, body):
    """"v1,<base64 HMAC-SHA256 of message_id.timestamp.body>"."""
    signed_content = f"{message_id}.{timestamp}.".encode("utf-8") + body
    digest = hmac.new(secret_key(secret), signed_content, hashlib.sha256).digest()
    return "v1," + base64.b64encode(digest).decode("ascii")


def signed_headers(secrets_, message_id, body, timestamp=None):
    """The three Standard Webhooks headers for a delivery.

    secrets_ is one secret, or a list during a rotation (new and old): the
    delivery is signed with each, and the client accepts any that matches.
    """
    if isinstance(secrets_, str):
        secrets_ = [secrets_]
    timestamp = int(time.time()) if timestamp is None else int(timestamp)
    return {
        "webhook-id": message_id,
        "webhook-timestamp": str(timestamp),
        "webhook-signature": " ".join(
            signature(secret, message_id, timestamp, body) for secret in secrets_
        ),
    }


def verify(secret, headers, body, now=None, tolerance=TIMESTAMP_TOLERANCE_SECONDS):
    """What a receiver does: raises SignatureError unless the delivery is genuine and recent.

    For 3lay's own test receiver and tests; clients use the official libraries.
    Signatures are compared in constant time, so timing can't reveal how close
    a forged signature was.
    """
    headers = {name.lower(): value for name, value in headers.items()}
    message_id = headers.get("webhook-id")
    timestamp = headers.get("webhook-timestamp")
    signatures = headers.get("webhook-signature")
    if not (message_id and timestamp and signatures):
        raise SignatureError("Missing webhook-id, webhook-timestamp or webhook-signature")

    try:
        sent_at = int(timestamp)
    except ValueError as exc:
        raise SignatureError("webhook-timestamp isn't a number") from exc
    now = time.time() if now is None else now
    if sent_at < now - tolerance:
        raise SignatureError("Too old: possibly a replayed delivery")
    if sent_at > now + tolerance:
        raise SignatureError("Timestamp is in the future")

    expected = signature(secret, message_id, sent_at, body).split(",", 1)[1]
    for candidate in signatures.split(" "):
        version, _, value = candidate.partition(",")
        if version == "v1" and hmac.compare_digest(value, expected):
            return
    raise SignatureError("No matching signature")
