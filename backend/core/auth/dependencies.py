"""Route dependencies for reading the caller's identity."""

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from core.auth.security import decode_access_token
from core.auth.service import get_user_by_id
from core.db.session import get_db
from core.db.models import User

# auto_error=False: a missing Authorization header has to reach the optional
# dependency as None rather than being turned into a 403 by HTTPBearer.
bearer_scheme = HTTPBearer(auto_error=False)


def get_current_user_optional(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User | None:
    """The signed-in user, or None for an anonymous caller."""
    if credentials is None:
        return None
    user_id = decode_access_token(credentials.credentials)
    if user_id is None:
        return None
    return get_user_by_id(db, user_id)


def get_current_user(user: User | None = Depends(get_current_user_optional)) -> User:
    """The signed-in user; rejects the request if there is not one."""
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user
