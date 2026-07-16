"""Phase 1: JWT Auth + Persistent Session Storage for Place of Power."""

import os
from datetime import timedelta, timezone as dt_timezone
from typing import Optional, Any

# --- SQLAlchemy Models -----------------------------------------------------------
from app.models import Base  # shared Base with TelegramChannel etc.
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy import Integer, String, DateTime, Boolean, ForeignKey


class AppUser(Base):
    """Application user that logs into PlaceOfPower web UI."""

    __tablename__ = "app_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    hashed_password: Mapped[str] = mapped_column(String(128))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class ApiSessionConfig(Base):
    """Stores Telegram API credentials per user."""

    __tablename__ = "api_session_configs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("app_users.id"))
    api_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    phone_number: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    session_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)


# --- JWT helpers -------------------------------------------------------------------
try:
    from jose import jwt, ExpiredSignatureError, JWTError  # type: ignore[import-untyped]
except ModuleNotFoundError as exc:
    raise RuntimeError("Module 'python-jose' not found. Run `pip install python-jose[cryptography]`") from exc

SECRET_KEY = os.getenv("PLACEOFPOWER_SECRET_KEY", "dev-secret-key-change-in-production-please-use-secrets-token-url-safe!")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES: int = 480

# --- Password hashing context -----------------------------------------------------
try:
    from passlib.context import CryptContext   # type: ignore[import-untyped]
except ModuleNotFoundError as exc:
    raise RuntimeError("Module 'passlib' not found. Run `pip install passlib`") from exc

# Use sha256_crypt (no 72-byte limit like bcrypt) - more compatible
pwd_context = CryptContext(schemes=["sha256_crypt"], deprecated="auto")

def verify_password(plain_pw: str, hashed_pw: str) -> bool:
    """Verify a plain-text password against its hash."""
    return pwd_context.verify(plain_pw, hashed_pw)

def create_hash(password: str) -> str:
    """Create hash for storage in DB (user registration / update)."""
    return pwd_context.hash(password)


# --- JWT token helpers -----------------------------------------------------------

def create_access_token(data: dict[str, Any], expires_delta: timedelta | None = None):
    """Create a signed JWT token; defaults to 8-hour expiry."""
    from datetime import datetime
    expire_dt = datetime.now(dt_timezone.utc) + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    payload: dict[str, object] = {**data}
    payload.update({"exp": expire_dt})
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> dict:
    """Decode and validate JWT token, raise on expiry/invalid."""
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except ExpiredSignatureError:
        from fastapi import HTTPException
        raise HTTPException(401, "Token expired")
    except JWTError:
        from fastapi import HTTPException
        raise HTTPException(401, "Invalid token")


# --- FastAPI dependency: get current user from token ---

from fastapi import Depends, HTTPException
from fastapi.security import OAuth2PasswordBearer
from app.database import get_db
from sqlalchemy.orm import Session

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/telegram/api/token")

async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> AppUser:
    """FastAPI dependency: decode JWT, fetch user from DB."""
    payload = decode_token(token)
    username: str = payload.get("sub")
    if not username:
        raise HTTPException(401, "Invalid token payload")

    user = db.query(AppUser).filter(AppUser.username == username).first()
    if not user:
        raise HTTPException(401, "User not found")
    if not user.is_active:
        raise HTTPException(400, "Inactive user")
    return user


async def get_current_user_optional(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> Optional[AppUser]:
    """Same as get_current_user but returns None instead of raising."""
    try:
        return await get_current_user(token, db)
    except HTTPException:
        return None