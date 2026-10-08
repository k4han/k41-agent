"""Durable skill activation state and model context projection."""

from __future__ import annotations

from dataclasses import asdict
import hashlib
from typing import Any

from langchain_core.messages import HumanMessage, ToolMessage

from agent.modules.skills.sources import workspace_key


def activation_key(agent_name: str, workspace, name: str) -> str:
    return hashlib.sha256(f"{agent_name}\0{workspace_key(workspace)}\0{name}".encode()).hexdigest()


def skill_events(messages, active: dict | None = None) -> dict:
    result = dict(active or {})
    for message in messages:
        if not isinstance(message, ToolMessage) or message.name != "skill" or message.status == "error":
            continue
        artifact = message.artifact
        if not isinstance(artifact, dict) or artifact.get("kind") != "skill_activation":
            continue
        if artifact["action"] == "unload":
            result.pop(artifact["key"], None)
        else:
            result[artifact["key"]] = artifact["skill"]
    return result


def model_skill_history(messages):
    result = []
    for message in messages:
        if isinstance(message, ToolMessage) and message.name == "skill" and isinstance(message.artifact, dict) and message.artifact.get("kind") == "skill_activation":
            action = message.artifact["action"]
            name = message.artifact.get("skill", {}).get("name", message.artifact.get("name", ""))
            message = message.model_copy(update={"content": f"Skill {name}: {action} completed. Active instructions are provided in the skill context.", "artifact": None})
        result.append(message)
    return result


def skill_commands(messages) -> tuple[list[tuple[str, str]], str]:
    """Only explicit leading commands in the latest user message activate skills."""
    latest = next((message for message in reversed(messages) if isinstance(message, HumanMessage)), None)
    if latest is None:
        return [], ""
    content = latest.content
    if isinstance(content, list):
        content = "\n".join(str(block.get("text", "")) for block in content if isinstance(block, dict) and block.get("type") == "text")
    commands = []
    for line in str(content).splitlines():
        parts = line.split()
        if not parts or parts[0].lower() != "/skill" or not line.startswith("/"):
            break
        if len(parts) < 2:
            raise ValueError("Skill name is required. Use /skill NAME or /skill load NAME.")
        action = parts[1].lower()
        if action in {"load", "unload", "refresh"}:
            if len(parts) < 3:
                raise ValueError(f"Skill name is required. Use /skill {action} NAME.")
            commands.append((action, parts[2]))
        else:
            commands.append(("load", parts[1]))
    return list(dict.fromkeys(commands)), latest.id or hashlib.sha256(str(content).encode()).hexdigest()


def requested_skills(messages) -> list[str]:
    return [name for action, name in skill_commands(messages)[0] if action == "load"]


async def active_context(state: dict, *, workspace, thread_id: str, agent_name: str, names, tools) -> tuple[str, dict, list[str]]:
    from agent.modules.skills.packages import get_skill_packages

    active = dict(state.get("active_skills", {}))
    processed = list(state.get("processed_skill_messages", []))
    commands, message_id = skill_commands(state.get("messages", []))
    if not any(tool.name == "skill" for tool in tools):
        if commands and message_id not in processed:
            raise PermissionError("This agent does not have the skill tool enabled.")
        return "", {}, processed
    packages = get_skill_packages()
    request_key = message_id
    if commands and request_key not in processed:
        for action, name in commands:
            key = activation_key(agent_name, workspace, name)
            if action == "unload":
                active.pop(key, None)
            elif key not in active or action == "refresh":
                loaded = await packages.activate(name, workspace=workspace, thread_id=thread_id, agent_name=agent_name, allowed_names=names)
                active[key] = asdict(loaded)
        processed = [*processed[-99:], request_key]
    sections = []
    for key, entry in list(active.items()):
        if packages.authorize_active(entry, workspace=workspace, agent_name=agent_name, allowed_names=names):
            entry = await packages.restore(entry, workspace=workspace, thread_id=thread_id)
            active[key] = entry
            sections.append(entry["content"])
        else:
            active.pop(key, None)
    return ("\n\n<active_skills>\n" + "\n\n".join(sections) + "\n</active_skills>" if sections else ""), active, processed
