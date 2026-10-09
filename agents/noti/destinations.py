"""A client's destinations: where each event's envelope is delivered.

Destinations are part of the client's versioned configuration (for now, a file
in agents/tmp/), each with a stable ID that stays the same across versions.
The signing secret is never in the configuration: it's looked up by the
destination's ID (for now from agents/.env; later the encrypted
destination_secrets table, with the key in Key Vault).

    {
      "id": "dest_rolepay_main",
      "type": "webhook",
      "url": "https://webhook.site/...",
      "events": ["event.completed", "event.failed"],
      "retry_delays": [15, 30, 60]          <- optional; 3lay's default otherwise
    }
"""

import os
import re

import jsonschema

EVENT_TYPES = ["event.completed", "event.failed"]

# 3lay's limits on what a client can choose for retries. Retries happen inside
# the run, so the total wait is kept short.
DEFAULT_RETRY_DELAYS = [15, 30, 60]  # seconds before each retry: exponential, doubling
MAX_RETRIES = 5
MAX_RETRY_DELAY = 120
MAX_TOTAL_RETRY_WAIT = 300

DESTINATION_SCHEMA = {
    "type": "object",
    "required": ["id", "type", "url", "events"],
    "additionalProperties": False,
    "properties": {
        "id": {"type": "string", "pattern": "^dest_[a-z0-9_]+$"},
        "type": {"const": "webhook"},
        # https only: deliveries carry personal data. http is allowed for a
        # receiver on this machine while developing.
        "url": {"type": "string",
                "pattern": r"^(https://[^\s]+|http://(localhost|127\.0\.0\.1)(:\d+)?(/[^\s]*)?)$"},
        "events": {"type": "array", "items": {"enum": EVENT_TYPES},
                   "minItems": 1, "uniqueItems": True},
        "retry_delays": {"type": "array", "maxItems": MAX_RETRIES,
                         "items": {"type": "number", "minimum": 0,
                                   "maximum": MAX_RETRY_DELAY}},
        "description": {"type": "string"},
    },
}


def check_destinations(destinations):
    """Raises ValueError listing every problem; returns the destinations if fine."""
    if not isinstance(destinations, list):
        raise ValueError("Destinations must be a list.")
    problems, seen = [], set()
    validator = jsonschema.Draft202012Validator(DESTINATION_SCHEMA)
    for i, destination in enumerate(destinations):
        name = destination.get("id", "no id") if isinstance(destination, dict) else "?"
        label = f"destination {i} ({name})"
        for error in validator.iter_errors(destination):
            where = ".".join(str(p) for p in error.absolute_path) or "destination"
            problems.append(f"{label}: {where}: {error.message}")
        if isinstance(destination, dict):
            if sum(destination.get("retry_delays", [])) > MAX_TOTAL_RETRY_WAIT:
                problems.append(f"{label}: retry_delays add up to more than "
                                f"{MAX_TOTAL_RETRY_WAIT} seconds")
            if destination.get("id") in seen:
                problems.append(f"{label}: duplicate id")
            seen.add(destination.get("id"))
    if problems:
        raise ValueError("Invalid destinations:\n  " + "\n  ".join(problems))
    return destinations


def secret_variable(destination_id):
    """The environment variable holding a destination's secret.

    e.g. dest_rolepay_main -> NOTI_SECRET_DEST_ROLEPAY_MAIN
    """
    return "NOTI_SECRET_" + re.sub(r"[^A-Z0-9]", "_", destination_id.upper())


def destination_secret(destination_id):
    """The destination's signing secret, or None if it hasn't been set.

    For now from the environment (agents/.env); later decrypted from the
    destination_secrets table. Never logged or stored with a delivery.
    """
    return os.environ.get(secret_variable(destination_id)) or None
