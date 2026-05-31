"""Authentication & security helpers — password hashing and JWT tokens.

This is the single place that knows how to hash/verify passwords and how to
issue/validate JSON Web Tokens. Other modules depend only on `get_current_user`
to protect their endpoints, keeping the sensitive logic centralised here.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import User

# bcrypt is the password hashing scheme; passlib handles salting/verification.
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# `tokenUrl` must match the login route so Swagger UI's "Authorize" button and
# the OAuth2 password flow point at the right endpoint.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="api/auth/login")


def hash_password(plain_password: str) -> str:
    """Return a bcrypt hash for the given plaintext password."""
    return pwd_context.hash(plain_password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Check a plaintext password against a stored bcrypt hash."""
    return pwd_context.verify(plain_password, hashed_password)


def create_access_token(subject: str, expires_minutes: int | None = None) -> str:
    """Create a signed JWT whose `sub` claim identifies the user."""
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=expires_minutes or settings.access_token_expire_minutes
    )
    payload = {"sub": subject, "exp": expire}
    return jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)


def get_current_user(
    token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)
) -> User:
    """FastAPI dependency: resolve the authenticated user from a bearer token.

    Raises 401 if the token is missing/invalid/expired or the user no longer
    exists or has been deactivated.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(
            token, settings.secret_key, algorithms=[settings.jwt_algorithm]
        )
        subject = payload.get("sub")
        if subject is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    user = db.get(User, int(subject))
    if user is None or not user.is_active:
        raise credentials_exception
    return user
