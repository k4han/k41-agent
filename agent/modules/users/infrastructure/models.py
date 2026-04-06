from sqlalchemy import Column, String, Boolean, DateTime, ForeignKey, UniqueConstraint, Integer

from agent.shared.infrastructure.db.base import BaseModel, utcnow


class User(BaseModel):
    __tablename__ = "users"

    is_active = Column(Boolean, default=True, nullable=False)
    password_hash = Column(String(255), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class UserIdentity(BaseModel):
    __tablename__ = "user_identities"

    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    platform = Column(String(50), nullable=False)
    external_id = Column(String(255), nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("platform", "external_id", name="uq_platform_external_id"),
    )


class PairingCode(BaseModel):
    __tablename__ = "pairing_codes"

    code = Column(String(32), unique=True, nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    is_used = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


__all__ = ["User", "UserIdentity", "PairingCode"]
