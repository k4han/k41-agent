"""Tool for generating images through an OpenAI-compatible image API."""

from __future__ import annotations

import base64
import logging
import re
from pathlib import Path
from typing import Annotated, Any
from uuid import uuid4

import httpx
from langchain_core.tools import BaseTool, InjectedToolArg, StructuredTool
from langgraph.prebuilt import ToolRuntime
from openai import OpenAI

from agent.modules.providers import (
    ProviderType,
    get_default_llm_settings,
    list_providers,
)
from agent.modules.tools.decorators import register_tool
from agent.modules.tools.domain import (
    ToolCapability,
    ToolCategory,
    ToolConfigField,
    ToolConfigSchema,
    ToolConfigValue,
)
from agent.modules.tools.result import ToolError, ToolErrorCode
from agent.modules.tools.builtin.workspace import get_workspace
from agent.modules.tools.runtime.context import ToolContext
from agent.modules.tools.runtime.thread_storage import (
    ensure_physical_workspace_storage,
    ensure_sandbox_workspace_storage,
    generated_images_dir_for_workspace,
    ingest_attachment_file_to_sandbox,
    virtual_generated_image_path,
)
from agent.modules.workspaces import derive_workspace_scope

DEFAULT_IMAGE_MODEL = "gpt-image-1"
DEFAULT_IMAGE_SIZE = "1024x1024"
GENERATED_IMAGES_DIR = Path.home() / ".k41-agent" / "generated-images"
logger = logging.getLogger(__name__)
GENERATE_IMAGE_TOOL_DESCRIPTION = (
    "Generate an image from a text prompt and return the saved file path. "
    "After a successful result, the client UI displays the generated image "
    "automatically from the saved path. Do not repeat the image path, embed "
    "the image, or include a second copy of it in your response; ask the user "
    "what they want to adjust or create next."
)

_IMAGE_CONFIG_SCHEMA = ToolConfigSchema(
    fields=(
        ToolConfigField(
            name="provider",
            input_type="text",
            label="Provider",
            description=(
                "Provider name for an OpenAI-compatible image API. "
                "Leave empty to use the default LLM provider."
            ),
            default="",
        ),
        ToolConfigField(
            name="model",
            input_type="text",
            label="Model",
            description="Image model to use.",
            default=DEFAULT_IMAGE_MODEL,
            required=True,
        ),
        ToolConfigField(
            name="size",
            input_type="select",
            label="Size",
            description="Generated image dimensions.",
            default=DEFAULT_IMAGE_SIZE,
            required=True,
            options=("1024x1024", "1024x1536", "1536x1024", "512x512", "256x256"),
        ),
        ToolConfigField(
            name="quality",
            input_type="select",
            label="Quality",
            description="Image quality hint. Availability depends on the selected model.",
            default="auto",
            options=("auto", "low", "medium", "high", "standard", "hd"),
        ),
    )
)


def _safe_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "-", value.strip().lower())
    return cleaned.strip("-")[:48] or "image"


def _output_path(output_dir: Path, prompt: str, content_type: str = "") -> Path:
    extension = "png"
    if "jpeg" in content_type or "jpg" in content_type:
        extension = "jpg"
    elif "webp" in content_type:
        extension = "webp"
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir / f"{_safe_name(prompt)}-{uuid4().hex[:8]}.{extension}"


def _output_target(runtime: ToolRuntime[Any, Any] | None) -> tuple[Path, bool]:
    if runtime is not None and ToolContext.from_runtime(runtime).thread_id:
        try:
            workspace_scope = derive_workspace_scope(get_workspace(runtime))
        except (ValueError, TypeError, KeyError) as exc:
            logger.debug("Failed to resolve image workspace target: %s", exc)
        else:
            return generated_images_dir_for_workspace(workspace_scope), True
    GENERATED_IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    return GENERATED_IMAGES_DIR, False


def _display_path(path: Path, *, thread_scoped: bool) -> str:
    if thread_scoped:
        return virtual_generated_image_path(path.name)
    return str(path)


