from sqlalchemy import Column, DateTime, String

from agent.shared.infrastructure.db import BaseModel, utcnow


class User(BaseModel):
    __tablename__ = "users"

    external_id = Column(String(255), nullable=False, unique=True, index=True)
    platform = Column(String(50), nullable=False)
    username = Column(String(255), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


__all__ = ["User"]
