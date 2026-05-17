from sqlalchemy import Column, DateTime, String, Text, UniqueConstraint

from agent.shared.infrastructure.db import BaseModel, utcnow


class BackgroundTaskRecord(BaseModel):
    __tablename__ = "background_tasks"

    task_id = Column(String(64), nullable=False, index=True)
    thread_id = Column(String(512), nullable=False, index=True)
    request = Column(Text, nullable=False, default="")
    agent_name = Column(String(255), nullable=False, default="default")
    working_dir = Column(Text, nullable=True)
    notify_platform = Column(String(50), nullable=False, default="")
    notify_external_id = Column(String(255), nullable=False, default="")
    notify_channel_id = Column(String(255), nullable=False, default="")
    status = Column(String(50), nullable=False, default="pending", index=True)
    result = Column(Text, nullable=False, default="")
    error = Column(Text, nullable=False, default="")
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False, index=True)
    deleted_at = Column(DateTime(timezone=True), nullable=True, index=True)

    __table_args__ = (
        UniqueConstraint("task_id", name="uq_background_tasks_task_id"),
        UniqueConstraint("thread_id", name="uq_background_tasks_thread_id"),
    )


__all__ = ["BackgroundTaskRecord"]
