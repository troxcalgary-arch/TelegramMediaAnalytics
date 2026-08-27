"""Telegram Analytics Router — JWT Auth + Background Tasks + Paginated Results."""

from typing import Optional, List, Dict, Any
import asyncio
import uuid
import os
import json
import logging
import re
import time
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Form, HTTPException, Depends, Request, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.security import OAuth2PasswordRequestForm, OAuth2PasswordBearer
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.auth_models import (
    AppUser, ApiSessionConfig,
    create_access_token, verify_password, create_hash,
    get_current_user, oauth2_scheme
)
from app.services.telegram_service import DownloadCancelled, TelegramService
from app.services.session_manager import SessionBusyError, telegram_session_manager

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

# Setup logging
logger = logging.getLogger(__name__)

# In-memory task storage (replace with Redis/DB in production)
scan_tasks: Dict[str, Dict[str, Any]] = {}

# Store temporary auth sessions (in production use Redis/DB)
auth_sessions: Dict[str, Dict] = {}


def _positive_seconds(name: str, default: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
        return value if value > 0 else default
    except ValueError:
        return default


AUTH_OPERATION_TIMEOUT_SECONDS = _positive_seconds("TG_AUTH_TIMEOUT_SECONDS", 60.0)
AUTH_SESSION_TTL_SECONDS = _positive_seconds("TG_AUTH_SESSION_TTL_SECONDS", 600.0)


def _session_name_for(phone: str, configured: Optional[str] = None) -> str:
    return configured or f"auth_{phone.replace('+', '').replace('-', '_')}"


async def _release_auth_session(session_id: str) -> None:
    session = auth_sessions.pop(session_id, None)
    if session:
        await telegram_session_manager.release(session["session_name"], session["owner"])


async def cleanup_expired_auth_sessions() -> int:
    """Disconnect and remove incomplete auth flows that exceeded their TTL."""
    cutoff = time.monotonic() - AUTH_SESSION_TTL_SECONDS
    expired = [
        session_id
        for session_id, session in list(auth_sessions.items())
        if session.get("last_activity", session.get("created_at", 0)) < cutoff
    ]
    for session_id in expired:
        session = auth_sessions.get(session_id, {})
        logger.warning(
            "Expiring Telegram auth session=%s owner=%s pid=%s",
            session.get("session_name"),
            session.get("owner"),
            os.getpid(),
        )
        await _release_auth_session(session_id)
    return len(expired)


async def auth_session_cleanup_loop() -> None:
    """Periodically clean stale multi-request authentication flows."""
    interval = max(5.0, min(60.0, AUTH_SESSION_TTL_SECONDS / 2))
    while True:
        await asyncio.sleep(interval)
        await cleanup_expired_auth_sessions()


async def shutdown_telegram_sessions() -> None:
    for session_id in list(auth_sessions):
        await _release_auth_session(session_id)
    await telegram_session_manager.close_all()

def _make_task_id() -> str:
    return uuid.uuid4().hex[:12]

# Path to metadata JSON files — use project root (where main.py is)
METADATA_DIR = Path(__file__).resolve().parent.parent.parent

# Scan history file
HISTORY_FILE = METADATA_DIR / "scan_history.json"


# ---------- Helper: Save/Load results per channel ----------
RESULTS_DIR = METADATA_DIR / "scan_results"
RESULTS_DIR.mkdir(exist_ok=True)


def get_result_file(channel_id: str) -> Path:
    """Get result file path for a channel."""
    safe_id = channel_id.replace('@', '').replace('-', '').replace('/', '_')
    return RESULTS_DIR / f"results_{safe_id}.json"


def save_scan_result(channel_id: str, result: Dict[str, Any]) -> None:
    """Save scan result for a channel."""
    result["channel_id"] = channel_id
    result["saved_at"] = datetime.utcnow().isoformat() + "Z"
    safe_id = channel_id.replace("@", "at_").replace("/", "_").replace("-", "")
    result_file = RESULTS_DIR / f"results_{safe_id}.json"
    try:
        result_file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning(f"Failed to save scan result: {e}")


def load_scan_result(channel_id: str) -> Optional[Dict[str, Any]]:
    """Load saved scan result for a specific channel."""
    safe_id = channel_id.replace("@", "at_").replace("/", "_").replace("-", "")
    result_file = RESULTS_DIR / f"results_{safe_id}.json"
    if result_file.exists():
        try:
            return json.loads(result_file.read_text(encoding="utf-8"))
        except Exception:
            pass
    return None


def load_all_results() -> List[Dict[str, Any]]:
    """Load all saved scan results."""
    results = []
    for file in RESULTS_DIR.glob("results_*.json"):
        try:
            data = json.loads(file.read_text())
            if "channel_id" in data:
                results.append(data)
        except:
            pass
    return results


def _is_video_message(message: Dict[str, Any]) -> bool:
    """Return True when a serialized media message is an actual video."""
    mime_type = (message.get("video") or {}).get("mime_type") or ""
    return mime_type.startswith("video/")


def _build_video_author_stats(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Build legacy author stats from already-scanned video messages."""
    stats: Dict[int, Dict[str, Any]] = {}
    for message in messages:
        sid = message.get("sender_id")
        if not sid:
            continue

        sender = message.get("sender") or {}
        if sender.get("username"):
            name = f"@{sender['username']}"
        else:
            name = " ".join(
                part for part in [sender.get("first_name"), sender.get("last_name")] if part
            ) or f"ID:{sid}"

        if sid not in stats:
            stats[sid] = {
                "user_id": sid,
                "name": name,
                "video_count": 0,
                "last_date": 0,
            }

        stats[sid]["video_count"] += 1
        stats[sid]["last_date"] = max(stats[sid]["last_date"], message.get("date_unix") or 0)

    return sorted(stats.values(), key=lambda item: item["video_count"], reverse=True)


def _write_selected_message_metadata(real_msg, file_path: str, file_name: str, topic_id: Optional[int]) -> None:
    """Write a Markdown sidecar file next to a selected downloaded media file."""
    sender = getattr(real_msg, "sender", None)
    sender_id = real_msg.sender_id
    username = getattr(sender, "username", None) if sender else None
    first_name = getattr(sender, "first_name", None) if sender else None
    last_name = getattr(sender, "last_name", None) if sender else None
    media = getattr(real_msg, "file", None)
    mime = getattr(media, "mime_type", None) or ""
    size = getattr(media, "size", None) or 0
    document = getattr(getattr(real_msg, "media", None), "document", None)
    photo = getattr(real_msg, "photo", None)
    file_id = getattr(document, "id", None) or getattr(photo, "id", None) or "N/A"

    md_path = os.path.splitext(file_path)[0] + ".md"
    meta_lines = [
        f"# Metadata: {file_name}",
        "",
        f"- **Message ID**: {real_msg.id}",
        f"- **Sender ID**: {sender_id}",
        f"- **Username**: @{username}" if username else "- **Username**: N/A",
        f"- **Name**: {((first_name or '') + ' ' + (last_name or '')).strip()}",
        f"- **Date**: {real_msg.date.isoformat() if real_msg.date else 'N/A'}",
        f"- **Caption**: {real_msg.text or ''}",
        f"- **MIME**: {mime}",
        f"- **Size**: {size} bytes",
        f"- **File ID**: {file_id}",
        f"- **Topic ID**: {topic_id or 'N/A'}",
    ]

    if real_msg.video:
        v = real_msg.video
        if getattr(v, "duration", None): meta_lines.append(f"- **Duration**: {v.duration}s")
        if getattr(v, "width", None) and getattr(v, "height", None):
            meta_lines.append(f"- **Resolution**: {v.width}x{v.height}")
    if real_msg.audio:
        a = real_msg.audio
        if getattr(a, "duration", None): meta_lines.append(f"- **Duration**: {a.duration}s")
        if getattr(a, "title", None): meta_lines.append(f"- **Title**: {a.title}")
        if getattr(a, "performer", None): meta_lines.append(f"- **Performer**: {a.performer}")
    if real_msg.document:
        for attr in getattr(real_msg.document, "attributes", []) or []:
            if hasattr(attr, "file_name") and attr.file_name:
                meta_lines.append(f"- **Original filename**: {attr.file_name}")
                break

    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(meta_lines) + "\n")


def _safe_folder_name(value: Any, fallback: str) -> str:
    """Return a Windows-safe folder name with a stable fallback."""
    safe = re.sub(r'[<>:"/\\|?*]', '_', str(value or fallback)).strip().strip('.')
    safe = re.sub(r'\s+', ' ', safe).strip()[:120].rstrip()
    return safe or fallback


async def _resolve_topic_folder_name(service: TelegramService, channel, topic_id: Optional[int]) -> Optional[str]:
    """Resolve forum topic title when possible; fall back to the topic ID."""
    if topic_id is None:
        return None

    fallback = f"Topic {topic_id}"
    try:
        from telethon.tl.functions.messages import GetForumTopicsByIDRequest

        result = await service.client(GetForumTopicsByIDRequest(peer=channel, topics=[topic_id]))
        topics = getattr(result, "topics", None) or []
        for topic in topics:
            if getattr(topic, "id", None) == topic_id:
                return getattr(topic, "title", None) or fallback
    except Exception as e:
        logger.info(f"Could not resolve topic title for {topic_id}: {e}")

    return fallback


# ---------- Pydantic schemas ----------
class ConnectConfigPayload(BaseModel):
    api_id: int
    phone_number: Optional[str] = None


class ScanPayload(BaseModel):
    channel_id: str
    media_type: str = "video"  # video, audio, all
    days: Optional[int] = None
    limit: int = 1000
    start_date: Optional[str] = None  # YYYY-MM-DD
    end_date: Optional[str] = None    # YYYY-MM-DD


class DownloadPayload(BaseModel):
    channel_id: str
    media_type: str = "video"
    days: Optional[int] = None
    download_path: str
    limit: int = 1000
    # Anti-flood protection
    delay_min: float = 2.0  # Min delay between files (sec)
    delay_max: float = 5.0  # Max delay between files (sec)
    skip_existing: bool = True  # Skip existing files
    start_date: Optional[str] = None  # YYYY-MM-DD
    end_date: Optional[str] = None    # YYYY-MM-DD


class DownloadSelectedPayload(BaseModel):
    channel_id: str
    message_ids: List[int]
    download_path: str
    delay_min: float = 2.0
    delay_max: float = 5.0
    skip_existing: bool = True


# ---------- Helper: Load .env for prefill ----------
def get_env_config() -> Dict[str, Any]:
    """Read .env for prefill values."""
    env_path = METADATA_DIR / ".env"
    config = {"api_id": "", "api_hash": "", "phone": ""}
    logger.info(f"[Config] Looking for .env at: {env_path}")
    logger.info(f"[Config] METADATA_DIR: {METADATA_DIR}")
    logger.info(f"[Config] .env exists: {env_path.exists()}")
    if env_path.exists():
        try:
            content = env_path.read_text(encoding="utf-8-sig")  # utf-8-sig handles BOM
            logger.info(f"[Config] .env file size: {len(content)} bytes")
            for line in content.splitlines():
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    k, v = line.split('=', 1)
                    k = k.strip()
                    v = v.strip().strip('"').strip("'")  # Remove quotes too
                    logger.info(f"[Config] Found key={k}, value_len={len(v)}")
                    if k == "TG_API_ID" and v and v != "your_api_id_here":
                        try:
                            config["api_id"] = int(v)
                            logger.info(f"[Config] Set api_id={config['api_id']}")
                        except ValueError:
                            config["api_id"] = v
                            logger.info(f"[Config] Set api_id (str)={config['api_id']}")
                    elif k == "TG_API_HASH" and v and v != "your_api_hash_here":
                        config["api_hash"] = v
                        logger.info(f"[Config] Set api_hash (len={len(v)})")
                    elif k == "TG_PHONE" and v and v != "your_phone_number_here":
                        config["phone"] = v
                        logger.info(f"[Config] Set phone={v[:5]}...")
        except Exception as e:
            logger.error(f"[Config] Failed to read .env: {e}")
    else:
        logger.warning(f"[Config] .env file NOT found at {env_path}")
    logger.info(f"[Config] Final config: api_id={config['api_id']}, api_hash_len={len(config['api_hash'])}, phone={config['phone'][:5] if config['phone'] else ''}")

    # Default download path: Downloads folder in app directory
    default_download = METADATA_DIR / "Downloads"
    default_download.mkdir(exist_ok=True)
    config["download_path"] = str(default_download)

    return config


# ---------- Helper: Load video metadata ----------
async def load_video_metadata(chat_id: int = -1001911644885, topic_id: Optional[int] = None) -> List[Dict]:
    """Load video metadata from JSON files.

    For full channel scan (topic_id=None): prefer chat_video_metadata.json (all topics)
    For specific topic scan: use topic_video_metadata.json
    """
    videos = []

    if topic_id is not None:
        # Specific topic requested - use topic file
        topic_file = METADATA_DIR / "topic_video_metadata.json"
        if topic_file.exists():
            try:
                data = json.loads(topic_file.read_text())
                videos = data.get("videos", [])
                # Filter by topic_id if needed
                if videos:
                    videos = [v for v in videos if v.get("topic_id") == topic_id]
            except:
                pass

    # For full channel or if topic file failed/empty - use chat file (all topics)
    if not videos:
        chat_file = METADATA_DIR / "chat_video_metadata.json"
        if chat_file.exists():
            try:
                data = json.loads(chat_file.read_text())
                videos = data.get("videos", [])
            except:
                pass

    return videos


# ---------- Auth endpoints ----------
@router.post("/api/token", summary="Login → JWT token")
async def login_for_access_token(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db)
):
    user = db.query(AppUser).filter(AppUser.username == form_data.username).first()
    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=401,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not user.is_active:
        raise HTTPException(status_code=400, detail="Inactive user")

    access_token = create_access_token(data={"sub": user.username})
    return {"access_token": access_token, "token_type": "bearer"}


@router.post("/api/register", summary="Register new web user")
async def register_user(
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db)
):
    if db.query(AppUser).filter(AppUser.username == username).first():
        raise HTTPException(status_code=400, detail="Username already exists")

    user = AppUser(username=username, hashed_password=create_hash(password))
    db.add(user)
    db.commit()
    db.refresh(user)
    return {"msg": "User created", "user_id": user.id}


# ---------- Telegram session config (persisted per user) ----------
@router.post("/api/session", summary="Save Telegram API credentials for current user")
async def save_session_config(
    payload: ConnectConfigPayload,
    current_user: AppUser = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    cfg = db.query(ApiSessionConfig).filter(ApiSessionConfig.user_id == current_user.id).first()
    if cfg:
        cfg.api_id = payload.api_id
        cfg.phone_number = payload.phone_number
    else:
        cfg = ApiSessionConfig(
            user_id=current_user.id,
            api_id=payload.api_id,
            phone_number=payload.phone_number
        )
        db.add(cfg)
    db.commit()
    return {"msg": "Session config saved"}


@router.get("/api/session", summary="Get saved Telegram API credentials")
async def get_session_config(
    current_user: AppUser = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    cfg = db.query(ApiSessionConfig).filter(ApiSessionConfig.user_id == current_user.id).first()
    if not cfg:
        raise HTTPException(status_code=404, detail="No session config found")
    return {"api_id": cfg.api_id, "phone_number": cfg.phone_number}


# ---------- Auth endpoints ----------

# ---------- Config prefill endpoint ----------
@router.get("/api/config", summary="Get .env config for form prefill")
async def get_env_config_endpoint():
    """Returns .env values for pre-filling the connection form."""
    logger.info("[API] GET /api/config called")
    result = get_env_config()
    logger.info(f"[API] Returning config: api_id={result['api_id']}, api_hash_len={len(result['api_hash'])}, phone={result['phone'][:5] if result['phone'] else ''}")
    return result


# ---------- Auth endpoints ----------

@router.post("/api/auth/connect", summary="Start Telegram authentication (send code)")
async def auth_connect(
    api_id: int = Form(...),
    api_hash: str = Form(...),
    phone: str = Form(...),
):
    """Start Telegram auth: connect client and send code."""
    await cleanup_expired_auth_sessions()
    session_name = _session_name_for(phone)
    session_id = _make_task_id()
    owner = f"auth:{session_id}"
    try:
        service = await telegram_session_manager.reserve(
            session_name, api_id, api_hash, phone, owner
        )
    except SessionBusyError as exc:
        raise HTTPException(409, "Session is busy. Wait for the active Telegram operation to finish.") from exc

    try:
        await asyncio.wait_for(service.connect(), timeout=AUTH_OPERATION_TIMEOUT_SECONDS)
        sent = await asyncio.wait_for(
            service.client.send_code_request(phone),
            timeout=AUTH_OPERATION_TIMEOUT_SECONDS,
        )
        now = time.monotonic()
        auth_sessions[session_id] = {
            "service": service,
            "session_name": session_name,
            "owner": owner,
            "api_id": api_id,
            "api_hash": api_hash,
            "phone": phone,
            "phone_code_hash": sent.phone_code_hash,
            "created_at": now,
            "last_activity": now,
        }
        return {"session_id": session_id, "message": "Code sent to Telegram"}
    except Exception as e:
        logger.exception(
            "Telegram auth connect failed session=%s owner=%s pid=%s",
            session_name,
            owner,
            os.getpid(),
        )
        await telegram_session_manager.release(session_name, owner)
        raise HTTPException(400, f"Failed to send code: {str(e)}")


@router.post("/api/auth/verify-code", summary="Verify authentication code")
async def auth_verify_code(
    session_id: str = Form(...),
    code: str = Form(...),
):
    """Verify the code sent to Telegram."""
    await cleanup_expired_auth_sessions()
    session = auth_sessions.get(session_id)
    if not session:
        raise HTTPException(404, "Session not found or expired")

    service = session["service"]
    session["last_activity"] = time.monotonic()
    try:
        await asyncio.wait_for(
            service.client.sign_in(
                phone=session["phone"],
                code=code,
                phone_code_hash=session["phone_code_hash"],
            ),
            timeout=AUTH_OPERATION_TIMEOUT_SECONDS,
        )

        # Check if 2FA is needed
        me = await asyncio.wait_for(
            service.client.get_me(), timeout=AUTH_OPERATION_TIMEOUT_SECONDS
        )

        # Save session config for this user (simplified - in prod associate with web user)
        # For now, just mark as authenticated
        auth_sessions[session_id]["authenticated"] = True
        auth_sessions[session_id]["user_id"] = me.id
        auth_sessions[session_id]["tg_user"] = {
            "id": me.id,
            "username": me.username,
            "first_name": me.first_name,
            "last_name": me.last_name,
            "phone": me.phone
        }

        return {"message": "Authenticated successfully", "user_id": me.id, "need_2fa": False}
    except Exception as e:
        if "TwoFactorAuth" in str(type(e)) or "password" in str(e).lower():
            # 2FA required
            return {"need_2fa": True, "message": "Two-factor authentication required"}
        logger.exception(
            "Telegram auth code verification failed session=%s owner=%s pid=%s",
            session["session_name"],
            session["owner"],
            os.getpid(),
        )
        raise HTTPException(400, f"Invalid code: {str(e)}")


@router.post("/api/auth/verify-2fa", summary="Verify 2FA password")
async def auth_verify_2fa(
    session_id: str = Form(...),
    password: str = Form(...),
):
    """Verify 2FA password."""
    await cleanup_expired_auth_sessions()
    session = auth_sessions.get(session_id)
    if not session:
        raise HTTPException(404, "Session not found or expired")

    service = session["service"]
    session["last_activity"] = time.monotonic()
    try:
        await asyncio.wait_for(
            service.client.sign_in(password=password),
            timeout=AUTH_OPERATION_TIMEOUT_SECONDS,
        )

        me = await asyncio.wait_for(
            service.client.get_me(), timeout=AUTH_OPERATION_TIMEOUT_SECONDS
        )
        auth_sessions[session_id]["authenticated"] = True
        auth_sessions[session_id]["user_id"] = me.id
        auth_sessions[session_id]["tg_user"] = {
            "id": me.id,
            "username": me.username,
            "first_name": me.first_name,
            "last_name": me.last_name,
            "phone": me.phone
        }

        return {"message": "2FA verified, authenticated successfully", "user_id": me.id, "need_2fa": False}
    except Exception as e:
        logger.exception(
            "Telegram auth 2FA verification failed session=%s owner=%s pid=%s",
            session["session_name"],
            session["owner"],
            os.getpid(),
        )
        raise HTTPException(400, f"Invalid 2FA password: {str(e)}")


@router.get("/api/auth/status", summary="Check authentication status")
async def auth_status(session_id: Optional[str] = Query(None)):
    """Check if there's an active authenticated session."""
    await cleanup_expired_auth_sessions()
    if not session_id:
        return {"authenticated": False}

    session = auth_sessions.get(session_id)
    if not session:
        return {"authenticated": False}

    return {
        "authenticated": session.get("authenticated", False),
        "user_id": session.get("user_id")
    }


@router.post("/api/auth/logout", summary="Logout and cleanup")
async def auth_logout(session_id: str = Form(...)):
    """Logout and cleanup session."""
    await _release_auth_session(session_id)
    return {"message": "Logged out"}


@router.post("/api/auth/save-session", summary="Save authenticated session to user config and get JWT")
async def auth_save_session(
    session_id: str = Form(...),
    current_user: AppUser = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Save authenticated Telegram session to user's config and return JWT."""
    await cleanup_expired_auth_sessions()
    session = auth_sessions.get(session_id)
    if not session or not session.get("authenticated"):
        raise HTTPException(400, "No authenticated session to save")

    try:
        cfg = db.query(ApiSessionConfig).filter(ApiSessionConfig.user_id == current_user.id).first()
        if cfg:
            cfg.api_id = session["api_id"]
            cfg.phone_number = session["phone"]
            cfg.session_name = session["session_name"]
        else:
            cfg = ApiSessionConfig(
                user_id=current_user.id,
                api_id=session["api_id"],
                phone_number=session["phone"],
                session_name=session["session_name"],
            )
            db.add(cfg)
        db.commit()
        access_token = create_access_token(data={"sub": current_user.username})
        return {"access_token": access_token, "token_type": "bearer", "message": "Session saved successfully"}
    finally:
        await _release_auth_session(session_id)


@router.post("/api/auth/auto-login", summary="Auto-login after Telegram auth - creates user if needed and returns JWT")
async def auth_auto_login(
    session_id: str = Form(...),
    db: Session = Depends(get_db)
):
    """After Telegram auth, auto-create/login web user and return JWT token."""
    await cleanup_expired_auth_sessions()
    session = auth_sessions.get(session_id)
    if not session or not session.get("authenticated"):
        raise HTTPException(400, "No authenticated session")

    try:
        tg_user = session.get("tg_user")
        if not tg_user:
            raise HTTPException(400, "Telegram user info not available")

        username = f"tg_{tg_user['id']}"
        user = db.query(AppUser).filter(AppUser.username == username).first()
        if not user:
            import secrets

            password = secrets.token_urlsafe(32)
            user = AppUser(username=username, hashed_password=create_hash(password))
            db.add(user)
            db.commit()
            db.refresh(user)

        cfg = db.query(ApiSessionConfig).filter(ApiSessionConfig.user_id == user.id).first()
        if cfg:
            cfg.api_id = session["api_id"]
            cfg.phone_number = session["phone"]
            cfg.session_name = session["session_name"]
        else:
            cfg = ApiSessionConfig(
                user_id=user.id,
                api_id=session["api_id"],
                phone_number=session["phone"],
                session_name=session["session_name"],
            )
            db.add(cfg)
        db.commit()

        access_token = create_access_token(data={"sub": user.username})
        return {"access_token": access_token, "token_type": "bearer", "message": "Logged in successfully"}
    finally:
        await _release_auth_session(session_id)


# ---------- Paginated video results ----------
@router.get("/api/videos", summary="Get paginated video metadata")
async def get_videos(
    page: int = Query(1, ge=1, description="Page number (1-based)"),
    per_page: int = Query(50, ge=1, le=200, description="Items per page"),
    topic_id: Optional[int] = Query(None, description="Filter by topic ID"),
    sender_id: Optional[int] = Query(None, description="Filter by sender ID"),
    username: Optional[str] = Query(None, description="Filter by username (without @)"),
    date_from: Optional[str] = Query(None, description="Filter from date (YYYY-MM-DD)"),
    date_to: Optional[str] = Query(None, description="Filter to date (YYYY-MM-DD)"),
    sort_by: Optional[str] = Query("date_desc", description="Sort: date_desc, date_asc, duration_desc, duration_asc, size_desc, size_asc"),
    channel_id: str = Query(-1001911644885, description="Channel ID or @username"),
):
    """Returns paginated video metadata from saved scan results for a specific channel."""
    result = load_scan_result(str(channel_id))

    if not result:
        # Fallback to old metadata files - for full channel, use chat file
        chat_id = int(channel_id) if str(channel_id).lstrip('-').isdigit() else -1001911644885
        videos = await load_video_metadata(chat_id, topic_id=None)
    else:
        videos = result.get("messages", [])

    # Apply filters
    if topic_id:
        videos = [v for v in videos if v.get("topic_id") == topic_id]
    if sender_id:
        videos = [v for v in videos if v.get("sender_id") == sender_id]
    if username:
        username_lower = username.lower().lstrip('@')
        videos = [v for v in videos if (v.get("sender") or {}).get("username") and (v.get("sender") or {}).get("username", "").lower() == username_lower]

    # Date filters
    if date_from:
        try:
            from_ts = int(datetime.strptime(date_from, "%Y-%m-%d").timestamp())
            videos = [v for v in videos if v.get("date_unix", 0) >= from_ts]
        except:
            pass
    if date_to:
        try:
            to_ts = int(datetime.strptime(date_to, "%Y-%m-%d").timestamp()) + 86399  # end of day
            videos = [v for v in videos if v.get("date_unix", 0) <= to_ts]
        except:
            pass

    # Sort
    sort_key = None
    reverse = True
    if sort_by == "date_desc":
        sort_key = lambda v: v.get("date_unix", 0)
        reverse = True
    elif sort_by == "date_asc":
        sort_key = lambda v: v.get("date_unix", 0)
        reverse = False
    elif sort_by == "duration_desc":
        sort_key = lambda v: v.get("video", {}).get("attributes", {}).get("video", {}).get("duration", 0)
        reverse = True
    elif sort_by == "duration_asc":
        sort_key = lambda v: v.get("video", {}).get("attributes", {}).get("video", {}).get("duration", 0)
        reverse = False
    elif sort_by == "size_desc":
        sort_key = lambda v: v.get("video", {}).get("size", 0)
        reverse = True
    elif sort_by == "size_asc":
        sort_key = lambda v: v.get("video", {}).get("size", 0)
        reverse = False

    if sort_key:
        videos.sort(key=sort_key, reverse=reverse)
    else:
        # Default: date descending
        videos.sort(key=lambda v: v.get("date_unix", 0), reverse=True)

    total = len(videos)
    total_pages = (total + per_page - 1) // per_page

    start = (page - 1) * per_page
    end = start + per_page
    page_videos = videos[start:end]

    return {
        "page": page,
        "per_page": per_page,
        "total": total,
        "total_pages": total_pages,
        "start": start,
        "channel_id": channel_id,
        "videos": page_videos
    }


@router.get("/api/videos/stats", summary="Get video statistics summary")
async def get_videos_stats(
    channel_id: str = Query(-1001911644885, description="Channel ID or @username"),
    page: int = Query(1, ge=1, description="Page number (1-based)"),
    per_page: int = Query(50, ge=1, le=200, description="Items per page"),
    username: Optional[str] = Query(None, description="Filter by username (without @)"),
    sort_by: Optional[str] = Query("count_desc", description="Sort: count_desc, count_asc, size_desc, size_asc, date_desc, date_asc"),
):
    """Returns aggregated statistics by author for a specific channel with pagination."""
    result = load_scan_result(str(channel_id))

    media_type = "video"
    if not result:
        # Fallback to old metadata files - for full channel, use chat file
        chat_id = int(channel_id) if str(channel_id).lstrip('-').isdigit() else -1001911644885
        videos = await load_video_metadata(chat_id, topic_id=None)
    else:
        media_type = result.get("media_type") or "video"
        videos = result.get("messages", [])
        if "media_type" not in result and any(not _is_video_message(v) for v in videos):
            media_type = "all"

    if media_type == "video":
        videos = [v for v in videos if _is_video_message(v)]
    video_count = sum(1 for v in videos if _is_video_message(v))

    stats = {}
    for v in videos:
        sid = v.get("sender_id")
        if not sid:
            continue
        sender = v.get("sender") or {}
        if sid not in stats:
            stats[sid] = {
                "sender_id": sid,
                "username": sender.get("username"),
                "first_name": sender.get("first_name"),
                "last_name": sender.get("last_name"),
                "count": 0,
                "total_size": 0,
                "last_date": None,
            }
        stats[sid]["count"] += 1
        stats[sid]["total_size"] += v.get("video", {}).get("size", 0)
        date_unix = v.get("date_unix")
        if date_unix and (stats[sid]["last_date"] is None or date_unix > stats[sid]["last_date"]):
            stats[sid]["last_date"] = date_unix

    # Apply username filter
    if username:
        username_lower = username.lower().lstrip('@')
        stats = {sid: s for sid, s in stats.items() if s.get("username") and s.get("username", "").lower() == username_lower}

    # Convert to list and sort
    sort_key = None
    reverse = True
    if sort_by == "count_desc":
        sort_key = lambda x: x["count"]
        reverse = True
    elif sort_by == "count_asc":
        sort_key = lambda x: x["count"]
        reverse = False
    elif sort_by == "size_desc":
        sort_key = lambda x: x["total_size"]
        reverse = True
    elif sort_by == "size_asc":
        sort_key = lambda x: x["total_size"]
        reverse = False
    elif sort_by == "date_desc":
        sort_key = lambda x: x["last_date"] or 0
        reverse = True
    elif sort_by == "date_asc":
        sort_key = lambda x: x["last_date"] or 0
        reverse = False

    sorted_stats = sorted(stats.values(), key=sort_key, reverse=reverse)

    total = len(sorted_stats)
    total_pages = (total + per_page - 1) // per_page

    start = (page - 1) * per_page
    end = start + per_page
    page_stats = sorted_stats[start:end]

    return {
        "total_videos": video_count,
        "total_media": len(videos),
        "media_type": media_type,
        "unique_authors": len(stats),
        "channel_id": channel_id,
        "page": page,
        "per_page": per_page,
        "total": total,
        "total_pages": total_pages,
        "start": start,
        "authors": page_stats
    }


async def _run_scan_task(task_id: str, service: TelegramService, session_name: str, owner: str,
                         channel_id: str, media_type: str, days: Optional[int], limit: int,
                         start_date: Optional[str] = None, end_date: Optional[str] = None):
    """Background scan task — updates scan_tasks dict with progress/result."""
    scan_tasks[task_id] = {"status": "running", "progress": 0, "message": "Connecting..."}
    logger.info(f"[Task {task_id}] Starting scan for channel {channel_id}, session={session_name}")
    logger.info(f"[Task {task_id}] Date params: days={days}, start_date={start_date}, end_date={end_date}")
    try:
        await service.connect()
        
        # Check if session is authorized
        if not await service.is_authorized():
            logger.error(f"[Task {task_id}] Session not authorized for channel {channel_id}")
            scan_tasks[task_id] = {"status": "error", "message": "Session not authorized. Please re-authenticate via /telegram/web (code/2FA)."}
            return
            
        logger.info(f"[Task {task_id}] Session authorized, fetching channel...")
        scan_tasks[task_id]["message"] = "Connected. Fetching channel..."
        channel, topic_id = await service.get_channel(channel_id)

        logger.info(f"[Task {task_id}] Channel found, scanning messages...")
        scan_tasks[task_id]["message"] = "Scanning messages..."

        def scan_progress(scanned: int, matched: int):
            scan_tasks[task_id]["message"] = f"Scanned {scanned} messages, found {matched}..."
            scan_tasks[task_id]["progress"] = 0  # unknown total

        messages = await service.get_messages_with_media(
            channel, filter_type=media_type, days=days, limit=limit, topic_id=topic_id,
            progress_callback=scan_progress,
            start_date=start_date, end_date=end_date
        )

        if media_type == "video":
            stats = _build_video_author_stats(messages)
            result = {
                "status": "completed",
                "media_type": media_type,
                "stats": stats,
                "message": f"Found {len(messages)} videos from {len(stats)} authors",
                "messages": messages
            }
        else:
            result = {
                "status": "completed",
                "media_type": media_type,
                "messages_count": len(messages),
                "message": f"Found {len(messages)} {media_type} messages",
                "messages": messages
            }

        # Save result to channel-specific file
        save_scan_result(channel_id, result)
        scan_tasks[task_id] = result

        # Add to scan history
        _add_to_history({
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "channel_id": channel_id,
            "channel_title": getattr(channel, "title", None) or channel_id,
            "media_type": media_type,
            "total_messages": len(messages),
            "unique_authors": len(set(m.get("sender_id") for m in messages if m.get("sender_id"))),
            "topic_id": topic_id,
            "message": result.get("message", ""),
        })

        logger.info(f"[Task {task_id}] Scan completed: {result.get('message', 'done')}")
    except Exception as e:
        logger.exception(f"[Task {task_id}] Scan error: {e}")
        scan_tasks[task_id] = {"status": "error", "message": str(e)}
    finally:
        await telegram_session_manager.release(session_name, owner)


async def _run_download_task(task_id: str, service: TelegramService, session_name: str, owner: str,
                             channel_id: str, media_type: str, days: Optional[int],
                             download_path: str, limit: int, delay_min: float = 2.0, delay_max: float = 5.0, skip_existing: bool = True,
                             start_date: Optional[str] = None, end_date: Optional[str] = None):
    scan_tasks[task_id] = {"status": "running", "progress": 0, "message": "Preparing download...", "cancel_requested": False}
    logger.info(f"[Task {task_id}] Starting download for channel {channel_id}, session={session_name}")
    try:
        await service.connect()
        
        # Check if session is authorized
        if not await service.is_authorized():
            logger.error(f"[Task {task_id}] Session not authorized for channel {channel_id}")
            scan_tasks[task_id] = {"status": "error", "message": "Session not authorized. Please re-authenticate via /telegram/web (code/2FA)."}
            return
            
        logger.info(f"[Task {task_id}] Session authorized, fetching channel...")
        channel, topic_id = await service.get_channel(channel_id)

        channel_title = getattr(channel, "title", None) or str(channel_id)
        safe_title = _safe_folder_name(channel_title, str(channel_id))
        channel_dir = os.path.join(download_path, safe_title)
        topic_title = await _resolve_topic_folder_name(service, channel, topic_id)
        safe_topic_title = _safe_folder_name(topic_title, f"Topic {topic_id}") if topic_title else None
        download_dir = os.path.join(channel_dir, safe_topic_title) if safe_topic_title else channel_dir
        os.makedirs(download_dir, exist_ok=True)
        display_dir = f"{safe_title}/{safe_topic_title}/" if safe_topic_title else f"{safe_title}/"

        # Store channel info in task for active-task tracking
        scan_tasks[task_id]["channel_id"] = channel_id
        scan_tasks[task_id]["channel_title"] = channel_title
        scan_tasks[task_id]["topic_id"] = topic_id
        if topic_title:
            scan_tasks[task_id]["topic_title"] = topic_title

        messages = await service.get_messages_with_media(
            channel, filter_type=media_type, days=days, limit=limit,
            topic_id=topic_id,
            start_date=start_date, end_date=end_date
        )

        def progress_cb(done: int, total: int):
            if scan_tasks.get(task_id, {}).get("cancel_requested"):
                raise DownloadCancelled()
            scan_tasks[task_id]["progress"] = int(done / total * 100) if total else 0
            scan_tasks[task_id]["message"] = f"Downloading {done}/{total}..."

        result = await service.download_all_media(
            messages, download_dir, channel=channel, progress_callback=progress_cb,
            delay_range=(delay_min, delay_max),
            skip_existing=skip_existing,
            cancel_callback=lambda: scan_tasks.get(task_id, {}).get("cancel_requested", False)
        )
        if result.get("cancelled"):
            scan_tasks[task_id] = {
                "status": "cancelled",
                "downloaded": result["downloaded"],
                "skipped": result.get("skipped", 0),
                "errors": result["errors"],
                "path": download_dir,
                "message": f"Download stopped. Downloaded {result['downloaded']} files, skipped {result.get('skipped', 0)}, {result['errors']} errors"
            }
            return
        scan_tasks[task_id] = {
            "status": "completed",
            "downloaded": result["downloaded"],
            "skipped": result.get("skipped", 0),
            "errors": result["errors"],
            "path": download_dir,
            "message": f"Downloaded {result['downloaded']} files to {display_dir}, skipped {result.get('skipped', 0)}, {result['errors']} errors"
        }
        logger.info(f"[Task {task_id}] Download completed: {scan_tasks[task_id].get('message', 'done')}")
    except DownloadCancelled:
        logger.info(f"[Task {task_id}] Download cancelled by user")
        scan_tasks[task_id] = {"status": "cancelled", "message": "Download stopped by user"}
    except Exception as e:
        logger.exception(f"[Task {task_id}] Download error: {e}")
        scan_tasks[task_id] = {"status": "error", "message": str(e)}
    finally:
        await telegram_session_manager.release(session_name, owner)


# ---------- Main action endpoints ----------
@router.post("/api/scan", summary="Start channel scan (background)")
async def start_scan(
    background: BackgroundTasks,
    payload: ScanPayload,
    current_user: AppUser = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    cfg = db.query(ApiSessionConfig).filter(ApiSessionConfig.user_id == current_user.id).first()
    if not cfg or not cfg.api_id:
        raise HTTPException(400, "Telegram API credentials not configured. POST /api/session first.")

    api_hash = os.getenv("TG_API_HASH", "")
    phone = cfg.phone_number or os.getenv("TG_PHONE", "")
    if not api_hash or not phone:
        raise HTTPException(500, "TG_API_HASH or phone not set in .env")

    logger.info(f"[Scan] Payload received: channel_id={payload.channel_id}, start_date={payload.start_date}, end_date={payload.end_date}, days={payload.days}, limit={payload.limit}")
    logger.info(f"User {current_user.username} starting scan for channel {payload.channel_id}, session={cfg.session_name}")
    task_id = _make_task_id()
    owner = f"task:{task_id}"
    session_name = _session_name_for(phone, cfg.session_name)
    try:
        service = await telegram_session_manager.reserve(
            session_name, cfg.api_id, api_hash, phone, owner
        )
    except SessionBusyError as exc:
        raise HTTPException(409, "Session is busy. Wait for the active Telegram operation to finish.") from exc
    background.add_task(
        _run_scan_task, task_id, service, session_name, owner,
        payload.channel_id, payload.media_type, payload.days, payload.limit,
        payload.start_date, payload.end_date
    )
    return {"task_id": task_id, "status": "started"}


@router.post("/api/download", summary="Start media download (background)")
async def start_download(
    background: BackgroundTasks,
    payload: DownloadPayload,
    current_user: AppUser = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    cfg = db.query(ApiSessionConfig).filter(ApiSessionConfig.user_id == current_user.id).first()
    if not cfg or not cfg.api_id:
        raise HTTPException(400, "Telegram API credentials not configured. POST /api/session first.")

    api_hash = os.getenv("TG_API_HASH", "")
    phone = cfg.phone_number or os.getenv("TG_PHONE", "")
    if not api_hash or not phone:
        raise HTTPException(500, "TG_API_HASH or phone not set in .env")

    logger.info(f"User {current_user.username} starting download for channel {payload.channel_id}, session={cfg.session_name}")

    # Check if this channel is already being downloaded
    for tid, t in scan_tasks.items():
        if t.get("status") == "running" and t.get("channel_id") == payload.channel_id:
            raise HTTPException(
                409,
                detail=f"Download for channel '{t.get('channel_title', payload.channel_id)}' ({payload.channel_id}) is already running in background (Task ID: {tid})"
            )

    task_id = _make_task_id()
    owner = f"task:{task_id}"
    session_name = _session_name_for(phone, cfg.session_name)
    try:
        service = await telegram_session_manager.reserve(
            session_name, cfg.api_id, api_hash, phone, owner
        )
    except SessionBusyError as exc:
        raise HTTPException(409, "Session is busy. Wait for the active Telegram operation to finish.") from exc
    background.add_task(
        _run_download_task, task_id, service, session_name, owner,
        payload.channel_id, payload.media_type, payload.days,
        payload.download_path, payload.limit,
        payload.delay_min, payload.delay_max, payload.skip_existing,
        payload.start_date, payload.end_date
    )
    return {"task_id": task_id, "status": "started"}


@router.post("/api/download-selected", summary="Download selected files by message IDs")
async def start_download_selected(
    background: BackgroundTasks,
    payload: DownloadSelectedPayload,
    current_user: AppUser = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    cfg = db.query(ApiSessionConfig).filter(ApiSessionConfig.user_id == current_user.id).first()
    if not cfg or not cfg.api_id:
        raise HTTPException(400, "Telegram API credentials not configured.")

    api_hash = os.getenv("TG_API_HASH", "")
    phone = cfg.phone_number or os.getenv("TG_PHONE", "")
    if not api_hash or not phone:
        raise HTTPException(500, "TG_API_HASH or phone not set in .env")

    logger.info(f"User {current_user.username} starting selected download: {len(payload.message_ids)} files for channel {payload.channel_id}")

    # Check duplicate
    for tid, t in scan_tasks.items():
        if t.get("status") == "running" and t.get("channel_id") == payload.channel_id:
            raise HTTPException(409, detail=f"Download already in progress for this channel (Task ID: {tid})")

    task_id = _make_task_id()
    owner = f"task:{task_id}"
    session_name = _session_name_for(phone, cfg.session_name)
    try:
        service = await telegram_session_manager.reserve(
            session_name, cfg.api_id, api_hash, phone, owner
        )
    except SessionBusyError as exc:
        raise HTTPException(409, "Session is busy. Wait for the active Telegram operation to finish.") from exc
    background.add_task(
        _run_download_selected_task, task_id, service, session_name, owner,
        payload.channel_id, payload.message_ids, payload.download_path,
        payload.delay_min, payload.delay_max, payload.skip_existing,
    )
    return {"task_id": task_id, "status": "started"}


async def _run_download_selected_task(task_id: str, service: TelegramService, session_name: str, owner: str,
                                      channel_id: str, message_ids: List[int], download_path: str,
                                      delay_min: float = 2.0, delay_max: float = 5.0, skip_existing: bool = True):
    """Download specific files by message IDs."""
    import random
    from telethon.errors import FloodWaitError

    scan_tasks[task_id] = {
        "status": "running",
        "progress": 0,
        "message": "Connecting...",
        "channel_id": channel_id,
        "cancel_requested": False,
    }
    logger.info(f"[Task {task_id}] Starting selected download: {len(message_ids)} files")

    try:
        await service.connect()

        if not await service.is_authorized():
            scan_tasks[task_id] = {"status": "error", "message": "Session not authorized."}
            return

        channel, topic_id = await service.get_channel(channel_id)
        channel_title = getattr(channel, "title", None) or channel_id
        scan_tasks[task_id]["channel_title"] = channel_title

        safe_title = _safe_folder_name(channel_title, str(channel_id))
        channel_dir = os.path.join(download_path, safe_title)
        topic_title = await _resolve_topic_folder_name(service, channel, topic_id)
        safe_topic_title = _safe_folder_name(topic_title, f"Topic {topic_id}") if topic_title else None
        download_dir = os.path.join(channel_dir, safe_topic_title) if safe_topic_title else channel_dir
        os.makedirs(download_dir, exist_ok=True)
        display_dir = f"{safe_title}/{safe_topic_title}/" if safe_topic_title else f"{safe_title}/"
        scan_tasks[task_id]["topic_id"] = topic_id
        if topic_title:
            scan_tasks[task_id]["topic_title"] = topic_title

        selected_items = []
        missing_messages = 0
        scan_tasks[task_id]["message"] = "Preparing selected files..."
        for index, msg_id in enumerate(message_ids):
            if scan_tasks.get(task_id, {}).get("cancel_requested"):
                raise DownloadCancelled()
            real_msg = await service.client.get_messages(channel, ids=msg_id)
            if not real_msg:
                missing_messages += 1
            selected_items.append({
                "id": msg_id,
                "message": real_msg,
                "size": getattr(getattr(real_msg, "file", None), "size", None) if real_msg else 0,
            })
            scan_tasks[task_id]["message"] = f"Preparing {index + 1}/{len(message_ids)}..."

        total = len(message_ids)
        total_bytes = sum(item["size"] or 0 for item in selected_items)
        completed_bytes = 0
        downloaded = 0
        skipped = 0
        errors = missing_messages

        scan_tasks[task_id]["total_bytes"] = total_bytes
        scan_tasks[task_id]["downloaded_bytes"] = 0

        for i, item in enumerate(selected_items):
            if scan_tasks.get(task_id, {}).get("cancel_requested"):
                raise DownloadCancelled()

            msg_id = item["id"]
            real_msg = item["message"]
            media_size = item["size"] or 0
            file_path = None
            try:
                if not real_msg:
                    scan_tasks[task_id]["progress"] = int((i + 1) / total * 100)
                    scan_tasks[task_id]["message"] = f"Downloading {i + 1}/{total}... message not found"
                    continue

                # Determine extension
                if real_msg.video:
                    ext = ".mp4"
                elif real_msg.photo:
                    ext = ".jpg"
                elif real_msg.audio:
                    ext = ".mp3"
                elif real_msg.document:
                    ext = ".bin"
                    for attr in getattr(real_msg.document, "attributes", []) or []:
                        if hasattr(attr, "file_name") and attr.file_name:
                            ext = os.path.splitext(attr.file_name)[1] or ".bin"
                            break
                else:
                    ext = ".bin"

                # Resolve sender username for subfolder
                sender_id = real_msg.sender_id or 0
                username = str(sender_id)
                if real_msg.sender:
                    s = real_msg.sender
                    username = getattr(s, "username", None) or getattr(s, "first_name", None) or str(sender_id)
                safe_username = _safe_folder_name(username, str(sender_id))

                user_dir = os.path.join(download_dir, safe_username)
                os.makedirs(user_dir, exist_ok=True)

                file_name = f"{msg_id}_{sender_id}{ext}"
                file_path = os.path.join(user_dir, file_name)

                if skip_existing and os.path.exists(file_path):
                    existing_size = os.path.getsize(file_path)
                    if media_size and existing_size >= media_size:
                        md_path = os.path.splitext(file_path)[0] + ".md"
                        if not os.path.exists(md_path):
                            _write_selected_message_metadata(real_msg, file_path, file_name, topic_id)
                        skipped += 1
                        completed_bytes += media_size
                        scan_tasks[task_id]["downloaded_bytes"] = completed_bytes
                        scan_tasks[task_id]["progress"] = (
                            int(completed_bytes / total_bytes * 100) if total_bytes else int((i + 1) / total * 100)
                        )
                        scan_tasks[task_id]["message"] = f"Downloading {i + 1}/{total}..."
                        continue
                    if existing_size == 0:
                        logger.info(f"[Task {task_id}] Re-downloading empty partial file: {file_path}")
                    else:
                        logger.info(
                            f"[Task {task_id}] Re-downloading partial file: "
                            f"{file_path} ({existing_size}/{media_size or 'unknown'} bytes)"
                        )

                logger.info(
                    f"[Task {task_id}] Downloading selected msg {msg_id} "
                    f"({i + 1}/{total}, {media_size} bytes) to {file_path}"
                )
                scan_tasks[task_id]["progress"] = int(i / total * 100)
                scan_tasks[task_id]["message"] = f"Downloading {i + 1}/{total}: 0%"
                scan_tasks[task_id]["current_file"] = file_name
                scan_tasks[task_id]["current_file_bytes"] = 0
                scan_tasks[task_id]["current_file_total"] = media_size

                def file_progress(current: int, file_total: int):
                    if scan_tasks.get(task_id, {}).get("cancel_requested"):
                        raise DownloadCancelled()

                    known_total = file_total or media_size
                    file_pct = int(current / known_total * 100) if known_total else 0
                    current_total_bytes = completed_bytes + current
                    overall = (
                        int(current_total_bytes / total_bytes * 100)
                        if total_bytes
                        else int(((i + (current / known_total if known_total else 0)) / total) * 100)
                    )
                    scan_tasks[task_id]["progress"] = max(0, min(100, overall))
                    scan_tasks[task_id]["message"] = f"Downloading {i + 1}/{total}: {file_pct}%"
                    scan_tasks[task_id]["downloaded_bytes"] = current_total_bytes
                    scan_tasks[task_id]["current_file_bytes"] = current
                    scan_tasks[task_id]["current_file_total"] = known_total
                    scan_tasks[task_id]["current_file_percent"] = file_pct

                await service.download_media_with_timeout(
                    real_msg, file=file_path, progress_callback=file_progress
                )
                _write_selected_message_metadata(real_msg, file_path, file_name, topic_id)
                downloaded += 1
                completed_bytes += media_size

                scan_tasks[task_id]["progress"] = int((i + 1) / total * 100)
                if total_bytes:
                    scan_tasks[task_id]["progress"] = int(completed_bytes / total_bytes * 100)
                scan_tasks[task_id]["downloaded_bytes"] = completed_bytes
                scan_tasks[task_id]["message"] = f"Downloading {i + 1}/{total}..."

                if i < len(message_ids) - 1:
                    delay = random.uniform(delay_min, delay_max)
                    await asyncio.sleep(delay)

            except DownloadCancelled:
                raise
            except FloodWaitError as e:
                logger.warning(f"[Task {task_id}] Flood wait {e.seconds}s while downloading msg {msg_id}")
                scan_tasks[task_id]["message"] = f"Flood wait {e.seconds}s on file {i + 1}/{total}..."
                await asyncio.sleep(e.seconds + 5)
                try:
                    if scan_tasks.get(task_id, {}).get("cancel_requested"):
                        raise DownloadCancelled()
                    real_msg = await service.client.get_messages(channel, ids=msg_id)
                    if real_msg and file_path:
                        await service.download_media_with_timeout(real_msg, file=file_path)
                        _write_selected_message_metadata(real_msg, file_path, os.path.basename(file_path), topic_id)
                        downloaded += 1
                        completed_bytes += media_size
                        scan_tasks[task_id]["downloaded_bytes"] = completed_bytes
                except DownloadCancelled:
                    raise
                except Exception:
                    errors += 1
            except Exception as e:
                errors += 1
                logger.error(f"Error downloading msg {msg_id}: {e}")
                scan_tasks[task_id]["progress"] = int((i + 1) / total * 100)
                scan_tasks[task_id]["message"] = f"Downloading {i + 1}/{total}... error"

        if scan_tasks.get(task_id, {}).get("cancel_requested"):
            raise DownloadCancelled()

        scan_tasks[task_id] = {
            "status": "completed",
            "downloaded": downloaded,
            "skipped": skipped,
            "errors": errors,
            "path": download_dir,
            "message": f"Downloaded {downloaded} files to {display_dir}, skipped {skipped}, {errors} errors"
        }
        logger.info(f"[Task {task_id}] Selected download completed: {downloaded}/{total}")
    except DownloadCancelled:
        logger.info(f"[Task {task_id}] Selected download cancelled by user")
        scan_tasks[task_id] = {
            "status": "cancelled",
            "progress": scan_tasks.get(task_id, {}).get("progress", 0),
            "message": "Download stopped by user",
            "downloaded_bytes": scan_tasks.get(task_id, {}).get("downloaded_bytes", 0),
            "total_bytes": scan_tasks.get(task_id, {}).get("total_bytes", 0),
        }
    except Exception as e:
        logger.exception(f"[Task {task_id}] Selected download error: {e}")
        scan_tasks[task_id] = {"status": "error", "message": str(e)}
    finally:
        await telegram_session_manager.release(session_name, owner)


@router.get("/api/task/{task_id}", summary="Check background task status")
async def get_task_status(task_id: str):
    task = scan_tasks.get(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    return task


@router.post("/api/task/{task_id}/cancel", summary="Cancel running download task")
async def cancel_task(task_id: str):
    task = scan_tasks.get(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    if task.get("status") != "running":
        return {"status": task.get("status"), "message": task.get("message", "Task is not running")}

    task["cancel_requested"] = True
    task["message"] = "Stopping download..."
    return {"status": "cancelling", "message": "Stopping download..."}


@router.get("/api/tasks/active", summary="List active (running) download tasks")
async def get_active_tasks():
    """Return all tasks with status='running', useful for reconnection after page reload."""
    active = []
    for tid, t in scan_tasks.items():
        if t.get("status") == "running" and "channel_id" in t:
            active.append({
                "task_id": tid,
                "channel_id": t.get("channel_id"),
                "channel_title": t.get("channel_title", ""),
                "message": t.get("message", ""),
                "progress": t.get("progress", 0),
            })
    return {"tasks": active}


@router.get("/api/version", summary="Check for updates")
async def check_version():
    """Compare local VERSION file with latest commit on GitHub."""
    import httpx
    local_version_file = METADATA_DIR / "VERSION"
    logger.info(f"[Version] Looking for VERSION at: {local_version_file}")
    logger.info(f"[Version] VERSION exists: {local_version_file.exists()}")
    local_commit = ""
    if local_version_file.exists():
        local_commit = local_version_file.read_text(encoding="utf-8").strip()
        logger.info(f"[Version] Local commit: {local_commit}")

    latest_commit = ""
    update_available = False
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                "https://api.github.com/repos/troxcalgary-arch/TelegramMediaAnalytics/commits/main",
                headers={"Accept": "application/vnd.github.v3+json"}
            )
            logger.info(f"[Version] GitHub API status: {resp.status_code}")
            if resp.status_code == 200:
                data = resp.json()
                latest_commit = data.get("sha", "")[:12]
                local_short = local_commit[:12]
                logger.info(f"[Version] Local short: {local_short}, Latest: {latest_commit}")
                if local_short and latest_commit and local_short != latest_commit:
                    update_available = True
                    logger.info(f"[Version] Update available: {local_short} != {latest_commit}")
                else:
                    logger.info(f"[Version] Up to date")
            else:
                logger.warning(f"[Version] GitHub API returned {resp.status_code}")
    except Exception as e:
        logger.error(f"[Version] Version check failed: {e}")

    logger.info(f"[Version] Result: local={local_commit[:12]}, latest={latest_commit[:12]}, update={update_available}")
    return {
        "local_commit": local_commit[:12],
        "latest_commit": latest_commit[:12],
        "update_available": update_available
    }


# ---------- Scan history ----------
def _load_history() -> List[Dict]:
    if HISTORY_FILE.exists():
        try:
            return json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"Failed to load history: {e}")
    return []

def _save_history(history: List[Dict]):
    HISTORY_FILE.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")

def _add_to_history(entry: Dict):
    logger.info(f"[History] Adding entry for channel {entry.get('channel_id')}")
    logger.info(f"[History] HISTORY_FILE: {HISTORY_FILE}")
    history = _load_history()
    history.insert(0, entry)
    # Keep max 100 entries
    if len(history) > 100:
        history = history[:100]
    _save_history(history)
    logger.info(f"[History] Saved {len(history)} entries")


@router.get("/api/scan-history", summary="Get scan history")
async def get_scan_history(limit: int = Query(3, ge=1, le=100)):
    """Return last N scan history entries."""
    history = _load_history()
    return {"history": history[:limit]}


@router.get("/api/scan-history/all", summary="Get full scan history")
async def get_scan_history_all():
    """Return full scan history for the history page."""
    return {"history": _load_history()}


@router.get("/history", response_class=HTMLResponse, summary="Scan history page")
async def scan_history_page(request: Request):
    return templates.TemplateResponse(request, "history.html")


# ---------- Legacy endpoints (for old frontend compatibility) ----------
@router.post("/api/connect", summary="[Legacy] Connect with credentials from form")
async def legacy_connect(
    api_id: int = Form(...),
    api_hash: str = Form(...),
    phone: str = Form(...)
):
    """Used by old telegram.html — stores creds in .env (not recommended)."""
    # In production, you'd save to user config instead
    return {"message": "Use /api/session with JWT auth instead"}


@router.get("/web", response_class=HTMLResponse, summary="Serve Telegram HTML UI")
async def telegram_web(request: Request):
    return templates.TemplateResponse(request, "telegram.html")
