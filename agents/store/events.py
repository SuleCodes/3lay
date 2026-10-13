"""Event records: what happened to every run, for the dashboard, billing and the watchdog.

A run is recorded in two writes (agreed 2026-10-12):

1. start_event, before the graph runs: an events row with status
   "processing". If the job crashes, the row stays "processing", so the
   watchdog can find it and the client still hears about the event. If a row
   for the same event_id + run already exists (a queue message delivered
   twice), the run is skipped: no second webhook, no second bill.
2. finish_event, after the graph: one transaction writing the outcome, the
   envelope, and every source, step and delivery attempt.

Personal data (envelope, sender, sources' extractions and checks) is kept for
PERSONAL_DATA_RETENTION, then deleted by a later job; statuses, counts,
timings, tokens and billable are kept.
"""

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from orchy.envelope import verdict
from store.db import deliveries, event_sources, event_steps, events

PERSONAL_DATA_RETENTION = timedelta(days=90)

# Billing policy: completed events and failures caused by the sender are
# billed; failures caused by 3lay aren't.
NOT_BILLABLE_ERRORS = {"processing_error", "processing_timeout"}

# Keys of a step record that have their own columns; everything else goes in details.
STEP_COLUMNS = {"node", "started_at", "finished_at", "outcome", "error", "document"}


def as_datetime(value):
    """Datetimes from the state's ISO strings (and leave datetimes as they are)."""
    if value is None or isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value)


def is_billable(status, error_code):
    if status == "completed":
        return True
    return error_code not in NOT_BILLABLE_ERRORS


# Every argument after engine is keyword-only, so callers have to name each one.
def start_event(engine, *, event_id, run, user_id, case_ref,  # pylint: disable=too-many-arguments
                received_at, config_version, now=None):
    """Records the run as "processing". Returns the row's id, or None if this
    event_id + run has been recorded already (then the run must be skipped)."""
    now = now or datetime.now(timezone.utc)
    received_at = as_datetime(received_at)
    row_id = str(uuid.uuid4())
    try:
        with engine.begin() as connection:
            existing = connection.execute(
                select(events.c.id).where(events.c.event_id == event_id,
                                          events.c.run == run)).first()
            if existing is not None:
                return None
            connection.execute(events.insert().values(
                id=row_id, event_id=event_id, run=run, user_id=user_id, case_ref=case_ref,
                received_at=received_at, started_at=now, status="processing",
                config_version=config_version,
                personal_data_expires_at=received_at + PERSONAL_DATA_RETENTION,
                created_at=now))
    except IntegrityError:
        # Two copies of the same message started at the same moment: the
        # unique event_id + run index let only one through.
        return None
    return row_id


def model_used(steps):
    return next((s["model"] for s in steps if s.get("model")), None)


def token_totals(steps):
    """Tokens added up over every model call, retries included: they all cost money."""
    totals = {"input_tokens": 0, "output_tokens": 0}
    for step in steps:
        for key in totals:
            totals[key] += (step.get("usage") or {}).get(key, 0)
    return totals


def source_row(event_pk, document):
    return {
        "id": str(uuid.uuid4()), "event_pk": event_pk, "position": document["index"],
        "name": document["name"], "content_type": document["content_type"],
        "location": document.get("location"), "forwarded_from": document.get("forwarded_from"),
        "status": document["status"], "attempts": document.get("attempts", 0),
        "verdict": verdict(document) if document["status"] in ("done", "failed") else None,
        "error_code": document.get("error_code"), "error_message": document.get("error_message"),
        "checks": document.get("checks") or [], "extraction": document.get("extraction"),
    }


def step_row(event_pk, sequence, step):
    return {
        "id": str(uuid.uuid4()), "event_pk": event_pk, "sequence": sequence,
        "node": step["node"], "source_position": step.get("document"),
        "outcome": step["outcome"], "started_at": as_datetime(step["started_at"]),
        "finished_at": as_datetime(step["finished_at"]), "error": step.get("error"),
        "details": {k: v for k, v in step.items() if k not in STEP_COLUMNS} or None,
    }


def delivery_row(event_pk, attempt):
    return {
        "id": str(uuid.uuid4()), "event_pk": event_pk,
        **{key: attempt.get(key) for key in (
            "destination_id", "url", "webhook_id", "attempt", "status", "http_status",
            "response_excerpt", "error", "duration_ms", "retry_in_seconds")},
        "attempted_at": as_datetime(attempt["attempted_at"]),
    }


def finish_event(engine, event_pk, final_state, now=None):
    """Writes the run's outcome and everything it produced, in one transaction."""
    now = now or datetime.now(timezone.utc)
    steps = final_state.get("steps", [])
    status = final_state.get("status")
    with engine.begin() as connection:
        connection.execute(events.update().where(events.c.id == event_pk).values(
            status=status, finished_at=now, sender=final_state.get("sender"),
            error_code=final_state.get("error_code"),
            error_message=final_state.get("error_message"),
            model=model_used(steps), **token_totals(steps),
            billable=is_billable(status, final_state.get("error_code")),
            envelope=final_state.get("envelope")))
        sources = [source_row(event_pk, d) for d in final_state.get("documents", [])]
        if sources:
            connection.execute(event_sources.insert(), sources)
        if steps:
            connection.execute(event_steps.insert(),
                               [step_row(event_pk, i, s) for i, s in enumerate(steps)])
        attempts = final_state.get("deliveries", [])
        if attempts:
            connection.execute(deliveries.insert(), [delivery_row(event_pk, a) for a in attempts])
