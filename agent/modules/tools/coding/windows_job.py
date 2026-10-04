"""Contain Windows descendants, including those whose parent has exited."""

from __future__ import annotations

import ctypes
import os
import subprocess
import threading
from ctypes import wintypes

import psutil


class BasicLimits(ctypes.Structure):
    _fields_ = [("process_time", ctypes.c_longlong), ("job_time", ctypes.c_longlong),
                ("flags", wintypes.DWORD), ("min_working_set", ctypes.c_size_t),
                ("max_working_set", ctypes.c_size_t), ("active_processes", wintypes.DWORD),
                ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD), ("scheduling", wintypes.DWORD)]


class ExtendedLimits(ctypes.Structure):
    _fields_ = [("basic", BasicLimits), ("io", ctypes.c_ulonglong * 6),
                ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t),
                ("peak_process_memory", ctypes.c_size_t), ("peak_job_memory", ctypes.c_size_t)]


class ThreadEntry(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("usage", wintypes.DWORD), ("id", wintypes.DWORD),
                ("owner", wintypes.DWORD), ("priority", wintypes.LONG),
                ("delta_priority", wintypes.LONG), ("flags", wintypes.DWORD)]


class ProcessEntry(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("usage", wintypes.DWORD), ("pid", wintypes.DWORD),
                ("heap", ctypes.c_size_t), ("module", wintypes.DWORD), ("threads", wintypes.DWORD),
                ("parent", wintypes.DWORD), ("priority", wintypes.LONG), ("flags", wintypes.DWORD),
                ("executable", wintypes.WCHAR * 260)]


def parent_process_ids(kernel) -> dict[int, int]:
    kernel.CreateToolhelp32Snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.Process32FirstW.argtypes = (wintypes.HANDLE, ctypes.c_void_p)
    kernel.Process32FirstW.restype = wintypes.BOOL
    kernel.Process32NextW.argtypes = (wintypes.HANDLE, ctypes.c_void_p)
    kernel.Process32NextW.restype = wintypes.BOOL
    snapshot = kernel.CreateToolhelp32Snapshot(0x00000002, 0)  # TH32CS_SNAPPROCESS
    if snapshot == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        entry = ProcessEntry()
        entry.size = ctypes.sizeof(entry)
        available = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
        result = {}
        while available:
            result[entry.pid] = entry.parent
            available = kernel.Process32NextW(snapshot, ctypes.byref(entry))
        return result
    finally:
        kernel.CloseHandle(snapshot)


def resume_initial_thread(kernel, process_id: int) -> None:
    kernel.CreateToolhelp32Snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.Thread32First.argtypes = (wintypes.HANDLE, ctypes.c_void_p)
    kernel.Thread32First.restype = wintypes.BOOL
    kernel.Thread32Next.argtypes = (wintypes.HANDLE, ctypes.c_void_p)
    kernel.Thread32Next.restype = wintypes.BOOL
    kernel.OpenThread.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel.OpenThread.restype = wintypes.HANDLE
    kernel.ResumeThread.argtypes = (wintypes.HANDLE,)
    kernel.ResumeThread.restype = wintypes.DWORD
    snapshot = kernel.CreateToolhelp32Snapshot(0x00000004, 0)  # TH32CS_SNAPTHREAD
    if snapshot == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        entry = ThreadEntry()
        entry.size = ctypes.sizeof(entry)
        available = kernel.Thread32First(snapshot, ctypes.byref(entry))
        while available:
            if entry.owner == process_id:
                thread = kernel.OpenThread(0x0002, False, entry.id)
                if not thread:
                    raise ctypes.WinError(ctypes.get_last_error())
                try:
                    if kernel.ResumeThread(thread) == 0xFFFFFFFF:
                        raise ctypes.WinError(ctypes.get_last_error())
                    return
                finally:
                    kernel.CloseHandle(thread)
            available = kernel.Thread32Next(snapshot, ctypes.byref(entry))
        raise OSError("Initial suspended process thread was not found.")
    finally:
        kernel.CloseHandle(snapshot)


