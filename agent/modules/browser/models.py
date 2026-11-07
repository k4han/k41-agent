"""Durable browser ownership, profiles, invocations and image metadata."""

from typing import Literal

from pydantic import BaseModel as Schema, ConfigDict, Field
from sqlalchemy import Column, Integer, String, Text, UniqueConstraint

from agent.shared.infrastructure.db import BaseModel


class BrowserAccessDenied(PermissionError):
    """The caller does not own this browser data. File system permission errors are different."""


class BrowserProfile(BaseModel):
    __tablename__ = "browser_profiles"

    profile_id = Column(String(64), unique=True, nullable=False)
    owner_id = Column(String(255), nullable=False, index=True)
    name = Column(String(100), nullable=False)


class BrowserProjectProfile(BaseModel):
    """The login profile an account's new conversations in a project start with."""

    __tablename__ = "browser_project_profiles"

    project_id = Column(Integer, nullable=False)
    owner_id = Column(String(255), nullable=False)
    mode = Column(String(16), nullable=False, default="profile", server_default="profile")
    profile_id = Column(String(64), nullable=True, index=True)

    __table_args__ = (UniqueConstraint("project_id", "owner_id", name="uq_browser_project_owner"),)


class BrowserAccountProfile(BaseModel):
    """Optional default login profile for an authenticated account."""

    __tablename__ = "browser_account_profiles"

    owner_id = Column(String(255), nullable=False, unique=True)
    profile_id = Column(String(64), nullable=True, index=True)


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
