"""Definitions of the ACP agents K41 knows how to run."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ANTIGRAVITY_CLI_AGENT_ID = "antigravity-cli"
OPENCODE_AGENT_ID = "opencode"


@dataclass(frozen=True)
class AcpAgentDefinition:
    """Static facts about one agent, independent of what is installed."""

    id: str
    display_name: str
    installation_source: Literal["path"] = "path"
    terms_url: str = ""
    license: str = "proprietary"
    docs_url: str = ""
    login_command: str = ""
    update_command: str = ""


AGENT_DEFINITIONS: dict[str, AcpAgentDefinition] = {
    ANTIGRAVITY_CLI_AGENT_ID: AcpAgentDefinition(
        id=ANTIGRAVITY_CLI_AGENT_ID,
        display_name="Google Antigravity (agy acp)",
        terms_url="https://antigravity.google/terms",
        license="proprietary",
        docs_url="https://antigravity.google/docs/ide/extensions/acp",
        login_command="agy acp --login",
        update_command="agy update",
    ),
    OPENCODE_AGENT_ID: AcpAgentDefinition(
        id=OPENCODE_AGENT_ID,
        display_name="OpenCode (opencode acp)",
        terms_url="https://opencode.ai",
        license="MIT",
        docs_url="https://opencode.ai",
        login_command="opencode auth login",
        update_command="npm i -g @opencode/cli@latest",
    ),
}


def get_agent_definition(agent_id: str) -> AcpAgentDefinition | None:
    return AGENT_DEFINITIONS.get(agent_id)
