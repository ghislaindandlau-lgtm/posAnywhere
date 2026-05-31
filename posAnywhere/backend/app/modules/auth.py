"""Authentication module — user registration, login and identity.

Endpoints:
  * POST /api/auth/register  — create a new user account (returns the profile)
  * POST /api/auth/login     — exchange email + password for a JWT access token
  * GET  /api/auth/me        — return the authenticated user's profile

Login uses the OAuth2 "password" form so Swagger UI's Authorize dialog works
out of the box; the form's `username` field is treated as the user's email.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User
from app.schemas import Token, UserCreate, UserOut
from app.security import (
    create_access_token,
    get_current_user,
    hash_password,
    verify_password,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])
logger = logging.getLogger(__name__)


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def register(payload: UserCreate, db: Session = Depends(get_db)) -> User:
    """Create a new user account. Emails are unique (case-insensitive)."""
    existing = db.query(User).filter(User.email == payload.email).first()
    if existing is not None:
        logger.warning("auth.register.duplicate email=%s", payload.email)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered",
        )

    user = User(
        email=payload.email,
        full_name=payload.full_name,
        hashed_password=hash_password(payload.password),
        role=payload.role,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    logger.info(
        "auth.register.success user_id=%s email=%s role=%s",
        user.id,
        user.email,
        user.role.value,
    )
    return user


@router.post("/login", response_model=Token)
def login(
    form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)
) -> Token:
    """Validate credentials and return a signed JWT access token."""
    email = form.username.strip().lower()
    user = db.query(User).filter(User.email == email).first()
    if user is None or not verify_password(form.password, user.hashed_password):
        logger.warning("auth.login.failed email=%s reason=bad_credentials", email)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not user.is_active:
        logger.warning("auth.login.failed email=%s reason=disabled", email)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Account is disabled"
        )

    access_token = create_access_token(subject=str(user.id))
    logger.info("auth.login.success user_id=%s email=%s", user.id, user.email)
    return Token(access_token=access_token)


@router.get("/me", response_model=UserOut)
def read_me(current_user: User = Depends(get_current_user)) -> User:
    """Return the profile of the currently authenticated user."""
    return current_user
