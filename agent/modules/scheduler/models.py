from sqlalchemy import Column, DateTime, Index, String, Text

from agent.shared.infrastructure.db import BaseModel, utcnow


class SchedulerJobRun(BaseModel):
    """One execution of a scheduled job, kept as run history."""

    __tablename__ = "scheduler_job_runs"

    run_id = Column(String(64), nullable=False, unique=True)
    job_id = Column(String(191), nullable=False, default="", index=True)
    task = Column(Text, nullable=False, default="")
    platform = Column(String(50), nullable=False, default="")
    user_id = Column(String(255), nullable=False, default="")
    trigger_source = Column(String(32), nullable=False, default="schedule")
    status = Column(String(32), nullable=False, default="running", index=True)
    thread_id = Column(String(512), nullable=False, default="")
    agent_name = Column(String(255), nullable=False, default="")
    provider = Column(String(255), nullable=False, default="")
    model = Column(String(255), nullable=False, default="")
    response = Column(Text, nullable=False, default="")
    error = Column(Text, nullable=False, default="")
    started_at = Column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_scheduler_job_runs_job_started", "job_id", "started_at"),
    )


__all__ = ["SchedulerJobRun"]
