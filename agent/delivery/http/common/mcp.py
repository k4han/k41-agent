from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends

from agent.modules.mcp import McpInstallRepository


def _install_repository() -> Iterator[McpInstallRepository]:
    repo = McpInstallRepository()
    try:
        yield repo
    finally:
        repo.close()


InstallRepository = Annotated[McpInstallRepository, Depends(_install_repository)]
