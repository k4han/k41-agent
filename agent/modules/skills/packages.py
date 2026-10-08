"""Skill package management, validation, imports and activation snapshots."""

from __future__ import annotations

import asyncio
from dataclasses import asdict, replace
import hashlib
import io
import json
import logging
from pathlib import Path
import re
import shlex
import tempfile
import time
from typing import Any
from urllib.parse import urlsplit
import uuid
import zipfile

import yaml

from agent.modules.skills.models import ActivatedSkill, SkillDiagnostic, SkillPackageSummary, SkillSource
from agent.modules.skills.package_io import WorkspacePackageIO
from agent.modules.skills.package_worker import native_path, pack_directory, unpack_archive, safe_path
from agent.modules.skills.parser import parse_skill_md
from agent.modules.skills.repository import normalize_skill_name
from agent.modules.skills.rendering import skill_content_xml
from agent.modules.skills.sources import global_repositories, package_id, repository_roots, workspace_key
from agent.modules.skills.execution import CACHE_LAYOUT, cache_path, environment_variable, execution_path, shell_guidance
from agent.modules.workspaces import resolve_workspace_ref
from agent.shared.infrastructure.revisions import SKILLS_REVISION, get_revision

logger = logging.getLogger(__name__)
DISABLED_STATE_TTL_SECONDS = 1.0

RUNTIME_GUIDANCE = """Use read for supporting text or images, and bash for scripts.
Resolve skill resource paths against skill_root; resolve task input/output paths against workspace_root.
Python: prefer uv run --no-project --script PATH (PEP 723). Use --with PACKAGE or
--with-requirements FILE only for dependencies explicitly declared by the skill.
For a bundled pyproject.toml use uv run --project SKILL_ROOT, respecting existing lockfiles.
JavaScript: node for plain scripts, pnpm dlx for package CLIs, or pnpm --ignore-workspace install
in skill_root for a bundled package.json; use --frozen-lockfile when a pnpm lockfile is provided.
Only omit --ignore-workspace when the skill itself bundles pnpm-workspace.yaml.
Use cached isolated dependencies. Do not infer package names from imports, alter the source
skill or workspace dependency files, or automatically retry a script after side effects.
Set bash workdir explicitly. Other runtimes and system packages must already be available."""


def validate_document(content: str, *, name: str | None = None, strict: bool = False) -> dict:
    directory = Path(name or "skill")
    try:
        if name is None and strict:
            candidate = parse_skill_md(content, directory)
            if candidate is not None:
                directory = Path(candidate.name)
        parsed = parse_skill_md(content, directory, strict=strict, expected_name=name if strict else None)
        if parsed is None:
            raise ValueError("SKILL.md requires valid YAML frontmatter and a non-empty description.")
        if strict:
            if not parsed.frontmatter.get("name"):
                raise ValueError("'name' is required.")
            if "compatibility" in parsed.frontmatter and not str(parsed.frontmatter["compatibility"] or "").strip():
                raise ValueError("'compatibility' must be non-empty when provided.")
            if "allowed-tools" in parsed.frontmatter and not isinstance(parsed.frontmatter["allowed-tools"], str):
                raise ValueError("'allowed-tools' must be a space-separated string.")
        diagnostics = [asdict(item) for item in parsed.diagnostics]
        try:
            from agent.modules.tools import get_tool_by_name
            aliases = {"Read": "read", "Bash": "bash", "Write": "write", "Edit": "edit", "Glob": "glob", "Grep": "grep"}
            for token in parsed.allowed_tools:
                tool_name = token.split("(", 1)[0]
                if get_tool_by_name(aliases.get(tool_name, tool_name)) is None:
                    diagnostics.append(asdict(SkillDiagnostic("unsupported_tool", f"Declared tool '{token}' is not available.", "allowed-tools")))
        except (ValueError, RuntimeError):
            pass
        return {"valid": True, "frontmatter": parsed.frontmatter, "body": parsed.body, "diagnostics": diagnostics}
    except ValueError as exc:
        fields = ("name", "description", "compatibility", "license", "metadata", "allowed-tools")
        field = next((item for item in fields if f"'{item}'" in str(exc)), "SKILL.md")
        return {"valid": False, "frontmatter": {}, "body": "", "diagnostics": [
            asdict(SkillDiagnostic("invalid_document", str(exc), field, "error"))]}


