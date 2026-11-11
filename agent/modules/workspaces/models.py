from sqlalchemy import Column, DateTime, Integer, String, Text, UniqueConstraint

from agent.shared.infrastructure.db.base import BaseModel, utcnow


class ThreadWorkspace(BaseModel):
    __tablename__ = "thread_workspaces"

    thread_id = Column(String(512), nullable=False, index=True)
    host_mirror_json = Column(Text, nullable=False, default="{}")
    sync_policy = Column(String(30), nullable=False, default="manual_apply", server_default="mirror_strict")
    working_dir = Column(Text, nullable=True)
    project_id = Column(Integer, nullable=True, index=True)
    project_workspace_id = Column(Integer, nullable=True, index=True)
    scope_key = Column(Text, nullable=True, index=True)
    scope_kind = Column(String(50), nullable=True)
    scope_label = Column(Text, nullable=True)
    scope_metadata_json = Column(Text, nullable=True)
    execution_backend = Column(String(50), nullable=True)
    execution_locator = Column(Text, nullable=True)
    execution_label = Column(Text, nullable=True)
    execution_metadata_json = Column(Text, nullable=True)
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


class ThreadMachine(BaseModel):
    """A machine or snapshot that an agent created for one conversation.

    This table, not provider metadata, decides which auxiliary resources a
    conversation may use: provider metadata can be edited from outside.
    ``thread_id`` is always the root conversation so sub-agents share it.
    """

    __tablename__ = "thread_machines"

    thread_id = Column(String(512), nullable=False, index=True)
    kind = Column(String(16), nullable=False, default="machine")
    backend = Column(String(50), nullable=False)
    locator = Column(String(255), nullable=False)
    root = Column(Text, nullable=False, default="")
    purpose = Column(Text, nullable=False, default="")
    status = Column(String(16), nullable=False, default="creating", index=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    last_used_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("kind", "backend", "locator", name="uq_thread_machines_resource"),
    )


__all__ = ["ThreadMachine", "ThreadWorkspace"]
