"""Telegram Analytics Router — JWT Auth + Background Tasks + Paginated Results."""

from typing import Optional, List, Dict, Any
import asyncio
import uuid
import os
import json
import logging
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
from app.services.telegram_service import TelegramService, get_telegram_service

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

# Setup logging
logger = logging.getLogger(__name__)

# In-memory task storage (replace with Redis/DB in production)
scan_tasks: Dict[str, Dict[str, Any]] = {}

# Store temporary auth sessions (in production use Redis/DB)
auth_sessions: Dict[str, Dict] = {}

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
    # Параметры защиты от FloodWait
    delay_min: float = 2.0  # Минимальная задержка между файлами (сек)
    delay_max: float = 5.0  # Максимальная задержка между файлами (сек)
    skip_existing: bool = True  # Пропускать существующие файлы


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

# Store temporary auth sessions (in production use Redis/DB)
auth_sessions: Dict[str, Dict] = {}

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
    db: Session = Depends(get_db)
):
    """Start Telegram auth: connect client and send code."""
    try:
        # Use deterministic session name based on phone — reuses existing .session file
        session_name = f"auth_{phone.replace('+', '').replace('-', '_')}"
        service = TelegramService(api_id, api_hash, phone, session_name)
        await service.connect()

        # Send code request
        sent = await service.client.send_code_request(phone)

        # Store session for verification
        session_id = _make_task_id()
        auth_sessions[session_id] = {
            "service": service,
            "api_id": api_id,
            "api_hash": api_hash,
            "phone": phone,
            "phone_code_hash": sent.phone_code_hash
        }

        return {"session_id": session_id, "message": "Code sent to Telegram"}
    except Exception as e:
        raise HTTPException(400, f"Failed to send code: {str(e)}")


@router.post("/api/auth/verify-code", summary="Verify authentication code")
async def auth_verify_code(
    session_id: str = Form(...),
    code: str = Form(...),
    db: Session = Depends(get_db)
):
    """Verify the code sent to Telegram."""
    session = auth_sessions.get(session_id)
    if not session:
        raise HTTPException(404, "Session not found or expired")

    service = session["service"]
    try:
        await service.client.sign_in(
            phone=session["phone"],
            code=code,
            phone_code_hash=session["phone_code_hash"]
        )

        # Check if 2FA is needed
        me = await service.client.get_me()

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
        raise HTTPException(400, f"Invalid code: {str(e)}")


@router.post("/api/auth/verify-2fa", summary="Verify 2FA password")
async def auth_verify_2fa(
    session_id: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db)
):
    """Verify 2FA password."""
    session = auth_sessions.get(session_id)
    if not session:
        raise HTTPException(404, "Session not found or expired")

    service = session["service"]
    try:
        await service.client.sign_in(password=password)

        me = await service.client.get_me()
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
        raise HTTPException(400, f"Invalid 2FA password: {str(e)}")


@router.get("/api/auth/status", summary="Check authentication status")
async def auth_status(session_id: Optional[str] = Query(None)):
    """Check if there's an active authenticated session."""
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
    session = auth_sessions.pop(session_id, None)
    if session and "service" in session:
        try:
            await session["service"].disconnect()
        except:
            pass
    return {"message": "Logged out"}


