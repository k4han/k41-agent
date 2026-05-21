from sqlalchemy import Column, DateTime, String, Text, UniqueConstraint

from agent.shared.infrastructure.db.base import BaseModel, utcnow


class ThreadWorkspace(BaseModel):
    __tablename__ = "thread_workspaces"

    thread_id = Column(String(512), nullable=False, index=True)
    working_dir = Column(Text, nullable=True)
    workspace_backend = Column(String(50), nullable=True)
    workspace_locator = Column(Text, nullable=True)
    workspace_label = Column(Text, nullable=True)
    workspace_metadata_json = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        default=utcnow,
        onupdate=utcnow,
        nullable=False,
        index=True,
    )

    __table_args__ = (
        UniqueConstraint("thread_id", name="uq_thread_workspaces_thread_id"),
    )


__all__ = ["ThreadWorkspace"]
