from sqlalchemy import Column, Float, Integer, String, Text, UniqueConstraint

from agent.shared.infrastructure.db.base import BaseModel


class InterruptionRecord(BaseModel):
    __tablename__ = "notification_interruptions"

    key = Column(String(64), nullable=False, unique=True)
    thread_id = Column(String(512), nullable=False, index=True)
    root_thread_id = Column(String(512), nullable=False)
    job_id = Column(String(64), nullable=False, index=True)
    checkpoint_id = Column(String(64), nullable=False)
    interrupt_id = Column(String(128), nullable=False)
    context_json = Column(Text, nullable=False)
    request_json = Column(Text, nullable=False)
    fallback_prompt = Column(Text, nullable=False)
    timeout_minutes = Column(Integer, nullable=False)
    deadline = Column(Float, nullable=False, index=True)
    notify_at = Column(Float, nullable=False)
    status = Column(String(20), nullable=False, index=True)


class NotificationDelivery(BaseModel):
    __tablename__ = "notification_deliveries"
    __table_args__ = (UniqueConstraint("event_key", "identity_id"),)

    event_key = Column(String(128), nullable=False)
    identity_id = Column(Integer, nullable=False)
    user_id = Column(Integer, nullable=False)
    event_type = Column(String(32), nullable=False)
    interruption_key = Column(String(64), nullable=True, index=True)
    thread_id = Column(String(512), nullable=False)
    text = Column(Text, nullable=False)
    status = Column(String(20), nullable=False, index=True)
    attempts = Column(Integer, nullable=False, default=0)
    due_at = Column(Float, nullable=False, index=True)
    token = Column(String(32), nullable=True, unique=True)
    telegram_json = Column(Text, nullable=False, default="{}")
    draft_json = Column(Text, nullable=False, default="{}")
