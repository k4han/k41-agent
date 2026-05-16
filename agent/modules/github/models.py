from sqlalchemy import BigInteger, Boolean, Column, DateTime, Integer, String, Text, UniqueConstraint

from agent.shared.infrastructure.db import BaseModel, utcnow


class GitHubInstallation(BaseModel):
    __tablename__ = "github_installations"

    installation_id = Column(BigInteger, unique=True, nullable=False, index=True)
    account_login = Column(String(255), nullable=False)
    account_type = Column(String(50), nullable=False, default="")
    repository_selection = Column(String(50), nullable=False, default="")
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class GitHubRepositoryBinding(BaseModel):
    __tablename__ = "github_repository_bindings"

    repository_id = Column(BigInteger, unique=True, nullable=False, index=True)
    installation_id = Column(BigInteger, nullable=False, index=True)
    full_name = Column(String(255), unique=True, nullable=False, index=True)
    account_login = Column(String(255), nullable=False, default="")
    private = Column(Boolean, default=False, nullable=False)
    default_branch = Column(String(255), nullable=False, default="main")
    enabled = Column(Boolean, default=False, nullable=False)
    agent_name = Column(String(255), nullable=False, default="")
    trigger_label = Column(String(255), nullable=False, default="")
    mention_triggers_json = Column(Text, nullable=False, default="[]")
    last_synced_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class GitHubWebhookDelivery(BaseModel):
    __tablename__ = "github_webhook_deliveries"

    delivery_id = Column(String(255), nullable=False)
    event = Column(String(100), nullable=False, default="")
    action = Column(String(100), nullable=False, default="")
    repository_full_name = Column(String(255), nullable=False, default="")
    received_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("delivery_id", name="uq_github_webhook_delivery_id"),
    )


__all__ = [
    "GitHubInstallation",
    "GitHubRepositoryBinding",
    "GitHubWebhookDelivery",
]
