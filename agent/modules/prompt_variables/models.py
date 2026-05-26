from sqlalchemy import Column, DateTime, String, Text, UniqueConstraint

from agent.shared.infrastructure.db.base import BaseModel, utcnow


class PromptVariable(BaseModel):
    __tablename__ = "prompt_variables"

    name = Column(String(64), nullable=False, index=True)
    value = Column(Text, nullable=False, default="")
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("name", name="uq_prompt_variables_name"),
    )


__all__ = ["PromptVariable"]
