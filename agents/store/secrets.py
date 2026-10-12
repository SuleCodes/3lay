"""Destination signing secrets: creating, rotating and reading them, encrypted at rest.

The secret is keyed by the destination's stable ID, outside the configuration,
so approving a new configuration version doesn't change it. Rotating keeps
the previous secret working for a grace period: both signatures are sent, so
the client can switch to the new secret without missing deliveries.
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from noti.signing import generate_secret
from store.db import destination_secrets
from store.secret_box import decrypt, encrypt, keys_from_environment

ROTATION_GRACE = timedelta(hours=24)
PREFIX_CHARS = len("whsec_") + 4


def _aware(moment):
    """SQLite gives back naive datetimes; Postgres gives timezone-aware ones."""
    return moment if moment is None or moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def create_or_rotate_secret(engine, user_id, destination_id, now=None):
    """A new secret for the destination, stored encrypted. Returns it, to show once.

    If the destination already has one, this is a rotation: the old secret
    stays valid for ROTATION_GRACE.
    """
    now = now or datetime.now(timezone.utc)
    key_version, keys = keys_from_environment()
    secret = generate_secret()
    ciphertext = encrypt(secret, keys[key_version], key_version, destination_id)
    with engine.begin() as connection:
        existing = connection.execute(
            select(destination_secrets).where(
                destination_secrets.c.destination_id == destination_id)).first()
        if existing is None:
            connection.execute(destination_secrets.insert().values(
                destination_id=destination_id, user_id=user_id, ciphertext=ciphertext,
                prefix=secret[:PREFIX_CHARS], created_at=now))
        elif existing.user_id != user_id:
            raise ValueError(f"{destination_id} belongs to another client")
        else:
            connection.execute(destination_secrets.update()
                               .where(destination_secrets.c.destination_id == destination_id)
                               .values(ciphertext=ciphertext,
                                       previous_ciphertext=existing.ciphertext,
                                       previous_expires_at=now + ROTATION_GRACE,
                                       prefix=secret[:PREFIX_CHARS], rotated_at=now))
    return secret


def has_secret(engine, destination_id):
    with engine.connect() as connection:
        return connection.execute(
            select(destination_secrets.c.destination_id).where(
                destination_secrets.c.destination_id == destination_id)).first() is not None


def signing_secrets(engine, destination_id, now=None):
    """The secrets to sign a delivery with: the current one, plus the previous one
    while a rotation's grace period lasts. An empty list if there's none."""
    now = now or datetime.now(timezone.utc)
    with engine.connect() as connection:
        row = connection.execute(
            select(destination_secrets).where(
                destination_secrets.c.destination_id == destination_id)).first()
    if row is None:
        return []
    _, keys = keys_from_environment()
    secrets_ = [decrypt(row.ciphertext, keys, destination_id)]
    expires = _aware(row.previous_expires_at)
    if row.previous_ciphertext and expires and expires > now:
        secrets_.append(decrypt(row.previous_ciphertext, keys, destination_id))
    return secrets_
