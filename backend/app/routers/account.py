import logging

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.dependencies import get_current_user
from app.models import ApiKey, MagicLinkToken, RetiredUsername, User
from app.schemas import (
    ApiKeyCreatedOut,
    ApiKeyOut,
    DeleteAccountIn,
    DeleteAccountOut,
    UserOut,
    UsernameAvailabilityOut,
    UsernameClaimedOut,
    UsernameIn,
)
from app.security import new_api_key, session_cookie_options
from app.services.storage import StorageNotConfiguredError, delete_client_blobs
from app.usernames import normalize_username

logger = logging.getLogger("3lay.account")
router = APIRouter(prefix="/account", tags=["account"])
settings = get_settings()

_TAKEN = "That username is already taken."


def _username_unavailable(db: Session, username: str) -> bool:
    """In use by a current account, or retired from a deleted one."""
    return bool(
        db.query(User.id).filter_by(username=username).first()
        or db.get(RetiredUsername, username)
    )


@router.get("/username-availability", response_model=UsernameAvailabilityOut)
def username_availability(
    username: str,
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> UsernameAvailabilityOut:
    """Check a username while the user types, before claiming it. Requires a
    session so it can't be used anonymously to enumerate clients."""
    try:
        normalized = normalize_username(username)
    except ValueError as exc:
        return UsernameAvailabilityOut(username=username.strip().lower(), available=False, reason=str(exc))

    if _username_unavailable(db, normalized):
        return UsernameAvailabilityOut(username=normalized, available=False, reason=_TAKEN)

    return UsernameAvailabilityOut(
        username=normalized,
        available=True,
        forwarding_address=f"{normalized}@{settings.client_forwarding_domain}",
    )


@router.post("/username", response_model=UsernameClaimedOut, status_code=status.HTTP_201_CREATED)
def claim_username(
    payload: UsernameIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> UsernameClaimedOut:
    """The onboarding step: sets the user's username (and so their forwarding
    address) and creates their first API key, returned once. A username can
    only be set once -- end users will be forwarding mail to the address."""
    if current_user.username:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Your username is already set.")

    try:
        username = normalize_username(payload.username)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    if _username_unavailable(db, username):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=_TAKEN)

    current_user.username = username
    raw_key, prefix, key_hash = new_api_key()
    key = ApiKey(user_id=current_user.id, name="Default key", prefix=prefix, key_hash=key_hash)
    db.add(key)

    try:
        # Username and key are saved together: either both or neither.
        db.commit()
    except IntegrityError as exc:
        # Someone claimed the same username between the check above and now;
        # the unique index on users.username is the real guarantee.
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=_TAKEN) from exc

    db.refresh(current_user)
    db.refresh(key)

    return UsernameClaimedOut(
        user=UserOut.model_validate(current_user),
        api_key=ApiKeyCreatedOut(api_key=ApiKeyOut.model_validate(key), key=raw_key),
    )


@router.delete("", response_model=DeleteAccountOut)
def delete_account(
    payload: DeleteAccountIn,
    response: Response,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DeleteAccountOut:
    """Permanently deletes the signed-in user's account and data: every email
    stored for their forwarding address, their API keys, their sign-in
    tokens and the account itself. Their username is retired so it can never
    be reused. Can't be undone."""
    if payload.confirm_email.strip().lower() != current_user.email.lower():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Type your account email exactly to confirm deletion.",
        )

    # 1. Stored emails first. If this fails, nothing else has been touched
    #    and the user can simply retry; doing it last could leave a client's
    #    documents behind with no account left to delete them from.
    deleted_blobs = 0
    address = current_user.forwarding_address
    if address:
        try:
            deleted_blobs = delete_client_blobs(address)
        except StorageNotConfiguredError as exc:
            logger.error("Can't delete account %s: %s", current_user.id, exc)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Account deletion is unavailable right now. Please try again later.",
            ) from exc
        except Exception as exc:
            logger.exception("Deleting stored data for %s failed", address)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="We couldn't delete your stored data. Nothing has been deleted; please try again.",
            ) from exc

    # 2. Then the database, in one transaction.
    if current_user.username:
        db.merge(RetiredUsername(username=current_user.username))
    db.query(MagicLinkToken).filter_by(email=current_user.email).delete(synchronize_session=False)
    db.delete(current_user)  # its API keys go with it (cascade on User.api_keys)
    db.commit()

    logger.info("Deleted account %s (%s); %d stored item(s) removed", current_user.id, address, deleted_blobs)

    # 3. Sign them out here. Sessions on other devices stop working on their
    #    next request, because the user they point to no longer exists.
    response.delete_cookie(settings.session_cookie_name, **session_cookie_options())
    return DeleteAccountOut(deleted_stored_items=deleted_blobs)
