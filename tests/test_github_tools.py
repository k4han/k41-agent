from typing import Any
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage
from langgraph.prebuilt import ToolNode
from langgraph.runtime import Runtime

from agent.modules.tools.builtin.github.tools import (
    github_get_pull_request,
    github_get_pull_request_diff,
    github_list_issue_pull_requests,
)
from agent.modules.tools.result import ToolError
from agent.modules.workflows.run_config import WorkflowContext
from agent.modules.workspaces import WorkspaceRef


def _call(tool: Any, **kwargs: Any) -> Any:
    """Call the underlying async tool function bypassing middleware wrappers."""
    return tool.coroutine.__wrapped__(**kwargs)


def _make_runtime(
    *,
    source: str = "github",
    repository_full_name: str = "octo/example",
    installation_id: int = 10,
) -> SimpleNamespace:
    workspace = WorkspaceRef(
        backend="local",
        locator="/tmp/octo/example",
        label=repository_full_name,
        metadata={
            "source": source,
            "repository_full_name": repository_full_name,
            "installation_id": installation_id,
            "branch": "main",
            "base_branch": "main",
        },
    )
    return SimpleNamespace(context={"workspace": workspace})


class FakeGitHubClient:
    def __init__(self) -> None:
        self.prs: list[dict] = []
        self.pr: dict | None = None
        self.diff: str = ""

    async def get_installation_token(self, installation_id: int) -> str:
        return f"token-{installation_id}"

    async def list_pull_requests_for_issue(
        self,
        *,
        installation_id: int,
        full_name: str,
        issue_number: int,
    ) -> list[dict]:
        return self.prs

    async def get_pull_request(
        self,
        *,
        installation_id: int,
        full_name: str,
        pull_request_number: int,
    ) -> dict:
        if self.pr is None:
            raise RuntimeError("no pr configured")
        return self.pr

    async def get_pull_request_diff(
        self,
        *,
        installation_id: int,
        full_name: str,
        pull_request_number: int,
    ) -> str:
        return self.diff


@pytest.fixture
def fake_client(monkeypatch: pytest.MonkeyPatch):
    client = FakeGitHubClient()
    import agent.modules.tools.builtin.github.tools as tools_module

    monkeypatch.setattr(tools_module, "GitHubAppClient", lambda: client)
    return client


@pytest.mark.asyncio
async def test_list_issue_pull_requests_returns_linked_prs(fake_client: FakeGitHubClient) -> None:
    fake_client.prs = [
        {
            "number": 2,
            "title": "Fix bug",
            "state": "open",
            "head": {"ref": "fix-bug"},
            "html_url": "https://github.com/octo/example/pull/2",
        },
        {
            "number": 3,
            "title": "Another fix",
            "state": "closed",
            "head": {"ref": "another-fix"},
            "html_url": "https://github.com/octo/example/pull/3",
        },
    ]

    result = await _call(
        github_list_issue_pull_requests,
        issue_number=7,
        runtime=_make_runtime(),
    )

    assert "Linked pull requests for issue #7" in result
    assert "PR #2 [open]: Fix bug (branch: fix-bug)" in result
    assert "PR #3 [closed]: Another fix (branch: another-fix)" in result


@pytest.mark.asyncio
async def test_list_issue_pull_requests_returns_empty_message(fake_client: FakeGitHubClient) -> None:
    fake_client.prs = []

    result = await _call(
        github_list_issue_pull_requests,
        issue_number=7,
        runtime=_make_runtime(),
    )

    assert "No linked pull requests found" in result


@pytest.mark.asyncio
async def test_list_issue_pull_requests_requires_github_workspace(fake_client: FakeGitHubClient) -> None:
    with pytest.raises(ToolError):
        await _call(
            github_list_issue_pull_requests,
            issue_number=7,
            runtime=_make_runtime(source="local"),
        )


@pytest.mark.asyncio
async def test_get_pull_request_returns_details(fake_client: FakeGitHubClient) -> None:
    fake_client.pr = {
        "number": 2,
        "title": "Fix bug",
        "state": "open",
        "html_url": "https://github.com/octo/example/pull/2",
        "user": {"login": "octocat"},
        "draft": False,
        "head": {"ref": "fix-bug"},
        "base": {"ref": "main"},
        "body": "This fixes the bug.",
    }

    result = await _call(
        github_get_pull_request,
        pull_request_number=2,
        runtime=_make_runtime(),
    )

    assert "Pull Request #2: Fix bug" in result
    assert "State: open" in result
    assert "Author: octocat" in result
    assert "Head: fix-bug -> Base: main" in result
    assert "This fixes the bug." in result


@pytest.mark.asyncio
async def test_get_pull_request_diff_returns_diff(fake_client: FakeGitHubClient) -> None:
    fake_client.diff = "@@ -1,3 +1,3 @@\n- old\n+ new\n"

    result = await _call(
        github_get_pull_request_diff,
        pull_request_number=2,
        runtime=_make_runtime(),
    )

    assert "Diff for PR #2 in octo/example" in result
    assert "- old" in result
    assert "+ new" in result


@pytest.mark.asyncio
async def test_get_pull_request_diff_preserves_text_for_shared_retention(fake_client: FakeGitHubClient) -> None:
    fake_client.diff = "a" * 200_000

    result = await _call(
        github_get_pull_request_diff,
        pull_request_number=2,
        runtime=_make_runtime(),
    )

    assert fake_client.diff in result


@pytest.mark.parametrize(
    "tool",
    [
        github_list_issue_pull_requests,
        github_get_pull_request,
        github_get_pull_request_diff,
    ],
)
def test_github_tool_runtime_is_marked_injected(tool: Any) -> None:
    """ToolRuntime must be recognized as injected so ToolNode passes it."""
    assert "runtime" in tool._injected_args_keys
    assert "runtime" not in tool.args_schema.model_fields


@pytest.mark.asyncio
async def test_tool_node_injects_runtime_for_github_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: LangGraph ToolNode must pass runtime into GitHub tools."""
    import agent.modules.tools.builtin.github.tools as tools_module

    client = FakeGitHubClient()
    client.prs = [
        {
            "number": 2,
            "title": "Fix bug",
            "state": "open",
            "head": {"ref": "fix-bug"},
            "html_url": "https://github.com/octo/example/pull/2",
        }
    ]
    monkeypatch.setattr(tools_module, "GitHubAppClient", lambda: client)
    monkeypatch.setattr(
        tools_module,
        "get_github_repository_store",
        lambda: SimpleNamespace(get_binding_by_full_name=lambda _: None),
    )

    workspace = WorkspaceRef(
        backend="local",
        locator="/tmp/octo/example",
        label="octo/example",
        metadata={
            "source": "github",
            "repository_full_name": "octo/example",
            "installation_id": 10,
            "branch": "main",
            "base_branch": "main",
        },
    )
    context = WorkflowContext(workspace=workspace, agent_name="default")
    runtime = Runtime(context=context)
    config = {"configurable": {"thread_id": "github-runtime-test"}}
    state = {
        "messages": [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "github_list_issue_pull_requests",
                        "args": {"issue_number": 7},
                        "id": "call-1",
                    }
                ],
            )
        ]
    }

    node = ToolNode([github_list_issue_pull_requests])
    result = await node.ainvoke(state, config=config, runtime=runtime)

    tool_message = result["messages"][-1]
    assert "Linked pull requests for issue #7" in tool_message.content
    assert "PR #2 [open]: Fix bug" in tool_message.content
