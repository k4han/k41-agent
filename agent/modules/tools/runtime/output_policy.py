"""Shared presentation limits and safety quotas for every tool output."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import TextIO

MAX_MODEL_BYTES = 50 * 1024
MAX_MODEL_LINES = 2000
MAX_CAPTURE_BYTES = 1024 * 1024
MAX_STORED_BYTES = 10 * 1024 * 1024
RETENTION_SECONDS = 7 * 24 * 60 * 60
CAPTURE_NOTICE = "[capture quota exceeded; some output was lost]"


def bounded_text(text: str, marker: str = "", *, tail: bool = False) -> tuple[str, bool]:
    encoded = text.encode("utf-8")
    if len(encoded) <= MAX_MODEL_BYTES and len(text.splitlines()) <= MAX_MODEL_LINES:
        return text, False
    marker = marker or "[output truncated]"
    allowance = MAX_MODEL_BYTES - len(marker.encode("utf-8")) - 4
    lines = text.splitlines(keepends=True)
    if tail:
        head = "".join(lines[:(MAX_MODEL_LINES - 4) // 2]).encode("utf-8")[:allowance // 2]
        end = "".join(lines[-(MAX_MODEL_LINES - 4) // 2:]).encode("utf-8")[-allowance // 2:]
        return f"{head.decode('utf-8', errors='ignore').rstrip(chr(10))}\n\n{marker}\n\n{end.decode('utf-8', errors='ignore').lstrip(chr(10))}", True
    preview = "".join(lines[:MAX_MODEL_LINES - 2]).encode("utf-8")[:allowance]
    return f"{preview.decode('utf-8', errors='ignore').rstrip(chr(10))}\n\n{marker}", True


def capture_bytes(chunks: Iterable[bytes], max_bytes: int | None = None) -> tuple[bytes, bool]:
    """Bound source ingestion without imposing a model presentation limit."""
    captured = bytearray()
    max_bytes = MAX_STORED_BYTES if max_bytes is None else max_bytes
    for chunk in chunks:
        remaining = max_bytes - len(captured)
        captured.extend(chunk[:remaining])
        if len(chunk) > remaining:
            return bytes(captured), True
    return bytes(captured), False


class TextCapture:
    """Accumulate complete output up to the shared source safety quota."""

    def __init__(self, max_bytes: int | None = None) -> None:
        self.max_bytes = MAX_STORED_BYTES if max_bytes is None else max_bytes
        self.buffer = bytearray()
        self.has_lines = False
        self.size = 0
        self.truncated = False

    def append(self, text: str) -> None:
        if self.truncated:
            return
        raw = text.encode("utf-8")
        remaining = self.max_bytes - self.size
        kept = raw[:remaining].decode("utf-8", errors="ignore").encode("utf-8")
        self.buffer.extend(kept)
        self.size += len(kept)
        self.truncated = len(raw) > remaining

    def append_line(self, text: str) -> None:
        self.append(("\n" if self.has_lines else "") + text)
        self.has_lines = True

    def content(self) -> str:
        text = self.buffer.decode("utf-8")
        return text + f"\n\n{CAPTURE_NOTICE}" if self.truncated else text


def captured_lines(handle: TextIO) -> Iterator[tuple[str, bool]]:
    """Read physical lines without allowing one line to exhaust memory."""
    while piece := handle.readline(64 * 1024):
        capture = TextCapture()
        capture.append(piece)
        if not piece.endswith("\n"):
            while rest := handle.readline(64 * 1024):
                capture.append(rest)
                if rest.endswith("\n"):
                    break
        yield capture.buffer.decode("utf-8"), capture.truncated
