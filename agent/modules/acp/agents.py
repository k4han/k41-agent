"""Definitions of the ACP agents K41 knows how to run."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ANTIGRAVITY_CLI_AGENT_ID = "antigravity-cli"


@dataclass(frozen=True)
class AcpAgentDefinition:
    """Static facts about one agent, independent of what is installed."""

    id: str
    display_name: str
    installation_source: Literal["path"] = "path"
    terms_url: str = ""


AGENT_DEFINITIONS: dict[str, AcpAgentDefinition] = {
    ANTIGRAVITY_CLI_AGENT_ID: AcpAgentDefinition(
        id=ANTIGRAVITY_CLI_AGENT_ID,
        display_name="Google Antigravity (agy acp)",
        terms_url="https://antigravity.google/terms",
    ),
}


def get_agent_definition(agent_id: str) -> AcpAgentDefinition | None:
    return AGENT_DEFINITIONS.get(agent_id)
