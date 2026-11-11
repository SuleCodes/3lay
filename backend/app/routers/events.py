"""The client's events (processed emails) for the dashboard, and Resend.

Read from the records the pipeline writes (events, event_sources,
event_steps, deliveries). The customer is always the session's user: another
client's event is a 404. What's shown is what a client needs to follow and
debug their integration: documents, verdicts, extracted data, a simple
timeline and every delivery attempt; not our internals (models, tokens).

GET /events/{event_id} is the page behind every webhook's dashboard_url
(app.3lay.live/events/{event_id}); it shows the event's latest run.
"""

import base64
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.orm import Session, aliased

from app.database import get_db
from app.dependencies import get_current_user
from app.models import ClientConfiguration, Delivery, Event, EventSource, EventStep, User
from app.observability import tag
from app.schemas import (
    DeliveryAttemptOut,
    EventDetailOut,
    EventDocumentOut,
    EventPageOut,
    EventSummaryOut,
    TimelineStepOut,
)
from app.services import boardy

router = APIRouter(prefix="/events", tags=["events"])

PAGE_SIZE = 25
VERDICTS = ("accepted", "needs_review", "rejected")


def _aware(moment: datetime | None) -> datetime | None:
    return moment if moment is None or moment.tzinfo else moment.replace(tzinfo=timezone.utc)


# Cursor: the last event of a page, (received_at, id), opaque to the client.

def _cursor(event: Event) -> str:
    raw = f"{_aware(event.received_at).isoformat()}|{event.id}"
    return base64.urlsafe_b64encode(raw.encode()).decode()


