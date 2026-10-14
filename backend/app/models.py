import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.config import get_settings
from app.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String, unique=True, index=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    # Chosen once, during onboarding after the first sign-in; null until then.
    # Always stored lowercase (see app/usernames.py). It's the local part of
    # the client's forwarding address, so it can't change once set: their
    # end users will already be forwarding mail to it.
    username: Mapped[str | None] = mapped_column(String, unique=True, index=True, nullable=True)

    api_keys: Mapped[list["ApiKey"]] = relationship(back_populates="user", cascade="all, delete-orphan")

    @property
    def forwarding_address(self) -> str | None:
        """`{username}@{CLIENT_FORWARDING_DOMAIN}`, e.g. rolepay-agent@in.3lay.live.
        Derived rather than stored, so it always follows the configured domain."""
        if not self.username:
            return None
        return f"{self.username}@{get_settings().client_forwarding_domain}"


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String, default="Default key")

    # Only a hash of the secret is stored. `prefix` is the short, non-secret
    # portion shown in the dashboard so a key can be recognised without ever
    # showing the full value again after creation.
    prefix: Mapped[str] = mapped_column(String, nullable=False)
    key_hash: Mapped[str] = mapped_column(String, nullable=False, unique=True, index=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped[User] = relationship(back_populates="api_keys")


class RetiredUsername(Base):
    """Usernames that belonged to deleted accounts, kept so they can never be
    claimed again. A client's end users may still be forwarding mail to
    `<username>@<domain>`; if someone else could take the name, they would
    start receiving that client's documents. Holds no link to the deleted
    user and no personal data -- just the name."""

    __tablename__ = "retired_usernames"

    username: Mapped[str] = mapped_column(String, primary_key=True)
    retired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class MagicLinkToken(Base):
    __tablename__ = "magic_link_tokens"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String, nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String, nullable=False, unique=True, index=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ClientConfiguration(Base):
    """One version of a client's configuration: what the agents do with its events.

    Holds the document types (each with its schema, instructions and rules),
    analyses and destinations, as JSON checked by the agents' configuration
    check (agents/store/configurations.py) when it's saved. Versions are never
    edited: a change is a new version. Exactly one version per client is
    active (enforced by the partial unique index below), and every event
    records the version it ran under. Secrets are never in here: see
    DestinationSecret.
    """

    __tablename__ = "client_configurations"
    __table_args__ = (
        Index("uq_client_configurations_user_version", "user_id", "version", unique=True),
        Index("uq_client_configurations_one_active", "user_id", unique=True,
              postgresql_where=text("status = 'active'")),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    # Deleting a client deletes its configurations (and, below, its secrets).
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"),
                                         nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    # "draft", "active" or "retired" ("approved" comes with the dashboard and Boardy).
    status: Mapped[str] = mapped_column(String, nullable=False, default="draft")
    config: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class DestinationSecret(Base):
    """A destination's webhook signing secret, encrypted.

    Keyed by the destination's stable ID from the configuration, so approving a
    new configuration version doesn't change the secret. Stored encrypted
    (AES-256-GCM, agents/store/secret_box.py) with 3lay's key from Key Vault,
    never hashed: 3lay needs the actual secret to sign every delivery. During a
    rotation the previous secret is kept until previous_expires_at, and both
    signatures are sent.
    """

    __tablename__ = "destination_secrets"

    destination_id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"),
                                         nullable=False, index=True)
    ciphertext: Mapped[str] = mapped_column(String, nullable=False)
    previous_ciphertext: Mapped[str | None] = mapped_column(String, nullable=True)
    previous_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True),
                                                                 nullable=True)
    # Short, non-secret start of the secret (e.g. "whsec_MfKQ"), shown in the
    # dashboard so it can be recognised, like API key prefixes.
    prefix: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# Event records, written by the agents' pipeline (agents/store/events.py) and
# read by the dashboard. One Event row per run of an event; its sources, steps
# and delivery attempts hang off it and are deleted with it.

