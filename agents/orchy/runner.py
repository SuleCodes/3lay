"""Runs one event end to end: configuration, duplicate check, graph, record.

Used by scripts/run_orchy.py now, and by the queue job in E6, so a local run
and a deployed run go through exactly the same steps:

    1. the client's active configuration, from the database
    2. start_event: record the run as "processing", or skip it if this
       event_id + run was already recorded (a queue message delivered twice)
    3. the graph: load_inputs ... deliver
    4. finish_event: record the outcome, sources, steps and deliveries

If the graph raises, the record stays "processing" for the watchdog to find.

Without a database (working offline), pass `settings` from local files: the
graph runs the same way, and nothing is recorded.
"""

from dataclasses import dataclass
from datetime import datetime, timezone

from orchy.emails import read_recipient
from orchy.graph import build_graph
from store.configurations import load_active_configuration, run_settings
from store.db import engine
from store.events import finish_event, start_event


@dataclass
class EventRun:
    """What happened: the final state (None if skipped), and whether it was recorded."""

    final_state: dict | None
    recorded: bool
    skipped_reason: str | None = None


def client_from_email(email_path):
    """The client's username from the To address: rolepay-agent@in.3lay.live -> rolepay-agent."""
    recipient = read_recipient(email_path)
    if not recipient:
        raise ValueError("The email has no To address, so there's no client to run it for.")
    return recipient.split("@", 1)[0]


def initial_state(email_path, event_id, run, received_at, settings):
    return {
        "event_id": event_id,
        "run": run,
        "case_ref": event_id,  # each event is its own case until case matching exists
        "received_at": received_at,
        "email_path": str(email_path),
        **settings,
        "steps": [],
    }


# Every argument after email_path is keyword-only, so callers have to name each one.
def process_event(email_path, *, event_id, run=1,  # pylint: disable=too-many-arguments
                  received_at=None, client=None, settings=None):
    """Runs the event. With settings given, runs offline without recording."""
    received_at = received_at or datetime.now(timezone.utc).isoformat(timespec="milliseconds")

    if settings is not None:
        state = initial_state(email_path, event_id, run, received_at, settings)
        return EventRun(build_graph().invoke(state), recorded=False)

    db = engine()
    client = client or client_from_email(email_path)
    config, version, user_id = load_active_configuration(db, client)
    event_pk = start_event(db, event_id=event_id, run=run, user_id=user_id, case_ref=event_id,
                           received_at=received_at, config_version=version)
    if event_pk is None:
        return EventRun(None, recorded=False,
                        skipped_reason=f"{event_id} run {run} has already been recorded")

    state = initial_state(email_path, event_id, run, received_at, run_settings(config, version))
    final_state = build_graph().invoke(state)
    finish_event(db, event_pk, final_state)
    return EventRun(final_state, recorded=True)
