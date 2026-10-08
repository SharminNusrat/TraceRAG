"""Password hashing and access tokens."""

import logging
import secrets
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from config import settings

logger = logging.getLogger(__name__)

ALGORITHM = "HS256"
# bcrypt hashes at most 72 bytes and raises on anything longer, so passwords
# are trimmed to that boundary. 
MAX_PASSWORD_BYTES = 72

# PyJWT warns on every encode and decode below this, so it is checked once here.
MIN_SECRET_KEY_BYTES = 32

if settings.secret_key:
    SECRET_KEY = settings.secret_key
    if len(SECRET_KEY.encode("utf-8")) < MIN_SECRET_KEY_BYTES:
        logger.warning(
            f"secret_key is shorter than {MIN_SECRET_KEY_BYTES} bytes, which weakens "
            f"token signing. Generate one with: python -c \"import secrets; "
            f"print(secrets.token_urlsafe(32))\""
        )
else:
    # A shipped fallback secret would be no secret at all, so generate a fresh
    # one per process instead. Tokens then stop working on restart, which is a
    # visible nuisance in development and a hard failure in production - both
    # better than a signing key everyone knows.
    SECRET_KEY = secrets.token_urlsafe(32)
    logger.warning(
        "secret_key is not set; generated a temporary one. Tokens will be "
        "rejected after a restart. Set secret_key in backend/.env."
    )


def _password_bytes(password: str) -> bytes:
    return password.encode("utf-8")[:MAX_PASSWORD_BYTES]


def hash_password(password: str) -> str:
    return bcrypt.hashpw(_password_bytes(password), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(_password_bytes(password), password_hash.encode("utf-8"))
    except ValueError:
        return False


def create_access_token(user_id: int) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),  # the JWT spec requires `sub` to be a string
        "iat": now,
        "exp": now + timedelta(minutes=settings.access_token_expire_minutes),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_access_token(token: str) -> int | None:
    """The user id carried by a valid token, or None if it is not usable."""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return int(payload["sub"])
    except (jwt.PyJWTError, KeyError, TypeError, ValueError):
        return None
