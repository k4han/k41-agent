"""Durable Instabox operation tracking and encrypted private port tokens."""

from sqlalchemy import Column, DateTime, Integer, String, Text, UniqueConstraint

from agent.shared.infrastructure.db import BaseModel
from agent.shared.infrastructure.db.base import utcnow


class InstaboxOperation(BaseModel):
    """A long-running Instabox change that must survive reloads and restarts.

    The row is written with its ``Idempotency-Key`` before the API call so a
    crash between the request and the response can be resent safely.
    """

    __tablename__ = "instabox_operations"

    idempotency_key = Column(String(200), nullable=False, unique=True)
    kind = Column(String(32), nullable=False, index=True)
    operation_id = Column(String(64), nullable=True, index=True)
    machine_id = Column(String(64), nullable=True, index=True)
    target_json = Column(Text, nullable=False, default="{}")
    status = Column(String(16), nullable=False, default="submitting", index=True)
    phase = Column(String(64), nullable=True)
    result_json = Column(Text, nullable=False, default="null")
    error_json = Column(Text, nullable=False, default="null")
    created_by = Column(String(255), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)


class InstaboxPortToken(BaseModel):
    """Encrypted token of a private preview URL; Instabox shows it only once."""

    __tablename__ = "instabox_port_tokens"

    machine_id = Column(String(64), nullable=False)
    port = Column(Integer, nullable=False)
    token_encrypted = Column(Text, nullable=False)
    url = Column(Text, nullable=False, default="")
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    __table_args__ = (UniqueConstraint("machine_id", "port", name="uq_instabox_port_token"),)


__all__ = ["InstaboxOperation", "InstaboxPortToken"]
