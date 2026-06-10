from typing import Literal, Optional

from pydantic import BaseModel, Field

from agent.modules.tools import HumanResumePayload, PlanResumePayload
from agent.modules.workspaces import WorkspaceRef


class ChatAttachment(BaseModel):
    """An attachment included with a chat message (text or image)."""

    name: str = Field(..., description="Filename or label for the attachment.")
    mime_type: str = Field(default="", description="MIME type of the attachment (e.g. 'text/plain', 'image/png').")
    size: int = Field(default=0, description="Size of the attachment in bytes.")
    kind: Literal["text", "image"] = Field(..., description="Type of attachment content.")
    content: Optional[str] = Field(default=None, description="Text content when kind is 'text'.")
    base64: Optional[str] = Field(default=None, description="Base64-encoded content when kind is 'image'.")


class ChatRequest(BaseModel):
    """Request body for sending a chat message to the agent."""

    message: str = Field(..., description="The user message to send to the agent.")
    user_id: str = Field(default="anonymous", description="Identifier for the user sending the message.")
    thread_id: Optional[str] = Field(default=None, description="Existing thread ID to continue. Omit to create a new thread.")
    new_thread: bool = Field(default=False, description="Force creation of a new conversation thread.")
    checkpoint_id: Optional[str] = Field(default=None, description="Specific checkpoint to resume from within a thread.")
    workflow: Optional[str] = Field(default=None, description="Workflow/graph name to use. Defaults to the agent's default workflow.")
    workspace: Optional[WorkspaceRef] = Field(default=None, description="Workspace reference for file operations. Required for dashboard chats.")
    agent_name: Optional[str] = Field(default=None, description="Agent card name to use. Defaults to 'default'.")
    provider: Optional[str] = Field(default=None, description="LLM provider name override.")
    model: Optional[str] = Field(default=None, description="LLM model name override.")
    resume_payload: Optional[HumanResumePayload] = Field(default=None, description="Payload to resume after a human-in-the-loop pause.")
    attachments: list[ChatAttachment] = Field(default_factory=list, description="File or image attachments to include with the message.")
    resume: bool = Field(default=False, description="Whether this message is resuming a paused conversation.")


class ChatResponse(BaseModel):
    """Response body for a synchronous chat request."""

    response: str = Field(..., description="The agent's full text response.")
    thread_id: str = Field(..., description="The conversation thread ID.")
    workflow: str = Field(..., description="The workflow/graph that was executed.")


class GraphListResponse(BaseModel):
    """Response listing all registered workflow graphs."""

    graphs: list[str] = Field(..., description="List of registered workflow graph names.")


class ProviderSummary(BaseModel):
    """Summary of an LLM provider."""

    name: str = Field(..., description="Provider identifier name.")
    type: str = Field(..., description="Provider type (e.g. 'openai', 'anthropic').")
    default_model: str = Field(..., description="Default model for this provider.")
    models: list[str] = Field(..., description="Available model names.")
    enabled: bool = Field(..., description="Whether the provider is enabled.")


class ProviderListResponse(BaseModel):
    """Response listing all configured LLM providers."""

    providers: list[ProviderSummary] = Field(..., description="List of configured providers.")


class ModelOption(BaseModel):
    """A single model option within a provider catalog."""

    id: str = Field(..., description="Model identifier.")
    label: str = Field(..., description="Display label for the model.")
    source: str = Field(default="", description="Source of the model info.")
    context_window: int | None = Field(default=None, description="Context window size in tokens.")
    input_types: list[str] | None = Field(default=None, description="Supported input types (e.g. 'text', 'image').")


class ModelCatalog(BaseModel):
    """Model catalog for a single provider."""

    provider: str = Field(..., description="Provider name.")
    provider_type: str = Field(..., description="Provider type.")
    default_model: str = Field(..., description="Default model identifier.")
    can_list_models: bool = Field(default=False, description="Whether the provider supports listing models.")
    models: list[ModelOption] = Field(default_factory=list, description="Available model options.")
    error: str | None = Field(default=None, description="Error message if catalog fetch failed.")


class ModelCatalogListResponse(BaseModel):
    """Response listing model catalogs for all providers."""

    providers: list[ModelCatalog] = Field(..., description="Model catalogs grouped by provider.")


class HealthResponse(BaseModel):
    """Health check response."""

    status: str = Field(..., description="Service status ('ok' or 'error').")
    graphs: list[str] = Field(..., description="Registered workflow graph names.")


class PairingCodeResponse(BaseModel):
    """Response body for a user pairing code generation request."""

    user_id: str = Field(..., description="The paired user ID.")
    pairing_code: str = Field(..., description="One-time pairing code for client authentication.")


class ReconnectRequest(BaseModel):
    """Request body for reconnecting to an active chat stream."""

    thread_id: str = Field(..., description="The thread ID to reconnect to.")


class EditChatRequest(BaseModel):
    """Request body for editing a previous message and re-running from that point."""

    message: str = Field(..., min_length=1, description="The edited user message content.")
    user_id: str = Field(default="anonymous", description="Identifier for the user.")
    thread_id: str = Field(..., min_length=1, description="The thread ID containing the message to edit.")
    message_index: int = Field(..., ge=0, description="Index of the message to edit within the thread.")
    source_checkpoint_id: str = Field(..., min_length=1, description="Checkpoint ID to fork from.")
    workflow: Optional[str] = Field(default=None, description="Workflow/graph name to use.")
    workspace: Optional[WorkspaceRef] = Field(default=None, description="Workspace reference for file operations.")
    agent_name: Optional[str] = Field(default=None, description="Agent card name. Defaults to 'default'.")
    provider: Optional[str] = Field(default=None, description="LLM provider name override.")
    model: Optional[str] = Field(default=None, description="LLM model name override.")
