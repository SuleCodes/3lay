from datetime import datetime

from pydantic import BaseModel, EmailStr


class RequestLinkIn(BaseModel):
    email: EmailStr


class RequestLinkOut(BaseModel):
    message: str = "If that email is valid, a sign-in link is on its way."


class UserOut(BaseModel):
    id: str
    email: str
    created_at: datetime
    # Both null until the user completes onboarding by choosing a username.
    username: str | None
    forwarding_address: str | None

    model_config = {"from_attributes": True}


class VerifyOut(BaseModel):
    user: UserOut
    # True until the user has chosen a username. The frontend should send
    # them to the username step (POST /account/username) before anything
    # else -- that step is also what creates their first API key.
    needs_username: bool


class ApiKeyOut(BaseModel):
    id: str
    name: str
    prefix: str
    created_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None

    model_config = {"from_attributes": True}


class ApiKeyCreateIn(BaseModel):
    name: str = "New key"


class ApiKeyCreatedOut(BaseModel):
    api_key: ApiKeyOut
    key: str  # raw secret, shown once


class UsernameIn(BaseModel):
    username: str


class UsernameAvailabilityOut(BaseModel):
    # The normalized form (lowercase, trimmed) -- what would actually be saved.
    username: str
    available: bool
    # Why it isn't available (invalid, reserved or taken); null when it is.
    reason: str | None = None
    # The address it would give, so the UI can preview it while typing.
    forwarding_address: str | None = None


class DeleteAccountIn(BaseModel):
    # The user must type their account email to confirm. A guard against
    # accidental clicks: deletion can't be undone.
    confirm_email: str


class DeleteAccountOut(BaseModel):
    message: str = "Your account and its data have been deleted."
    # How many stored emails (blobs) were deleted from ingest storage.
    deleted_stored_items: int


class UsernameClaimedOut(BaseModel):
    user: UserOut
    # The user's first API key. The raw `key` is shown this once only.
    api_key: ApiKeyCreatedOut
