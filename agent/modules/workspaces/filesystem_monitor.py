"""Dependency-free filesystem notifications for the portable sandbox worker."""

from __future__ import annotations

import ctypes
import os
from pathlib import Path
import struct
import sys
import threading
import time
from uuid import uuid4

from agent.modules.projects import snapshot_worker


class FilesystemMonitor:
    """Treat native events as hints and periodically reconcile the complete inventory."""

    def __init__(self, root: str):
        self.lock = threading.RLock()
        self.root = Path(root).resolve()
        self.epoch = uuid4().hex
        self.revision = 0
        self.fd = -1
        self.watches = {}
        self.inventory = None
        self.last_scan = 0.0
        self.dirty_since = None
        self.last_event = 0.0
        if sys.platform == "linux":
            try:
                self.libc = ctypes.CDLL(None, use_errno=True)
                self.libc.inotify_init1.argtypes = [ctypes.c_int]
                self.libc.inotify_add_watch.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_uint32]
                self.fd = self.libc.inotify_init1(os.O_NONBLOCK | os.O_CLOEXEC)
            except (AttributeError, OSError):
                self.fd = -1

    def _directories(self):
        yield self.root
        for name, path in snapshot_worker._inventory(self.root).items():
            if path.is_dir() and not path.is_symlink():
                yield path

    def _watch(self):
        if self.fd < 0:
            return
        known = set(self.watches.values())
        for path in self._directories():
            if not path.is_relative_to(self.root):
                self.close()
                return
            if path in known:
                continue
            # MODIFY, ATTRIB, CLOSE_WRITE, MOVE, CREATE, DELETE and self changes.
            descriptor = self.libc.inotify_add_watch(self.fd, os.fsencode(path), 0x00000FCE)
            if descriptor < 0:
                # Watch limits and unsupported mounts must fall back to scanning.
                self.close()
                return
            self.watches[descriptor] = path

    def _events(self, now):
        rebuild = False
        while self.fd >= 0:
            try:
                raw = os.read(self.fd, 256 * 1024)
            except BlockingIOError:
                break
            if not raw:
                break
            offset = 0
            while offset < len(raw):
                descriptor, mask, _, length = struct.unpack_from("iIII", raw, offset)
                offset += 16
                name = os.fsdecode(raw[offset:offset + length].split(b"\0", 1)[0])
                offset += length
                if mask & 0x00008000:
                    self.watches.pop(descriptor, None)
                path = self.watches.get(descriptor)
                if name in snapshot_worker.EXCLUDED:
                    continue
                if path is not None:
                    relative = (path / name).relative_to(self.root).parts
                    if relative and relative[0] == ".k41-agent" and (len(relative) < 2 or relative[1] not in snapshot_worker.INTERNAL):
                        if len(relative) == 1:
                            rebuild = True
                        continue
                self.dirty_since = now if self.dirty_since is None else self.dirty_since
                self.last_event = now
                if mask & (0x00004000 | 0x00000FC0):
                    rebuild = True
                if mask & 0x00004000:
                    self.last_scan = float("-inf")
        return rebuild

    def poll(self, since: str | None = None, *, force: bool = False) -> dict:
        with self.lock:
            return self._poll(since, force=force)

    def _poll(self, since=None, *, force=False):
        now = time.monotonic()
        rebuild = self._events(now)
        scan = force or self.inventory is None or now - self.last_scan >= (1 if self.fd < 0 else 30)
        if scan:
            inventory = {name: snapshot_worker._signature(path)
                         for name, path in snapshot_worker._inventory(self.root).items()}
            if inventory != self.inventory:
                self.revision += 1
            self.inventory = inventory
            self.last_scan = now
        if self.dirty_since is not None and (now - self.last_event >= .3 or now - self.dirty_since >= 1):
            self.revision += 1
            self.dirty_since = None
        if scan or rebuild:
            self._watch()
        token = f"{self.epoch}:{self.revision}"
        return {"token": token, "changed": force or token != since,
                "mode": "inotify" if self.fd >= 0 else "scan"}

    def close(self):
        with self.lock:
            if self.fd >= 0:
                os.close(self.fd)
                self.fd = -1
            self.watches.clear()