class SkillPackages:
    def __init__(self, repository):
        self.repository = repository
        self.previews: dict[str, dict] = {}
        self.preview_lock = asyncio.Lock()
        self.last_cleanup: dict[str, float] = {}
        self._disabled_state: dict[str, bool] = {}
        self._disabled_state_revision = -1
        self._disabled_state_expires_at = 0.0
        from agent.modules.skills.snapshots import SnapshotStore
        self.snapshots = SnapshotStore(repository.root.parent / "skill-snapshots")

    @property
    def cache_root(self) -> Path:
        from agent.shared.config import get_config_service
        configured = get_config_service().get_str("skills.cache_root", "").strip()
        return (Path(configured).expanduser() if configured else self.repository.root.parent / "s").resolve()

    def execution_transport(self, workspace, thread_id=None):
        ref = resolve_workspace_ref(workspace)
        return WorkspacePackageIO(str(self.cache_root) if ref.backend == "local" else ref, thread_id=thread_id)

    def execution_destination(self, workspace, version):
        return cache_path(version) if resolve_workspace_ref(workspace).backend == "local" else f".k41-agent/s/{cache_path(version)}"

    async def _prepare_execution(self, workspace, archive, version, thread_id):
        started = time.monotonic()
        transport = self.execution_transport(workspace, thread_id)
        destination = self.execution_destination(workspace, version)
        metadata = {"version": version, "layout": CACHE_LAYOUT}
        reused = False
        try:
            raw, _ = await transport.read(f"{destination}/.skill-manifest.json")
            if json.loads(raw) != metadata:
                raise FileExistsError("Skill execution cache has a conflicting version.")
            reused = True
        except FileNotFoundError:
            result = await transport.install([(destination, archive)], reuse=True, manifest=metadata)
            reused = bool(result.get("reused"))
        await transport.operation("touch", destination)
        key = str(transport.ref.model_dump())
        if time.monotonic() - self.last_cleanup.get(key, -86400) > 3600:
            await transport.operation("cleanup", "" if transport.ref.backend == "local" else ".k41-agent/s", cache_only=True)
            self.last_cleanup[key] = time.monotonic()
        logger.info("Skill package prepared backend=%s archive_bytes=%d cache_reused=%s duration_seconds=%.3f",
                    transport.ref.backend, len(archive), reused, time.monotonic() - started)
        return transport, destination

    async def _runtime(self, workspace, transport, resources):
        ref = resolve_workspace_ref(workspace)
        requested = []
        if any(item.endswith(".py") for item in resources) or "pyproject.toml" in resources:
            requested.append("uv")
        if any(item.endswith((".js", ".cjs", ".mjs", ".ts")) for item in resources) or "package.json" in resources:
            requested.append("node")
        if "package.json" in resources:
            requested.append("pnpm")
        runtime = await transport.operation("runtime_status" if ref.backend == "local" else "ensure_runtime", requested=requested)
        if ref.backend == "local":
            from agent.modules.tools import get_coding_service
            runtime["shell"] = get_coding_service().shell
        runtime.setdefault("errors", [f"Missing runtime: {name}. Install it before executing this skill." for name in requested if not runtime["tools"].get(name)])
        return runtime

    def _instructions(self, parsed, skill_root, workspace_root, resources, runtime, mode, variable):
        parsed = replace(parsed, path=Path(skill_root), resources=resources)
        content = skill_content_xml(parsed) + f"\n<skill_root>{skill_root}</skill_root>\n<workspace_root>{workspace_root}</workspace_root>\n" + RUNTIME_GUIDANCE
        content += "\n" + shell_guidance(runtime["shell"], variable, direct=mode == "source")
        content += "\nAvailable runtime executables: " + json.dumps(runtime["tools"])
        if runtime.get("bin_directory"):
            content += "\nFor sandbox shell commands prepend: export PATH=" + shlex.quote(runtime["bin_directory"]) + ":$PATH"
        return content + "\n" + "\n".join(runtime.get("errors", []))

    def _disabled(self) -> set[str]:
        from agent.shared.config import get_config_service
        disabled = set(get_config_service().get("skills.disabled_ids", []) or [])
        now = time.monotonic()
        revision = get_revision(SKILLS_REVISION)
        if revision != self._disabled_state_revision or now >= self._disabled_state_expires_at:
            state = self.repository.root / ".skill-state.json"
            values = json.loads(state.read_bytes()) if state.is_file() else {}
            self._disabled_state = values
            self._disabled_state_revision = revision
            self._disabled_state_expires_at = now + DISABLED_STATE_TTL_SECONDS
        disabled.update(key for key, enabled in self._disabled_state.items() if not enabled)
        disabled.difference_update(key for key, enabled in self._disabled_state.items() if enabled)
        return disabled

    def authorize_active(self, active: dict, *, workspace, agent_name: str, allowed_names=None) -> bool:
        """Authorize a pinned checkpoint independently of mutable source availability."""
        return (active.get("agent_name") == agent_name
                and active.get("workspace_key") == workspace_key(workspace)
                and active.get("id") not in self._disabled()
                and (active.get("source", {}).get("scope") == "project"
                     or allowed_names is None or active.get("name") in allowed_names))

    async def inventory(self, workspace=None, scope: str | None = None) -> list[SkillPackageSummary]:
        from agent.modules.skills import get_repository_skill_dir
        started = time.monotonic()
        sources = []
        if workspace is not None and scope != "global":
            ref = resolve_workspace_ref(workspace)
            root = ref.locator if ref.backend == "local" else str(ref.metadata.get("root") or "")
            for directory in repository_roots(get_repository_skill_dir()):
                sources.append(("project", root, WorkspacePackageIO(ref), directory, ref.model_dump()))
        if scope != "project":
            for repository in global_repositories(self.repository):
                sources.append(("global", str(repository.root), WorkspacePackageIO(str(repository.root)), "", None))
        packages = []
        seen = set()
        disabled = self._disabled()
        for kind, root, transport, directory, ref in sources:
            result = await transport.operation("scan", directory)
            for entry in result["skills"]:
                identity = package_id(kind, root, entry["directory"], ref)
                parsed = parse_skill_md(entry["content"], Path(entry["directory"]))
                name = parsed.name if parsed else Path(entry["directory"]).name
                diagnostics = parsed.diagnostics if parsed else (SkillDiagnostic("invalid_document", "Skill document cannot be parsed.", "SKILL.md", "error"),)
                diagnostics = (*diagnostics, *(SkillDiagnostic("invalid_package", message, "resources", "error") for message in entry.get("errors", [])))
                enabled = identity not in disabled and parsed is not None and not entry.get("errors")
                shadowed = name in seen
                if enabled:
                    seen.add(name)
                packages.append(SkillPackageSummary(identity, name, parsed.description if parsed else "Invalid skill document.",
                    SkillSource(identity, kind, root, entry["directory"], ref), entry["version"], enabled, shadowed, diagnostics))
        logger.info("Skill discovery packages=%d sources=%d duration_seconds=%.3f", len(packages), len(sources), time.monotonic() - started)
        return packages

    async def find(self, identity: str, workspace=None) -> SkillPackageSummary:
        for package in await self.inventory(workspace):
            if package.id == identity:
                return package
        raise FileNotFoundError("Skill package was not found in this scope.")

    def transport(self, package: SkillPackageSummary, thread_id=None) -> WorkspacePackageIO:
        return WorkspacePackageIO(package.source.workspace or package.source.root, thread_id=thread_id, boundary=package.source.directory)

    async def detail(self, identity: str, workspace=None) -> dict:
        package = await self.find(identity, workspace)
        raw, info = await self.transport(package).read(f"{package.source.directory}/SKILL.md".lstrip("/"))
        content = raw.decode("utf-8-sig")
        return {**asdict(package), "content": content, "file_version": info["version"], **validate_document(content, name=package.name)}

    async def create(self, name: str, content: str, *, scope="global", workspace=None) -> dict:
        from agent.modules.skills import get_repository_skill_dir
        name = normalize_skill_name(name)
        document = validate_document(content, name=name, strict=True)
        if not document["valid"]:
            raise ValueError(document["diagnostics"][0]["message"])
        if scope == "project":
            if workspace is None:
                raise ValueError("A workspace is required for project skills.")
            transport = WorkspacePackageIO(workspace)
            path = f"{get_repository_skill_dir()}/{name}"
        else:
            transport = WorkspacePackageIO(str(self.repository.root))
            path = name
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("SKILL.md", content.encode())
        await transport.install([(path, buffer.getvalue())])
        self.invalidate()
        return {"status": "created"}

    def invalidate(self):
        from agent.modules.skills import reload_skills
        self._disabled_state_expires_at = 0.0
        reload_skills()

    async def enabled(self, identity: str, enabled: bool, workspace=None):
        from agent.shared.config import get_config_service
        await self.find(identity, workspace)
        disabled = self._disabled()
        disabled.discard(identity) if enabled else disabled.add(identity)
        transport = WorkspacePackageIO(str(self.repository.root))
        for attempt in range(5):
            try:
                raw, info = await transport.read(".skill-state.json")
                state = json.loads(raw)
                version = info["version"]
            except FileNotFoundError:
                state, version = {}, None
            state[identity] = enabled
            try:
                await transport.write(".skill-state.json", json.dumps(state).encode(), expected_version=version)
                break
            except FileExistsError:
                if attempt == 4:
                    raise
        self.invalidate()

    def resource_path(self, package: SkillPackageSummary, path: str) -> str:
        from agent.modules.skills.package_worker import validate_member
        validate_member(path)
        return f"{package.source.directory}/{path}".lstrip("/")

    async def read_file(self, identity: str, path: str, workspace=None):
        package = await self.find(identity, workspace)
        return await self.transport(package).read(self.resource_path(package, path))

    async def write_file(self, identity: str, path: str, data: bytes, *, expected_version=None, workspace=None):
        package = await self.find(identity, workspace)
        if path == "SKILL.md":
            expected_name = package.name
            if any(item.code in {"invalid_document", "invalid_name"} for item in package.diagnostics):
                repaired = validate_document(data.decode("utf-8-sig"))
                expected_name = normalize_skill_name(str(repaired["frontmatter"].get("name", package.name)))
            document = validate_document(data.decode("utf-8-sig"), name=expected_name, strict=True)
            if not document["valid"]:
                raise ValueError(document["diagnostics"][0]["message"])
        result = await self.transport(package).write(self.resource_path(package, path), data, expected_version=expected_version)
        self.invalidate()
        return result

    async def remove(self, identity: str, workspace=None):
        package = await self.find(identity, workspace)
        await self.transport(package).operation("delete", package.source.directory)
        self.invalidate()

    async def rename(self, identity: str, name: str, *, expected_version: str, workspace=None):
        package = await self.find(identity, workspace)
        name = normalize_skill_name(name)
        raw, info = await self.read_file(identity, "SKILL.md", workspace)
        if info["version"] != expected_version:
            raise FileExistsError("Skill document changed; reload before renaming.")
        document = validate_document(raw.decode("utf-8-sig"), name=package.name)
        if not document["valid"]:
            raise ValueError("Repair the invalid SKILL.md before renaming this package.")
        document["frontmatter"]["name"] = name
        content = "---\n" + yaml.safe_dump(document["frontmatter"], sort_keys=False, allow_unicode=True) + "---\n" + document["body"] + "\n"
        transport = self.transport(package)
        destination = str(Path(package.source.directory).parent / name).replace("\\", "/")
        import base64
        await transport.operation("rename_package", package.source.directory, destination=destination,
            document=base64.b64encode(content.encode()).decode(), expected_version=expected_version)
        self.invalidate()
        identity = package_id(package.source.scope, package.source.root, destination, package.source.workspace)
        await self.enabled(identity, package.enabled, workspace)

    async def preview(self, *, kind: str, path="", data: bytes | None = None, url="", ref="HEAD", subdirectory="", installation_id: int | None = None, scope="global", workspace=None):
        from agent.shared.config import get_config_service
        from agent.modules.skills.package_worker import DEFAULT_LIMITS
        from agent.modules.skills import get_repository_skill_dir
        if scope == "project" and workspace is None:
            raise ValueError("A workspace is required for project imports.")
        limits = {key: get_config_service().get_int(f"skills.{key}", default) for key, default in DEFAULT_LIMITS.items()}
        commit = None
        with tempfile.TemporaryDirectory() as temporary:
            staging = Path(temporary)
            if kind == "directory":
                source = Path(path).expanduser().resolve()
                if not source.is_dir():
                    raise FileNotFoundError("Source directory was not found.")
            elif kind == "zip":
                from agent.shared.config import get_config_service
                if data is None or len(data) > get_config_service().get_int("skills.max_file_bytes", 64 * 1024 * 1024):
                    raise ValueError("ZIP upload exceeds the limit.")
                unpack_archive(data, staging, limits)
                source = staging
            elif kind == "git":
                parsed = urlsplit(url)
                if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
                    raise ValueError("Provide an HTTPS Git repository URL without embedded credentials.")
                if ref.startswith("-") or not re.fullmatch(r"[A-Za-z0-9_./-]+", ref):
                    raise ValueError("Invalid Git ref.")
                environment = None
                if installation_id is not None:
                    if parsed.hostname != "github.com":
                        raise ValueError("GitHub credentials can only be used with github.com.")
                    import os
                    from agent.modules.github import get_github_automation_service
                    github = get_github_automation_service()
                    full_name = parsed.path.strip("/").removesuffix(".git")
                    binding = await github.store.get_binding_by_full_name(full_name)
                    if not binding or int(binding["installation_id"]) != int(installation_id):
                        raise PermissionError("Connect and synchronize this GitHub repository before importing it with installation credentials.")
                    token = await github.client.get_installation_token(int(installation_id))
                    header = __import__("base64").b64encode(f"x-access-token:{token}".encode()).decode()
                    environment = {**os.environ, "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "http.extraHeader", "GIT_CONFIG_VALUE_0": f"AUTHORIZATION: basic {header}"}
                await self._git(["clone", "--depth", "1", "--no-checkout", "--", url, str(staging / "repo")], environment)
                await self._git(["-C", str(staging / "repo"), "fetch", "--depth", "1", "origin", ref], environment)
                commit = (await self._git(["-C", str(staging / "repo"), "rev-parse", "FETCH_HEAD"], environment)).strip()
                await self._git(["-C", str(staging / "repo"), "checkout", "--detach", commit], environment)
                source = safe_path((staging / "repo").resolve(), subdirectory, allow_root=True)
            else:
                raise ValueError("Unknown import source.")
            candidates = [source] if (source / "SKILL.md").is_file() else [
                child for child in sorted(source.rglob("SKILL.md"))
                if not any(part in {".git", "node_modules", ".venv"} for part in child.relative_to(source).parts)
            ]
            candidates = [item if item.is_dir() else item.parent for item in candidates]
            if not candidates:
                raise ValueError("No SKILL.md files were found in the import source.")
            if len(candidates) > 200:
                raise ValueError("Import contains more than 200 skills.")
            bundles = []
            names = set()
            for candidate in candidates:
                content = (candidate / "SKILL.md").read_text(encoding="utf-8-sig")
                parsed = parse_skill_md(content, candidate)
                if parsed is None:
                    raise ValueError(f"Invalid skill document at {candidate.name}.")
                name = normalize_skill_name(parsed.name)
                if name in names:
                    raise ValueError("Import contains duplicate skill names.")
                names.add(name)
                bundles.append({"name": name, "description": parsed.description, "diagnostics": [asdict(item) for item in parsed.diagnostics],
                                "data": await asyncio.to_thread(pack_directory, candidate.resolve(), limits)})
        transport = WorkspacePackageIO(workspace if scope == "project" else str(self.repository.root))
        directory = get_repository_skill_dir() if scope == "project" else ""
        paths = {bundle["name"]: f"{directory}/{bundle['name']}".lstrip("/") for bundle in bundles}
        versions = (await transport.operation("versions", paths=list(paths.values())))["versions"]
        for bundle in bundles:
            bundle["existing_version"] = versions[paths[bundle["name"]]]
            bundle["conflict"] = bundle["existing_version"] is not None
        token = uuid.uuid4().hex
        async with self.preview_lock:
            self.previews = {key: value for key, value in self.previews.items() if value["expires"] > time.monotonic()}
            if len(self.previews) >= 8:
                raise ValueError("Too many pending imports; finish or discard an existing preview.")
            self.previews[token] = {"bundles": bundles, "expires": time.monotonic() + 1800, "commit": commit,
                                    "scope": scope, "workspace_key": workspace_key(transport.ref), "versions": versions}
        return {"preview_id": token, "commit": commit, "skills": [{key: value for key, value in bundle.items() if key != "data"} for bundle in bundles]}

    async def _git(self, arguments: list[str], environment=None) -> str:
        import os
        import subprocess
        from types import SimpleNamespace
        from agent.modules.tools import kill_process_tree
        from agent.shared.infrastructure.subprocess_utils import hidden_subprocess_kwargs
        process = await asyncio.create_subprocess_exec("git", *arguments, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, env={**(environment or os.environ), "GIT_TERMINAL_PROMPT": "0"},
            start_new_session=os.name != "nt",
            **hidden_subprocess_kwargs(creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)))
        try:
            output, error = await asyncio.wait_for(process.communicate(), 120)
        except asyncio.TimeoutError:
            await asyncio.to_thread(kill_process_tree, SimpleNamespace(pid=process.pid, poll=lambda: process.returncode, kill=process.kill))
            await process.wait()
            raise ValueError("Git import timed out.") from None
        if process.returncode:
            raise ValueError("Git import failed. Check repository URL, ref and connection permissions.")
        return output.decode()

    async def import_preview(self, token: str, *, scope="global", workspace=None, overwrite=False):
        from agent.modules.skills import get_repository_skill_dir
        async with self.preview_lock:
            preview = self.previews.get(token)
            if not preview or preview["expires"] <= time.monotonic():
                raise FileNotFoundError("Import preview expired; preview the source again.")
            if scope == "project" and workspace is None:
                raise ValueError("Project imports require a workspace.")
            transport = WorkspacePackageIO(workspace if scope == "project" else str(self.repository.root))
            if preview["scope"] != scope or preview["workspace_key"] != workspace_key(transport.ref):
                raise ValueError("Import destination changed; preview the source again in the selected scope.")
            directory = get_repository_skill_dir() if scope == "project" else ""
            await transport.install([(f"{directory}/{bundle['name']}".lstrip("/"), bundle["data"]) for bundle in preview["bundles"]],
                                    overwrite=overwrite, expected_versions=preview["versions"])
            self.previews.pop(token)
        self.invalidate()
        return {"status": "imported", "commit": preview["commit"], "names": [bundle["name"] for bundle in preview["bundles"]]}

    async def activate(self, name: str, *, workspace, thread_id: str, agent_name: str, allowed_names=None) -> ActivatedSkill:
        from agent.shared.config import get_config_service
        from agent.modules.tools import conversation_key
        started = time.monotonic()
        selected = next((item for item in await self.inventory(workspace)
                         if item.name == name and item.enabled and not item.shadowed
                         and (item.source.scope == "project" or allowed_names is None or name in allowed_names)), None)
        if selected is None:
            raise PermissionError("Skill is unavailable in the current agent/workspace.")
        ref = resolve_workspace_ref(workspace)
        source = self.transport(selected, thread_id)
        resources = (await source.operation("resources", selected.source.directory))["resources"]
        mode = "snapshot"
        if ref.backend == "local" and get_config_service().get_str("skills.local_execution_mode", "snapshot") == "source" and not any(
            Path(item).name in {"pyproject.toml", "package.json", "pnpm-workspace.yaml"} for item in resources
        ):
            mode = "source"
        if mode == "source":
            version = selected.version
            transport = source
            destination = selected.source.directory
        else:
            archive, version = await source.archive(selected.source.directory)
            with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
                resources = [item.filename for item in bundle.infolist() if not item.is_dir() and item.filename != "SKILL.md"]
            await asyncio.to_thread(self.snapshots.retain, archive, conversation_key(thread_id))
            transport, destination = await self._prepare_execution(ref, archive, version, thread_id)
        ref = ref if ref.backend == "local" else transport.ref
        root = ref.locator if ref.backend == "local" else str(ref.metadata.get("root") or "")
        execution_root = transport.ref.locator if ref.backend == "local" else root
        skill_root = execution_path(execution_root, destination, resources) if ref.backend == "local" else f"{root}/{destination}"
        raw, _ = await transport.read(f"{destination}/SKILL.md")
        runtime = await self._runtime(ref, transport, resources)
        parsed = parse_skill_md(raw.decode("utf-8-sig"), Path(selected.source.directory), resources=resources)
        if parsed is None:
            raise ValueError("Activated skill document is invalid.")
        variable = environment_variable(name)
        content = self._instructions(parsed, skill_root, root, resources, runtime, mode, variable)
        logger.info("Skill activated id=%s backend=%s mode=%s files=%d duration_seconds=%.3f", selected.id, ref.backend,
                    mode, len(resources), time.monotonic() - started)
        return ActivatedSkill(selected.id, name, version, agent_name, workspace_key(ref), skill_root, root, content, asdict(selected.source),
                              mode, destination, variable, runtime["shell"])

    async def restore(self, active: dict, *, workspace, thread_id: str):
        version = active["version"]
        if not re.fullmatch(r"[a-f0-9]{64}", version) or not re.fullmatch(r"[a-f0-9]{32}", active["id"]):
            raise ValueError("Invalid checkpoint skill identity.")
        ref = resolve_workspace_ref(workspace)
        if active.get("execution_mode") == "source":
            source = active["source"]
            transport = WorkspacePackageIO(source["root"], boundary=source["directory"])
            versions = await transport.operation("versions", source["directory"], paths=[source["directory"]])
            if versions["versions"][source["directory"]] != version:
                raise ValueError(f"Local skill source changed or was removed. Run /skill refresh {active['name']} before continuing.")
            from agent.modules.tools import get_coding_service
            if get_coding_service().shell != active.get("shell"):
                resources = (await transport.operation("resources", source["directory"]))["resources"]
                document, _ = await transport.read(f"{source['directory']}/SKILL.md")
                runtime = await self._runtime(ref, transport, resources)
                parsed = parse_skill_md(document.decode("utf-8-sig"), Path(source["directory"]))
                return {**active, "shell": runtime["shell"], "content": self._instructions(parsed, active["skill_root"],
                    active["workspace_root"], resources, runtime, "source", active["environment_variable"])}
            return active
        transport = self.execution_transport(ref, thread_id)
        destination = self.execution_destination(ref, version)
        restored = dict(active)
        archive = None
        try:
            raw, _ = await transport.read(f"{destination}/.skill-manifest.json")
            if json.loads(raw) != {"version": version, "layout": CACHE_LAYOUT}:
                raise ValueError("Skill execution cache has a conflicting version.")
            await transport.operation("touch", destination)
        except FileNotFoundError:
            archive = await asyncio.to_thread(self.snapshots.read, version)
            transport, destination = await self._prepare_execution(ref, archive, version, thread_id)
        from agent.modules.tools import get_coding_service
        shell_changed = ref.backend == "local" and get_coding_service().shell != active.get("shell")
        workspace_root = ref.locator if ref.backend == "local" else str(transport.ref.metadata.get("root") or "")
        execution_root = transport.ref.locator if ref.backend == "local" else workspace_root
        root_changed = (native_path(Path(active["skill_root"])) != native_path(Path(execution_root) / destination)
                        if ref.backend == "local" else active["skill_root"] != f"{workspace_root}/{destination}")
        if archive is not None or not active.get("package_path") or active.get("package_path") != destination or shell_changed or root_changed:
            if archive is None:
                archive = await asyncio.to_thread(self.snapshots.read, version)
            with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
                resources = [item.filename for item in bundle.infolist() if not item.is_dir() and item.filename != "SKILL.md"]
                document = bundle.read("SKILL.md").decode("utf-8-sig")
            runtime = await self._runtime(ref, transport, resources)
            if runtime.get("errors") and ref.backend != "local":
                raise RuntimeError("Skill snapshot restored, but runtime setup failed: " + "; ".join(runtime["errors"]))
            skill_root = execution_path(execution_root, destination, resources) if ref.backend == "local" else f"{workspace_root}/{destination}"
            parsed = parse_skill_md(document, Path(active["source"].get("directory") or active["name"]))
            variable = environment_variable(active["name"])
            restored.update(skill_root=skill_root, workspace_root=workspace_root, execution_mode="snapshot", package_path=destination,
                            environment_variable=variable, shell=runtime["shell"],
                            content=self._instructions(parsed, skill_root, workspace_root, resources, runtime, "snapshot", variable))
        return restored


def get_skill_packages() -> SkillPackages:
    from agent.bootstrap.container import require_active_container
    return require_active_container().skill_packages
