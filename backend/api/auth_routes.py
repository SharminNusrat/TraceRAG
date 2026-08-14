import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from api.schemas import LoginRequest, RegisterRequest, TokenResponse, UserResponse
from core.auth import authenticate_user, create_access_token, create_user, get_current_user
from core.auth.service import get_user_by_email
from core.db import User, get_db

router = APIRouter(prefix="/auth", tags=["auth"])
logger = logging.getLogger(__name__)


def token_response(user: User) -> TokenResponse:
    return TokenResponse(
        access_token=create_access_token(user.user_id),
        user=UserResponse.model_validate(user),
    )


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
def register(request: RegisterRequest, db: Session = Depends(get_db)):
    if get_user_by_email(db, request.email) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists.",
        )

    user = create_user(db, request.full_name, request.email, request.password)
    return token_response(user)


@router.post("/login", response_model=TokenResponse)
def login(request: LoginRequest, db: Session = Depends(get_db)):
    user = authenticate_user(db, request.email, request.password)
    if user is None:
        # Deliberately the same message for an unknown email and a wrong
        # password, so the endpoint cannot be used to discover who is
        # registered.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password.",
        )

    return token_response(user)


@router.get("/me", response_model=UserResponse)
def me(user: User = Depends(get_current_user)):
    """Who the caller is. The frontend uses this to validate a stored token."""
    return user
