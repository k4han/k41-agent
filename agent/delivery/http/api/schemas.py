from typing import Optional

from pydantic import BaseModel


class ChatRequest(BaseModel):
    message: str
    user_id: str = "anonymous"
    thread_id: Optional[str] = None
    new_thread: bool = False
    workflow: Optional[str] = None
    working_dir: Optional[str] = None
    agent_name: Optional[str] = None
    provider: Optional[str] = None
    model: Optional[str] = None


class ChatResponse(BaseModel):
    response: str
    thread_id: str
    workflow: str


class PairingCodeResponse(BaseModel):
    user_id: str
    pairing_code: str
