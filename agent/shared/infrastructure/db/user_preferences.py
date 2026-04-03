"""User preferences database model."""

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text

from agent.shared.infrastructure.db.base import BaseModel, utcnow


class UserPreferences(BaseModel):
    """Store user-specific preference overrides.

    This table allows per-user settings that override global config.
    Currently not actively used but reserved for future features.
    """

    __tablename__ = "user_preferences"

    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    key = Column(String(255), nullable=False)
    value = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


__all__ = ["UserPreferences"]
