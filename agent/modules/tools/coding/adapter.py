"""LangChain adapter preserving structured artifacts and error status."""

import json
import os
from typing import Annotated, Any

from langchain_core.messages import ToolMessage
from langchain_core.tools import InjectedToolArg, StructuredTool
from langgraph.prebuilt import ToolRuntime

from agent.modules.tools.coding.models import InvocationContext, PermissionRule, ToolDefinition, ToolResult
from agent.modules.tools.coding.schemas import (EditInput, ExecInput, GlobInput, GrepInput, ListInput,
    ProcessInput, ProcessReadInput, ProcessWriteInput, ReadInput, WriteInput)
from agent.modules.tools.coding.names import TOOL_ALIASES
from agent.modules.tools.coding.service import get_coding_service
from agent.modules.tools.coding.storage import bounded_text
from agent.modules.tools.runtime.context import get_context_value, get_thread_id


SCHEMAS = {"bash": ExecInput, "read_process_output": ProcessReadInput,
           "write_process_input": ProcessWriteInput, "stop_process": ProcessInput,
           "read": ReadInput, "list_dir": ListInput, "edit": EditInput,
           "write": WriteInput, "glob": GlobInput, "grep": GrepInput}
DESCRIPTIONS = {
    "bash": "Run an isolated workspace shell command using the configured shell. Despite the tool name, Windows normally uses PowerShell: use the shell syntax reported in context, and do not assume POSIX commands such as head exist. Specify workdir explicitly; cwd and environment changes do not persist. Activated skills provide K41_SKILL_* environment variables and K41_WORKSPACE_ROOT. Long commands return process_id; observe with read_process_output. timeout_seconds kills the process tree; yield_time_ms only limits the initial wait. Use dedicated file tools for reading/searching/editing.",
    "read_process_output": "Observe a workspace process using its explicit byte cursor. Returns next cursor, state and exit code; no implicit output consumption.",
    "write_process_input": "Send exact stdin text to an owned workspace process. Include newline explicitly when required. Returns process state and new output.",
    "stop_process": "Stop an owned workspace process and its descendants; wait for bounded pipe cleanup.",
    "read": "Read a bounded UTF-8 text page with line numbers, next_offset and content version, or view a supported image. Use byte_offset=0 and next_byte_offset to read long lines without loss. Pass the returned version as expected_version when editing.",
    "list_dir": "List a bounded page of workspace directory entries with next_offset.",
    "edit": "Replace exact text in an existing file. Read the file first and pass its version as expected_version. Copy old_string from the file without line-number prefixes; preserve whitespace and include enough context for a unique match. Set replace_all=True only to replace every exact occurrence. Empty matches and unchanged replacements are rejected. Returns change counts and a new version; diffs are displayed in the UI.",
    "write": "Create or rewrite a text file, optionally append. Existing BOM, newline style and mode are preserved. Use expected_version to reject stale overwrites.",
    "glob": "Find workspace paths by glob pattern, including brace alternatives. Results are bounded; ignored directories and external symlinks are excluded.",
    "grep": "Search file contents using regex or fixed_strings. Invalid regex is an error. Returns bounded file/line matches and search-engine metadata.",
}


def invocation_context(runtime: Any) -> InvocationContext:
    from agent.modules.tools.builtin.workspace import get_workspace
    from agent.modules.agents import get_catalog_service
    from agent.shared.config.service import get_config_service

    raw = getattr(runtime, "context", None)
    workspace = get_workspace(runtime)
    agent_name = get_context_value(raw, "agent_name", "default")
    agent = get_catalog_service().get_agent(agent_name)
    rules = get_config_service().get("tools.permissions", []) or []
    if agent is not None and getattr(agent, "tool_permissions", None) is not None:
        rules = agent.tool_permissions
    configurable = (getattr(runtime, "config", None) or {}).get("configurable", {})
    root = os.path.realpath(workspace.locator) if workspace.backend == "local" else str(workspace.metadata.get("root") or "")
    from agent.modules.skills import get_skill_packages
    packages = get_skill_packages()
    state = getattr(runtime, "state", None) or {}
    active = [item for item in state.get("active_skills", {}).values()
              if packages.authorize_active(item, workspace=workspace, agent_name=agent_name,
                                           allowed_names=get_context_value(raw, "allowed_skill_names", None))]
    skill_roots = tuple(item["skill_root"] for item in active)
    skill_environment = tuple((item["environment_variable"], item["skill_root"]) for item in active if item.get("environment_variable"))
    if len(active) == 1:
        skill_environment += (("K41_SKILL_ROOT", active[0]["skill_root"]),)
    return InvocationContext(agent_name=agent_name, workspace=root,
        backend=workspace.backend, locator=workspace.locator,
        thread_id=get_thread_id(getattr(runtime, "config", None)) or "",
        message_id=str(configurable.get("coding_message_id", "")),
        tool_call_id=str(getattr(runtime, "tool_call_id", "") or ""),
        approval_supported=bool(get_context_value(raw, "approval_supported", configurable.get("approval_supported", False))),
        permission_rules=tuple(PermissionRule.model_validate(rule) for rule in rules),
        skill_roots=skill_roots, skill_environment=skill_environment,
        skill_cache_root=str(packages.cache_root) if workspace.backend == "local" else "")


