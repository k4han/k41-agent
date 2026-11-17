"""Snapshot legacy ACP base cards into independent cards, with rollback backups."""

from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import yaml

from agent.modules.agents.models import AgentConfig
from agent.modules.agents.parser import agent_markdown_name, parse_agent_markdown_content, serialize_agent_config
from agent.modules.agents.repository import DEFAULT_AGENTS_DIR, FilesystemAgentRepository
from agent.modules.mcp.install_repository import McpInstallRepository


CARD_FIELDS = ("system_prompt", "tools", "tool_configs", "permission_mode", "tool_permissions")


def _write_card(path: Path, content: bytes) -> None:
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _bindings(mcp: McpInstallRepository, name: str) -> list[dict]:
    return [{"server_name": row["server_name"], "enabled": row["agent_enabled"]}
            for row in mcp.list_agent_installs(name)]


def _replace_bindings(mcp: McpInstallRepository, name: str, bindings: list[dict]) -> None:
    for row in mcp.list_agent_installs(name):
        if not mcp.delete_agent_install(name, row["install_id"]):
            raise RuntimeError(f"Could not remove an MCP binding for '{name}'.")
    for row in bindings:
        if mcp.bind_agent_server(agent_name=name, **row) is None:
            raise RuntimeError(f"Could not bind MCP server '{row['server_name']}' to '{name}'.")


def _card_path(repository: FilesystemAgentRepository, agents_dir: Path, name: str) -> Path:
    """Resolve user card identities even when legacy settings prevent validation."""
    paths = []
    for card in repository.list_cards():
        if card.source != "user":
            continue
        path = Path(card.path)
        try:
            card_name = card.name if card.valid else agent_markdown_name(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, yaml.YAMLError):
            continue
        if card_name == name:
            paths.append(path)
    if len(paths) > 1:
        raise ValueError(f"Multiple user cards are named '{name}'. Resolve duplicate names before migrating.")
    return paths[0] if paths else agents_dir / f"{name}.md"


def migrate_cards(agents_dir: Path, names: list[str], mcp: McpInstallRepository) -> Path | None:
    """Validate all cards first, then migrate them or restore every affected card."""
    agents_dir = agents_dir.resolve()
    repository = FilesystemAgentRepository(agents_dir)
    configs = repository.load()
    changes = []
    for name in dict.fromkeys(names):
        if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
            raise ValueError(f"Invalid card name: {name}")
        path = _card_path(repository, agents_dir, name)
        if not path.resolve().is_relative_to(agents_dir):
            raise ValueError(f"Card '{name}' must stay inside the agents directory.")
        original = path.read_bytes()
        match = re.fullmatch(r"\s*---\s*\n(.*?)\n---\s*\n?(.*)", original.decode("utf-8"), re.DOTALL)
        if match is None:
            raise ValueError(f"Card '{name}' has no valid frontmatter.")
        data = yaml.safe_load(match[1])
        settings = data.get("acp") or {}
        if "base_agent" not in settings:
            continue
        base_name = (settings.pop("base_agent") or "").strip()
        data["acp"] = settings
        cleaned = "---\n" + yaml.safe_dump(data, sort_keys=False) + "---\n" + match[2]
        config = parse_agent_markdown_content(cleaned)
        if config.name != name or config.graph_type != "acp_agent":
            raise ValueError(f"Card '{name}' must have the matching name and use acp_agent.")
        wanted_bindings = _bindings(mcp, name)
        if base_name:
            base = configs.get(base_name)
            if base is None or base.name == name or base.internal or base.graph_type in {"acp_agent", "router"}:
                raise ValueError(f"Card '{name}' has an unavailable base agent '{base_name}'.")
            values = config.model_dump()
            values.update({field: getattr(base, field) for field in CARD_FIELDS})
            config = AgentConfig.model_validate(values)
            wanted_bindings = _bindings(mcp, base_name)
        content = serialize_agent_config(config)
        changes.append((name, path, original, content.encode("utf-8"), _bindings(mcp, name), wanted_bindings))
    if not changes:
        return None

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = agents_dir / ".acp-card-backups" / f"{stamp}-{uuid4().hex[:8]}"
    backup.mkdir(parents=True)
    for name, _, original, _, _, _ in changes:
        (backup / f"{name}.md").write_bytes(original)
    (backup / "bindings.json").write_text(json.dumps(
        {name: previous for name, _, _, _, previous, _ in changes}, indent=2,
    ), encoding="utf-8")

    try:
        for name, path, _, content, _, wanted in changes:
            _replace_bindings(mcp, name, wanted)
            _write_card(path, content)
    except Exception as error:
        failures = [error]
        for name, path, original, _, previous, _ in changes:
            for restore in (lambda: _write_card(path, original), lambda: _replace_bindings(mcp, name, previous)):
                try:
                    restore()
                except Exception as rollback_error:
                    failures.append(rollback_error)
        if len(failures) > 1:
            raise ExceptionGroup(f"Migration failed; recovery backups are in {backup}", failures)
        raise
    return backup


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agents-dir", type=Path, default=Path(DEFAULT_AGENTS_DIR))
    parser.add_argument("--card", action="append", required=True)
    args = parser.parse_args()
    mcp = McpInstallRepository()
    try:
        backup = migrate_cards(args.agents_dir, args.card, mcp)
        print(f"Migration complete. Backup: {backup}" if backup else "No legacy cards to migrate.")
    finally:
        mcp.close()


if __name__ == "__main__":
    main()