def _parse_cursor(cursor: str) -> tuple[datetime, str]:
    try:
        received, event_pk = base64.urlsafe_b64decode(cursor.encode()).decode().split("|", 1)
        return datetime.fromisoformat(received), event_pk
    except (ValueError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid cursor") from exc


def _delivery_outcome(attempts: list[Delivery]) -> str:
    """Delivered if every webhook's latest attempt was; failed if any wasn't."""
    if not attempts:
        return "none"
    latest: dict[str, Delivery] = {}
    for attempt in attempts:
        if attempt.destination_id not in latest or attempt.attempt > latest[attempt.destination_id].attempt:
            latest[attempt.destination_id] = attempt
    return "delivered" if all(a.status == "delivered" for a in latest.values()) else "failed"


def _undelivered():
    """Events where some webhook's latest attempt didn't succeed."""
    later = aliased(Delivery)
    return exists().where(
        Delivery.event_pk == Event.id,
        Delivery.status != "delivered",
        ~exists().where(later.event_pk == Delivery.event_pk,
                        later.destination_id == Delivery.destination_id,
                        later.attempt > Delivery.attempt))


@router.get("", response_model=EventPageOut)
def list_events(
    status_filter: str | None = Query(None, alias="status", pattern="^(processing|completed|failed)$"),
    needs_review: bool = False,
    delivery_failed: bool = False,
    since: datetime | None = None,
    until: datetime | None = None,
    cursor: str | None = None,
    limit: int = Query(PAGE_SIZE, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> EventPageOut:
    """The client's events, newest first, a page at a time (cursor, not page numbers:
    new events arriving while you page don't shift the pages)."""
    query = select(Event).where(Event.user_id == current_user.id)
    if status_filter:
        query = query.where(Event.status == status_filter)
    if needs_review:
        query = query.where(exists().where(EventSource.event_pk == Event.id,
                                           EventSource.verdict == "needs_review"))
    if delivery_failed:
        query = query.where(_undelivered())
    if since:
        query = query.where(Event.received_at >= since)
    if until:
        query = query.where(Event.received_at < until)
    if cursor:
        received, event_pk = _parse_cursor(cursor)
        query = query.where(or_(Event.received_at < received,
                                and_(Event.received_at == received, Event.id < event_pk)))
    rows = db.scalars(query.order_by(Event.received_at.desc(), Event.id.desc()).limit(limit + 1)).all()
    page, more = rows[:limit], len(rows) > limit

    pks = [e.id for e in page]
    counts: dict[str, dict[str, int]] = {pk: {} for pk in pks}
    documents = dict.fromkeys(pks, 0)
    for event_pk, verdict, count in db.execute(
            select(EventSource.event_pk, EventSource.verdict, func.count())
            .where(EventSource.event_pk.in_(pks))
            .group_by(EventSource.event_pk, EventSource.verdict)) if pks else []:
        documents[event_pk] += count
        if verdict:
            counts[event_pk][verdict] = count
    attempts: dict[str, list[Delivery]] = {pk: [] for pk in pks}
    for attempt in db.scalars(select(Delivery).where(Delivery.event_pk.in_(pks))) if pks else []:
        attempts[attempt.event_pk].append(attempt)

    return EventPageOut(
        events=[EventSummaryOut(
            event_id=e.event_id, run=e.run, received_at=e.received_at, finished_at=e.finished_at,
            sender=e.sender, status=e.status, error_code=e.error_code, documents=documents[e.id],
            verdicts={v: counts[e.id].get(v, 0) for v in VERDICTS},
            delivery="pending" if e.status == "processing" else _delivery_outcome(attempts[e.id]),
            reference=e.trace_id) for e in page],
        next_cursor=_cursor(page[-1]) if more else None,
    )


def _latest_run(db: Session, user: User, event_id: str) -> Event:
    event = db.scalars(select(Event).where(Event.user_id == user.id, Event.event_id == event_id)
                       .order_by(Event.run.desc()).limit(1)).first()
    if event is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")
    tag(**{"event.id": event_id})
    return event


def _reasons(source: EventSource) -> list[str]:
    if source.status == "failed":
        return [source.error_message or "The document couldn't be processed."]
    return [f"{c.get('name')}: {c.get('message')}" if c.get("message") else str(c.get("name"))
            for c in (source.checks or []) if c.get("passed") is False]


STAGES = {"load_inputs": ("read", "Read the email and its attachments"),
          "extract": ("extracted", "Read the fields from each document"),
          "validate": ("checked", "Checked the extracted data"),
          "deliver": ("delivered", "Sent the results to your webhook")}


def _timeline(event: Event, steps: list[EventStep]) -> list[TimelineStepOut]:
    """The run in a few plain stages, each with how long it took (from the step records)."""
    timeline = [TimelineStepOut(stage="received", label="Email received", at=event.received_at,
                                duration_ms=None, outcome="ok", detail=event.sender)]
    for node, (stage, label) in STAGES.items():
        mine = [s for s in steps if s.node == node]
        if not mine:
            continue
        start = min(_aware(s.started_at) for s in mine)
        end = max(_aware(s.finished_at) for s in mine)
        failed = [s for s in mine if s.outcome == "failed"]
        retried = [s for s in mine if s.outcome == "retry"]
        timeline.append(TimelineStepOut(
            stage=stage, label=label, at=start,
            duration_ms=round((end - start).total_seconds() * 1000),
            outcome="failed" if failed else "retried" if retried else "ok",
            detail=(failed[-1].error if failed else
                    f"{len(retried)} attempt(s) retried" if retried else None)))
    return timeline


def _resend_check(db: Session, event: Event) -> str | None:
    """Why Resend isn't available for this event, or None if it is (the same rules the
    agents' resend enforces; checked here too so the button can explain itself)."""
    if not event.envelope:
        return "The event hasn't finished processing."
    if _aware(event.personal_data_expires_at) <= datetime.now(timezone.utc):
        return "Older than 90 days: its data has been deleted."
    live = _live_destinations(db, event.user_id)
    event_type = event.envelope.get("event_type")
    if not any(d.get("type") == "webhook" and event_type in d.get("events", []) for d in live):
        return f"No webhook in your live setup receives {event_type} events."
    return None


def _live_destinations(db: Session, user_id: str) -> list[dict]:
    live = db.scalars(select(ClientConfiguration).where(
        ClientConfiguration.user_id == user_id, ClientConfiguration.status == "active")).first()
    return list((live.config or {}).get("destinations", [])) if live else []


def _detail(db: Session, event: Event) -> EventDetailOut:
    sources = db.scalars(select(EventSource).where(EventSource.event_pk == event.id)
                         .order_by(EventSource.position)).all()
    steps = db.scalars(select(EventStep).where(EventStep.event_pk == event.id)
                       .order_by(EventStep.sequence)).all()
    attempts = db.scalars(select(Delivery).where(Delivery.event_pk == event.id)
                          .order_by(Delivery.attempted_at, Delivery.attempt)).all()
    deleted = _aware(event.personal_data_expires_at) <= datetime.now(timezone.utc)
    unavailable = _resend_check(db, event)
    return EventDetailOut(
        event_id=event.event_id, run=event.run, received_at=event.received_at,
        finished_at=event.finished_at, sender=event.sender, status=event.status,
        error_code=event.error_code, error_message=event.error_message,
        config_version=event.config_version, reference=event.trace_id, data_deleted=deleted,
        documents=[EventDocumentOut(
            name=s.name, content_type=s.content_type, location=s.location,
            forwarded_from=s.forwarded_from, verdict=s.verdict, reasons=_reasons(s),
            data=None if deleted else s.extraction, error_code=s.error_code) for s in sources],
        timeline=_timeline(event, steps),
        deliveries=[DeliveryAttemptOut(
            attempted_at=a.attempted_at, destination_id=a.destination_id, url=a.url,
            attempt=a.attempt, status=a.status, http_status=a.http_status,
            response_excerpt=a.response_excerpt, error=a.error, duration_ms=a.duration_ms,
            trigger=a.trigger, webhook_id=a.webhook_id) for a in attempts],
        can_resend=unavailable is None, resend_unavailable_reason=unavailable,
    )


@router.get("/{event_id}", response_model=EventDetailOut)
def get_event(
    event_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> EventDetailOut:
    return _detail(db, _latest_run(db, current_user, event_id))


@router.post("/{event_id}/resend", response_model=EventDetailOut)
def resend_event(
    event_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> EventDetailOut:
    """Delivers the event's webhook again: the same message (same webhook-id), to the
    webhooks in the live setup, one attempt each. The result is in the deliveries."""
    _latest_run(db, current_user, event_id)  # 404 for anyone else's
    boardy.resend_event(current_user.id, event_id, _live_destinations(db, current_user.id))
    db.expire_all()  # the attempts were written with the agents' connection
    return _detail(db, _latest_run(db, current_user, event_id))
