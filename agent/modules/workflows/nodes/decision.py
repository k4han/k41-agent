"""Decision model node — inspects workspace tree, selects relevant files, and curates context."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel, Field

from agent.modules.decisions import (
    ChoiceAnswer,
    ChoiceQuestion,
    DecisionClient,
    NoulAnswer,
    NoulQuestion,
)
from agent.modules.providers import get_resolved_chat_model
from agent.modules.agents import render_agent_prompt
from agent.modules.decisions import FILE_SELECTION_INSTRUCTIONS, FILE_INCLUSION_INSTRUCTIONS
from agent.modules.usage import with_usage_tracking
from agent.modules.workflows.model_effort import get_workflow_reasoning_effort_kwargs
from agent.shared.infrastructure.parsing import safe_str_strip

logger = logging.getLogger(__name__)

DEFAULT_IGNORED_DIRS = frozenset({
    ".cache",
    ".git",
    ".idea",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tmp",
    ".tmp_pytest",
    ".venv",
    ".vscode",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "target",
    "venv",
})

CONTEXT_MARKER = "<workspace_context>"

_default_decision_client: DecisionClient | None = None


def set_default_decision_client(client: DecisionClient | None) -> None:
    """Set global fallback decision client (primarily for testing)."""
    global _default_decision_client
    _default_decision_client = client


class FileCandidateSelection(BaseModel):
    """Structured decision on which files to inspect based on user request and directory tree."""

    candidate_files: list[str] = Field(
        default_factory=list,
        description="Relative file paths from the directory tree that should be inspected for the user request.",
    )
    reasoning: str = Field(
        default="",
        description="Brief explanation of why these files were selected.",
    )


class FileInclusionDecision(BaseModel):
    """Structured decision on whether an inspected file should be included in model context."""

    should_include: bool = Field(
        description="True if this file provides useful, relevant context needed to solve the request.",
    )
    reasoning: str = Field(
        default="",
        description="Brief explanation of why this file is included or excluded.",
    )
    use_full_content: bool = Field(
        default=True,
        description="True if the full file content should be provided, False if only an excerpt/summary is needed.",
    )
    relevant_summary_or_excerpt: str = Field(
        default="",
        description="Concise summary or key excerpts if use_full_content is False or as additional context.",
    )


def extract_file_paths(
    workspace_dir: str | Path,
    max_files: int = 50,
    ignored_dirs: frozenset[str] | set[str] | None = None,
) -> list[str]:
    """Extract relative file paths from the workspace directory."""
    root = Path(workspace_dir).resolve()
    if not root.is_dir():
        return []
    ignore_set = ignored_dirs if ignored_dirs is not None else DEFAULT_IGNORED_DIRS
    file_paths: list[str] = []

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in ignore_set and not d.startswith(".tmp")]
        rel_dir = os.path.relpath(dirpath, root)
        for f in sorted(filenames):
            if f.startswith(".tmp") or f.endswith((".pyc", ".db", ".db-wal", ".db-shm")):
                continue
            rel_file = os.path.normpath(os.path.join(rel_dir, f)).replace("\\", "/")
            if rel_file.startswith("./"):
                rel_file = rel_file[2:]
            file_paths.append(rel_file)
            if len(file_paths) >= max_files:
                return file_paths
    return file_paths


def generate_directory_tree(
    workspace_dir: str | Path,
    max_depth: int = 4,
    max_entries: int = 350,
    ignored_dirs: frozenset[str] | set[str] | None = None,
) -> str:
    """Generate a clean ASCII directory tree representation of the workspace."""
    root = Path(workspace_dir).resolve()
    if not root.is_dir():
        return f"(Directory not found: {workspace_dir})"

    ignore_set = ignored_dirs if ignored_dirs is not None else DEFAULT_IGNORED_DIRS
    lines: list[str] = [root.name or "."]
    entry_count = 0
    truncated = False

    def _walk(directory: Path, prefix: str = "", depth: int = 1) -> None:
        nonlocal entry_count, truncated
        if depth > max_depth or truncated:
            return

        try:
            children = sorted(
                directory.iterdir(),
                key=lambda p: (not p.is_dir(), p.name.lower()),
            )
        except (PermissionError, OSError):
            return

        filtered = [
            c for c in children
            if c.name not in ignore_set and not c.name.startswith(".tmp")
        ]

        count = len(filtered)
        for i, child in enumerate(filtered):
            if entry_count >= max_entries:
                truncated = True
                lines.append(f"{prefix}└── ... (tree truncated at {max_entries} entries)")
                return

            entry_count += 1
            is_last = (i == count - 1)
            connector = "└── " if is_last else "├── "
            lines.append(f"{prefix}{connector}{child.name}{'/' if child.is_dir() else ''}")

            if child.is_dir():
                sub_prefix = f"{prefix}{'    ' if is_last else '│   '}"
                _walk(child, prefix=sub_prefix, depth=depth + 1)

    _walk(root)
    return "\n".join(lines)


def read_file_safe(
    workspace_dir: str | Path,
    rel_path: str,
    max_bytes: int = 60_000,
) -> str | None:
    """Safely read bounded text content of a file within the workspace root."""
    try:
        root = Path(workspace_dir).resolve()
        clean_rel = rel_path.strip().strip("'\"").lstrip("/\\")
        if clean_rel.startswith("./"):
            clean_rel = clean_rel[2:]
        target = (root / clean_rel).resolve()

        if target != root and not target.is_relative_to(root):
            logger.warning("Path traversal attempt blocked: %s", rel_path)
            return None

        if not target.is_file():
            return None

        size = target.stat().st_size
        truncated = size > max_bytes
        with target.open("rb") as f:
            raw = f.read(max_bytes + 1)

        if b"\0" in raw:
            # Skip binary files
            return None

        text = raw.decode("utf-8", errors="replace")
        if truncated or len(text) > max_bytes:
            text = text[:max_bytes] + "\n...[content truncated]"
        return text
    except Exception as exc:
        logger.debug("Failed reading %s: %s", rel_path, exc)
        return None


def _extract_json_dict(text: str) -> dict[str, Any] | None:
    """Best-effort JSON extraction from model response text."""
    clean = text.strip()
    try:
        data = json.loads(clean)
        if isinstance(data, dict):
            return data
    except Exception:
        pass

    match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", clean)
    if match:
        try:
            data = json.loads(match.group(1).strip())
            if isinstance(data, dict):
                return data
        except Exception:
            pass

    match = re.search(r"(\{[\s\S]*\})", clean)
    if match:
        try:
            data = json.loads(match.group(1).strip())
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    return None


def _resolve_decision_client(
    decision_client: DecisionClient | None = None,
    config: RunnableConfig | None = None,
) -> DecisionClient | None:
    """Resolve a DecisionClient from explicit arg, config, container, or settings."""
    if decision_client is not None:
        return decision_client

    configurable = (config or {}).get("configurable", {}) if isinstance(config, dict) else {}
    client_from_config = configurable.get("decision_client")
    if client_from_config is not None:
        return client_from_config

    if _default_decision_client is not None:
        return _default_decision_client

    # 1. Check AppContainer decision_service
    try:
        from agent.bootstrap.container import get_active_container
        container = get_active_container()
        if container is not None and hasattr(container, "decision_service"):
            service = getattr(container, "decision_service")
            if service is not None:
                if hasattr(service, "client") and getattr(service, "client") is not None:
                    return getattr(service, "client")
                if isinstance(service, DecisionClient):
                    return service
    except Exception as exc:
        logger.debug("Failed resolving decision client from container: %s", exc)

    # 2. Check settings / ConfigService for Cloudflare provider
    try:
        from agent.shared.config import get_config_service
        config_service = get_config_service()
        provider = config_service.get_str("decision.provider", "").strip().lower()
        if provider == "mock":
            from agent.modules.decisions import MockDecisionClient
            return MockDecisionClient()

        from agent.modules.decisions import create_decision_client, load_decision_settings
        client = create_decision_client(load_decision_settings(config_service))
        if client is not None:
            return client
    except Exception as exc:
        logger.debug("Failed resolving decision client from settings: %s", exc)

    return None


def _resolve_decision_model(runtime: Any, config: RunnableConfig | None = None) -> Any:
    """Resolve fallback chat model for decision nodes with configurable overrides."""
    configurable = (config or {}).get("configurable", {}) if isinstance(config, dict) else {}
    provider = configurable.get("decision_provider")
    model = configurable.get("decision_model")

    agent_config = None
    if runtime is not None:
        ctx = getattr(runtime, "context", None)
        if ctx is not None:
            if not provider:
                provider = ctx.get_provider()
            if not model:
                model = ctx.get_model()

            try:
                from agent.modules.agents import get_catalog_service
                catalog = get_catalog_service()
                agent_config = catalog.get_agent(ctx.get_agent_name())
                if agent_config is not None:
                    if not provider:
                        provider = agent_config.provider
                    if not model:
                        model = agent_config.model
            except Exception as exc:
                logger.debug("Could not resolve agent config from catalog: %s", exc)

    resolved = get_resolved_chat_model(provider_name=provider, model=model)
    return resolved, agent_config


async def _decide_candidate_files_clef(
    client: DecisionClient,
    user_input: str,
    file_paths: list[str],
    directory_tree: str,
    max_candidates: int = 8,
) -> list[str]:
    """Use Cloudflare Clef evaluate_choice to rank candidate files."""
    if not file_paths:
        return []

    criteria: dict[str, str] = {
        "none": "No files needed (general conversation, greeting, or question not related to files in repo)",
    }
    for p in file_paths[:35]:
        criteria[p] = f"Source file: {p}"

    instructions = FILE_SELECTION_INSTRUCTIONS
    state = f"User Request: {user_input}\nWorkspace Directory Tree:\n{directory_tree}"

    answer = await client.evaluate_choice(
        state=state,
        instructions=instructions,
        criteria=criteria,
    )

    choice = getattr(answer, "choice", "none")
    probs = getattr(answer, "probabilities", {}) or {}

    if choice == "none" and probs.get("none", 0.0) >= 0.5:
        return []

    sorted_files = [
        p for p, prob in sorted(probs.items(), key=lambda item: item[1], reverse=True)
        if p != "none" and p in criteria and prob > 0.03
    ]

    if not sorted_files and choice != "none" and choice in criteria:
        sorted_files = [choice]

    return sorted_files[:max_candidates]


async def _decide_file_inclusion_clef(
    client: DecisionClient,
    user_input: str,
    file_path: str,
    file_content: str,
) -> FileInclusionDecision:
    """Use Cloudflare Clef evaluate_noul to decide whether to include a file."""
    instructions = FILE_INCLUSION_INSTRUCTIONS
    state = (
        f"User Request: {user_input}\n"
        f"Inspected File: {file_path}\n"
        f"File Content Preview:\n{file_content[:3000]}"
    )
    noul_ans = await client.evaluate_noul(
        state=state,
        instructions=instructions,
        threshold=0.5,
    )
    return FileInclusionDecision(
        should_include=noul_ans.decision,
        reasoning=f"Cloudflare Clef decision (confidence: {noul_ans.confidence:.2f}, noul: {noul_ans.noul:.2f})",
        use_full_content=True,
    )


async def _decide_candidate_files_llm(
    llm: Any,
    user_input: str,
    directory_tree: str,
    *,
    caller_agent_name: str,
    provider_name: str,
    model_name: str,
    model_kwargs: dict[str, Any],
    max_candidates: int = 8,
    config: RunnableConfig | None = None,
) -> FileCandidateSelection:
    """Fallback: invoke LLM chat model to select candidate files."""
    system_prompt = await render_agent_prompt("context-file-selector", values={"max_candidates": max_candidates})

    user_prompt = (
        f"User Request:\n{user_input}\n\n"
        f"Workspace Directory Tree:\n{directory_tree}"
    )

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_prompt),
    ]

    usage_config = with_usage_tracking(
        config,
        agent_name=caller_agent_name,
        provider_name=provider_name,
        model_name=model_name,
        call_kind="decision_candidates",
        internal=True,
    )

    try:
        return await llm.with_structured_output(FileCandidateSelection).ainvoke(
            messages,
            config=usage_config,
            **model_kwargs,
        )
    except Exception as exc:
        logger.debug("Structured output for candidate selection failed, falling back to JSON: %s", exc)

    try:
        response = await llm.ainvoke(messages, config=usage_config, **model_kwargs)
        raw_text = getattr(response, "content", "") or ""
        parsed = _extract_json_dict(raw_text)
        if parsed and "candidate_files" in parsed:
            return FileCandidateSelection(
                candidate_files=[str(f) for f in parsed.get("candidate_files", [])[:max_candidates]],
                reasoning=str(parsed.get("reasoning", "")),
            )
    except Exception as exc:
        logger.warning("Decision model candidate selection invocation failed: %s", exc)

    return FileCandidateSelection(candidate_files=[], reasoning="Fallback due to decision model error")


async def _decide_file_inclusion_llm(
    llm: Any,
    user_input: str,
    file_path: str,
    file_content: str,
    *,
    caller_agent_name: str,
    provider_name: str,
    model_name: str,
    model_kwargs: dict[str, Any],
    config: RunnableConfig | None = None,
    system_prompt: str | None = None,
) -> FileInclusionDecision:
    """Fallback: invoke LLM chat model to evaluate whether a file should be included."""
    system_prompt = system_prompt or await render_agent_prompt("context-file-curator")

    user_prompt = (
        f"User Request:\n{user_input}\n\n"
        f"File Path: {file_path}\n\n"
        f"File Content:\n```\n{file_content}\n```"
    )

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_prompt),
    ]

    usage_config = with_usage_tracking(
        config,
        agent_name=caller_agent_name,
        provider_name=provider_name,
        model_name=model_name,
        call_kind="decision_inclusion",
        internal=True,
    )

    try:
        return await llm.with_structured_output(FileInclusionDecision).ainvoke(
            messages,
            config=usage_config,
            **model_kwargs,
        )
    except Exception as exc:
        logger.debug("Structured output for file inclusion failed, falling back to JSON: %s", exc)

    try:
        response = await llm.ainvoke(messages, config=usage_config, **model_kwargs)
        raw_text = getattr(response, "content", "") or ""
        parsed = _extract_json_dict(raw_text)
        if parsed and "should_include" in parsed:
            return FileInclusionDecision(
                should_include=bool(parsed.get("should_include")),
                reasoning=str(parsed.get("reasoning", "")),
                use_full_content=bool(parsed.get("use_full_content", True)),
                relevant_summary_or_excerpt=str(parsed.get("relevant_summary_or_excerpt", "")),
            )
    except Exception as exc:
        logger.warning("Decision model file inclusion invocation failed for %s: %s", file_path, exc)

    return FileInclusionDecision(
        should_include=bool(file_content.strip()),
        reasoning="Fallback included",
        use_full_content=True,
    )


async def decision_node(
    state: dict[str, Any],
    config: RunnableConfig,
    runtime: Any = None,
) -> dict[str, Any]:
    """Inspect directory tree, choose relevant files, and enrich context before main LLM."""
    messages = state.get("messages", [])
    if not messages:
        return {}

    last_message = messages[-1]
    if not isinstance(last_message, HumanMessage):
        return {}

    original_content = str(last_message.content or "")
    if CONTEXT_MARKER in original_content:
        # Already prepared in a previous execution of this turn
        return {}

    # Resolve workspace directory
    workspace_dir = ""
    caller_agent_name = "default"
    if runtime is not None and hasattr(runtime, "context"):
        ctx = runtime.context
        workspace_dir = ctx.get_working_dir()
        caller_agent_name = ctx.get_agent_name()
    if not workspace_dir:
        configurable = (config or {}).get("configurable", {}) if isinstance(config, dict) else {}
        workspace_dir = str(configurable.get("working_dir") or Path.cwd())

    # Build workspace directory tree
    configurable = (config or {}).get("configurable", {}) if isinstance(config, dict) else {}
    max_tree_depth = int(configurable.get("decision_tree_max_depth", 4))
    max_candidates = int(configurable.get("decision_max_candidates", 6))
    max_file_bytes = int(configurable.get("decision_max_file_bytes", 50_000))

    directory_tree = generate_directory_tree(
        workspace_dir,
        max_depth=max_tree_depth,
    )

    # 1. Check for dedicated Cloudflare DecisionClient
    decision_client = _resolve_decision_client(config=config)
    decision_source = "cloudflare" if decision_client is not None else "llm"

    candidate_files: list[str] = []
    if decision_client is not None:
        try:
            logger.info("Using Cloudflare DecisionClient (%s) for file selection", type(decision_client).__name__)
            all_files = extract_file_paths(workspace_dir, max_files=40)
            candidate_files = await _decide_candidate_files_clef(
                client=decision_client,
                user_input=original_content,
                file_paths=all_files,
                directory_tree=directory_tree,
                max_candidates=max_candidates,
            )
        except Exception as exc:
            logger.warning("Cloudflare Clef file selection failed, cascading to LLM fallback: %s", exc)
            decision_client = None
            decision_source = "llm_cascade"

    # 2. Fallback to LLM if decision_client is not available or failed
    llm = None
    resolved = None
    agent_config = None
    inclusion_prompt = None
    model_kwargs: dict[str, Any] = {}
    if decision_client is None:
        try:
            resolved, agent_config = _resolve_decision_model(runtime, config)
            llm = resolved.model
            inclusion_prompt = await render_agent_prompt("context-file-curator")
            model_kwargs = (
                get_workflow_reasoning_effort_kwargs(
                    getattr(runtime, "context", None),
                    agent_config,
                    resolved,
                )
                if agent_config is not None else {}
            )
            candidate_selection = await _decide_candidate_files_llm(
                llm=llm,
                user_input=original_content,
                directory_tree=directory_tree,
                caller_agent_name=caller_agent_name,
                provider_name=resolved.provider_name,
                model_name=resolved.model_name,
                model_kwargs=model_kwargs,
                max_candidates=max_candidates,
                config=config,
            )
            candidate_files = [f.strip() for f in candidate_selection.candidate_files if f.strip()]
        except Exception as exc:
            logger.warning("Failed resolving LLM for decisions, using directory tree only: %s", exc)
            candidate_files = []

    logger.info("Decision model (%s) selected %d candidate file(s): %s", decision_source, len(candidate_files), candidate_files)

    # 3. Read candidate files and evaluate inclusion concurrently
    async def _evaluate_candidate(rel_path: str) -> dict[str, Any] | None:
        content = await asyncio.to_thread(read_file_safe, workspace_dir, rel_path, max_file_bytes)
        if content is None:
            return None

        if decision_client is not None:
            try:
                inclusion = await _decide_file_inclusion_clef(
                    client=decision_client,
                    user_input=original_content,
                    file_path=rel_path,
                    file_content=content,
                )
            except Exception as e:
                logger.debug("Cloudflare Clef inclusion failed for %s, trying LLM: %s", rel_path, e)
                inclusion = None
        else:
            inclusion = None

        if inclusion is None and llm is not None and resolved is not None:
            inclusion = await _decide_file_inclusion_llm(
                llm=llm,
                user_input=original_content,
                file_path=rel_path,
                file_content=content,
                caller_agent_name=caller_agent_name,
                provider_name=resolved.provider_name,
                model_name=resolved.model_name,
                model_kwargs=model_kwargs,
                config=config,
                system_prompt=inclusion_prompt,
            )

        if inclusion is None:
            # Safe default inclusion
            inclusion = FileInclusionDecision(should_include=True, reasoning="Default included")

        if not inclusion.should_include:
            logger.debug("Decision model rejected candidate %s: %s", rel_path, inclusion.reasoning)
            return None

        file_body = content if inclusion.use_full_content else (
            inclusion.relevant_summary_or_excerpt or content
        )

        return {
            "path": rel_path,
            "reasoning": inclusion.reasoning,
            "content": file_body,
            "use_full_content": inclusion.use_full_content,
        }

    included_files: list[dict[str, Any]] = []
    if candidate_files:
        results = await asyncio.gather(*[_evaluate_candidate(f) for f in candidate_files], return_exceptions=True)
        for res in results:
            if isinstance(res, dict) and res is not None:
                included_files.append(res)
            elif isinstance(res, Exception):
                logger.warning("Error evaluating candidate file: %s", res)

    logger.info("Decision model approved %d file(s) for context injection", len(included_files))

    # 4. Assemble curated context
    context_parts: list[str] = [
        CONTEXT_MARKER,
        f"<directory_tree>\n{directory_tree}\n</directory_tree>",
    ]

    if included_files:
        files_section = ["<relevant_files>"]
        for item in included_files:
            path_str = item["path"]
            reasoning = safe_str_strip(item.get("reasoning", ""))
            body = item.get("content", "")
            files_section.append(f'<file path="{path_str}">')
            if reasoning:
                files_section.append(f"<!-- Context Note: {reasoning} -->")
            files_section.append(body)
            files_section.append("</file>")
        files_section.append("</relevant_files>")
        context_parts.append("\n".join(files_section))

    context_parts.append("</workspace_context>")
    curated_context = "\n\n".join(context_parts)

    # 5. Enrich the user message with context and return
    enriched_content = f"{original_content}\n\n{curated_context}"
    updated_message = HumanMessage(
        content=enriched_content,
        id=getattr(last_message, "id", None),
    )

    return {
        "messages": [updated_message],
        "directory_tree": directory_tree,
        "candidate_files": candidate_files,
        "included_files": included_files,
        "curated_context": curated_context,
        "decision_source": decision_source,
    }


__all__ = [
    "CONTEXT_MARKER",
    "FileCandidateSelection",
    "FileInclusionDecision",
    "decision_node",
    "extract_file_paths",
    "generate_directory_tree",
    "read_file_safe",
    "set_default_decision_client",
]
