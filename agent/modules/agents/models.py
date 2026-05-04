"""Agent configuration model loaded from MD files."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class AgentConfig(BaseModel):
    """Configuration for a single agent defined via a Markdown file."""

    name: str
    display_name: str = ""
    description: str = ""
    graph_type: str  # Registered workflow name
    model: str = ""
    tools: list[str] = Field(default_factory=list)
    sub_agents: Optional[list[str]] = None  # None = leaf (no call_agent), list = allowed targets
    max_context_tokens: int = 50_000
    routing_hints: str = ""
    capabilities: list[str] = Field(default_factory=list)
    system_prompt: str = ""  # Markdown body content (after frontmatter)
