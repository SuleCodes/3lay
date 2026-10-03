import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, String
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
