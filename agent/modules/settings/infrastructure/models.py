from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text

from agent.shared.infrastructure.db import BaseModel, utcnow


class UserPreferences(BaseModel):
    __tablename__ = "user_preferences"

    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    key = Column(String(255), nullable=False)
    value = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


__all__ = ["UserPreferences"]
