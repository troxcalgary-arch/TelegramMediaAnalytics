"""Process-local ownership and lifecycle management for Telethon sessions."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Optional

if TYPE_CHECKING:
    from app.services.telegram_service import TelegramService

logger = logging.getLogger(__name__)


def _positive_timeout_from_env(name: str, default: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
        return value if value > 0 else default
    except ValueError:
        return default


DISCONNECT_TIMEOUT_SECONDS = _positive_timeout_from_env(
    "TG_DISCONNECT_TIMEOUT_SECONDS", 30.0
)


def _default_service_factory(*args, **kwargs):
    from app.services.telegram_service import TelegramService

    return TelegramService(*args, **kwargs)


class SessionBusyError(RuntimeError):
    """Raised when a Telethon session is already reserved by another operation."""


@dataclass
class _ManagedSession:
    service: Any
    api_id: int
    api_hash: str
    phone: str
    lock: asyncio.Lock
    owner: Optional[str] = None
    last_used: float = 0.0


class TelegramSessionManager:
    """Keep one TelegramService instance and one operation lock per session name."""

    def __init__(self, service_factory: Optional[Callable[..., Any]] = None):
        self._service_factory = service_factory or _default_service_factory
        self._sessions: dict[str, _ManagedSession] = {}
        self._registry_lock = asyncio.Lock()

    async def reserve(
        self,
        session_name: str,
        api_id: int,
        api_hash: str,
        phone: str,
        owner: str,
    ) -> "TelegramService":
        """Atomically reserve a session and return its singleton service instance."""
        async with self._registry_lock:
            state = self._sessions.get(session_name)
            if state and (state.owner is not None or state.lock.locked()):
                raise SessionBusyError(
                    f"Telegram session '{session_name}' is busy with operation '{state.owner}'"
                )

            credentials = (api_id, api_hash, phone)
            if state and (state.api_id, state.api_hash, state.phone) != credentials:
                try:
                    await asyncio.wait_for(
                        state.service.disconnect(), timeout=DISCONNECT_TIMEOUT_SECONDS
                    )
                except Exception:
                    logger.exception(
                        "Failed to disconnect stale Telegram client session=%s pid=%s",
                        session_name,
                        os.getpid(),
                    )
                    raise SessionBusyError(
                        f"Telegram session '{session_name}' could not close its previous client"
                    )
                state = None

            if state is None:
                state = _ManagedSession(
                    service=self._service_factory(api_id, api_hash, phone, session_name),
                    api_id=api_id,
                    api_hash=api_hash,
                    phone=phone,
                    lock=asyncio.Lock(),
                )
                self._sessions[session_name] = state

            await state.lock.acquire()
            state.owner = owner
            state.last_used = time.monotonic()
            logger.info(
                "Reserved Telegram session=%s owner=%s pid=%s",
                session_name,
                owner,
                os.getpid(),
            )
            return state.service

    async def release(self, session_name: str, owner: str) -> None:
        """Disconnect and release a session, even when disconnect itself fails."""
        async with self._registry_lock:
            state = self._sessions.get(session_name)
            if state is None or state.owner != owner:
                return
            service = state.service

        try:
            await asyncio.wait_for(
                service.disconnect(), timeout=DISCONNECT_TIMEOUT_SECONDS
            )
        except Exception:
            logger.exception(
                "Failed to disconnect Telegram session=%s owner=%s pid=%s",
                session_name,
                owner,
                os.getpid(),
            )
        finally:
            async with self._registry_lock:
                state = self._sessions.get(session_name)
                if state is not None and state.owner == owner:
                    state.owner = None
                    state.last_used = time.monotonic()
                    if state.lock.locked():
                        state.lock.release()
                    logger.info(
                        "Released Telegram session=%s owner=%s pid=%s",
                        session_name,
                        owner,
                        os.getpid(),
                    )

    async def close_all(self) -> None:
        """Disconnect all managed clients during application shutdown."""
        async with self._registry_lock:
            sessions = list(self._sessions.items())
        for session_name, state in sessions:
            try:
                await asyncio.wait_for(
                    state.service.disconnect(), timeout=DISCONNECT_TIMEOUT_SECONDS
                )
            except Exception:
                logger.exception(
                    "Failed to close Telegram session=%s during shutdown pid=%s",
                    session_name,
                    os.getpid(),
                )
        async with self._registry_lock:
            self._sessions.clear()

    def owner_of(self, session_name: str) -> Optional[str]:
        state = self._sessions.get(session_name)
        return state.owner if state else None


class SingleProcessGuard:
    """Hold an OS-level file lock so only one app process can use session files."""

    def __init__(self, path: Path):
        self.path = path
        self._file = None

    def acquire(self) -> None:
        workers = int(os.getenv("WEB_CONCURRENCY", os.getenv("UVICORN_WORKERS", "1")))
        if workers != 1:
            raise RuntimeError("Telegram session storage requires exactly one Uvicorn worker")

        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch(exist_ok=True)
        handle = self.path.open("r+b")
        if self.path.stat().st_size == 0:
            handle.write(b"0")
            handle.flush()

        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, IOError) as exc:
            handle.close()
            raise RuntimeError(
                "Another TelegramMediaAnalytics process is already using the Telethon session files"
            ) from exc
        self._file = handle

    def release(self) -> None:
        handle = self._file
        if handle is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()
            self._file = None


telegram_session_manager = TelegramSessionManager()
