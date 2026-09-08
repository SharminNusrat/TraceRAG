"""Keeping other people's credentials out of the database in plain text.

"""

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from config import settings


class SecretError(RuntimeError):
    """A credential could not be stored or read back."""


def available() -> bool:
    """Whether credentials can be stored at all.

    Without a configured secret the application invents one per process, so
    anything encrypted now would be unreadable after the next restart - a
    connection that silently stops working rather than failing when it is made.
    """
    return bool(settings.secret_key)


def _cipher() -> Fernet:
    if not available():
        raise SecretError(
            "Storing an access token needs secret_key set in backend/.env. "
            "Without it the key changes on every restart and the token could "
            "not be read back."
        )
    # Fernet wants 32 url-safe base64 bytes; the secret is free-form text.
    digest = hashlib.sha256(settings.secret_key.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt(value: str) -> str:
    return _cipher().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt(value: str) -> str:
    try:
        return _cipher().decrypt(value.encode("ascii")).decode("utf-8")
    except InvalidToken as error:
        # Almost always a changed secret_key rather than a corrupted value.
        raise SecretError(
            "A stored access token could not be read. This happens when "
            "secret_key changes; the connection has to be made again."
        ) from error
