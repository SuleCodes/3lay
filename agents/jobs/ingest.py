"""The ingest job: takes emails off the ingest queue and runs Orchy on each.

    python -m jobs.ingest

Runs as a Container Apps job (caj-3lay-orchy) that the queue wakes up: each
execution processes the waiting messages, up to MAX_MESSAGES_PER_EXECUTION,
then exits. Each message, written by the ingest function, points at a raw
email in Blob Storage:

    {"ingest_id": "...", "client": "rolepay-agent@in.3lay.live", "origin": "...",
     "container": "raw-ingest", "blob_name": "...", "received_at": "..."}

For each message: download the email, run process_event (the same function
local runs use), and delete the message only once the run is recorded.

- If it crashes, the message isn't deleted: it reappears after
  VISIBILITY_TIMEOUT and is tried again. A message tried MAX_ATTEMPTS times is
  moved to the poison queue (<queue>-poison) instead of looping forever.
- A message for a client with no active configuration (e.g. still
  onboarding) goes straight to the poison queue, where it's kept and visible.
- A message whose event was already recorded (delivered twice) is skipped by
  process_event and deleted.
- A message whose email is gone (the client deleted their account, which
  deletes their stored emails) is skipped and deleted.

Settings (App Configuration AGENTS:*, see settings.py): INGEST_STORAGE_ACCOUNT_NAME,
INGEST_QUEUE_NAME (default ingest-jobs), RAW_CONTAINER_NAME (default
raw-ingest). Storage is reached with the job's managed identity
(DefaultAzureCredential; AZURE_CLIENT_ID names the user-assigned identity).
"""

import json
import logging
import os
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from azure.core.exceptions import ResourceNotFoundError

from orchy.runner import process_event
from settings import load_settings
from store.configurations import ConfigurationNotFound

MAX_MESSAGES_PER_EXECUTION = 10
MAX_ATTEMPTS = 5
# How long a message stays hidden while it's being processed. Longer than any
# run takes (extraction retries plus delivery waits), so another execution
# never picks up a message that's still being worked on.
VISIBILITY_TIMEOUT = 30 * 60

log = logging.getLogger("jobs.ingest")


@dataclass
class Clients:
    """The Azure clients the job works with."""

    queue: Any            # the ingest queue
    poison_queue: Any     # where messages that can't be processed are kept
    blobs: Any            # the raw emails


class PoisonMessage(Exception):
    """A message that can't be processed and shouldn't be retried."""


def username_of(client_address):
    """rolepay-agent@in.3lay.live -> rolepay-agent"""
    return client_address.split("@", 1)[0].lower()


def parse(message, raw_container):
    """The job from a queue message's text. Raises PoisonMessage if it's unusable."""
    try:
        job = json.loads(message.content)
        required = {key: job[key] for key in ("ingest_id", "client", "blob_name", "received_at")}
    except (ValueError, KeyError, TypeError) as exc:
        raise PoisonMessage(f"Not a valid ingest message: {exc}") from exc
    # Only read from the container the ingest function writes to.
    if job.get("container", raw_container) != raw_container:
        raise PoisonMessage(f"Unexpected container {job.get('container')!r}")
    return required


def poison(poison_queue, message, reason):
    """Keeps the message, with the reason, in the poison queue for investigation."""
    poison_queue.send_message(json.dumps({
        "reason": reason,
        "failed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "attempts": message.dequeue_count,
        "message": message.content,
    }))


def handle(message, clients, raw_container, *, run=process_event):
    """Processes one message. Returns what happened: "done", "skipped", "poisoned" or "retry".

    run is the event runner; tests pass a fake.
    """
    if message.dequeue_count > MAX_ATTEMPTS:
        poison(clients.poison_queue, message, f"Failed {MAX_ATTEMPTS} times")
        clients.queue.delete_message(message)
        return "poisoned"

    try:
        job = parse(message, raw_container)
        with tempfile.TemporaryDirectory() as folder:
            email_path = Path(folder) / "email.eml"
            email_path.write_bytes(
                clients.blobs.get_blob_client(raw_container, job["blob_name"])
                .download_blob().readall())
            result = run(email_path, event_id=job["ingest_id"],
                         client=username_of(job["client"]), received_at=job["received_at"])
    except ResourceNotFoundError:
        # The email was deleted with its client's account: nothing to process.
        log.info("Message %s skipped: its email no longer exists", message.id)
        clients.queue.delete_message(message)
        return "skipped"
    except (PoisonMessage, ConfigurationNotFound) as exc:
        log.warning("Message %s moved to the poison queue: %s", message.id, exc)
        poison(clients.poison_queue, message, str(exc))
        clients.queue.delete_message(message)
        return "poisoned"
    except Exception:  # pylint: disable=broad-exception-caught
        # Left on the queue: it reappears after VISIBILITY_TIMEOUT and is retried.
        log.exception("Message %s failed (attempt %s); it will be retried",
                      message.id, message.dequeue_count)
        return "retry"

    clients.queue.delete_message(message)
    if result.final_state is None:
        log.info("Event %s skipped: %s", job["ingest_id"], result.skipped_reason)
        return "skipped"
    log.info("Event %s for %s: %s", job["ingest_id"], username_of(job["client"]),
             result.final_state.get("status"))
    return "done"


def connect():
    """The queue, poison queue and blob clients, using the job's managed identity."""
    # Imported here so the tests (which pass fakes) don't need Azure credentials.
    # pylint: disable=import-outside-toplevel
    from azure.identity import DefaultAzureCredential
    from azure.storage.blob import BlobServiceClient
    from azure.storage.queue import (QueueClient, TextBase64DecodePolicy,
                                     TextBase64EncodePolicy)

    account = os.environ["INGEST_STORAGE_ACCOUNT_NAME"]
    queue_name = os.environ.get("INGEST_QUEUE_NAME", "ingest-jobs")
    credential = DefaultAzureCredential()
    queue_url = f"https://{account}.queue.core.windows.net"
    # The ingest function base64-encodes its messages; the poison queue does the same.
    queue = QueueClient(queue_url, queue_name, credential=credential,
                        message_decode_policy=TextBase64DecodePolicy())
    poison_queue = QueueClient(queue_url, f"{queue_name}-poison", credential=credential,
                               message_encode_policy=TextBase64EncodePolicy())
    blobs = BlobServiceClient(f"https://{account}.blob.core.windows.net", credential=credential)
    return Clients(queue, poison_queue, blobs)


def ensure_queue(queue_client):
    try:
        queue_client.create_queue()
    except Exception:  # pylint: disable=broad-exception-caught
        pass  # it already exists


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    load_settings()
    clients = connect()
    ensure_queue(clients.poison_queue)
    raw_container = os.environ.get("RAW_CONTAINER_NAME", "raw-ingest")

    outcomes = []
    for message in clients.queue.receive_messages(messages_per_page=1,
                                                  visibility_timeout=VISIBILITY_TIMEOUT,
                                                  max_messages=MAX_MESSAGES_PER_EXECUTION):
        outcomes.append(handle(message, clients, raw_container))
    log.info("Processed %d message(s): %s", len(outcomes),
             {o: outcomes.count(o) for o in sorted(set(outcomes))})
    # A non-zero exit marks the execution as failed in the portal when anything needs a retry.
    return 1 if "retry" in outcomes else 0


if __name__ == "__main__":
    sys.exit(main())
