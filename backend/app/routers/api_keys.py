from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.models import ApiKey, User
from app.schemas import ApiKeyCreatedOut, ApiKeyCreateIn, ApiKeyOut
from app.security import new_api_key

router = APIRouter(prefix="/api-keys", tags=["api-keys"])


@router.get("", response_model=list[ApiKeyOut])
def list_api_keys(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ApiKeyOut]:
    keys = (
        db.query(ApiKey)
        .filter_by(user_id=current_user.id)
        .order_by(ApiKey.created_at.desc())
        .all()
    )
    return [ApiKeyOut.model_validate(k) for k in keys]


@router.post("", response_model=ApiKeyCreatedOut, status_code=status.HTTP_201_CREATED)
def create_api_key(
    payload: ApiKeyCreateIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiKeyCreatedOut:
    if not current_user.username:
        # Keys belong to a client account, which starts with the username step.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Choose a username before creating API keys.",
        )

    raw_key, prefix, key_hash = new_api_key()
    key = ApiKey(user_id=current_user.id, name=payload.name, prefix=prefix, key_hash=key_hash)
    db.add(key)
    db.commit()
    db.refresh(key)

    return ApiKeyCreatedOut(api_key=ApiKeyOut.model_validate(key), key=raw_key)


@router.post("/{key_id}/revoke", response_model=ApiKeyOut)
def revoke_api_key(
    key_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ApiKeyOut:
    key = db.query(ApiKey).filter_by(id=key_id, user_id=current_user.id).first()
    if not key:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="API key not found.")

    if key.revoked_at is None:
        key.revoked_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(key)

    return ApiKeyOut.model_validate(key)
