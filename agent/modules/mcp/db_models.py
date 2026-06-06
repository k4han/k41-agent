from __future__ import annotations

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy import UniqueConstraint

from agent.shared.infrastructure.db import BaseModel, utcnow


class MCPCredential(BaseModel):
    __tablename__ = "mcp_credentials"

    credential_ref = Column(String(128), unique=True, nullable=False, index=True)
    kind = Column(String(50), nullable=False, default="secret")
    payload_json = Column(Text, nullable=False, default="")
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class MCPServerInstall(BaseModel):
    __tablename__ = "mcp_server_installs"

    server_name = Column(String(255), unique=True, nullable=False, index=True)
    registry_name = Column(String(255), nullable=False, default="", index=True)
    registry_version = Column(String(255), nullable=False, default="")
    source_type = Column(String(50), nullable=False, default="custom")
    title = Column(String(255), nullable=False, default="")
    description = Column(Text, nullable=False, default="")
    verified = Column(Boolean, default=False, nullable=False)
    transport = Column(String(50), nullable=False, default="stdio")
    command = Column(Text, nullable=False, default="")
    args_json = Column(Text, nullable=False, default="[]")
    url = Column(Text, nullable=False, default="")
    env_template_json = Column(Text, nullable=False, default="{}")
    headers_template_json = Column(Text, nullable=False, default="{}")
    credential_ref = Column(String(128), nullable=False, default="")
    registry_metadata_json = Column(Text, nullable=False, default="{}")
    enabled = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class AgentMCPInstall(BaseModel):
    __tablename__ = "agent_mcp_installs"

    agent_name = Column(String(255), nullable=False, index=True)
    mcp_server_install_id = Column(
        Integer,
        ForeignKey("mcp_server_installs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    enabled = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "agent_name",
            "mcp_server_install_id",
            name="uq_agent_mcp_installs_agent_server",
        ),
    )


__all__ = ["AgentMCPInstall", "MCPCredential", "MCPServerInstall"]
