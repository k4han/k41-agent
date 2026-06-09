from sqlalchemy import Column, DateTime, String, UniqueConstraint

from agent.shared.infrastructure.db import BaseModel, utcnow


class ConversationThread(BaseModel):
    __tablename__ = "conversation_threads"

    thread_id = Column(String(512), nullable=False, index=True)
    platform = Column(String(50), nullable=False, default="unknown", index=True)
    user_id = Column(String(255), nullable=False, default="", index=True)
    channel_id = Column(String(255), nullable=False, default="")
    agent_name = Column(String(255), nullable=False, default="")
    provider = Column(String(255), nullable=False, default="")
    model = Column(String(255), nullable=False, default="")
    title = Column(String(255), nullable=False, default="")
    kind = Column(String(50), nullable=False, default="user", index=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        default=utcnow,
        onupdate=utcnow,
        nullable=False,
        index=True,
    )
    deleted_at = Column(DateTime(timezone=True), nullable=True, index=True)

    __table_args__ = (
        UniqueConstraint("thread_id", name="uq_conversation_threads_thread_id"),
    )


__all__ = ["ConversationThread"]
