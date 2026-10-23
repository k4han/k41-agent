"""Persistent project catalog, independent of conversation lifetime."""

from sqlalchemy import Boolean, CheckConstraint, Column, DateTime, Integer, String, Text, UniqueConstraint

from agent.shared.infrastructure.db.base import BaseModel, utcnow


class Project(BaseModel):
    __tablename__ = "projects"

    key = Column(Text, nullable=False, unique=True)
    label = Column(String(255), nullable=False)
    status = Column(String(20), nullable=False, default="active", index=True)
    is_pinned = Column(Boolean, nullable=False, default=False)
    workspace_json = Column(Text, nullable=False)
    migration_status = Column(String(30), nullable=False, default="pending")
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    __table_args__ = (
        CheckConstraint("status IN ('active', 'archived', 'removed')", name="ck_projects_status"),
    )


class ProjectWorkspace(BaseModel):
    """Durable files belonging to a project, independent of execution instances."""

    __tablename__ = "project_workspaces"

    project_id = Column(Integer, nullable=False, index=True)
    identity_key = Column(Text, nullable=False)
    kind = Column(String(30), nullable=False, default="shared")
    source_json = Column(Text, nullable=False)
    runtime_aliases_json = Column(Text, nullable=False, default="[]")
    head_revision = Column(String(64), nullable=True)
    previous_revision = Column(String(64), nullable=True)
    persistence_status = Column(String(30), nullable=False, default="dirty")
    last_saved_at = Column(DateTime(timezone=True), nullable=True)
    last_error = Column(Text, nullable=False, default="")
    pinned_revision = Column(String(64), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    __table_args__ = (UniqueConstraint("project_id", "identity_key", name="uq_project_workspace_identity"),)


class WorkspaceRuntime(BaseModel):
    """The replaceable execution instance of one durable workspace."""

    __tablename__ = "workspace_runtimes"

    project_workspace_id = Column(Integer, nullable=False, unique=True)
    execution_json = Column(Text, nullable=False)
    generation = Column(Integer, nullable=False, default=1)
    status = Column(String(30), nullable=False, default="ready")
    transition_json = Column(Text, nullable=False, default="null")
    # Re-provision decision ("sandbox_lost") waiting for the user to confirm how
    # the workspace should be recovered; "{}" when no decision is pending.
    pending_decision_json = Column(Text, nullable=False, default="{}")
    last_used_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