class Event(Base):
    """One run of one event (an email, later other inputs).

    Created as "processing" when the run starts and completed in one
    transaction when it ends, so a crash leaves a "processing" row for the
    watchdog rather than no trace. event_id + run is unique: a queue message
    delivered twice is recognised and skipped.

    Personal data (envelope, sender, and the sources' extractions and checks)
    is deleted after personal_data_expires_at (90 days by default); statuses,
    counts, timings, tokens and billable are kept for billing and metrics.
    """

    __tablename__ = "events"
    __table_args__ = (
        Index("uq_events_event_id_run", "event_id", "run", unique=True),
        Index("ix_app_events_user_received", "user_id", "received_at"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    event_id: Mapped[str] = mapped_column(String, nullable=False)
    run: Mapped[int] = mapped_column(Integer, nullable=False)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"),
                                         nullable=False, index=True)
    case_ref: Mapped[str] = mapped_column(String, nullable=False)
    sender: Mapped[str | None] = mapped_column(String, nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # "processing", then "completed" or "failed".
    status: Mapped[str] = mapped_column(String, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String, nullable=True)
    error_message: Mapped[str | None] = mapped_column(String, nullable=True)
    config_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    model: Mapped[str | None] = mapped_column(String, nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Completed events and failures caused by the sender are billed; failures
    # caused by 3lay (processing_error, processing_timeout) aren't.
    billable: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    envelope: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    personal_data_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                               nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class EventSource(Base):
    """One input of an event: today an email attachment (PDF or image), later
    audio, messages and other formats."""

    __tablename__ = "event_sources"
    __table_args__ = (
        Index("uq_event_sources_event_position", "event_pk", "position", unique=True),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    event_pk: Mapped[str] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"),
                                          nullable=False, index=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False)  # order within the event
    name: Mapped[str] = mapped_column(String, nullable=False)
    content_type: Mapped[str] = mapped_column(String, nullable=False)
    location: Mapped[str | None] = mapped_column(String, nullable=True)
    forwarded_from: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    verdict: Mapped[str | None] = mapped_column(String, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String, nullable=True)
    error_message: Mapped[str | None] = mapped_column(String, nullable=True)
    checks: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    extraction: Mapped[dict | None] = mapped_column(JSONB, nullable=True)


class EventStep(Base):
    """One step of a run's timeline: a node, and the source it worked on if any."""

    __tablename__ = "event_steps"
    __table_args__ = (
        Index("uq_event_steps_event_sequence", "event_pk", "sequence", unique=True),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    event_pk: Mapped[str] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"),
                                          nullable=False, index=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    node: Mapped[str] = mapped_column(String, nullable=False)
    source_position: Mapped[int | None] = mapped_column(Integer, nullable=True)
    outcome: Mapped[str] = mapped_column(String, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    error: Mapped[str | None] = mapped_column(String, nullable=True)
    details: Mapped[dict | None] = mapped_column(JSONB, nullable=True)  # model, tokens, counts


class Delivery(Base):
    """One attempt to deliver an event's envelope to one destination.

    Records the exact URL it went to (the configuration's URL can change);
    never the signing secret or the signature.
    """

    __tablename__ = "deliveries"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    event_pk: Mapped[str] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"),
                                          nullable=False, index=True)
    destination_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    url: Mapped[str] = mapped_column(String, nullable=False)
    webhook_id: Mapped[str] = mapped_column(String, nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    # "delivered", "retry" or "gave_up"; the last attempt is the outcome.
    status: Mapped[str] = mapped_column(String, nullable=False)
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_excerpt: Mapped[str | None] = mapped_column(String, nullable=True)
    error: Mapped[str | None] = mapped_column(String, nullable=True)
    attempted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    retry_in_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)


class BoardyConversation(Base):
    """One onboarding conversation with Boardy, the client's onboarding agent.

    The conversation itself (messages, draft) is saved by LangGraph's
    checkpointer in the "boardy" schema, encrypted, under the thread id
    "<user_id>:<id>" (agents/boardy/conversations.py). This row is what the API
    needs around it: who it belongs to, the samples the client uploaded (in
    Blob Storage, under "<user_id>/<id>/"), and the client's feedback.
    """

    __tablename__ = "boardy_conversations"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    # Deleting a client deletes these rows (their checkpoints and sample blobs
    # are removed by the retention job: see tools/notes.json).
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"),
                                         nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    # [{"id", "name", "content_type", "blob"}], in upload order.
    samples: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    feedback: Mapped[str | None] = mapped_column(String, nullable=True)
    feedback_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