def _resolve_provider(provider_name: str):
    default_provider, _ = get_default_llm_settings()
    target_name = provider_name.strip() or default_provider.strip()
    providers = list_providers()
    provider = next(
        (item for item in providers if item.name == target_name),
        None,
    )
    if provider is None:
        raise ToolError(
            ToolErrorCode.INVALID_INPUT,
            (
                "Image provider is not configured. Set the generate_image "
                "provider config in Dashboard > Settings > Tools > "
                "generate_image (or configure llm.default_model provider)."
            ),
        )
    if provider.provider_type not in {ProviderType.OPENAI, ProviderType.OPENAI_COMPATIBLE}:
        raise ToolError(
            ToolErrorCode.INVALID_INPUT,
            "generate_image requires an OpenAI-compatible provider.",
        )
    if not provider.api_key:
        raise ToolError(
            ToolErrorCode.INVALID_INPUT,
            f"API key is not configured for provider '{provider.name}'.",
        )
    return provider


def _write_image_from_url(url: str, prompt: str, output_dir: Path) -> Path:
    try:
        with httpx.Client(timeout=httpx.Timeout(60, connect=10)) as client:
            response = client.get(url)
            response.raise_for_status()
    except httpx.RequestError as exc:
        raise ToolError(ToolErrorCode.UPSTREAM, f"Image download failed: {exc}") from exc
    except httpx.HTTPStatusError as exc:
        raise ToolError(
            ToolErrorCode.UPSTREAM,
            f"Image download HTTP {exc.response.status_code}: {exc.response.reason_phrase}",
        ) from exc

    path = _output_path(output_dir, prompt, response.headers.get("content-type", ""))
    path.write_bytes(response.content)
    return path


