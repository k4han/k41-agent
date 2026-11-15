"""Durable task admission, executions, and parent notifications."""

from sqlalchemy import Column, Float, Integer, String, Text

from agent.shared.infrastructure.db.base import BaseModel


class SubagentTaskRecord(BaseModel):
    __tablename__ = "subagent_tasks"

    task_id = Column(String(64), nullable=False, unique=True)
    request_key = Column(String(64), nullable=False, unique=True)
    parent_thread_id = Column(String(512), nullable=False, index=True)
    root_thread_id = Column(String(512), nullable=False, index=True)
    thread_id = Column(String(512), nullable=False, unique=True)
    tool_call_id = Column(String(255), nullable=False)
    parent_message_id = Column(String(255), nullable=False, default="")
    agent_name = Column(String(255), nullable=False)
    request = Column(Text, nullable=False)
    context_json = Column(Text, nullable=False)
    parent_context_json = Column(Text, nullable=False)
    status = Column(String(32), nullable=False, default="pending", index=True)
    version = Column(Integer, nullable=False, default=1)
    active_job_id = Column(String(64), nullable=False, default="")
    checkpoint_id = Column(String(64), nullable=True)
    result = Column(Text, nullable=False, default="")
    error = Column(Text, nullable=False, default="")
    interrupts_json = Column(Text, nullable=False, default="[]")
    created_at = Column(Float, nullable=False)
    updated_at = Column(Float, nullable=False)
    deleted_at = Column(Float, nullable=True)


class SubagentExecutionRecord(BaseModel):
    __tablename__ = "subagent_executions"

    job_id = Column(String(64), nullable=False, unique=True)
    request_key = Column(String(64), nullable=False, unique=True)
    task_id = Column(String(64), nullable=False, default="", index=True)
    thread_id = Column(String(512), nullable=False, index=True)
    root_thread_id = Column(String(512), nullable=False, index=True)
    kind = Column(String(32), nullable=False)
    status = Column(String(32), nullable=False, default="pending", index=True)
    context_json = Column(Text, nullable=False)
    input_json = Column(Text, nullable=False)
    resume_json = Column(Text, nullable=True)
    lease_owner = Column(String(64), nullable=False, default="")
    lease_until = Column(Float, nullable=False, default=0)
    attempt = Column(Integer, nullable=False, default=0)
    error = Column(Text, nullable=False, default="")
    created_at = Column(Float, nullable=False)
    updated_at = Column(Float, nullable=False)


class SubagentNotificationRecord(BaseModel):
    __tablename__ = "subagent_notifications"

    event_id = Column(String(128), nullable=False, unique=True)
    task_id = Column(String(64), nullable=False, index=True)
    job_id = Column(String(64), nullable=False)
    parent_thread_id = Column(String(512), nullable=False, index=True)
    root_thread_id = Column(String(512), nullable=False, index=True)
    version = Column(Integer, nullable=False)
    payload_json = Column(Text, nullable=False)
    context_json = Column(Text, nullable=False)
    status = Column(String(32), nullable=False, default="pending", index=True)
    wake_job_id = Column(String(64), nullable=False, default="", index=True)
    created_at = Column(Float, nullable=False)


__all__ = ["SubagentTaskRecord", "SubagentExecutionRecord", "SubagentNotificationRecord"]
