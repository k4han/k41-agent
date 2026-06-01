from sqlalchemy import Boolean, Column, DateTime, String, Text, UniqueConstraint

from agent.shared.infrastructure.db.base import BaseModel, utcnow


class RuntimeSetting(BaseModel):
    __tablename__ = "runtime_settings"

    key = Column(String(512), nullable=False, unique=True, index=True)
    value_json = Column(Text, nullable=False)
    encrypted = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("key", name="uq_runtime_settings_key"),
    )


__all__ = ["RuntimeSetting"]
