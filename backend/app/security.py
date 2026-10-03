import hashlib
import secrets
from datetime import datetime, timedelta, timezone

import jwt

from app.config import get_settings

settings = get_settings()

API_KEY_PREFIX_LEN = 8


def ensure_aware(dt: datetime) -> datetime:
    """Postgres returns timezone-aware values for DateTime(timezone=True)
    columns, but a naive value (e.g. from a hand-written row) would make the
    comparison raise. We always write UTC, so treat a naive value as UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def hash_token(raw: str) -> str:
    """One-way hash for magic-link tokens and API keys. We never need to
    reverse it -- only compare a freshly hashed incoming value against what's
    stored, the same way you'd treat a password."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def new_magic_link_token() -> tuple[str, str, datetime]:
    """Returns (raw_token, token_hash, expires_at). The raw token goes in the
    emailed link; only the hash is persisted."""
    raw = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=settings.magic_link_expire_minutes)
    return raw, hash_token(raw), expires_at


def new_api_key() -> tuple[str, str, str]:
    """Returns (raw_key, prefix, key_hash). The raw key is shown to the user
    exactly once, at creation time -- only the hash is persisted after that."""
    raw = f"3lay_live_{secrets.token_urlsafe(32)}"
    prefix = raw[: len("3lay_live_") + API_KEY_PREFIX_LEN]
    return raw, prefix, hash_token(raw)


def create_session_token(user_id: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "iat": now,
        "exp": now + timedelta(minutes=settings.jwt_expire_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def decode_session_token(token: str) -> str | None:
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
        return payload.get("sub")
    except jwt.PyJWTError:
        return None
