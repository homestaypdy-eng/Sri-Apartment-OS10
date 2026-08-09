import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import (
    TokenError,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.db.session import get_db
from app.modules.audit.schemas import record_audit_log, record_login_event
from app.modules.auth.models import PasswordResetToken, Role, User, UserSession
from app.modules.auth.schemas import (
    ForgotPasswordRequest,
    ForgotPasswordResponse,
    LoginRequest,
    RefreshRequest,
    ResetPasswordRequest,
    SessionOut,
    TokenPair,
    UserCreate,
    UserOut,
)
from app.modules.notifications.service import send_email, EmailNotConfiguredError
from app.shared.rate_limit import is_rate_limited, record_attempt, reset
from app.shared.rbac import get_current_user, require_role

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.post("/register", response_model=UserOut, status_code=201)
def register_user(
    payload: UserCreate,
    db: Session = Depends(get_db),
    # Only an existing admin can create new users — no open self-signup for an
    # internal ops system. (Bootstrap of the first admin is done via seed script.)
    _admin: User = Depends(require_role("admin")),
):
    role = db.scalar(select(Role).where(Role.name == payload.role))
    if role is None:
        raise HTTPException(400, f"Unknown role '{payload.role}'")

    if db.scalar(select(User).where(User.email == payload.email)):
        raise HTTPException(409, "Email already registered")

    user = User(
        name=payload.name,
        email=payload.email,
        password_hash=hash_password(payload.password),
        role_id=role.id,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return UserOut(id=user.id, name=user.name, email=user.email, role=role.name, is_active=user.is_active)


@router.post("/login", response_model=TokenPair)
def login(payload: LoginRequest, request: Request, db: Session = Depends(get_db)):
    ip = request.client.host if request.client else "unknown"
    user_agent = request.headers.get("user-agent")
    rate_limit_key = f"{ip}:{payload.email}"

    if is_rate_limited(rate_limit_key):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Too many failed login attempts. Try again in a few minutes.",
        )

    user = db.scalar(select(User).where(User.email == payload.email))

    if user is None or not verify_password(payload.password, user.password_hash):
        record_attempt(rate_limit_key)
        record_login_event(db, payload.email, success=False, user_id=None, ip_address=ip, user_agent=user_agent)
        db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect email or password")
    if not user.is_active:
        record_attempt(rate_limit_key)
        record_login_event(db, payload.email, success=False, user_id=user.id, ip_address=ip, user_agent=user_agent)
        db.commit()
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Account is deactivated")

    reset(rate_limit_key)
    record_login_event(db, payload.email, success=True, user_id=user.id, ip_address=ip, user_agent=user_agent)

    session = UserSession(user_id=user.id, ip_address=ip, user_agent=user_agent)
    db.add(session)
    db.commit()
    db.refresh(session)

    return TokenPair(
        access_token=create_access_token(str(user.id), user.role.name),
        refresh_token=create_refresh_token(str(user.id), user.role.name, jti=str(session.id)),
    )


@router.post("/refresh", response_model=TokenPair)
def refresh(payload: RefreshRequest, db: Session = Depends(get_db)):
    try:
        data = decode_token(payload.refresh_token, expected_type="refresh")
    except TokenError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired refresh token")

    user = db.get(User, data["sub"])
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found or inactive")

    # A refresh token issued before this session-tracking feature existed
    # has no jti claim — treated as still valid (no regression for
    # tokens already in the wild), but a token WITH a jti must point to
    # a real, unrevoked session, so revoking a session takes effect
    # immediately rather than waiting for natural JWT expiry.
    session_id = data.get("jti")
    if session_id:
        session = db.get(UserSession, session_id)
        if session is None or session.revoked_at is not None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session has been revoked")
        session.last_used_at = datetime.now(timezone.utc)
        db.commit()

    return TokenPair(
        access_token=create_access_token(str(user.id), user.role.name),
        refresh_token=create_refresh_token(str(user.id), user.role.name, jti=session_id),
    )


PASSWORD_RESET_TOKEN_EXPIRE_MINUTES = 30


@router.post("/forgot-password", response_model=ForgotPasswordResponse)
def forgot_password(payload: ForgotPasswordRequest, db: Session = Depends(get_db)):
    """
    Always returns the same generic message regardless of whether the
    email exists — telling an unauthenticated caller "that email isn't
    registered" is itself a real information leak (confirms who has an
    account), so the response can't distinguish the two cases even
    though the real logic behind it obviously differs.
    """
    generic_response = ForgotPasswordResponse(message="If that email is registered, a password reset link has been sent.")
    user = db.scalar(select(User).where(User.email == payload.email))
    if user is None or not user.is_active:
        return generic_response

    raw_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    reset_row = PasswordResetToken(
        user_id=user.id, token_hash=token_hash,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=PASSWORD_RESET_TOKEN_EXPIRE_MINUTES),
    )
    db.add(reset_row)
    db.commit()

    try:
        send_email(
            user.email, "Password Reset — Sri Apartment Homestay",
            f"Hi {user.name},\n\nUse this code to reset your password (valid for {PASSWORD_RESET_TOKEN_EXPIRE_MINUTES} minutes): {raw_token}\n\n"
            f"If you didn't request this, you can safely ignore this email.",
        )
    except EmailNotConfiguredError:
        pass  # best-effort — the token still exists and works if the admin shares it another way

    return generic_response


