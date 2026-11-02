"""Bounded log files for background processes and the systemd service."""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import traceback
from contextlib import closing
from pathlib import Path

from agent.shared.infrastructure.subprocess_utils import hidden_subprocess_kwargs

LOG_DIR = Path.home() / ".k41-agent" / "logs"
SERVER_LOG_FILE = LOG_DIR / "server.log"
TRAY_LOG_FILE = LOG_DIR / "tray.log"
UPDATE_LOG_FILE = LOG_DIR / "update.log"
LOG_MAX_BYTES = 10 * 1024 * 1024
LOG_BACKUP_COUNT = 5


class RotatingLogWriter:
    """Preserve raw output while bounding the active file and its backups."""

    def __init__(
        self,
        path: Path,
        *,
        max_bytes: int = LOG_MAX_BYTES,
        backup_count: int = LOG_BACKUP_COUNT,
    ) -> None:
        if max_bytes <= 0 or backup_count <= 0:
            raise ValueError("Log size and backup count must be positive.")
        self.path = path
        self.max_bytes = max_bytes
        self.backup_count = backup_count
        path.parent.mkdir(parents=True, exist_ok=True)
        self.stream = path.open("ab")
        self.size = self.stream.tell()

    def _rollover(self) -> None:
        self.stream.close()
        for index in range(self.backup_count, 0, -1):
            source = self.path if index == 1 else Path(f"{self.path}.{index - 1}")
            if source.exists():
                source.replace(Path(f"{self.path}.{index}"))
        self.stream = self.path.open("ab")
        self.size = 0

    def write(self, data: bytes) -> None:
        while data:
            if self.size >= self.max_bytes:
                self._rollover()
            available = self.max_bytes - self.size
            if len(data) > available:
                # Keep ordinary lines together; oversized output still stays bounded.
                boundary = data.rfind(b"\n", 0, available)
                if boundary < 0 and self.size:
                    self._rollover()
                    continue
                if boundary >= 0:
                    available = boundary + 1
            chunk, data = data[:available], data[available:]
            self.stream.write(chunk)
            self.size += len(chunk)
        self.stream.flush()

    def close(self) -> None:
        self.stream.close()


def logged_command(
    command: list[str],
    log_file: Path,
    *,
    python_executable: str | None = None,
    tee: bool = False,
) -> list[str]:
    """Wrap a command so one process owns and rotates its combined output."""
    return [
        python_executable or sys.executable,
        "-m", "agent.bootstrap.logs",
        "--log-file", str(log_file),
        *(["--tee"] if tee else []),
        "--", *command,
    ]


def run_logged_process(command: list[str], log_file: Path, *, tee: bool = False) -> int:
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env.setdefault("PYTHONIOENCODING", "utf-8")
    with closing(RotatingLogWriter(log_file)) as writer:
        try:
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env=env,
                **hidden_subprocess_kwargs(),
            )
        except OSError:
            writer.write(traceback.format_exc().encode("utf-8"))
            return 1

        def forward_signal(signum, frame) -> None:
            if process.poll() is None:
                try:
                    process.send_signal(signum)
                except ProcessLookupError:
                    pass

        previous_handlers = {}
        if os.name != "nt":
            for signum in (signal.SIGTERM, signal.SIGINT):
                previous_handlers[signum] = signal.signal(signum, forward_signal)
        try:
            assert process.stdout is not None
            with process.stdout:
                while chunk := process.stdout.read1(64 * 1024):
                    writer.write(chunk)
                    if tee and sys.stdout is not None:
                        try:
                            sys.stdout.buffer.write(chunk)
                            sys.stdout.buffer.flush()
                        except OSError:
                            tee = False
            returncode = process.wait()
            return returncode if returncode >= 0 else 128 - returncode
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            for signum, handler in previous_handlers.items():
                signal.signal(signum, handler)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log-file", required=True, type=Path)
    parser.add_argument("--tee", action="store_true")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("A command is required.")
    raise SystemExit(run_logged_process(command, args.log_file, tee=args.tee))


if __name__ == "__main__":
    main()
