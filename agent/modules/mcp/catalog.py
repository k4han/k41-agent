"""Static catalog of well-known MCP servers shown on the dashboard."""

from __future__ import annotations

from dataclasses import dataclass, field

from agent.modules.mcp.models import MCPTransport


@dataclass(frozen=True, slots=True)
class PopularMcpEnvField:
    """Describes one environment variable / credential expected by a popular MCP server."""

    key: str
    label: str
    description: str = ""
    required: bool = True
    secret: bool = False


@dataclass(frozen=True, slots=True)
class PopularMcpServer:
    """A well-known MCP server template the user can install with one click."""

    id: str
    name: str
    description: str
    transport: MCPTransport
    command: str = ""
    args: tuple[str, ...] = ()
    url: str = ""
    env_fields: tuple[PopularMcpEnvField, ...] = field(default_factory=tuple)
    homepage: str = ""


POPULAR_MCP_SERVERS: tuple[PopularMcpServer, ...] = (
    PopularMcpServer(
        id="filesystem",
        name="Filesystem",
        description="Read and write files within an allowed directory.",
        transport=MCPTransport.STDIO,
        command="npx",
        args=("-y", "@modelcontextprotocol/server-filesystem", "{root_path}"),
        env_fields=(
            PopularMcpEnvField(
                key="root_path",
                label="Root path",
                description="Absolute directory the server is allowed to access.",
                secret=False,
            ),
        ),
        homepage="https://github.com/modelcontextprotocol/servers/tree/main/src/filesystem",
    ),
    PopularMcpServer(
        id="github",
        name="GitHub",
        description="Browse repositories, issues and pull requests on GitHub.",
        transport=MCPTransport.STDIO,
        command="npx",
        args=("-y", "@modelcontextprotocol/server-github"),
        env_fields=(
            PopularMcpEnvField(
                key="GITHUB_PERSONAL_ACCESS_TOKEN",
                label="GitHub token",
                description="Personal access token with repo scope.",
                secret=True,
            ),
        ),
        homepage="https://github.com/modelcontextprotocol/servers/tree/main/src/github",
    ),
    PopularMcpServer(
        id="gitlab",
        name="GitLab",
        description="Browse projects, issues and merge requests on GitLab.",
        transport=MCPTransport.STDIO,
        command="npx",
        args=("-y", "@modelcontextprotocol/server-gitlab"),
        env_fields=(
            PopularMcpEnvField(
                key="GITLAB_PERSONAL_ACCESS_TOKEN",
                label="GitLab token",
                secret=True,
            ),
            PopularMcpEnvField(
                key="GITLAB_API_URL",
                label="GitLab API URL",
                description="Defaults to https://gitlab.com/api/v4.",
                required=False,
            ),
        ),
        homepage="https://github.com/modelcontextprotocol/servers/tree/main/src/gitlab",
    ),
    PopularMcpServer(
        id="postgres",
        name="Postgres",
        description="Read-only SQL access to a Postgres database.",
        transport=MCPTransport.STDIO,
        command="npx",
        args=("-y", "@modelcontextprotocol/server-postgres", "{connection_string}"),
        env_fields=(
            PopularMcpEnvField(
                key="connection_string",
                label="Connection string",
                description="postgresql://user:pass@host:5432/dbname",
                secret=True,
            ),
        ),
        homepage="https://github.com/modelcontextprotocol/servers/tree/main/src/postgres",
    ),
    PopularMcpServer(
        id="sqlite",
        name="SQLite",
        description="Read and query a local SQLite database file.",
        transport=MCPTransport.STDIO,
        command="uvx",
        args=("mcp-server-sqlite", "--db-path", "{db_path}"),
        env_fields=(
            PopularMcpEnvField(
                key="db_path",
                label="Database path",
                description="Absolute path to the .sqlite/.db file.",
            ),
        ),
        homepage="https://github.com/modelcontextprotocol/servers-archived/tree/main/src/sqlite",
    ),
    PopularMcpServer(
        id="slack",
        name="Slack",
        description="Read messages and post to Slack channels.",
        transport=MCPTransport.STDIO,
        command="npx",
        args=("-y", "@modelcontextprotocol/server-slack"),
        env_fields=(
            PopularMcpEnvField(
                key="SLACK_BOT_TOKEN",
                label="Slack bot token",
                description="xoxb-... token from a Slack app with the right scopes.",
                secret=True,
            ),
            PopularMcpEnvField(
                key="SLACK_TEAM_ID",
                label="Slack team ID",
                required=False,
            ),
        ),
        homepage="https://github.com/modelcontextprotocol/servers/tree/main/src/slack",
    ),
    PopularMcpServer(
        id="gdrive",
        name="Google Drive",
        description="Search and read files from Google Drive.",
        transport=MCPTransport.STDIO,
        command="npx",
        args=("-y", "@modelcontextprotocol/server-gdrive"),
        env_fields=(
            PopularMcpEnvField(
                key="GDRIVE_OAUTH_PATH",
                label="OAuth credentials path",
                description="Path to the saved OAuth credentials JSON.",
            ),
        ),
        homepage="https://github.com/modelcontextprotocol/servers/tree/main/src/gdrive",
    ),
    PopularMcpServer(
        id="memory",
        name="Memory",
        description="Persistent knowledge graph for cross-session memory.",
        transport=MCPTransport.STDIO,
        command="npx",
        args=("-y", "@modelcontextprotocol/server-memory"),
        env_fields=(),
        homepage="https://github.com/modelcontextprotocol/servers/tree/main/src/memory",
    ),
    PopularMcpServer(
        id="sequential-thinking",
        name="Sequential Thinking",
        description="Structured step-by-step reasoning tool.",
        transport=MCPTransport.STDIO,
        command="npx",
        args=("-y", "@modelcontextprotocol/server-sequential-thinking"),
        env_fields=(),
        homepage="https://github.com/modelcontextprotocol/servers/tree/main/src/sequentialthinking",
    ),
    PopularMcpServer(
        id="brave-search",
        name="Brave Search",
        description="Web search powered by the Brave Search API.",
        transport=MCPTransport.STDIO,
        command="npx",
        args=("-y", "@modelcontextprotocol/server-brave-search"),
        env_fields=(
            PopularMcpEnvField(
                key="BRAVE_API_KEY",
                label="Brave API key",
                secret=True,
            ),
        ),
        homepage="https://github.com/modelcontextprotocol/servers/tree/main/src/brave-search",
    ),
    PopularMcpServer(
        id="puppeteer",
        name="Puppeteer",
        description="Headless browser automation for scraping and screenshots.",
        transport=MCPTransport.STDIO,
        command="npx",
        args=("-y", "@modelcontextprotocol/server-puppeteer"),
        env_fields=(),
        homepage="https://github.com/modelcontextprotocol/servers/tree/main/src/puppeteer",
    ),
    PopularMcpServer(
        id="fetch",
        name="Fetch",
        description="HTTP fetch tool optimized for LLM consumption.",
        transport=MCPTransport.STDIO,
        command="uvx",
        args=("mcp-server-fetch",),
        env_fields=(),
        homepage="https://github.com/modelcontextprotocol/servers/tree/main/src/fetch",
    ),
    PopularMcpServer(
        id="time",
        name="Time",
        description="Time/timezone utilities (no credentials).",
        transport=MCPTransport.STDIO,
        command="uvx",
        args=("mcp-server-time",),
        env_fields=(),
        homepage="https://github.com/modelcontextprotocol/servers/tree/main/src/time",
    ),
    PopularMcpServer(
        id="sentry",
        name="Sentry",
        description="Query Sentry issues and events.",
        transport=MCPTransport.STDIO,
        command="uvx",
        args=("mcp-server-sentry",),
        env_fields=(
            PopularMcpEnvField(
                key="SENTRY_AUTH_TOKEN",
                label="Sentry auth token",
                secret=True,
            ),
        ),
        homepage="https://github.com/modelcontextprotocol/servers/tree/main/src/sentry",
    ),
)


def get_popular_server(server_id: str) -> PopularMcpServer | None:
    for entry in POPULAR_MCP_SERVERS:
        if entry.id == server_id:
            return entry
    return None


__all__ = [
    "POPULAR_MCP_SERVERS",
    "PopularMcpEnvField",
    "PopularMcpServer",
    "get_popular_server",
]