@router.post("/api/auth/save-session", summary="Save authenticated session to user config and get JWT")
async def auth_save_session(
    session_id: str = Form(...),
    current_user: AppUser = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Save authenticated Telegram session to user's config and return JWT."""
    session = auth_sessions.get(session_id)
    if not session or not session.get("authenticated"):
        raise HTTPException(400, "No authenticated session to save")

    # Save API config
    cfg = db.query(ApiSessionConfig).filter(ApiSessionConfig.user_id == current_user.id).first()
    if cfg:
        cfg.api_id = session["api_id"]
        cfg.phone_number = session["phone"]
    else:
        cfg = ApiSessionConfig(
            user_id=current_user.id,
            api_id=session["api_id"],
            phone_number=session["phone"]
        )
        db.add(cfg)
    db.commit()

    # Disconnect service
    try:
        await session["service"].disconnect()
    except:
        pass
    auth_sessions.pop(session_id, None)

    # Return JWT token
    access_token = create_access_token(data={"sub": current_user.username})
    return {"access_token": access_token, "token_type": "bearer", "message": "Session saved successfully"}


@router.post("/api/auth/auto-login", summary="Auto-login after Telegram auth - creates user if needed and returns JWT")
async def auth_auto_login(
    session_id: str = Form(...),
    db: Session = Depends(get_db)
):
    """After Telegram auth, auto-create/login web user and return JWT token."""
    session = auth_sessions.get(session_id)
    if not session or not session.get("authenticated"):
        raise HTTPException(400, "No authenticated session")

    tg_user = session.get("tg_user")
    if not tg_user:
        raise HTTPException(400, "Telegram user info not available")

    # Create or get web user based on Telegram user
    username = f"tg_{tg_user['id']}"
    user = db.query(AppUser).filter(AppUser.username == username).first()

    if not user:
        # Create new web user
        import secrets
        password = secrets.token_urlsafe(32)  # Random password, user logs in via Telegram
        user = AppUser(username=username, hashed_password=create_hash(password))
        db.add(user)
        db.commit()
        db.refresh(user)

    # Save Telegram API config for this user
    cfg = db.query(ApiSessionConfig).filter(ApiSessionConfig.user_id == user.id).first()
    if cfg:
        cfg.api_id = session["api_id"]
        cfg.phone_number = session["phone"]
        cfg.session_name = session["service"].session_name
    else:
        cfg = ApiSessionConfig(
            user_id=user.id,
            api_id=session["api_id"],
            phone_number=session["phone"],
            session_name=session["service"].session_name
        )
        db.add(cfg)
    db.commit()

    # Disconnect Telegram service
    try:
        await session["service"].disconnect()
    except:
        pass
    auth_sessions.pop(session_id, None)

    # Return JWT
    access_token = create_access_token(data={"sub": user.username})
    return {"access_token": access_token, "token_type": "bearer", "message": "Logged in successfully"}


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

    if not result:
        # Fallback to old metadata files - for full channel, use chat file
        chat_id = int(channel_id) if str(channel_id).lstrip('-').isdigit() else -1001911644885
        videos = await load_video_metadata(chat_id, topic_id=None)
    else:
        videos = result.get("messages", [])

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
        "total_videos": len(videos),
        "unique_authors": len(stats),
        "channel_id": channel_id,
        "page": page,
        "per_page": per_page,
        "total": total,
        "total_pages": total_pages,
        "start": start,
        "authors": page_stats
    }


