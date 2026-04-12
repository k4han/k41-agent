from sqlalchemy import Boolean, Column, DateTime, String

from agent.shared.infrastructure.db.base import BaseModel, utcnow


class AdminCredential(BaseModel):
    __tablename__ = "admin_credentials"

    username = Column(String(64), unique=True, nullable=False, index=True, default="admin")
    password_hash = Column(String(255), nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


__all__ = ["AdminCredential"]