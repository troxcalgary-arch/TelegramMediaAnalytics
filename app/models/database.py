"""Telegram analytics database models."""

from typing import Optional
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import Integer, String, DateTime, BigInteger, Boolean, ForeignKey
from datetime import datetime

from app.models import Base
class TelegramChannel(Base):
    __tablename__ = "telegram_channels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    channel_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    username: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    title: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    users: Mapped[list["TelegramUser"]] = relationship("TelegramUser", back_populates="channel")
    videos: Mapped[list["TelegramVideo"]] = relationship("TelegramVideo", back_populates="channel")
    audios: Mapped[list["TelegramAudio"]] = relationship("TelegramAudio", back_populates="channel")


class TelegramUser(Base):
    __tablename__ = "telegram_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger)
    username: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    first_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    last_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    channel_id: Mapped[int] = mapped_column(Integer, ForeignKey("telegram_channels.id"))
    channel: Mapped["TelegramChannel"] = relationship("TelegramChannel", back_populates="users")

    videos: Mapped[list["TelegramVideo"]] = relationship("TelegramVideo", back_populates="user")
    audios: Mapped[list["TelegramAudio"]] = relationship("TelegramAudio", back_populates="user")


class TelegramVideo(Base):
    __tablename__ = "telegram_videos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    message_id: Mapped[int] = mapped_column(BigInteger)
    file_id: Mapped[str] = mapped_column(String(255))
    file_name: Mapped[str] = mapped_column(String(500))
    file_path: Mapped[str] = mapped_column(String(1000))
    file_size: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    duration: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    mime_type: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    message_date: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    downloaded: Mapped[bool] = mapped_column(Boolean, default=False)

    channel_id: Mapped[int] = mapped_column(Integer, ForeignKey("telegram_channels.id"))
    channel: Mapped["TelegramChannel"] = relationship("TelegramChannel", back_populates="videos")

    user_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("telegram_users.id"), nullable=True)
    user: Mapped[Optional["TelegramUser"]] = relationship("TelegramUser", back_populates="videos")


class TelegramAudio(Base):
    __tablename__ = "telegram_audios"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    message_id: Mapped[int] = mapped_column(BigInteger)
    file_id: Mapped[str] = mapped_column(String(255))
    file_name: Mapped[str] = mapped_column(String(500))
    file_path: Mapped[str] = mapped_column(String(1000))
    file_size: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    duration: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    mime_type: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    title: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    performer: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    message_date: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    downloaded: Mapped[bool] = mapped_column(Boolean, default=False)

    channel_id: Mapped[int] = mapped_column(Integer, ForeignKey("telegram_channels.id"))
    channel: Mapped["TelegramChannel"] = relationship("TelegramChannel", back_populates="audios")

    user_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("telegram_users.id"), nullable=True)
    user: Mapped[Optional["TelegramUser"]] = relationship("TelegramUser", back_populates="audios")