from fastapi import Cookie, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models import User
from app.security import decode_session_token

settings = get_settings()

_UNAUTHORIZED = HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")


def get_current_user(
    db: Session = Depends(get_db),
    **_: None,
) -> User:  # pragma: no cover - replaced below, kept for import clarity
    raise _UNAUTHORIZED


def _build_get_current_user():
    cookie_name = settings.session_cookie_name

    def dependency(
        db: Session = Depends(get_db),
        session_cookie: str | None = Cookie(default=None, alias=cookie_name),
    ) -> User:
        if not session_cookie:
            raise _UNAUTHORIZED

        user_id = decode_session_token(session_cookie)
        if not user_id:
            raise _UNAUTHORIZED

        user = db.get(User, user_id)
        if not user:
            raise _UNAUTHORIZED

        return user

    return dependency


# Real dependency used by routers -- built with the configured cookie name
# baked in, since FastAPI's Cookie() needs a fixed alias at declaration time.
get_current_user = _build_get_current_user()
