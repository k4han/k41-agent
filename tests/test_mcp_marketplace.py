from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from agent.modules.mcp import MCPServerConfig, MCPTransport
from agent.modules.mcp.install_repository import McpInstallRepository
from agent.modules.mcp.installer import McpInstallError, McpMarketplaceService
from agent.modules.mcp.registry_client import (
    McpInstallTarget,
    McpRegistryInput,
    McpSearchResult,
    normalize_registry_entry,
)
from agent.shared.infrastructure.db import Base, load_orm_models


def _create_test_db(tmp_path: Path) -> str:
    db_path = tmp_path / "mcp.sqlite"
    database_url = f"sqlite:///{db_path.as_posix()}"
    load_orm_models()
    engine = create_engine(database_url, echo=False)
    try:
        Base.metadata.create_all(engine)
    finally:
        engine.dispose()
    return database_url


def test_registry_normalizes_package_remote_and_auth_summary() -> None:
    result = normalize_registry_entry(
        {
            "server": {
                "name": "io.github/example/github",
                "title": "GitHub MCP",
                "description": "Manage GitHub repositories.",
                "version": "1.2.3",
                "repository": {"url": "https://github.com/example/github-mcp"},
                "packages": [
                    {
                        "registryType": "npm",
                        "identifier": "@example/github-mcp",
                        "version": "1.2.3",
                        "environmentVariables": [
                            {
                                "name": "GITHUB_TOKEN",
                                "description": "GitHub API token.",
                                "isRequired": True,
                                "isSecret": True,
                            }
                        ],
                    }
                ],
                "remotes": [
                    {
                        "type": "streamable-http",
                        "url": "https://mcp.example.test/mcp",
                        "headers": [
                            {
                                "name": "Authorization",
                                "value": "Bearer {GITHUB_TOKEN}",
                                "isRequired": True,
                                "isSecret": True,
                            }
                        ],
                    }
                ],
            },
            "_meta": {
                "io.modelcontextprotocol.registry/official": {
                    "status": "active",
                    "isLatest": True,
                }
            },
        }
    )

    assert result.registry_name == "io.github/example/github"
    assert result.verified is True
    assert result.is_latest is True
    assert result.repository_url == "https://github.com/example/github-mcp"
    assert result.auth_summary == "Requires secrets: Authorization, Github Token"

    package = result.install_targets[0]
    assert package.id == "package:0"
    assert package.command == "npx"
    assert package.args == ("-y", "@example/github-mcp@1.2.3")
    assert package.env_template == {"GITHUB_TOKEN": "{GITHUB_TOKEN}"}
    assert package.required_inputs[0].secret is True

    remote = result.install_targets[1]
    assert remote.id == "remote:0"
    assert remote.transport == "streamable_http"
    assert remote.url == "https://mcp.example.test/mcp"
    assert remote.headers_template == {"Authorization": "Bearer {GITHUB_TOKEN}"}


def test_install_repository_encrypts_and_resolves_credential_ref(tmp_path: Path) -> None:
    database_url = _create_test_db(tmp_path)
    repo = McpInstallRepository(database_url, key_path=tmp_path / "mcp.key")
    try:
        install = repo.install_registry_server(
            agent_name="agent_a",
            server_name="github",
            registry_name="io.github/example/github",
            registry_version="1.2.3",
            title="GitHub MCP",
            description="Manage GitHub repositories.",
            verified=True,
            transport="stdio",
            command="npx",
            args=["-y", "@example/github-mcp@1.2.3"],
            env_template={"GITHUB_TOKEN": "{GITHUB_TOKEN}"},
            credential_payload={"GITHUB_TOKEN": "secret-token"},
        )

        assert install["credential_ref"].startswith("mcpcred_")
        assert repo.list_agent_server_names("agent_a") == ["github"]
        config = repo.get_server_config("github")
        assert config is not None
        assert config.env == {"GITHUB_TOKEN": "secret-token"}

        updated = repo.update_custom_server(
            server_name="github",
            config=MCPServerConfig(
                name="github",
                transport=MCPTransport.STDIO,
                command="npx",
                args=("-y", "@example/github-mcp@1.2.3"),
                env={"GITHUB_TOKEN": ""},
                enabled=True,
            ),
            credential_payload={"env": {"GITHUB_TOKEN": ""}},
        )
        assert updated is True
        updated_config = repo.get_server_config("github")
        assert updated_config is not None
        assert updated_config.env == {"GITHUB_TOKEN": "secret-token"}
    finally:
        repo.close()

    engine = create_engine(database_url, echo=False)
    try:
        with engine.connect() as connection:
            encrypted_payloads = connection.execute(
                text("select payload_json from mcp_credentials")
            ).scalars().all()
        assert encrypted_payloads
        assert all("secret-token" not in payload for payload in encrypted_payloads)
    finally:
        engine.dispose()


