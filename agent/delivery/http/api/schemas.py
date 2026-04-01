from typing import Optional

from pydantic import BaseModel


class ChatRequest(BaseModel):
    message: str
    user_id: str = "anonymous"
    workflow: Optional[str] = None
    service_type: str = "default"
    working_dir: Optional[str] = None
    agent_name: Optional[str] = None


class ChatResponse(BaseModel):
    response: str
    thread_id: str
    workflow: str