class WindowsJob:
    def __init__(self, process: subprocess.Popen[bytes]) -> None:
        self.lock = threading.Lock()
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
        kernel.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel.TerminateJobObject.argtypes = (wintypes.HANDLE, wintypes.UINT)
        kernel.TerminateJobObject.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel.CloseHandle.restype = wintypes.BOOL
        kernel.SetInformationJobObject.argtypes = (wintypes.HANDLE, wintypes.INT, ctypes.c_void_p, wintypes.DWORD)
        kernel.SetInformationJobObject.restype = wintypes.BOOL
        self.kernel = kernel
        self.handle = kernel.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = ExtendedLimits()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE; no breakaway.
        if not kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)) or not kernel.AssignProcessToJobObject(self.handle, int(process._handle)):
            error = ctypes.get_last_error()
            kernel.CloseHandle(self.handle)
            self.handle = None
            raise ctypes.WinError(error)
        self.root = psutil.Process(process.pid)
        self.root_created = self.root.create_time()
        self.known = {process.pid: self.root}
        self.tracking_lock = threading.Lock()
        self.tracking_stop = threading.Event()
        self.tracker = threading.Thread(target=self.track, daemon=True)
        self.tracker.start()

    def scan(self) -> None:
        # Some launchers create independent jobs rather than inherit ours.
        # Retain process identities so exited ancestors still identify orphans.
        with self.tracking_lock:
            parents = {}
            current = parent_process_ids(self.kernel)
            for pid, ppid in current.items():
                parents.setdefault(ppid, []).append(pid)
            pending = list(self.known)
            seen = set(pending)
            while pending:
                parent = pending.pop()
                try:
                    if parent in current and psutil.Process(parent).create_time() != self.known[parent].create_time():
                        continue
                except psutil.Error:
                    continue
                for child_pid in parents.get(parent, []):
                    if child_pid in seen:
                        continue
                    try:
                        child = psutil.Process(child_pid)
                        if child.create_time() >= self.root_created:
                            self.known[child.pid] = child
                            pending.append(child.pid)
                            seen.add(child.pid)
                    except psutil.Error:
                        pass

    def track(self) -> None:
        while not self.tracking_stop.is_set():
            try:
                self.scan()
            except (psutil.Error, OSError):
                pass
            self.tracking_stop.wait(0.005)

    def terminate(self) -> None:
        self.scan()
        with self.tracking_lock:
            for process in reversed(list(self.known.values())):
                if process.pid == self.root.pid:
                    continue
                try:
                    if process.is_running():
                        process.kill()
                except psutil.Error:
                    pass
        with self.lock:
            if self.handle and not self.kernel.TerminateJobObject(self.handle, 1):
                raise ctypes.WinError(ctypes.get_last_error())

    def close(self) -> None:
        self.tracking_stop.set()
        self.tracker.join(timeout=1)
        with self.lock:
            if self.handle:
                self.kernel.CloseHandle(self.handle)
                self.handle = None


def cancel_pipe_read(thread: threading.Thread) -> None:
    """Cancel a pending synchronous Windows read before closing its handle."""
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenThread.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel.OpenThread.restype = wintypes.HANDLE
    kernel.CancelSynchronousIo.argtypes = (wintypes.HANDLE,)
    kernel.CancelSynchronousIo.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.OpenThread(0x0001, False, thread.native_id or 0)
    if handle:
        try:
            kernel.CancelSynchronousIo(handle)
        finally:
            kernel.CloseHandle(handle)


def spawn_contained(*args, **kwargs) -> subprocess.Popen[bytes]:
    if os.name == "nt":
        kwargs["creationflags"] = kwargs.get("creationflags", 0) | 0x00000004  # CREATE_SUSPENDED
    process = subprocess.Popen(*args, **kwargs)
    if os.name == "nt":
        try:
            process.coding_job = WindowsJob(process)
            # Popen closes its initial thread handle. Reopen that thread and
            # resume only after containment, removing the child creation race.
            resume_initial_thread(process.coding_job.kernel, process.pid)
        except Exception:
            process.kill()
            process.wait(timeout=5)
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream:
                    stream.close()
            if containment := getattr(process, "coding_job", None):
                containment.close()
            raise
    return process