def test_install_repository_replaces_orphan_credentials(tmp_path: Path) -> None:
    database_url = _create_test_db(tmp_path)
    repo = McpInstallRepository(database_url, key_path=tmp_path / "mcp.key")
    try:
        first = repo.install_registry_server(
            agent_name="agent_a",
            server_name="github",
            registry_name="io.github/example/github",
            registry_version="1.2.3",
            title="GitHub MCP",
            description="Manage GitHub repositories.",
            verified=True,
            transport="stdio",
            command="npx",
            args=["-y", "@example/github-mcp@1.2.3"],
            env_template={"GITHUB_TOKEN": "{GITHUB_TOKEN}"},
            credential_payload={"GITHUB_TOKEN": "secret-token"},
        )
        first_ref = first["credential_ref"]

        updated = repo.update_custom_server(
            server_name="github",
            config=MCPServerConfig(
                name="github",
                transport=MCPTransport.STDIO,
                command="npx",
                args=("-y", "@example/github-mcp@1.2.3"),
                env={"GITHUB_TOKEN": "rotated-token"},
                enabled=True,
            ),
            credential_payload={"env": {"GITHUB_TOKEN": "rotated-token"}},
        )
        assert updated is True

        engine = create_engine(database_url, echo=False)
        try:
            with engine.connect() as connection:
                refs = connection.execute(
                    text("select credential_ref from mcp_credentials")
                ).scalars().all()
        finally:
            engine.dispose()

        assert len(refs) == 1
        assert first_ref not in refs
    finally:
        repo.close()


def test_install_repository_clears_credential_when_form_wiped(tmp_path: Path) -> None:
    database_url = _create_test_db(tmp_path)
    repo = McpInstallRepository(database_url, key_path=tmp_path / "mcp.key")
    try:
        repo.install_registry_server(
            agent_name="agent_a",
            server_name="github",
            registry_name="io.github/example/github",
            registry_version="1.2.3",
            title="GitHub MCP",
            description="Manage GitHub repositories.",
            verified=True,
            transport="stdio",
            command="npx",
            args=["-y", "@example/github-mcp@1.2.3"],
            env_template={"GITHUB_TOKEN": "{GITHUB_TOKEN}"},
            credential_payload={"GITHUB_TOKEN": "secret-token"},
        )

        updated = repo.update_custom_server(
            server_name="github",
            config=MCPServerConfig(
                name="github",
                transport=MCPTransport.STDIO,
                command="npx",
                args=(),
                env={},
                headers={},
                enabled=True,
            ),
            credential_payload={},
        )
        assert updated is True

        engine = create_engine(database_url, echo=False)
        try:
            with engine.connect() as connection:
                refs = connection.execute(
                    text("select credential_ref from mcp_credentials")
                ).scalars().all()
        finally:
            engine.dispose()

        assert refs == []
    finally:
        repo.close()


@pytest.mark.asyncio
async def test_marketplace_install_validates_inputs_and_is_idempotent(
    tmp_path: Path,
) -> None:
    database_url = _create_test_db(tmp_path)
    target = McpInstallTarget(
        id="package:0",
        label="npm via npx",
        transport="stdio",
        registry_type="npm",
        command="npx",
        args=("-y", "@example/github-mcp@1.2.3"),
        env_template={"GITHUB_TOKEN": "{GITHUB_TOKEN}"},
        required_inputs=(
            McpRegistryInput(
                key="GITHUB_TOKEN",
                label="GitHub Token",
                required=True,
                secret=True,
                source="env",
            ),
        ),
    )
    server = McpSearchResult(
        registry_name="io.github/example/github",
        title="GitHub MCP",
        description="Manage GitHub repositories.",
        version="1.2.3",
        is_latest=True,
        verified=True,
        install_targets=(target,),
        required_inputs=target.required_inputs,
        auth_summary="Requires secrets: GitHub Token",
    )

    class FakeRegistryClient:
        async def get_server_version(self, server_name: str, version: str) -> McpSearchResult:
            assert server_name == "io.github/example/github"
            assert version == "latest"
            return server

    repo = McpInstallRepository(database_url, key_path=tmp_path / "mcp.key")
    try:
        service = McpMarketplaceService(
            registry_client=FakeRegistryClient(),
            repository=repo,
        )

        with pytest.raises(McpInstallError):
            await service.install(
                agent_name="agent_a",
                registry_name="io.github/example/github",
                version="latest",
                target_id="package:0",
                input_values={},
            )

        first = await service.install(
            agent_name="agent_a",
            registry_name="io.github/example/github",
            version="latest",
            target_id="package:0",
            input_values={"GITHUB_TOKEN": "secret-token"},
        )
        second = await service.install(
            agent_name="agent_a",
            registry_name="io.github/example/github",
            version="latest",
            target_id="package:0",
            input_values={"GITHUB_TOKEN": "secret-token"},
        )

        assert first["status"] == "installed"
        assert second["status"] == "installed"
        assert len(repo.list_agent_installs("agent_a")) == 1
        assert repo.list_agent_server_names("agent_a") == ["github"]
    finally:
        repo.close()
