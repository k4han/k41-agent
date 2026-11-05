"""Definitions of the ACP agents K41 knows how to run."""

from __future__ import annotations

from dataclasses import dataclass

ANTIGRAVITY_AGENT_ID = "antigravity-acp"


@dataclass(frozen=True)
class AcpAgentDefinition:
    """Static facts about one agent, independent of what is installed."""

    id: str
    display_name: str
    # Environment variable that relocates the agent's credentials and settings.
    # Isolation also shields K41 from hooks other applications install in the
    # agent's default home, which can make every tool call fail.
    home_env: str
    # Maps an auth method id to the environment variable holding its API key.
    api_key_env_by_method: tuple[tuple[str, str], ...] = ()
    # Only archives from these hosts are accepted, whatever the registry says.
    allowed_download_hosts: tuple[str, ...] = ()
    # Auth methods this release supports, in display order.
    supported_auth_methods: tuple[str, ...] = ()
    # A sign-in link is only shown to the user if it points at one of these.
    allowed_auth_hosts: tuple[str, ...] = ()
    terms_url: str = ""


AGENT_DEFINITIONS: dict[str, AcpAgentDefinition] = {
    ANTIGRAVITY_AGENT_ID: AcpAgentDefinition(
        id=ANTIGRAVITY_AGENT_ID,
        display_name="Google Antigravity",
        home_env="GEMINI_HOME",
        api_key_env_by_method=(("gemini-api-key", "GEMINI_API_KEY"),),
        allowed_download_hosts=("dl.google.com",),
        supported_auth_methods=("oauth-personal", "gemini-api-key"),
        allowed_auth_hosts=("accounts.google.com",),
        terms_url="https://antigravity.google/terms",
    ),
}


def get_agent_definition(agent_id: str) -> AcpAgentDefinition | None:
    return AGENT_DEFINITIONS.get(agent_id)
