from typing import Literal, Optional

from pydantic import BaseModel, Field

from agent.modules.workspaces import WorkspaceRef


class ChatAttachment(BaseModel):
    name: str
    mime_type: str = ""
    size: int = 0
    kind: Literal["text", "image"]
    content: Optional[str] = None
    base64: Optional[str] = None


class ChatRequest(BaseModel):
    message: str
    user_id: str = "anonymous"
    thread_id: Optional[str] = None
    new_thread: bool = False
    workflow: Optional[str] = None
    workspace: Optional[WorkspaceRef] = None
    agent_name: Optional[str] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    attachments: list[ChatAttachment] = Field(default_factory=list)


class ChatResponse(BaseModel):
    response: str
    thread_id: str
    workflow: str


class PairingCodeResponse(BaseModel):
    user_id: str
    pairing_code: str
