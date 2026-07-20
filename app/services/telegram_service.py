"""
Сервис для работы с Telegram через Telethon
"""

from telethon import TelegramClient
from telethon.tl.functions.messages import GetHistoryRequest
from telethon.tl.types import InputMessagesFilterVideo, InputMessagesFilterDocument, InputMessagesFilterVoice
from telethon.tl.types import InputPeerChannel
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict
import os
import asyncio
import logging

logger = logging.getLogger(__name__)

class TelegramService:
    def __init__(self, api_id: int, api_hash: str, phone: str, session_name: str = None):
        self.api_id = api_id
        self.api_hash = api_hash
        self.phone = phone
        self.session_name = session_name or f'session_{phone.replace("+", "").replace("-", "_")}'
        self.client = TelegramClient(self.session_name, api_id, api_hash)
    
    async def connect(self):
        """Connect to Telegram (no auto-login for auth flow)"""
        if not self.client.is_connected():
            await self.client.connect()
        return self.client.is_connected()

    async def is_authorized(self) -> bool:
        """Check if client is authorized (has valid session)"""
        try:
            return await self.client.is_user_authorized()
        except Exception:
            return False
    
    async def connect_and_login(self):
        """Full connection with login (for scanning/downloading)"""
        await self.client.start(self.phone)
        return self.client.is_connected()
    
    async def get_channel(self, channel_identifier: str):
        """Get channel by username or ID. Supports topic format (e.g. -1001911644885_1194)."""
        # Extract topic_id if present (format: channelid_topicid)
        topic_id = None
        raw = channel_identifier.strip()
        if '_' in raw and raw.startswith('-'):
            parts = raw.rsplit('_', 1)
            if parts[1].isdigit():
                topic_id = int(parts[1])
                raw = parts[0]

        if raw.startswith('-') or raw.isdigit():
            channel_id = int(raw)
            entity = await self.client.get_entity(channel_id)
        else:
            entity = await self.client.get_entity(raw)
        return entity, topic_id
    
    async def get_messages_with_media(
        self,
        channel,
        filter_type: str = "video",
        days: Optional[int] = None,
        limit: int = 1000,
        topic_id: Optional[int] = None,
        progress_callback=None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None
    ) -> List[Dict]:
        """
        Получает сообщения с медиа из канала

        filter_type: video, audio, photo, document, all
        days: фильтр по дням (None = все время, назад от текущей даты)
        topic_id: фильтр по топику форума (None = все топики)
        progress_callback: callable(scanned_total, matched_count) для обновления прогресса
        start_date: фильтр от даты (YYYY-MM-DD)
        end_date: фильтр до даты (YYYY-MM-DD)
        """
        # Calculate start date
        offset_date = None
        if days:
            offset_date = datetime.now(timezone.utc) - timedelta(days=days)
        
        messages = []
        offset_id = 0
        scanned_total = 0
        # limit=0 means "no limit" — iterate until no more messages
        unlimited = limit <= 0

        while unlimited or len(messages) < limit:
            batch_size = 100 if unlimited else min(100, limit - len(messages))
            history = await self.client(
                GetHistoryRequest(
                    peer=channel,
                    offset_id=offset_id,
                    limit=batch_size,
                    max_id=0,
                    min_id=0,
                    offset_date=offset_date,
                    add_offset=0,
                    hash=0
                )
            )
            
            batch = history.messages
            if not batch:
                break
            
            # Filter by media type
            for msg in batch:
                if days and msg.date < offset_date:
                    continue

                # Date range filter
                if start_date:
                    try:
                        start_dt = datetime.strptime(start_date, "%Y-%m-%d")
                        if msg.date.replace(tzinfo=None) < start_dt:
                            continue
                    except ValueError:
                        pass
                if end_date:
                    try:
                        end_dt = datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)
                        if msg.date.replace(tzinfo=None) >= end_dt:
                            continue
                    except ValueError:
                        pass

                # Forum topic filter
                if topic_id is not None:
                    # Check message_thread_id first
                    msg_thread = getattr(msg, "message_thread_id", None)
                    if msg_thread is not None and msg_thread != topic_id:
                        continue
                    # If no message_thread_id, check reply_to chain
                    if msg_thread is None:
                        reply = getattr(msg, "reply_to", None)
                        if reply:
                            top_id = getattr(reply, "reply_to_top_msg_id", None)
                            reply_id = getattr(reply, "reply_to_msg_id", None)
                            # Message belongs to topic if it replies to topic starter or to a message in the topic
                            if top_id != topic_id and reply_id != topic_id:
                                continue
                        else:
                            # No reply — could be the topic starter itself
                            if msg.id != topic_id:
                                continue
                    
                has_media = False
                if filter_type == "video" and msg.video:
                    has_media = True
                elif filter_type == "audio" and msg.audio:
                    has_media = True
                elif filter_type == "photo" and msg.photo:
                    has_media = True
                elif filter_type == "document" and msg.document and not msg.video and not msg.audio and not msg.photo:
                    has_media = True
                elif filter_type == "all" and (msg.video or msg.audio or msg.photo or msg.document):
                    has_media = True
                    
                if has_media:
                    messages.append(self._serialize_message(msg))

            scanned_total += len(batch)
            if progress_callback:
                progress_callback(scanned_total, len(messages))

            offset_id = batch[-1].id

        # Batch-resolve senders
        sender_ids = set(m.get("sender_id") for m in messages if m.get("sender_id"))
        resolved_senders: Dict[int, Dict] = {}
        for uid in sender_ids:
            try:
                entity = await self.client.get_entity(uid)
                resolved_senders[uid] = {
                    "id": getattr(entity, "id", None),
                    "username": getattr(entity, "username", None),
                    "first_name": getattr(entity, "first_name", None),
                    "last_name": getattr(entity, "last_name", None),
                }
            except Exception:
                resolved_senders[uid] = {"id": uid}

        # Attach resolved senders to messages
        for m in messages:
            sid = m.get("sender_id")
            if sid and sid in resolved_senders:
                m["sender"] = resolved_senders[sid]

        return messages

    @staticmethod
    def _serialize_message(msg) -> Dict:
        """Convert a Telethon Message to a JSON-serializable dict matching the
        legacy format expected by the frontend (video/sender/caption/topic_id)."""
        # --- Build the media dict under a unified key ---
        video_info: Dict = {}
        if msg.video:
            v = msg.video
            attrs: Dict = {}
            video_attrs: Dict = {}
            if getattr(v, "duration", None) is not None:
                video_attrs["duration"] = v.duration
            if getattr(v, "width", None) is not None:
                video_attrs["w"] = v.width
            if getattr(v, "height", None) is not None:
                video_attrs["h"] = v.height
            if getattr(v, "supports_streaming", None) is not None:
                video_attrs["supports_streaming"] = v.supports_streaming
            if video_attrs:
                attrs["video"] = video_attrs
            video_info = {
                "id": v.id,
                "access_hash": getattr(v, "access_hash", None),
                "file_reference": getattr(v, "file_reference", None).hex() if getattr(v, "file_reference", None) else None,
                "dc_id": getattr(v, "dc_id", None),
                "size": v.size,
                "mime_type": getattr(v, "mime_type", None),
                "attributes": attrs,
            }
        elif msg.photo:
            p = msg.photo
            sizes = getattr(p, "sizes", []) or []
            largest = sizes[-1] if sizes else None
            photo_size = getattr(largest, "size", None)
            if photo_size is None and largest is not None:
                progressive_sizes = getattr(largest, "sizes", [])
                photo_size = progressive_sizes[-1] if progressive_sizes else 0
            video_info = {
                "id": p.id,
                "access_hash": getattr(p, "access_hash", None),
                "file_reference": getattr(p, "file_reference", None).hex() if getattr(p, "file_reference", None) else None,
                "dc_id": getattr(p, "dc_id", None),
                "size": photo_size or 0,
                "mime_type": "image/jpeg",
                "attributes": {
                    "video": {
                        "w": getattr(largest, "w", None),
                        "h": getattr(largest, "h", None),
                    }
                },
            }
        elif msg.audio:
            a = msg.audio
            video_info = {
                "id": a.id,
                "access_hash": getattr(a, "access_hash", None),
                "file_reference": getattr(a, "file_reference", None).hex() if getattr(a, "file_reference", None) else None,
                "dc_id": getattr(a, "dc_id", None),
                "size": a.size,
                "mime_type": getattr(a, "mime_type", None),
                "attributes": {
                    "audio": {
                        "duration": getattr(a, "duration", None),
                        "title": getattr(a, "title", None),
                        "performer": getattr(a, "performer", None),
                    }
                },
            }
        elif msg.document:
            d = msg.document
            file_name = None
            for attr in getattr(d, "attributes", []) or []:
                if hasattr(attr, "file_name"):
                    file_name = attr.file_name
                    break
            video_info = {
                "id": d.id,
                "access_hash": getattr(d, "access_hash", None),
                "file_reference": getattr(d, "file_reference", None).hex() if getattr(d, "file_reference", None) else None,
                "dc_id": getattr(d, "dc_id", None),
                "size": d.size,
                "mime_type": getattr(d, "mime_type", None),
                "attributes": {"file_name": file_name},
            }

        # --- Sender ---
        sender: Dict = {}
        if msg.sender:
            s = msg.sender
            sender = {
                "id": getattr(s, "id", None),
                "username": getattr(s, "username", None),
                "first_name": getattr(s, "first_name", None),
                "last_name": getattr(s, "last_name", None),
            }

        # --- Topic / message thread (for forum topics) ---
        topic_id = None
        if getattr(msg, "message_thread_id", None):
            topic_id = msg.message_thread_id
        elif getattr(msg, "reply_to", None) and getattr(msg.reply_to, "reply_to_msg_id", None):
            topic_id = getattr(msg.reply_to, "reply_to_top_msg_id", None) or msg.reply_to.reply_to_msg_id

        return {
            "message_id": msg.id,
            "sender_id": msg.sender_id,
            "date": msg.date.isoformat() if msg.date else None,
            "date_unix": int(msg.date.timestamp()) if msg.date else None,
            "sender": sender,
            "video": video_info,
            "caption": msg.text or "",
            "topic_id": topic_id,
        }
    
    async def get_user_video_stats(self, channel, days: Optional[int] = None) -> List[Dict]:
        """
        Получает статистику пользователей по количеству видео
        Возвращает список с сортировкой от большего к меньшему
        """
        from collections import Counter
        
        video_messages = await self.get_messages_with_media(channel, "video", days, limit=5000)
        
        # Calculate statistics
        user_ids = [m["sender_id"] for m in video_messages]
        stats = Counter(user_ids)
        
        # Build results with names
        results = []
        for user_id, count in stats.most_common():
            try:
                entity = await self.client.get_entity(user_id)
                name = f"@{entity.username}" if entity.username else f"{entity.first_name} {entity.last_name or ''}".strip()
            except:
                name = f"ID:{user_id}"
            
            results.append({
                "user_id": user_id,
                "name": name,
                "video_count": count,
                "last_date": max(m.get("date_unix", 0) for m in video_messages if m["sender_id"] == user_id)
            })
        
        return results
    
    async def download_all_media(
        self,
        messages: List[Dict],
        download_path: str,
        channel=None,
        progress_callback=None,
        delay_range: tuple = (2.0, 5.0),
        skip_existing: bool = True
    ) -> Dict:
        """
        Скачивает все медиа файлы с защитой от флуд-контроля

        delay_range: диапазон случайных задержек между файлами (сек)
        skip_existing: пропускать уже существующие файлы
        """
        import random
        from telethon.errors import FloodWaitError

        os.makedirs(download_path, exist_ok=True)

        downloaded = 0
        skipped = 0
        errors = 0
        total = len(messages)

        for i, msg_data in enumerate(messages):
            msg_id = msg_data["message_id"]
            v = msg_data.get("video", {})
            mime = v.get("mime_type", "") or ""
            attrs = v.get("attributes", {})
            sender = msg_data.get("sender", {})
            try:
                # Determine extension from mime type (mime has priority)
                if "video" in mime:
                    ext = ".mp4"
                elif "image" in mime:
                    ext = ".jpg"
                elif "audio" in mime:
                    ext = ".mp3"
                elif attrs.get("file_name"):
                    ext = os.path.splitext(attrs["file_name"])[1] or ".bin"
                else:
                    ext = ".bin"

                # Determine username folder
                username = sender.get("username") or sender.get("first_name") or str(msg_data.get("sender_id", "unknown"))
                import re as _re
                safe_username = _re.sub(r'[<>:"/\\|?*]', '_', username).strip().strip('.')
                if not safe_username:
                    safe_username = str(msg_data.get("sender_id", "unknown"))

                user_dir = os.path.join(download_path, safe_username)
                os.makedirs(user_dir, exist_ok=True)

                file_name = f"{msg_id}_{msg_data['sender_id']}{ext}"
                file_path = os.path.join(user_dir, file_name)

                if skip_existing and os.path.exists(file_path):
                    skipped += 1
                    if progress_callback:
                        progress_callback(downloaded + skipped, total)
                    continue

                # Fetch the real Telethon message object by ID
                real_msg = await self.client.get_messages(channel, ids=msg_id) if channel else None
                if not real_msg:
                    errors += 1
                    continue

                await self.client.download_media(real_msg, file=file_path)
                downloaded += 1

                # Write metadata .md sidecar file
                md_path = os.path.join(user_dir, f"{os.path.splitext(file_name)[0]}.md")
                meta_lines = [
                    f"# Metadata: {file_name}",
                    "",
                    f"- **Message ID**: {msg_id}",
                    f"- **Sender ID**: {msg_data.get('sender_id')}",
                    f"- **Username**: @{sender.get('username', 'N/A')}" if sender.get("username") else f"- **Username**: N/A",
                    f"- **Name**: {(sender.get('first_name') or '')} {(sender.get('last_name') or '')}".strip(),
                    f"- **Date**: {msg_data.get('date', 'N/A')}",
                    f"- **Caption**: {msg_data.get('caption', '')}",
                    f"- **MIME**: {mime}",
                    f"- **Size**: {v.get('size', 0)} bytes",
                    f"- **File ID**: {v.get('id')}",
                    f"- **Topic ID**: {msg_data.get('topic_id', 'N/A')}",
                ]
                if attrs.get("video"):
                    va = attrs["video"]
                    if va.get("duration"): meta_lines.append(f"- **Duration**: {va['duration']}s")
                    if va.get("w") and va.get("h"): meta_lines.append(f"- **Resolution**: {va['w']}x{va['h']}")
                if attrs.get("audio"):
                    aa = attrs["audio"]
                    if aa.get("duration"): meta_lines.append(f"- **Duration**: {aa['duration']}s")
                    if aa.get("title"): meta_lines.append(f"- **Title**: {aa['title']}")
                    if aa.get("performer"): meta_lines.append(f"- **Performer**: {aa['performer']}")
                if attrs.get("file_name"):
                    meta_lines.append(f"- **Original filename**: {attrs['file_name']}")
                with open(md_path, "w", encoding="utf-8") as f:
                    f.write("\n".join(meta_lines) + "\n")

                if progress_callback:
                    progress_callback(downloaded + skipped, total)

                if i < len(messages) - 1:
                    delay = random.uniform(delay_range[0], delay_range[1])
                    await asyncio.sleep(delay)

            except FloodWaitError as e:
                wait_time = e.seconds + 5
                logger.warning(f"FloodWait: нужно подождать {wait_time} сек...")
                await asyncio.sleep(wait_time)
                try:
                    real_msg = await self.client.get_messages(channel, ids=msg_id) if channel else None
                    if real_msg:
                        await self.client.download_media(real_msg, file=file_path)
                        downloaded += 1
                        if progress_callback:
                            progress_callback(downloaded + skipped, total)
                except Exception as retry_e:
                    errors += 1
                    logger.error(f"Ошибка после FloodWait: {retry_e}")

            except Exception as e:
                errors += 1
                logger.error(f"Ошибка загрузки msg {msg_id}: {e}")

        return {"downloaded": downloaded, "skipped": skipped, "errors": errors, "total": total}
    
    async def get_users_list(self, channel) -> List[Dict]:
        """Get unique channel users (only those who posted)"""
        participants = []
        offset = 0
        
        while True:
            result = await self.client(
                GetHistoryRequest(
                    peer=channel,
                    offset_id=offset,
                    limit=100,
                    max_id=0,
                    min_id=0,
                    add_offset=0,
                    hash=0
                )
            )
            
            if not result.messages:
                break
            
            for msg in result.messages:
                if msg.sender_id:
                    participants.append(msg.sender_id)
            
            offset = result.messages[-1].id
        
        # Get unique users
        unique_ids = list(set(participants))
        
        users = []
        for user_id in unique_ids:
            try:
                entity = await self.client.get_entity(user_id)
                users.append({
                    "user_id": user_id,
                    "username": getattr(entity, 'username', None),
                    "first_name": getattr(entity, 'first_name', None),
                    "last_name": getattr(entity, 'last_name', None)
                })
            except Exception as e:
                users.append({
                    "user_id": user_id,
                    "username": None,
                    "first_name": None,
                    "last_name": None
                })
        
        return users
    
    async def download_media(
        self, 
        message, 
        download_path: str,
        file_name: Optional[str] = None
    ) -> str:
        """Download media file"""
        os.makedirs(download_path, exist_ok=True)
        
        if file_name is None:
            ext = ".mp4" if message.video else ".mp3" if message.audio else ".bin"
            file_name = f"{message.id}{ext}"
        
        full_path = os.path.join(download_path, file_name)
        
        await self.client.download_media(message, file=full_path)
        return full_path
    
    async def disconnect(self):
        await self.client.disconnect()

def get_telegram_service():
    """Factory for creating service"""
    # Values are read from .env
    api_id = int(os.getenv("TG_API_ID", "0"))
    api_hash = os.getenv("TG_API_HASH", "")
    phone = os.getenv("TG_PHONE", "")
    
    return TelegramService(api_id, api_hash, phone)