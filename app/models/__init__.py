"""Shared SQLAlchemy base and models."""

from sqlalchemy.orm import DeclarativeBase

class Base(DeclarativeBase):
    pass

# Import all models to register them
from app.models.database import (
    TelegramChannel,
    TelegramUser,
    TelegramVideo,
    TelegramAudio,
)
from app.models.auth_models import (
    AppUser,
    ApiSessionConfig,
)

__all__ = [
    "Base",
    "TelegramChannel",
    "TelegramUser",
    "TelegramVideo",
    "TelegramAudio",
    "AppUser",
    "ApiSessionConfig",
]