"""Tool for generating images through an OpenAI-compatible image API."""

from __future__ import annotations

import base64
import re
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
from langchain_core.tools import BaseTool, StructuredTool
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

DEFAULT_IMAGE_MODEL = "gpt-image-1"
DEFAULT_IMAGE_SIZE = "1024x1024"
GENERATED_IMAGES_DIR = Path.home() / ".k41-agent" / "generated-images"
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


def _output_path(prompt: str, content_type: str = "") -> Path:
    extension = "png"
    if "jpeg" in content_type or "jpg" in content_type:
        extension = "jpg"
    elif "webp" in content_type:
        extension = "webp"
    GENERATED_IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    return GENERATED_IMAGES_DIR / f"{_safe_name(prompt)}-{uuid4().hex[:8]}.{extension}"


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
                "provider config or llm.default_model provider."
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


def _write_image_from_url(url: str, prompt: str) -> Path:
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

    path = _output_path(prompt, response.headers.get("content-type", ""))
    path.write_bytes(response.content)
    return path


def _build_generate_image_tool(config: dict[str, ToolConfigValue]) -> BaseTool:
    provider_name = str(config.get("provider") or "")
    model = str(config.get("model") or DEFAULT_IMAGE_MODEL)
    size = str(config.get("size") or DEFAULT_IMAGE_SIZE)
    quality = str(config.get("quality") or "auto")

    def _generate_image(prompt: str) -> str:
        """Generate an image from a text prompt and return the saved file path."""
        provider = _resolve_provider(provider_name)
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

        image = result.data[0]
        b64_json = getattr(image, "b64_json", None)
        if b64_json:
            path = _output_path(prompt)
            path.write_bytes(base64.b64decode(b64_json))
            return f"Generated image saved to: {path}"

        url = getattr(image, "url", None)
        if url:
            path = _write_image_from_url(url, prompt)
            return f"Generated image saved to: {path}"

        raise ToolError(
            ToolErrorCode.UPSTREAM,
            "Image generation response did not include image data or URL.",
        )

    return StructuredTool.from_function(
        func=_generate_image,
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
