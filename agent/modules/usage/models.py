from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text

from agent.shared.infrastructure.db import BaseModel, utcnow


class LLMUsageEvent(BaseModel):
    __tablename__ = "llm_usage_events"

    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)
    thread_id = Column(String(512), nullable=False, default="", index=True)
    root_thread_id = Column(String(512), nullable=False, default="", index=True)
    platform = Column(String(50), nullable=False, default="unknown", index=True)
    user_id = Column(String(255), nullable=False, default="", index=True)
    channel_id = Column(String(255), nullable=False, default="", index=True)
    agent_name = Column(String(255), nullable=False, default="", index=True)
    provider_name = Column(String(255), nullable=False, default="", index=True)
    model_name = Column(String(255), nullable=False, default="", index=True)
    call_kind = Column(String(64), nullable=False, default="agent", index=True)
    internal = Column(Boolean, nullable=False, default=False, index=True)
    has_usage_metadata = Column(Boolean, nullable=False, default=False, index=True)
    input_tokens = Column(Integer, nullable=True)
    output_tokens = Column(Integer, nullable=True)
    total_tokens = Column(Integer, nullable=True)
    input_token_details_json = Column(Text, nullable=True)
    output_token_details_json = Column(Text, nullable=True)
    usage_metadata_json = Column(Text, nullable=True)
    run_id = Column(String(64), nullable=False, default="", index=True)
    parent_run_id = Column(String(64), nullable=False, default="", index=True)


__all__ = ["LLMUsageEvent"]