async def _run_scan_task(task_id: str, api_id: int, api_hash: str, phone: str,
                         channel_id: str, media_type: str, days: Optional[int], limit: int,
                         session_name: Optional[str] = None,
                         start_date: Optional[str] = None, end_date: Optional[str] = None):
    """Background scan task — updates scan_tasks dict with progress/result."""
    scan_tasks[task_id] = {"status": "running", "progress": 0, "message": "Connecting..."}
    logger.info(f"[Task {task_id}] Starting scan for channel {channel_id}, session={session_name}")
    logger.info(f"[Task {task_id}] Date params: days={days}, start_date={start_date}, end_date={end_date}")
    try:
        service = TelegramService(api_id, api_hash, phone, session_name)
        await service.connect()
        
        # Check if session is authorized
        if not await service.is_authorized():
            logger.error(f"[Task {task_id}] Session not authorized for channel {channel_id}")
            scan_tasks[task_id] = {"status": "error", "message": "Session not authorized. Please re-authenticate via /telegram/web (code/2FA)."}
            await service.disconnect()
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
            stats = await service.get_user_video_stats(channel, days=days)
            result = {
                "status": "completed",
                "stats": stats,
                "message": f"Found {len(messages)} videos from {len(stats)} authors",
                "messages": messages
            }
        else:
            result = {
                "status": "completed",
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
        await service.disconnect()
    except Exception as e:
        logger.exception(f"[Task {task_id}] Scan error: {e}")
        scan_tasks[task_id] = {"status": "error", "message": str(e)}


async def _run_download_task(task_id: str, api_id: int, api_hash: str, phone: str,
                             channel_id: str, media_type: str, days: Optional[int],
                             download_path: str, limit: int, delay_min: float = 2.0, delay_max: float = 5.0, skip_existing: bool = True,
                             session_name: Optional[str] = None):
    scan_tasks[task_id] = {"status": "running", "progress": 0, "message": "Preparing download..."}
    logger.info(f"[Task {task_id}] Starting download for channel {channel_id}, session={session_name}")
    try:
        service = TelegramService(api_id, api_hash, phone, session_name)
        await service.connect()
        
        # Check if session is authorized
        if not await service.is_authorized():
            logger.error(f"[Task {task_id}] Session not authorized for channel {channel_id}")
            scan_tasks[task_id] = {"status": "error", "message": "Session not authorized. Please re-authenticate via /telegram/web (code/2FA)."}
            await service.disconnect()
            return
            
        logger.info(f"[Task {task_id}] Session authorized, fetching channel...")
        channel, topic_id = await service.get_channel(channel_id)

        # Create subfolder named after the channel
        import re as _re
        channel_title = getattr(channel, "title", None) or str(channel_id)
        safe_title = _re.sub(r'[<>:"/\\|?*]', '_', channel_title).strip().strip('.')
        if not safe_title:
            safe_title = str(channel_id)
        channel_dir = os.path.join(download_path, safe_title)
        os.makedirs(channel_dir, exist_ok=True)

        # Store channel info in task for active-task tracking
        scan_tasks[task_id]["channel_id"] = channel_id
        scan_tasks[task_id]["channel_title"] = channel_title

        messages = await service.get_messages_with_media(
            channel, filter_type=media_type, days=days, limit=limit
        )

        def progress_cb(done: int, total: int):
            scan_tasks[task_id]["progress"] = int(done / total * 100) if total else 0
            scan_tasks[task_id]["message"] = f"Downloading {done}/{total}..."

        result = await service.download_all_media(
            messages, channel_dir, channel=channel, progress_callback=progress_cb,
            delay_range=(delay_min, delay_max),
            skip_existing=skip_existing
        )
        scan_tasks[task_id] = {
            "status": "completed",
            "downloaded": result["downloaded"],
            "skipped": result.get("skipped", 0),
            "errors": result["errors"],
            "path": channel_dir,
            "message": f"Downloaded {result['downloaded']} files to {safe_title}/, skipped {result.get('skipped', 0)}, {result['errors']} errors"
        }
        logger.info(f"[Task {task_id}] Download completed: {scan_tasks[task_id].get('message', 'done')}")
        await service.disconnect()
    except Exception as e:
        logger.exception(f"[Task {task_id}] Download error: {e}")
        scan_tasks[task_id] = {"status": "error", "message": str(e)}


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
    background.add_task(
        _run_scan_task, task_id, cfg.api_id, api_hash, phone,
        payload.channel_id, payload.media_type, payload.days, payload.limit,
        cfg.session_name, payload.start_date, payload.end_date
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
                detail=f"Скачивание для канала «{t.get('channel_title', payload.channel_id)}» ({payload.channel_id}) уже выполняется в фоне (Task ID: {tid})"
            )

    task_id = _make_task_id()
    background.add_task(
        _run_download_task, task_id, cfg.api_id, api_hash, phone,
        payload.channel_id, payload.media_type, payload.days,
        payload.download_path, payload.limit,
        payload.delay_min, payload.delay_max, payload.skip_existing,
        cfg.session_name
    )
    return {"task_id": task_id, "status": "started"}


@router.get("/api/task/{task_id}", summary="Check background task status")
async def get_task_status(task_id: str):
    task = scan_tasks.get(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    return task


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


# ---------- Scan history ----------
def _load_history() -> List[Dict]:
    if HISTORY_FILE.exists():
        try:
            return json.loads(HISTORY_FILE.read_text())
        except Exception:
            pass
    return []

def _save_history(history: List[Dict]):
    HISTORY_FILE.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")

def _add_to_history(entry: Dict):
    history = _load_history()
    history.insert(0, entry)
    # Keep max 100 entries
    if len(history) > 100:
        history = history[:100]
    _save_history(history)


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