"""Endpoints for 3lay's own infrastructure (the Cloudflare email Worker), not
for browsers or clients. Every request must carry X-3lay-Internal-Key,
matching BACKEND:INTERNAL_API_KEY."""

import hmac
import logging

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models import User

logger = logging.getLogger("3lay.internal")
router = APIRouter(prefix="/internal", tags=["internal"], include_in_schema=False)
settings = get_settings()


def require_internal_key(x_3lay_internal_key: str = Header(default="")) -> None:
    """Fails closed: with no INTERNAL_API_KEY configured, every request is
    refused. Constant-time comparison, so timing can't reveal the key."""
    expected = settings.internal_api_key or ""
    if not expected:
        logger.error("INTERNAL_API_KEY is not configured; refusing internal request")
    if not expected or not hmac.compare_digest(x_3lay_internal_key.encode(), expected.encode()):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")


class RecipientOut(BaseModel):
    address: str
    username: str


@router.get("/recipients/{address}", response_model=RecipientOut, dependencies=[Depends(require_internal_key)])
def check_recipient(address: str, db: Session = Depends(get_db)) -> RecipientOut:
    """Is `address` (e.g. rolepay-agent@in.3lay.live) a client's forwarding
    address? 200 if so, 404 if not -- the Worker bounces mail for 404s.

    Only exact `{username}@{CLIENT_FORWARDING_DOMAIN}` matches count: no
    plus-addressing, other domains or deleted (retired) usernames."""
    normalized = address.strip().lower()
    local, _, domain = normalized.rpartition("@")

    user = None
    if local and domain == settings.client_forwarding_domain:
        user = db.query(User).filter_by(username=local).first()

    if not user or not user.username:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown recipient")

    return RecipientOut(address=normalized, username=user.username)