@router.post("/reset-password", response_model=ForgotPasswordResponse)
def reset_password(payload: ResetPasswordRequest, db: Session = Depends(get_db)):
    token_hash = hashlib.sha256(payload.token.encode()).hexdigest()
    reset_row = db.scalar(select(PasswordResetToken).where(PasswordResetToken.token_hash == token_hash))

    if reset_row is None or reset_row.consumed_at is not None or reset_row.expires_at < datetime.now(timezone.utc):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid or expired reset token")

    user = db.get(User, reset_row.user_id)
    if user is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid or expired reset token")

    user.password_hash = hash_password(payload.new_password)
    reset_row.consumed_at = datetime.now(timezone.utc)

    # A password reset is a real security event — every existing session
    # must stop working immediately, the same principle as forcing a
    # logout everywhere when a password is compromised or changed.
    for session in db.scalars(select(UserSession).where(UserSession.user_id == user.id, UserSession.revoked_at.is_(None))).all():
        session.revoked_at = datetime.now(timezone.utc)

    record_audit_log(db, user.id, "auth.password_reset", entity_type="user", entity_id=str(user.id), details="Password reset via forgot-password flow")
    db.commit()
    return ForgotPasswordResponse(message="Password has been reset. Please log in with your new password.")


@router.get("/sessions", response_model=list[SessionOut])
def list_my_sessions(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Every active (non-revoked) session for the current user — the
    real 'which devices am I logged in on' view. (Access tokens don't
    carry a session id today, only refresh tokens do — so this can't
    reliably highlight "this is your current device" without adding jti
    to access tokens too, which is a separate, larger change; the core
    value here is seeing and revoking sessions, which doesn't need it.)"""
    return db.scalars(
        select(UserSession).where(UserSession.user_id == user.id, UserSession.revoked_at.is_(None)).order_by(UserSession.last_used_at.desc())
    ).all()


@router.post("/sessions/{session_id}/revoke", status_code=204)
def revoke_session(session_id: uuid.UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    session = db.get(UserSession, session_id)
    if session is None or session.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found")
    if session.revoked_at is None:
        session.revoked_at = datetime.now(timezone.utc)
        record_audit_log(db, user.id, "auth.session_revoke", entity_type="user_session", entity_id=str(session.id), details="Session revoked by user")
        db.commit()


@router.post("/sessions/revoke-all", status_code=204)
def revoke_all_sessions(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Logs out every device — the real 'sign out everywhere' action."""
    sessions = db.scalars(select(UserSession).where(UserSession.user_id == user.id, UserSession.revoked_at.is_(None))).all()
    for session in sessions:
        session.revoked_at = datetime.now(timezone.utc)
    if sessions:
        record_audit_log(db, user.id, "auth.session_revoke_all", entity_type="user", entity_id=str(user.id), details=f"Revoked {len(sessions)} session(s)")
    db.commit()
