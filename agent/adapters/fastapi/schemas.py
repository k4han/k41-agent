# agent/adapters/fastapi/schemas.py

from pydantic import BaseModel
from typing import Optional


class ChatRequest(BaseModel):
    message:      str
    user_id:      str             = "anonymous"
    workflow:     str             = "chat_agent"
    service_type: str             = "default"
    working_dir:  Optional[str]   = None


class ChatResponse(BaseModel):
    response:   str
    thread_id:  str
    workflow:   str
