from sqlalchemy import Boolean, Column, DateTime, Index, Integer, String, Text

from agent.shared.infrastructure.db import BaseModel, utcnow


class LLMUsageEvent(BaseModel):
    __tablename__ = "llm_usage_events"

    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)
    thread_id = Column(String(512), nullable=False, default="")
    root_thread_id = Column(String(512), nullable=False, default="", index=True)
    platform = Column(String(50), nullable=False, default="unknown")
    user_id = Column(String(255), nullable=False, default="")
    channel_id = Column(String(255), nullable=False, default="")
    agent_name = Column(String(255), nullable=False, default="")
    provider_name = Column(String(255), nullable=False, default="")
    model_name = Column(String(255), nullable=False, default="")
    call_kind = Column(String(64), nullable=False, default="agent")
    internal = Column(Boolean, nullable=False, default=False)
    has_usage_metadata = Column(Boolean, nullable=False, default=False)
    input_tokens = Column(Integer, nullable=True)
    output_tokens = Column(Integer, nullable=True)
    total_tokens = Column(Integer, nullable=True)
    input_token_details_json = Column(Text, nullable=True)
    output_token_details_json = Column(Text, nullable=True)
    usage_metadata_json = Column(Text, nullable=True)
    run_id = Column(String(64), nullable=False, default="")
    parent_run_id = Column(String(64), nullable=False, default="")

    # Keep time-only scans for retention and unfiltered dashboard queries.
    # Equality filters precede the time range in the remaining dashboard indexes.
    __table_args__ = (
        Index("ix_llm_usage_events_thread_created_id", "thread_id", "created_at", "id"),
        Index("ix_llm_usage_events_platform_created", "platform", "created_at"),
        Index("ix_llm_usage_events_user_created", "user_id", "created_at"),
        Index("ix_llm_usage_events_channel_created", "channel_id", "created_at"),
        Index("ix_llm_usage_events_agent_created", "agent_name", "created_at"),
        Index("ix_llm_usage_events_provider_created", "provider_name", "created_at"),
        Index("ix_llm_usage_events_model_created", "model_name", "created_at"),
        Index("ix_llm_usage_events_call_kind_created", "call_kind", "created_at"),
    )


__all__ = ["LLMUsageEvent"]
