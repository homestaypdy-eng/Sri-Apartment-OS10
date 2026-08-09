import uuid

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.core.security import TokenError, decode_token
from app.db.session import get_db
from app.modules.auth.models import User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")


def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> User:
    try:
        payload = decode_token(token, expected_type="access")
    except TokenError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token")

    user = db.get(User, uuid.UUID(payload["sub"]))
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found or inactive")
    return user


def require_role(*allowed_roles: str):
    """
    Route dependency factory enforcing role-based access control.
    Usage: @router.post(..., dependencies=[Depends(require_role("admin"))])
    """

    def _check(user: User = Depends(get_current_user)) -> User:
        if user.role.name not in allowed_roles:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"Role '{user.role.name}' is not permitted to perform this action",
            )
        return user

    return _check