class CodingStructuredTool(StructuredTool):
    """Validate model input independently of graph-injected runtime values."""

    def _parse_input(self, tool_input, tool_call_id):
        if isinstance(tool_input, dict):
            payload = dict(tool_input)
            injected = {key: payload.pop(key) for key in self._injected_args_keys if key in payload}
            parsed = super()._parse_input(payload, tool_call_id)
            return {**parsed, **injected}
        return super()._parse_input(tool_input, tool_call_id)


def coding_message_for_model(message: ToolMessage) -> ToolMessage:
    """Drop coding UI artifacts and normalize receipts from older checkpoints."""
    name = TOOL_ALIASES.get(message.name, message.name)
    if name not in SCHEMAS and name != "read_tool_output":
        return message
    artifact = message.artifact
    content = message.content
    if not isinstance(artifact, dict):
        if not isinstance(content, str) or not content.lstrip().startswith("{"):
            return message
        try:
            artifact = json.loads(content)
        except ValueError:
            return message
        if not isinstance(artifact, dict):
            return message
        content = artifact.get("content", content)
    if not {"status", "data", "output_refs", "capture_truncated", "output_truncated"}.issubset(artifact):
        return message
    try:
        result = ToolResult.model_validate({**artifact, "content": content})
    except ValueError:
        return message
    content = result.model_content(name)
    if isinstance(content, str):
        content, _ = bounded_text(content)
    return message.model_copy(update={"content": content, "artifact": None})


def make_coding_tool(name: str, *, retained_only: bool = False) -> StructuredTool:
    async def execute(params: Any, context: InvocationContext, service: Any) -> ToolResult:
        if retained_only:
            import posixpath
            from agent.modules.tools.coding.models import CodingError
            path = posixpath.normpath(params.file_path.replace("\\", "/"))
            if not path.startswith(".k41-agent/outputs/"):
                raise CodingError("permission_denied", "This read tool can only read retained tool output.")
        return await service.execute(name, params.model_dump(), context)

    description = DESCRIPTIONS[name] if not retained_only else "Read retained tool output under .k41-agent/outputs/ only. Use offset/limit or byte_offset and next_byte_offset for long lines."
    definition = ToolDefinition(name, description, SCHEMAS[name], execute)

    async def invoke(runtime: Annotated[ToolRuntime[Any, Any], InjectedToolArg], **kwargs: Any) -> Any:
        service = get_coding_service()
        try:
            context = invocation_context(runtime)
            result = await service.invoke(definition, kwargs, context)
        except (ValueError, OSError) as exc:
            result = service.failure(exc)
        except Exception as exc:
            from agent.modules.tools.coding.models import CodingError
            if not isinstance(exc, CodingError):
                raise
            result = service.failure(exc)
        call_id = getattr(runtime, "tool_call_id", None)
        if call_id:
            return ToolMessage(content=result.content, artifact=result.model_dump(exclude={"content"}),
                               name=name, tool_call_id=call_id,
                               status="error" if result.status in {"error", "partial_failure"} else "success")
        return result.content

    tool = CodingStructuredTool.from_function(name=name, description=description, coroutine=invoke,
                                       args_schema=SCHEMAS[name], infer_schema=False)
    object.__setattr__(tool, "coding_definition", definition)
    return tool
