"""Read bounded windows across a rotating log without relying on backup names."""

from __future__ import annotations

import base64
import codecs
import hashlib
import json
import os
import stat
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

MAX_CHUNK_BYTES = 64 * 1024


def _open_file(path: Path) -> BinaryIO:
    if os.name != "nt":
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        descriptor = os.open(path, flags)
        return os.fdopen(descriptor, "rb")

    import ctypes
    import msvcrt
    from ctypes import wintypes

    # Ordinary Python file handles prevent the writer from renaming on Windows.
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel.CreateFileW
    create_file.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    create_file.restype = wintypes.HANDLE
    generic_read = 0x80000000
    share_read_write_delete = 0x1 | 0x2 | 0x4
    open_existing = 3
    open_reparse_point = 0x00200000
    handle = create_file(str(path), generic_read, share_read_write_delete, None,
                         open_existing, open_reparse_point, None)
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except BaseException:
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle(handle)
        raise
    return os.fdopen(descriptor, "rb")


class LogHistoryExpired(Exception):
    """The file or bytes referenced by a cursor are no longer available."""


class LogSnapshotChanged(Exception):
    """Rotation changed the file inventory while it was being opened."""


@dataclass
class LogFile:
    path: Path
    stream: BinaryIO
    info: os.stat_result

    @property
    def identity(self) -> tuple[int, int]:
        return self.info.st_dev, self.info.st_ino


class LogSnapshot:
    """Hold open descriptors so renaming a backup cannot change a read."""

    def __init__(self, path: Path, backup_count: int) -> None:
        self.path = path
        self.backup_count = backup_count
        self.files: list[LogFile] = []
        self._stack = ExitStack()

    @staticmethod
    def _inventory(paths: list[Path]) -> list[tuple[Path, os.stat_result]]:
        inventory = []
        for path in paths:
            try:
                info = path.lstat()
            except FileNotFoundError:
                continue
            if not stat.S_ISREG(info.st_mode):
                raise PermissionError("Only regular log files can be read.")
            inventory.append((path, info))
        return inventory

    def __enter__(self) -> LogSnapshot:
        try:
            if any(parent.is_symlink() for parent in self.path.parents):
                raise PermissionError("Log directories must not be symbolic links.")
            paths = [Path(f"{self.path}.{index}") for index in range(self.backup_count, 0, -1)]
            # The writer stages the newest backup here before shifting numbered files.
            paths.extend([Path(f"{self.path}.rotating"), self.path])
            inventory = self._inventory(paths)
            identities = set()
            for path, expected in inventory:
                try:
                    stream = self._stack.enter_context(_open_file(path))
                except FileNotFoundError as exc:
                    raise LogSnapshotChanged from exc
                info = os.fstat(stream.fileno())
                identity = info.st_dev, info.st_ino
                if identity != (expected.st_dev, expected.st_ino) or identity in identities:
                    raise LogSnapshotChanged
                if not stat.S_ISREG(info.st_mode) or path.is_symlink():
                    raise PermissionError("Only regular log files can be read.")
                identities.add(identity)
                self.files.append(LogFile(path, stream, info))
            # Include previously absent paths so a new file cannot be silently skipped.
            current = self._inventory(paths)
            if [(path, info.st_dev, info.st_ino) for path, info in current] != [
                (file.path, *file.identity) for file in self.files
            ]:
                raise LogSnapshotChanged
            return self
        except BaseException:
            self._stack.close()
            raise

    def __exit__(self, *args) -> None:
        self._stack.close()

    @property
    def size(self) -> int:
        return sum(file.info.st_size for file in self.files)

    def read(self, start: int, end: int) -> bytes:
        chunks = []
        position = 0
        for file in self.files:
            size = file.info.st_size
            left, right = max(0, start - position), min(size, end - position)
            if left < right:
                file.stream.seek(left)
                chunk = file.stream.read(right - left)
                if len(chunk) != right - left:
                    raise LogHistoryExpired
                chunks.append(chunk)
            position += size
        return b"".join(chunks)

    @staticmethod
    def _guard(file: LogFile, offset: int) -> str:
        length = min(64, offset)
        file.stream.seek(offset - length)
        return hashlib.blake2s(file.stream.read(length), digest_size=16).hexdigest()

    def cursor(self, source: str, position: int) -> str | None:
        for index, file in enumerate(self.files):
            if position <= file.info.st_size or index == len(self.files) - 1:
                data = [source, *file.identity, position, self._guard(file, position)]
                return base64.urlsafe_b64encode(json.dumps(data).encode()).decode()
            position -= file.info.st_size
        return None

    def locate(self, source: str, cursor: str) -> int:
        try:
            data = json.loads(base64.b64decode(cursor, altchars=b"-_", validate=True))
            if (
                not isinstance(data, list) or len(data) != 5 or data[0] != source
                or any(type(value) is not int or value < 0 for value in data[1:4])
                or not isinstance(data[4], str) or len(data[4]) != 32
            ):
                raise ValueError
        except (ValueError, TypeError, UnicodeError) as exc:
            raise ValueError("Invalid log cursor.") from exc
        _, device, inode, offset, guard = data
        position = 0
        for file in self.files:
            if file.identity == (device, inode):
                if offset > file.info.st_size or self._guard(file, offset) != guard:
                    raise LogHistoryExpired
                return position + offset
            position += file.info.st_size
        raise LogHistoryExpired

    def page(
        self, source: str, *, before: str | None = None, after: str | None = None,
        limit: int = MAX_CHUNK_BYTES,
    ) -> dict:
        position = self.locate(source, before or after) if before or after else self.size
        if after:
            start, end = position, min(self.size, position + limit)
        else:
            start, end = max(0, position - limit), position
        raw = self.read(start, end)
        if not after and start:
            # Start at a UTF-8 boundary, including when rotation split a code point.
            skipped = 0
            while skipped < min(3, len(raw)) and raw[skipped] & 0xC0 == 0x80:
                skipped += 1
            start += skipped
            raw = raw[skipped:]
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        text = decoder.decode(raw, final=False)
        end -= len(decoder.getstate()[0])
        return {
            "source": source, "text": text, "bytes": end - start,
            "before": self.cursor(source, start), "after": self.cursor(source, end),
            "has_older": start > 0, "has_newer": end < self.size,
        }