def _build_generate_image_tool(config: dict[str, ToolConfigValue]) -> BaseTool:
    provider_name = str(config.get("provider") or "")
    model = str(config.get("model") or DEFAULT_IMAGE_MODEL)
    size = str(config.get("size") or DEFAULT_IMAGE_SIZE)
    quality = str(config.get("quality") or "auto")

    def _generate_image_sync(
        prompt: str,
        runtime: Annotated[ToolRuntime[Any, Any], InjectedToolArg],
    ) -> str:
        """Sync wrapper for local workspaces (used by legacy tests)."""
        provider = _resolve_provider(provider_name)
        output_dir, thread_scoped = _output_target(runtime)
        kwargs: dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "size": size,
        }
        if quality:
            kwargs["quality"] = quality

        client_kwargs = {"api_key": provider.api_key}
        if provider.base_url:
            client_kwargs["base_url"] = provider.base_url
        client = OpenAI(**client_kwargs)

        try:
            result = client.images.generate(**kwargs)
        except Exception as exc:
            raise ToolError(ToolErrorCode.UPSTREAM, f"Image generation failed: {exc}") from exc

        if not result.data:
            raise ToolError(ToolErrorCode.UPSTREAM, "Image generation returned no data.")

        def _sync_to_workspace_sync(filename: str, content: bytes) -> None:
            if runtime is None:
                return
            try:
                workspace = get_workspace(runtime)
                if workspace.backend == "local" and workspace.locator:
                    ws_dir = ensure_physical_workspace_storage(workspace.locator) / "generated-images"
                    ws_dir.mkdir(parents=True, exist_ok=True)
                    (ws_dir / filename).write_bytes(content)
            except Exception as sync_exc:
                logger.debug("Failed to sync generated image to local workspace: %s", sync_exc)

        image = result.data[0]
        b64_json = getattr(image, "b64_json", None)
        if b64_json:
            path = _output_path(output_dir, prompt)
            raw_bytes = base64.b64decode(b64_json)
            path.write_bytes(raw_bytes)
            _sync_to_workspace_sync(path.name, raw_bytes)
            return f"Generated image saved to: {_display_path(path, thread_scoped=thread_scoped)}"

        url = getattr(image, "url", None)
        if url:
            path = _write_image_from_url(url, prompt, output_dir)
            try:
                _sync_to_workspace_sync(path.name, path.read_bytes())
            except Exception:
                pass
            return f"Generated image saved to: {_display_path(path, thread_scoped=thread_scoped)}"

        raise ToolError(
            ToolErrorCode.UPSTREAM,
            "Image generation response did not include image data or URL.",
        )

    async def _generate_image(
        prompt: str,
        runtime: Annotated[ToolRuntime[Any, Any], InjectedToolArg],
    ) -> str:
        """Generate an image from a text prompt and return the saved file path."""
        provider = _resolve_provider(provider_name)
        output_dir, thread_scoped = _output_target(runtime)
        kwargs: dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "size": size,
        }
        if quality:
            kwargs["quality"] = quality

        client_kwargs = {"api_key": provider.api_key}
        if provider.base_url:
            client_kwargs["base_url"] = provider.base_url
        client = OpenAI(**client_kwargs)

        try:
            result = client.images.generate(**kwargs)
        except Exception as exc:
            raise ToolError(ToolErrorCode.UPSTREAM, f"Image generation failed: {exc}") from exc

        if not result.data:
            raise ToolError(ToolErrorCode.UPSTREAM, "Image generation returned no data.")

        async def _sync_to_workspace(filename: str, content: bytes) -> None:
            if runtime is None:
                return
            try:
                workspace = get_workspace(runtime)
                if workspace.backend == "local" and workspace.locator:
                    ws_dir = ensure_physical_workspace_storage(workspace.locator) / "generated-images"
                    ws_dir.mkdir(parents=True, exist_ok=True)
                    (ws_dir / filename).write_bytes(content)
                elif workspace.backend in {"daytona", "modal"}:
                    try:
                        from agent.modules.tools.runtime.context import ToolContext

                        thread_id = ToolContext.from_runtime(runtime).thread_id
                    except Exception:
                        thread_id = None
                    try:
                        await ensure_sandbox_workspace_storage(workspace, thread_id=thread_id)
                        await ingest_attachment_file_to_sandbox(
                            f"generated-images/{filename}", content, workspace, thread_id=thread_id
                        )
                    except Exception as exc:
                        logger.debug("Failed to sync generated image to sandbox workspace: %s", exc)
            except Exception as sync_exc:
                logger.debug("Failed to sync generated image to workspace: %s", sync_exc)

        image = result.data[0]
        b64_json = getattr(image, "b64_json", None)
        if b64_json:
            path = _output_path(output_dir, prompt)
            raw_bytes = base64.b64decode(b64_json)
            path.write_bytes(raw_bytes)
            await _sync_to_workspace(path.name, raw_bytes)
            return f"Generated image saved to: {_display_path(path, thread_scoped=thread_scoped)}"

        url = getattr(image, "url", None)
        if url:
            path = _write_image_from_url(url, prompt, output_dir)
            try:
                await _sync_to_workspace(path.name, path.read_bytes())
            except Exception:
                pass
            return f"Generated image saved to: {_display_path(path, thread_scoped=thread_scoped)}"

        raise ToolError(
            ToolErrorCode.UPSTREAM,
            "Image generation response did not include image data or URL.",
        )

    return StructuredTool.from_function(
        func=_generate_image_sync,
        coroutine=_generate_image,
        name="generate_image",
        description=GENERATE_IMAGE_TOOL_DESCRIPTION,
    )


generate_image = register_tool(
    category=ToolCategory.IMAGE,
    capabilities=[ToolCapability.NETWORK, ToolCapability.MUTATES_STATE],
    tags=["image", "generation"],
    config_schema=_IMAGE_CONFIG_SCHEMA,
    default_config=_IMAGE_CONFIG_SCHEMA.defaults(),
    factory=_build_generate_image_tool,
)(_build_generate_image_tool({}))


__all__ = ["GENERATE_IMAGE_TOOL_DESCRIPTION", "generate_image"]
