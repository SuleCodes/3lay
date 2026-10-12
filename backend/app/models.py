import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, text
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
