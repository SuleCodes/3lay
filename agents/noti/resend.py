"""Resending an event's webhook from the dashboard ("Resend").

A resend delivers the event's stored envelope again, as the same message:
the same webhook-id, so a client that already processed it ignores the
duplicate (Standard Webhooks). It goes to the webhooks in the client's **live**
setup that want this kind of event, so a URL fixed since (with Boardy) is used.

One attempt per webhook, answered straight away: no retries, because the
client is waiting for the button. Each attempt is recorded in deliveries like
any other, marked trigger "manual", numbered after the event's earlier
attempts. Refused (ResendRefused, with a reason for the client) when:
- the event isn't the client's, or has no envelope (it never finished);
- its personal data has expired (after 90 days the envelope is due for deletion);
- it was resent less than MIN_SECONDS_BETWEEN seconds ago;
- no webhook in the live setup wants this kind of event.
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy import desc, func, select

from noti.delivery import deliver
from store.db import deliveries, events
from store.events import delivery_row
from store.secrets import signing_secrets

MIN_SECONDS_BETWEEN = 10


class ResendRefused(Exception):
    """The event can't be resent; the message says why, in words for the client."""


class ResendNotFound(ResendRefused):
    """No such event for this client."""


def _aware(moment):
    return moment if moment is None or moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def latest_run(connection, user_id, event_id):
    """The event's most recent run, if it's this client's."""
    return connection.execute(
        select(events).where(events.c.user_id == user_id, events.c.event_id == event_id)
        .order_by(desc(events.c.run)).limit(1)).first()


def resend(engine, user_id, event_id, live_destinations, client, now=None):
    """Delivers the event once more to each live webhook that wants it. Returns the attempts.

    live_destinations: the "destinations" of the client's active configuration.
    client: an httpx.Client that doesn't follow redirects (as for deliveries).
    """
    now = now or datetime.now(timezone.utc)
    with engine.connect() as connection:
        event = latest_run(connection, user_id, event_id)
        if event is None:
            raise ResendNotFound("Event not found.")
        if not event.envelope:
            raise ResendRefused("This event hasn't finished processing, so there's nothing to send yet.")
        if _aware(event.personal_data_expires_at) <= now:
            raise ResendRefused("This event is older than 90 days: its data has been deleted, "
                                "so it can't be resent.")
        last_manual = _aware(connection.execute(
            select(func.max(deliveries.c.attempted_at))
            .where(deliveries.c.event_pk == event.id, deliveries.c.trigger == "manual")).scalar())
        if last_manual and now - last_manual < timedelta(seconds=MIN_SECONDS_BETWEEN):
            raise ResendRefused(f"It was just resent. Wait {MIN_SECONDS_BETWEEN} seconds "
                                "between resends.")
        previous = dict(connection.execute(
            select(deliveries.c.destination_id, func.max(deliveries.c.attempt))
            .where(deliveries.c.event_pk == event.id)
            .group_by(deliveries.c.destination_id)).all())

    event_type = event.envelope["event_type"]
    wanted = [d for d in live_destinations
              if d.get("type") == "webhook" and event_type in d.get("events", [])]
    if not wanted:
        raise ResendRefused(f"No webhook in your live setup receives {event_type} events. "
                            "Add or change one with Boardy, then resend.")

    attempts = []
    for destination in wanted:
        secrets_ = signing_secrets(engine, destination["id"]) or None
        # One attempt: no retry delays. Same envelope, so the same webhook-id.
        [attempt] = deliver(event.envelope, {**destination, "retry_delays": []}, secrets_, client)
        attempts.append({**attempt, "trigger": "manual",
                         "attempt": previous.get(destination["id"], 0) + 1})
    with engine.begin() as connection:
        connection.execute(deliveries.insert(), [delivery_row(event.id, a) for a in attempts])
    return attempts
