"""Durable browser ownership, profiles, invocations and image metadata."""

from typing import Literal

from pydantic import BaseModel as Schema, ConfigDict, Field
from sqlalchemy import Column, String, Text

from agent.shared.infrastructure.db import BaseModel


class BrowserProfile(BaseModel):
    __tablename__ = "browser_profiles"

    profile_id = Column(String(64), unique=True, nullable=False)
    owner_id = Column(String(255), nullable=False, index=True)
    name = Column(String(100), nullable=False)


class BrowserBinding(BaseModel):
    __tablename__ = "browser_bindings"

    thread_id = Column(String(512), unique=True, nullable=False)
    owner_id = Column(String(255), nullable=False, index=True)
    profile_id = Column(String(64), nullable=True)


class BrowserInvocation(BaseModel):
    __tablename__ = "browser_invocations"

    invocation_id = Column(String(64), unique=True, nullable=False)
    thread_id = Column(String(512), nullable=False, index=True)
    arguments_hash = Column(String(64), nullable=False)
    status = Column(String(24), nullable=False)
    result = Column(Text, nullable=True)


class BrowserArtifact(BaseModel):
    __tablename__ = "browser_artifacts"

    artifact_id = Column(String(64), unique=True, nullable=False)
    owner_id = Column(String(255), nullable=False, index=True)
    thread_id = Column(String(512), nullable=False, index=True)
    mime_type = Column(String(32), nullable=False)


class BrowserHandoffResume(Schema):
    model_config = ConfigDict(extra="forbid")

    action: Literal["browser_handoff"]
    handoff_id: str = Field(min_length=1, max_length=64)
    decision: Literal["return", "cancel"]
