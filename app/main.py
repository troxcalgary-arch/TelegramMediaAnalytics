"""FastAPI application with JWT Auth + persistent sessions (Phase 1)."""

import os
import logging
from contextlib import asynccontextmanager
from datetime import timedelta, timezone as dt_timezone

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session as DBSessionBase  # type alias for clarity

# --- Imports from local modules ---
from app.models import Base  # shared SQLAlchemy setup (TelegramChannel etc.)
from app.database import engine, get_db
from app.routers import telegram
from app.models.auth_models import (
    AppUser, ApiSessionConfig,
    create_access_token, verify_password, create_hash, get_current_user, oauth2_scheme
)

# Configure logging
import logging.handlers
from pathlib import Path
log_dir = Path(__file__).parent.parent / "logs"
log_dir.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.handlers.RotatingFileHandler(
            log_dir / "app.log",
            maxBytes=5_000_000,
            backupCount=3,
            encoding="utf-8"
        )
    ]
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# App config (JWT + static files / templates). -------------------------------------------------

app = FastAPI(title="PlaceOfPower - Telegram Media Analytics", version="0.2.0-alpha-phase1-jwt-auth")

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/telegram/api/token")

# Подключаем роутеры
app.include_router(telegram.router, prefix="/telegram", tags=["telegram"])

# Статические файлы и шаблоны
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")

# ---------------------------------------------------------------------------
# Database initialization — run once at startup to ensure all tables exist.
def init_db():
    """Create any missing tables in the SQLite database."""
    Base.metadata.create_all(bind=engine, checkfirst=True)

@app.on_event("startup")
async def on_startup():
    logger.info("[Phase1] Running DB initialization...")
    init_db()