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

    model_config = {"from_attributes": True}


class VerifyOut(BaseModel):
    user: UserOut
    # Only populated the first time this user ever verifies -- i.e. signup.
    # The frontend should show this once, then never expect to see the raw
    # key again.
    created_api_key: str | None = None


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
