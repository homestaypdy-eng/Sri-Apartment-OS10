import re
import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field, field_validator


class UserCreate(BaseModel):
    name: str
    email: EmailStr
    password: str = Field(min_length=8)
    role: str  # role name, e.g. "admin" or "staff"

    @field_validator("password")
    @classmethod
    def password_complexity(cls, v: str) -> str:
        if not re.search(r"[A-Za-z]", v) or not re.search(r"\d", v):
            raise ValueError("Password must contain at least one letter and one digit")
        return v


class UserOut(BaseModel):
    id: uuid.UUID
    name: str
    email: EmailStr
    role: str
    is_active: bool

    class Config:
        from_attributes = True


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: str


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ForgotPasswordResponse(BaseModel):
    message: str


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str = Field(min_length=8)


class SessionOut(BaseModel):
    id: uuid.UUID
    ip_address: str | None
    user_agent: str | None
    created_at: datetime
    last_used_at: datetime
    revoked_at: datetime | None
    is_current: bool = False

    class Config:
        from_attributes = True
