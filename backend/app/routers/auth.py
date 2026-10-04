from datetime import datetime, timezone
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.dependencies import get_current_user
from app.models import MagicLinkToken, User
from app.schemas import RequestLinkIn, RequestLinkOut, UserOut, VerifyOut
from app.security import (
    create_session_token,
    ensure_aware,
    hash_token,
    new_magic_link_token,
    session_cookie_options,
)
from app.services.email import EmailDeliveryError, send_magic_link_email

router = APIRouter(prefix="/auth", tags=["auth"])
settings = get_settings()


def _set_session_cookie(response: Response, user_id: str) -> None:
    token = create_session_token(user_id)
    response.set_cookie(
        key=settings.session_cookie_name,
        value=token,
        max_age=settings.jwt_expire_minutes * 60,
        **session_cookie_options(),
    )


@router.post("/request-link", response_model=RequestLinkOut)
def request_link(payload: RequestLinkIn, db: Session = Depends(get_db)) -> RequestLinkOut:
    email = payload.email.lower()

    raw_token, token_hash, expires_at = new_magic_link_token()
    token = MagicLinkToken(email=email, token_hash=token_hash, expires_at=expires_at)
    db.add(token)
    db.commit()

    query = urlencode({"token": raw_token})
    # Trailing slash: the frontend is a static export with trailingSlash, so
    # /auth/verify/ is the real page (no redirect needed to keep ?token=).
    link = f"{settings.frontend_url.rstrip('/')}/auth/verify/?{query}"
    try:
        send_magic_link_email(email, link)
    except EmailDeliveryError as exc:
        # The link never reached anyone, so don't leave a live token behind.
        db.delete(token)
        db.commit()
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc

    # Always return the same response whether or not the email is already a
    # user -- this endpoint doubles as both signup and login, so there's no
    # meaningful distinction to leak to the caller either way.
    return RequestLinkOut()


@router.get("/verify", response_model=VerifyOut)
def verify(token: str, response: Response, db: Session = Depends(get_db)) -> VerifyOut:
    token_hash = hash_token(token)
    record = db.query(MagicLinkToken).filter_by(token_hash=token_hash).first()

    now = datetime.now(timezone.utc)
    if not record or record.used_at is not None or ensure_aware(record.expires_at) < now:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="This link is invalid or has expired.")

    record.used_at = now

    user = db.query(User).filter_by(email=record.email).first()

    if not user:
        # Sign-up. No API key yet: the user first picks a username
        # (POST /account/username), which creates their first key.
        user = User(email=record.email)
        db.add(user)

    db.commit()
    db.refresh(user)

    _set_session_cookie(response, user.id)

    return VerifyOut(user=UserOut.model_validate(user), needs_username=user.username is None)


@router.post("/logout")
def logout(response: Response) -> dict[str, str]:
    response.delete_cookie(settings.session_cookie_name, **session_cookie_options())
    return {"message": "Logged out."}


@router.get("/me", response_model=UserOut)
def me(current_user: User = Depends(get_current_user)) -> UserOut:
    return UserOut.model_validate(current_user)
