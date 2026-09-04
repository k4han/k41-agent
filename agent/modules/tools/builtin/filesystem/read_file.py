import base64
from typing import Annotated, Any

from langchain_core.tools import tool, InjectedToolArg
from langgraph.prebuilt import ToolRuntime

from agent.modules.tools.decorators import register_tool
from agent.modules.tools.domain import ToolCapability, ToolCategory
from agent.modules.tools.builtin.workspace import get_file_io
from agent.modules.tools.result import ToolError, ToolErrorCode
from agent.modules.workspaces.backends import UnsupportedWorkspaceCapabilityError
from agent.modules.workspaces.constants import MAX_IMAGE_READ_BYTES


_BINARY_READ_UNSUPPORTED_MARKER = "does not support binary"


def _sniff_image_mime(raw: bytes) -> str | None:
    """Detect image mime type from magic bytes."""
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if raw.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if raw.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(raw) >= 12 and raw.startswith(b"RIFF") and raw[8:12] == b"WEBP":
        return "image/webp"
    return None


def _resolve_image_mime(_file_path: str, raw: bytes) -> str | None:
    """Resolve mime type for image content from magic bytes.

    Extension-based guessing is intentionally not trusted here: a text file
    renamed to ``.png`` must remain text instead of becoming a bogus image
    block. All supported formats (png, jpeg, gif, webp) have reliable magic
    bytes covered by :func:`_sniff_image_mime`.
    """
    return _sniff_image_mime(raw)


def _image_content_blocks(file_path: str, raw: bytes, mime_type: str) -> list[dict[str, str]]:
    encoded = base64.b64encode(raw).decode("ascii")
    text = (
        f"Image '{file_path}' ({mime_type}, {len(raw)} bytes). "
        "Paging parameters are ignored for images."
    )
    return [
        {"type": "text", "text": text},
        {"type": "image", "base64": encoded, "mime_type": mime_type},
    ]


def _is_binary_unsupported_error(exc: BaseException) -> bool:
    """Distinguish missing binary support from other read failures."""
    if isinstance(exc, UnsupportedWorkspaceCapabilityError):
        return True
    if isinstance(exc, ValueError):
        return _BINARY_READ_UNSUPPORTED_MARKER in str(exc).lower()
    return False


def _image_blocks_from_raw(
    file_path: str, raw: bytes
) -> list[dict[str, str]] | None:
    """Return multimodal blocks when raw bytes hold a supported image."""
    if not raw:
        return None
    mime_type = _resolve_image_mime(file_path, raw)
    if mime_type is None:
        return None
    if len(raw) > MAX_IMAGE_READ_BYTES:
        raise ValueError(
            f"Image '{file_path}' is too large "
            f"({len(raw)} bytes, limit {MAX_IMAGE_READ_BYTES} bytes)."
        )
    return _image_content_blocks(file_path, raw, mime_type)


async def _read_raw_bytes(file_io: Any, file_path: str) -> bytes | None:
    """Read raw bytes once, returning None when binary reads are unsupported."""
    reader = getattr(file_io, "read_bytes", None)
    if not callable(reader):
        return None
    try:
        return await reader(file_path)
    except FileNotFoundError:
        raise
    except Exception as exc:  # noqa: BLE001
        if _is_binary_unsupported_error(exc):
            return None
        raise


async def _try_read_image(file_io: Any, file_path: str) -> list[dict[str, str]] | None:
    """Return multimodal blocks when file content is a supported image."""
    try:
        raw = await _read_raw_bytes(file_io, file_path)
    except FileNotFoundError:
        raise
    except ValueError:
        raise
    if raw is None:
        return None
    return _image_blocks_from_raw(file_path, raw)


@register_tool(
    category=ToolCategory.FILE,
    capabilities=[ToolCapability.READ_FS, ToolCapability.REQUIRES_WORKSPACE],
    tags=["fs", "io"],
)
@tool
async def read_file(
    file_path: str,
    runtime: Annotated[ToolRuntime[Any, Any], InjectedToolArg],
    offset: int = 0,
    limit: int = 0,
    line_numbers: bool = False,
) -> Any:
    """Read file content in working directory.

    By default the whole file is returned as-is. Use ``offset`` (1-based line)
    and ``limit`` (max number of lines) to page through large files; ``limit``
    of 0 means "until end of file". Set ``line_numbers=True`` to prefix each
    returned line with its 1-based line number. Binary files are reported
    instead of being dumped as text.

    Image files (png, jpg, jpeg, webp, gif) are detected automatically and
    returned as multimodal content blocks so vision models can see them.
    Paging parameters are ignored for images.
    """
    file_io = await get_file_io(runtime)

    try:
        raw = await _read_raw_bytes(file_io, file_path)
    except FileNotFoundError as exc:
        raise ToolError(
            ToolErrorCode.NOT_FOUND, f"File does not exist: {file_path}"
        ) from exc
    except ValueError as exc:
        if _is_binary_unsupported_error(exc):
            raw = None
        else:
            raise ToolError(ToolErrorCode.INVALID_INPUT, str(exc)) from exc
    except UnsupportedWorkspaceCapabilityError:
        raw = None

    if raw is not None:
        try:
            blocks = _image_blocks_from_raw(file_path, raw)
        except ValueError as exc:
            raise ToolError(ToolErrorCode.INVALID_INPUT, str(exc)) from exc
        if blocks is not None:
            return blocks
        if b"\x00" in raw:
            return f"[Binary file detected: {file_path}. Cannot preview as text.]"
        content = raw.decode("utf-8", errors="replace")
        content = content.replace("\r\n", "\n").replace("\r", "\n")
        if offset <= 0 and limit <= 0 and not line_numbers:
            return content
    else:
        try:
            content = await file_io.read_text(file_path)
        except FileNotFoundError as exc:
            raise ToolError(
                ToolErrorCode.NOT_FOUND, f"File does not exist: {file_path}"
            ) from exc
        except ValueError as exc:
            raise ToolError(ToolErrorCode.INVALID_INPUT, str(exc)) from exc
        if "\0" in content:
            return f"[Binary file detected: {file_path}. Cannot preview as text.]"
        if offset <= 0 and limit <= 0 and not line_numbers:
            return content

    lines = content.splitlines()
    total = len(lines)
    start = max(offset, 1) if offset > 0 else 1
    if start > total:
        if total == 0:
            return f"[File is empty: {file_path} contains no lines.]"
        return f"[No lines in range: offset {offset} exceeds file length ({total} lines).]"
    if limit > 0:
        last = min(start + limit - 1, total)
    else:
        last = total

    selected = lines[start - 1 : last]
    body = "\n".join(selected)
    if line_numbers:
        width = len(str(last))
        numbered = [
            f"{start + index:>{width}}: {line}"
            for index, line in enumerate(selected)
        ]
        body = "\n".join(numbered)
    return f"[lines {start}-{last} of {total}]\n{body}"
