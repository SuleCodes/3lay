"""The client's webhooks: where their results are delivered, and their signing secrets.

Webhooks come from the client's live configuration (set up with Boardy, in
/setup): changing a URL or its events is a new configuration version, so it's
done there. Here a client sees them and can rotate a signing secret, e.g. if
it leaked or was lost. Secrets are only ever shown once, when created; this
lists only their non-secret prefix.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.models import ClientConfiguration, DestinationSecret, User
from app.schemas import RotatedSecretOut, WebhookOut, WebhooksOut
from app.services import boardy

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


def _live(db: Session, user: User) -> ClientConfiguration | None:
    return db.query(ClientConfiguration).filter_by(user_id=user.id, status="active").one_or_none()


def _webhook(db: Session, user: User, destination: dict) -> WebhookOut:
    secret = db.get(DestinationSecret, destination["id"])
    if secret is not None and secret.user_id != user.id:
        secret = None  # never another client's (destination ids are per client anyway)
    previous_expires = secret.previous_expires_at if secret else None
    if previous_expires is not None:
        if previous_expires.tzinfo is None:  # SQLite (tests) drops the timezone
            previous_expires = previous_expires.replace(tzinfo=timezone.utc)
        if previous_expires <= datetime.now(timezone.utc):
            previous_expires = None  # the old secret has stopped working
    return WebhookOut(
        id=destination["id"],
        url=destination["url"],
        events=destination.get("events", []),
        secret_prefix=secret.prefix if secret else None,
        secret_created_at=secret.created_at if secret else None,
        secret_rotated_at=secret.rotated_at if secret else None,
        previous_secret_expires_at=previous_expires,
    )


@router.get("", response_model=WebhooksOut)
def list_webhooks(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> WebhooksOut:
    live = _live(db, current_user)
    if live is None:
        return WebhooksOut(active_version=None, webhooks=[])
    destinations = [d for d in live.config.get("destinations", []) if d.get("type") == "webhook"]
    return WebhooksOut(active_version=live.version,
                       webhooks=[_webhook(db, current_user, d) for d in destinations])


@router.post("/{webhook_id}/rotate-secret", response_model=RotatedSecretOut)
def rotate_secret(
    webhook_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> RotatedSecretOut:
    """A new signing secret, shown once. The old one keeps working for a grace period."""
    live = _live(db, current_user)
    destination = next((d for d in (live.config.get("destinations", []) if live else [])
                        if d.get("id") == webhook_id and d.get("type") == "webhook"), None)
    if destination is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Webhook not found")
    secret = boardy.rotate_secret(current_user.id, webhook_id)
    db.expire_all()  # the store wrote with its own connection: read the new prefix and dates
    return RotatedSecretOut(webhook=_webhook(db, current_user, destination), secret=secret)
