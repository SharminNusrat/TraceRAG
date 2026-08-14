"""User lookup and credential checking; nothing else touches a hash."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.auth.security import hash_password, verify_password
from core.db.models import User


def normalize_email(email: str) -> str:
    return email.strip().lower()


def get_user_by_email(db: Session, email: str) -> User | None:
    return db.scalar(select(User).where(User.email == normalize_email(email)))


def get_user_by_id(db: Session, user_id: int) -> User | None:
    return db.get(User, user_id)


def create_user(db: Session, full_name: str, email: str, password: str) -> User:
    user = User(
        full_name=full_name.strip(),
        email=normalize_email(email),
        password_hash=hash_password(password),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def authenticate_user(db: Session, email: str, password: str) -> User | None:
    user = get_user_by_email(db, email)
    if user is None or not verify_password(password, user.password_hash):
        return None
    return user
