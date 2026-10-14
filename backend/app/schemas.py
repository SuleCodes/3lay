from datetime import datetime

from pydantic import BaseModel, EmailStr, Field


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


# Boardy, the onboarding agent (app/routers/boardy.py)


class BoardyMessageOut(BaseModel):
    role: str  # "client" or "boardy"
    text: str
    steps: list[str]  # what Boardy did before this reply, in plain words


class BoardyApprovalOut(BaseModel):
    summary: str


class BoardySampleOut(BaseModel):
    id: str
    name: str


class BoardySecretOut(BaseModel):
    destination_id: str
    url: str
    secret: str  # shown once, when the configuration is switched on


class BoardyConversationOut(BaseModel):
    id: str
    created_at: datetime
    # "ready" (waiting for the client), "awaiting_approval", or "failed" (the
    # last turn didn't finish: retry it).
    status: str
    messages: list[BoardyMessageOut]
    approval: BoardyApprovalOut | None
    missing: list[str]  # what the configuration still needs
    approved: bool
    saved_version: int | None
    changes_pending: bool  # changed since it was approved: not live until approved again
    samples: list[BoardySampleOut]
    feedback: str | None
    active_version: int | None  # the client's live configuration, if any
    # Approved in this conversation but not switched on yet (it failed): "Switch on" retries.
    activation_pending: bool
    activation_error: str | None = None
    # Only in the response that switched the configuration on: secrets for new webhooks.
    new_secrets: list[BoardySecretOut] = []


class BoardyConversationSummaryOut(BaseModel):
    id: str
    created_at: datetime
    updated_at: datetime


class BoardyMessageIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class BoardyApprovalIn(BaseModel):
    approved: bool
    comments: str = Field(default="", max_length=2000)


class BoardyFeedbackIn(BaseModel):
    text: str = Field(max_length=5000)


# Webhooks: the client's live destinations (app/routers/webhooks.py)


class WebhookOut(BaseModel):
    id: str
    url: str
    events: list[str]
    # The non-secret start of the current signing secret, to recognise it; null if it has none yet.
    secret_prefix: str | None
    secret_created_at: datetime | None
    secret_rotated_at: datetime | None
    # After a rotation, the old secret keeps working (both are sent) until then.
    previous_secret_expires_at: datetime | None


class WebhooksOut(BaseModel):
    active_version: int | None  # the live configuration the webhooks come from
    webhooks: list[WebhookOut]


class RotatedSecretOut(BaseModel):
    webhook: WebhookOut
    secret: str  # shown once
