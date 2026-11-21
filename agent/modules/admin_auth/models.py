from sqlalchemy import JSON, Boolean, Column, DateTime, ForeignKey, Integer, String

from agent.shared.infrastructure.db.base import Base, BaseModel, utcnow


class AdminCredential(BaseModel):
    __tablename__ = "admin_credentials"

    username = Column(String(64), unique=True, nullable=False, index=True, default="admin")
    password_hash = Column(String(255), nullable=False)
    session_version = Column(Integer, nullable=False, default=0, server_default="0")
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class AdminKeyboardShortcuts(Base):
    __tablename__ = "admin_keyboard_shortcuts"

    admin_id = Column(
        Integer, ForeignKey("admin_credentials.id", ondelete="CASCADE"), primary_key=True
    )
    revision = Column(Integer, nullable=False)
    overrides = Column(JSON, nullable=False)


__all__ = ["AdminCredential", "AdminKeyboardShortcuts"]
