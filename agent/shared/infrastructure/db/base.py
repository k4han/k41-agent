from datetime import datetime, timezone

from sqlalchemy import Column, Integer
from sqlalchemy.orm import declarative_base

Base = declarative_base()


def utcnow() -> datetime:
    """Return the current UTC datetime."""
    return datetime.now(timezone.utc)


class BaseModel(Base):
    __abstract__ = True

    id = Column(Integer, primary_key=True, autoincrement=True)


__all__ = ["Base", "BaseModel", "utcnow"]
