"""Persistent project catalog, independent of conversation lifetime."""

from sqlalchemy import Boolean, CheckConstraint, Column, DateTime, String, Text

from agent.shared.infrastructure.db.base import BaseModel, utcnow


class Project(BaseModel):
    __tablename__ = "projects"

    key = Column(Text, nullable=False, unique=True)
    label = Column(String(255), nullable=False)
    status = Column(String(20), nullable=False, default="active", index=True)
    is_pinned = Column(Boolean, nullable=False, default=False)
    workspace_json = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    __table_args__ = (
        CheckConstraint("status IN ('active', 'archived', 'removed')", name="ck_projects_status"),
    )
