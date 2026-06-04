from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator

from agent.modules.workspaces import WorkspaceRef


class ChatAttachment(BaseModel):
    name: str
    mime_type: str = ""
    size: int = 0
    kind: Literal["text", "image"]
    content: Optional[str] = None
    base64: Optional[str] = None


class PlanResumePayload(BaseModel):
    action: Literal["approve", "revise"]
    target_agent: Optional[str] = None
    feedback: Optional[str] = None

    @model_validator(mode="after")
    def _validate_action_payload(self) -> "PlanResumePayload":
        if self.action == "approve":
            if not (self.target_agent or "").strip():
                raise ValueError("target_agent is required when approving a plan.")
        elif not (self.feedback or "").strip():
            raise ValueError("feedback is required when revising a plan.")
        return self


class ChatRequest(BaseModel):
    message: str
    user_id: str = "anonymous"
    thread_id: Optional[str] = None
    new_thread: bool = False
    checkpoint_id: Optional[str] = None
    workflow: Optional[str] = None
    workspace: Optional[WorkspaceRef] = None
    agent_name: Optional[str] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    resume_payload: Optional[PlanResumePayload] = None
    attachments: list[ChatAttachment] = Field(default_factory=list)
    resume: bool = False


class ChatResponse(BaseModel):
    response: str
    thread_id: str
    workflow: str


class PairingCodeResponse(BaseModel):
    user_id: str
    pairing_code: str


class ReconnectRequest(BaseModel):
    thread_id: str


class EditChatRequest(BaseModel):
    message: str = Field(min_length=1)
    user_id: str = "anonymous"
    thread_id: str = Field(min_length=1)
    message_index: int = Field(ge=0)
    source_checkpoint_id: str = Field(min_length=1)
    workflow: Optional[str] = None
    workspace: Optional[WorkspaceRef] = None
    agent_name: Optional[str] = None
    provider: Optional[str] = None
    model: Optional[str] = None
