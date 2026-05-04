from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text

from agent.shared.infrastructure.db import BaseModel, utcnow


class BotSettings(BaseModel):
    __tablename__ = "bot_settings"

    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    platform = Column(String(50), nullable=False)
    enabled = Column(Boolean, default=True, nullable=False)
    config_json = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


__all__ = ["BotSettings"]
