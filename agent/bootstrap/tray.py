from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

TRAY_PID_FILE = Path.home() / ".k41-agent" / "tray.pid"
TRAY_LOG_FILE = Path.home() / ".k41-agent" / "tray.log"
SERVER_PID_FILE = Path.home() / ".k41-agent" / "server.pid"
SERVER_LOG_FILE = Path.home() / ".k41-agent" / "server.log"
SHUTDOWN_SIGNAL = Path.home() / ".k41-agent" / "shutdown.signal"

APP_NAME = "k41-agent"

# Re-export shared process helpers for backward compatibility
from agent.bootstrap.process_utils import (  # noqa: E402
    get_process_cmdline as _get_process_cmdline,
    get_running_server_pid as _get_running_server_pid,
    get_running_tray_pid as _get_running_tray_pid,
    is_k41_process as _is_k41_process,
    is_process_alive as _is_process_alive,
    is_tray_process as _is_tray_process,
)

# Cached Windows tray icon class to avoid redefining on every _create_icon call.
_WINDOWS_TRAY_ICON_CLS: type | None = None


def _get_windows_tray_icon_class(pystray_module):  # type: ignore[no-untyped-def]
    global _WINDOWS_TRAY_ICON_CLS
    if _WINDOWS_TRAY_ICON_CLS is not None:
        return _WINDOWS_TRAY_ICON_CLS
    from pystray._util import win32

    class WindowsTrayIcon(pystray_module.Icon):  # type: ignore[misc]
        def _on_notify(self, wparam, lparam):  # type: ignore[no-untyped-def]
            if lparam == win32.WM_RBUTTONUP:
                self.update_menu()
            return super()._on_notify(wparam, lparam)

    _WINDOWS_TRAY_ICON_CLS = WindowsTrayIcon
    return WindowsTrayIcon


def is_display_available() -> bool:
    if os.name == "nt":
        return True
    if sys.platform == "darwin":
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def check_tray_available() -> tuple[bool, str]:
    if not is_display_available():
        return False, "No display available (headless environment). System tray is not supported."
    try:
        import pystray  # noqa: F401
        from PIL import Image  # noqa: F401
    except ImportError as exc:
        return False, f"Missing tray dependencies: {exc}. Run: uv sync"
    return True, "ok"


def is_tray_running() -> bool:
    from agent.bootstrap.process_utils import is_tray_running as _is_tray_running_shared

    return _is_tray_running_shared(TRAY_PID_FILE, lenient_on_unverifiable=True)


def get_tray_pid() -> int | None:
    from agent.bootstrap.process_utils import get_running_tray_pid as _shared_get_tray_pid

    return _shared_get_tray_pid(TRAY_PID_FILE)


def is_server_running() -> bool:
    from agent.bootstrap.process_utils import is_server_running as _shared_is_server_running

    return _shared_is_server_running(SERVER_PID_FILE)


def get_server_pid() -> int | None:
    from agent.bootstrap.process_utils import get_running_server_pid as _shared_get_server_pid

    return _shared_get_server_pid(SERVER_PID_FILE)


def _get_dashboard_url() -> str:
    try:
        from agent.bootstrap.settings import load_bootstrap_config

        config = load_bootstrap_config()
        host = config.host
        port = config.port
        connect_host = "127.0.0.1" if host in {"0.0.0.0", "::", ""} else host
        if ":" in connect_host and not connect_host.startswith("["):
            connect_host = f"[{connect_host}]"
        return f"http://{connect_host}:{port}"
    except Exception:
        return "http://127.0.0.1:4141"


def _generate_icon(running: bool):
    try:
        from PIL import Image, ImageDraw
    except ImportError as exc:
        raise RuntimeError(f"Pillow is required for tray icon: {exc}") from exc

    size = 64
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    # Background circle color based on status
    if running:
        color = (34, 197, 94, 255)  # green-500
        border = (22, 163, 74, 255)
    else:
        color = (107, 114, 128, 255)  # gray-500
        border = (75, 85, 99, 255)

    # Outer circle with border
    draw.ellipse([2, 2, size - 2, size - 2], fill=color, outline=border, width=2)

    # Inner letter "K" stylized
    try:
        from PIL import ImageFont

        font = ImageFont.load_default()
        text = "K"
        # Use textbbox if available (Pillow 10+)
        try:
            bbox = draw.textbbox((0, 0), text, font=font)
            text_w = bbox[2] - bbox[0]
            text_h = bbox[3] - bbox[1]
        except Exception:
            text_w, text_h = 20, 28
        x = (size - text_w) // 2
        y = (size - text_h) // 2 - 1
        draw.text((x, y), text, fill=(255, 255, 255, 255), font=font)
    except Exception:
        # Fallback: draw a simple K with lines
        # Vertical line
        draw.line([22, 16, 22, 48], fill=(255, 255, 255, 255), width=4)
        # Diagonal lines for K
        draw.line([22, 32, 42, 16], fill=(255, 255, 255, 255), width=4)
        draw.line([22, 32, 42, 48], fill=(255, 255, 255, 255), width=4)

    # Small status dot at bottom-right
    dot_color = (34, 197, 94, 255) if running else (239, 68, 68, 255)
    draw.ellipse([size - 18, size - 18, size - 6, size - 6], fill=dot_color, outline=(255, 255, 255, 255), width=2)

    return image


def _get_resources_icon() -> Path | None:
    # Try to find bundled icon
    candidates = [
        Path(__file__).parent / "resources" / "tray.png",
        Path(__file__).parent / "resources" / "tray.ico",
        Path(__file__).parent.parent / "bootstrap" / "resources" / "tray.png",
    ]
    for p in candidates:
        if p.exists():
            return p
    return None


def load_tray_icon(running: bool):
    # Prefer bundled file if exists
    icon_path = _get_resources_icon()
    if icon_path is not None and icon_path.suffix.lower() == ".png":
        try:
            from PIL import Image

            return Image.open(icon_path)
        except Exception:
            pass
    # Generate dynamically
    return _generate_icon(running)


# --- Autostart helpers ---

def get_autostart_path() -> Path | None:
    if os.name == "nt":
        return None
    if sys.platform == "darwin":
        return Path.home() / "Library" / "LaunchAgents" / "com.k41.agent.tray.plist"
    return Path.home() / ".config" / "autostart" / "k41-agent-tray.desktop"


def is_autostart_enabled() -> bool:
    if os.name == "nt":
        try:
            import winreg

            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                0,
                winreg.KEY_READ,
            )
            try:
                winreg.QueryValueEx(key, "k41-agent-tray")
                return True
            except FileNotFoundError:
                return False
            finally:
                winreg.CloseKey(key)
        except Exception:
            return False
    else:
        p = get_autostart_path()
        return p.exists() if p else False


def _get_autostart_executable() -> str:
    # Use python executable that runs current env
    exe = sys.executable
    # On Windows, prefer pythonw.exe
    if os.name == "nt":
        p = Path(exe)
        if p.name.lower() == "python.exe":
            pythonw = p.with_name("pythonw.exe")
            if pythonw.exists():
                exe = str(pythonw)
    return exe


def _get_autostart_argv() -> list[str]:
    return [_get_autostart_executable(), "-m", "agent.bootstrap.tray"]


def _get_autostart_command() -> str:
    return f'"{_get_autostart_executable()}" -m agent.bootstrap.tray'


def _escape_desktop_arg(arg: str) -> str:
    if not arg:
        return '""'
    # Use shlex.quote-compatible logic but keep double-quote style for
    # .desktop Exec compatibility; shlex.quote would use single quotes.
    if " " in arg or "\t" in arg or '"' in arg or "\\" in arg or "'" in arg:
        escaped = arg.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return arg


def _format_desktop_exec(argv: list[str]) -> str:
    # Desktop Entry Exec escaping: prefer double-quote style for
    # compatibility; covers spaces, tabs, quotes and backslashes from
    # sys.executable. For full shell safety shlex.quote could be used,
    # but single-quote style is less common in .desktop files.
    return " ".join(_escape_desktop_arg(a) for a in argv)


def _xml_escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def enable_autostart() -> None:
    if os.name == "nt":
        try:
            import winreg

            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                0,
                winreg.KEY_WRITE,
            )
            try:
                cmd = _get_autostart_command()
                winreg.SetValueEx(key, "k41-agent-tray", 0, winreg.REG_SZ, cmd)
            finally:
                winreg.CloseKey(key)
        except Exception as exc:
            raise RuntimeError(f"Failed to enable autostart: {exc}") from exc
    elif sys.platform == "darwin":
        plist_path = get_autostart_path()
        assert plist_path is not None
        plist_path.parent.mkdir(parents=True, exist_ok=True)
        argv = _get_autostart_argv()
        plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.k41.agent.tray</string>
    <key>ProgramArguments</key>
    <array>
        <string>{_xml_escape(argv[0])}</string>
        <string>{_xml_escape(argv[1])}</string>
        <string>{_xml_escape(argv[2])}</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <false/>
</dict>
</plist>
"""
        plist_path.write_text(plist_content, encoding="utf-8")
    else:
        desktop_path = get_autostart_path()
        assert desktop_path is not None
        desktop_path.parent.mkdir(parents=True, exist_ok=True)
        argv = _get_autostart_argv()
        exec_cmd = _format_desktop_exec(argv)
        desktop_content = f"""[Desktop Entry]
Type=Application
Name=K41 Agent Tray
Comment=K41 Agent System Tray
Exec={exec_cmd}
Terminal=false
Categories=Utility;
X-GNOME-Autostart-enabled=true
"""
        desktop_path.write_text(desktop_content, encoding="utf-8")


def disable_autostart() -> None:
    if os.name == "nt":
        try:
            import winreg

            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                0,
                winreg.KEY_WRITE,
            )
            try:
                winreg.DeleteValue(key, "k41-agent-tray")
            except FileNotFoundError:
                pass
            finally:
                winreg.CloseKey(key)
        except Exception as exc:
            raise RuntimeError(f"Failed to disable autostart: {exc}") from exc
    else:
        p = get_autostart_path()
        if p is not None and p.exists():
            p.unlink(missing_ok=True)


def sync_autostart_from_config(tray_enabled: bool, tray_autostart: bool) -> str | None:
    """Sync OS autostart state with config. Returns message if changed, None otherwise.

    Centralizes the logic previously duplicated in cli._sync_autostart_from_config
    and ensures a single place manages enable/disable decisions.
    """
    desired = bool(tray_autostart) and bool(tray_enabled)
    current = is_autostart_enabled()
    if not tray_enabled and current:
        disable_autostart()
        return "Autostart disabled because tray.enabled=false."
    if desired and not current:
        enable_autostart()
        return "Autostart enabled per config (tray.autostart=true)."
    if not desired and current:
        disable_autostart()
        return "Autostart disabled per config (tray.autostart=false)."
    return None


# --- Server control helpers ---

def _open_dashboard(icon=None, item=None) -> None:
    url = _get_dashboard_url()
    try:
        webbrowser.open(url)
        if icon is not None:
            try:
                icon.notify(f"Opened {url}", "K41 Agent")
            except Exception:
                pass
    except Exception as exc:
        logger.exception("Failed to open dashboard: %s", exc)
        if icon is not None:
            try:
                icon.notify(f"Failed to open: {exc}", "K41 Agent")
            except Exception:
                pass


def _view_logs(icon=None, item=None) -> None:
    log_file = SERVER_LOG_FILE
    if not log_file.exists():
        if icon is not None:
            try:
                icon.notify("Log file not found", "K41 Agent")
            except Exception:
                pass
        return
    try:
        if os.name == "nt":
            os.startfile(str(log_file))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(log_file)])
        else:
            subprocess.Popen(["xdg-open", str(log_file)])
    except Exception as exc:
        logger.exception("Failed to open logs: %s", exc)
        if icon is not None:
            try:
                icon.notify(f"Failed to open logs: {exc}", "K41 Agent")
            except Exception:
                pass


def _open_config_folder(icon=None, item=None) -> None:
    folder = Path.home() / ".k41-agent"
    try:
        if os.name == "nt":
            os.startfile(str(folder))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(folder)])
        else:
            subprocess.Popen(["xdg-open", str(folder)])
    except Exception as exc:
        logger.exception("Failed to open config folder: %s", exc)


def _start_server(icon=None, item=None) -> None:
    if is_server_running():
        if icon is not None:
            try:
                icon.notify("Server is already running", "K41 Agent")
            except Exception:
                pass
        return
    try:
        from agent.bootstrap.process_utils import spawn_detached_process

        exe = sys.executable
        if os.name == "nt":
            p = Path(exe)
            if p.name.lower() == "python.exe":
                pythonw = p.with_name("pythonw.exe")
                if pythonw.exists():
                    exe = str(pythonw)
        env = os.environ.copy()
        env["K41_DAEMONIZED"] = "1"
        spawn_detached_process(
            [exe, "-m", "agent.bootstrap.cli"],
            SERVER_LOG_FILE,
            env=env,
        )
        if icon is not None:
            try:
                icon.notify("Starting server...", "K41 Agent")
            except Exception:
                pass
    except Exception as exc:
        logger.exception("Failed to start server: %s", exc)
        if icon is not None:
            try:
                icon.notify(f"Failed to start: {exc}", "K41 Agent")
            except Exception:
                pass


def _stop_server(icon=None, item=None) -> None:
    pid = get_server_pid()
    if pid is None:
        if icon is not None:
            try:
                icon.notify("Server is not running", "K41 Agent")
            except Exception:
                pass
        return
    try:
        SHUTDOWN_SIGNAL.parent.mkdir(parents=True, exist_ok=True)
        SHUTDOWN_SIGNAL.write_text(str(pid), encoding="utf-8")
        if icon is not None:
            try:
                icon.notify(f"Stopping server (PID {pid})...", "K41 Agent")
            except Exception:
                pass
        # Poll briefly to give feedback, but don't block too long in tray thread
        def _wait_and_notify():
            for _ in range(10):
                time.sleep(0.5)
                if not is_server_running():
                    if icon is not None:
                        try:
                            icon.notify("Server stopped", "K41 Agent")
                        except Exception:
                            pass
                    return
            if icon is not None:
                try:
                    icon.notify("Server is stopping...", "K41 Agent")
                except Exception:
                    pass

        threading.Thread(target=_wait_and_notify, daemon=True).start()
    except Exception as exc:
        logger.exception("Failed to stop server: %s", exc)
        if icon is not None:
            try:
                icon.notify(f"Failed to stop: {exc}", "K41 Agent")
            except Exception:
                pass


def _restart_server(icon=None, item=None) -> None:
    _stop_server(icon, item)

    def _delayed_start():
        # Wait for server to stop then start
        for _ in range(12):
            if not is_server_running():
                break
            time.sleep(0.5)
        _start_server(icon, item)

    threading.Thread(target=_delayed_start, daemon=True).start()


def _sync_config_autostart(enabled: bool) -> None:
    try:
        from agent.shared.config import get_config_service

        svc = get_config_service()
        svc.update_setting("tray.autostart", enabled)
    except Exception:
        pass


def _enable_autostart_action(icon=None, item=None) -> None:
    try:
        enable_autostart()
        _sync_config_autostart(True)
        if icon is not None:
            try:
                icon.notify("Autostart enabled", "K41 Agent")
            except Exception:
                pass
            try:
                icon.update_menu()
            except Exception:
                pass
    except Exception as exc:
        if icon is not None:
            try:
                icon.notify(f"Failed: {exc}", "K41 Agent")
            except Exception:
                pass


def _disable_autostart_action(icon=None, item=None) -> None:
    try:
        disable_autostart()
        _sync_config_autostart(False)
        if icon is not None:
            try:
                icon.notify("Autostart disabled", "K41 Agent")
            except Exception:
                pass
            try:
                icon.update_menu()
            except Exception:
                pass
    except Exception as exc:
        if icon is not None:
            try:
                icon.notify(f"Failed: {exc}", "K41 Agent")
            except Exception:
                pass


def _quit_tray(icon, item) -> None:
    # Clean up pid file then stop icon
    try:
        TRAY_PID_FILE.unlink(missing_ok=True)
    except Exception:
        pass
    icon.stop()


def _quit_tray_and_stop_server(icon, item) -> None:
    pid = get_server_pid()
    if pid is not None:
        try:
            SHUTDOWN_SIGNAL.parent.mkdir(parents=True, exist_ok=True)
            SHUTDOWN_SIGNAL.write_text(str(pid), encoding="utf-8")
        except Exception:
            pass
    # Delay to allow server to begin shutdown, then quit tray
    def _delayed_quit():
        time.sleep(1.5)
        try:
            TRAY_PID_FILE.unlink(missing_ok=True)
        except Exception:
            pass
        icon.stop()

    threading.Thread(target=_delayed_quit, daemon=True).start()
    try:
        icon.notify("Stopping server and exiting tray...", "K41 Agent")
    except Exception:
        pass


class TrayApp:
    def __init__(self) -> None:
        self.icon = None
        self._stop_event = threading.Event()
        self._running_cache: bool | None = None

    def _build_menu(self):
        import pystray
        from pystray import MenuItem as Item

        from agent.bootstrap.version import APP_VERSION

        return pystray.Menu(
            Item(f"K41 Agent v{APP_VERSION}", None, enabled=False),
            Item(lambda item: f"Status: {'\u25cf Running' if is_server_running() else '\u25cb Stopped'}", None, enabled=False),
            pystray.Menu.SEPARATOR,
            Item("Open Dashboard", _open_dashboard),
            Item("Start Server", _start_server, enabled=lambda item: not is_server_running()),
            Item("Stop Server", _stop_server, enabled=lambda item: is_server_running()),
            Item("Restart Server", _restart_server),
            pystray.Menu.SEPARATOR,
            Item("View Logs", _view_logs),
            Item("Open Config Folder", _open_config_folder),
            pystray.Menu.SEPARATOR,
            Item("Enable Autostart", _enable_autostart_action, enabled=lambda item: not is_autostart_enabled(), visible=lambda item: not is_autostart_enabled()),
            Item("Disable Autostart", _disable_autostart_action, enabled=lambda item: is_autostart_enabled(), visible=lambda item: is_autostart_enabled()),
            pystray.Menu.SEPARATOR,
            Item("Quit Tray", _quit_tray),
            Item("Quit Tray && Stop Server", _quit_tray_and_stop_server),
        )

    def _create_icon(self, pystray, image, title, menu):
        """Create a tray icon without rebuilding a Win32 menu from a worker thread."""
        if os.name != "nt":
            return pystray.Icon("k41-agent", image, title, menu=menu)
        windows_cls = _get_windows_tray_icon_class(pystray)
        return windows_cls("k41-agent", image, title, menu=menu)

    def _status_poller(self) -> None:
        while not self._stop_event.is_set():
            try:
                running = is_server_running()
                if self._running_cache is None:
                    self._running_cache = running
                if running != self._running_cache:
                    self._running_cache = running
                    if self.icon is not None:
                        try:
                            new_icon = load_tray_icon(running)
                            self.icon.icon = new_icon
                        except Exception:
                            pass
                        try:
                            self.icon.title = f"K41 Agent - {'Running' if running else 'Stopped'}"
                        except Exception:
                            pass
            except Exception:
                pass
            # The menu refreshes on the Win32 UI thread when the user opens it.
            # Wait with early exit support — 5s reduces CPU and flicker vs 2s.
            self._stop_event.wait(5.0)

    def run(self) -> None:
        available, reason = check_tray_available()
        if not available:
            logger.warning(reason)
            # We still want to give a clear error to the user
            # This will be called from CLI which will handle typer.Exit
            raise RuntimeError(reason)

        try:
            import pystray
        except ImportError as exc:
            raise RuntimeError(f"pystray not installed: {exc}") from exc

        running = is_server_running()
        self._running_cache = running
        icon_image = load_tray_icon(running)
        title = f"K41 Agent - {'Running' if running else 'Stopped'}"
        self.icon = self._create_icon(pystray, icon_image, title, self._build_menu())

        # Start poller thread
        poller = threading.Thread(target=self._status_poller, daemon=True)
        poller.start()

        try:
            self.icon.run()
        finally:
            self._stop_event.set()
            # Cleanup pid file
            try:
                current_pid = os.getpid()
                if TRAY_PID_FILE.exists():
                    try:
                        file_pid = int(TRAY_PID_FILE.read_text(encoding="utf-8").strip())
                        if file_pid == current_pid:
                            TRAY_PID_FILE.unlink(missing_ok=True)
                    except Exception:
                        pass
            except Exception:
                pass


def run_tray_blocking() -> None:
    app = TrayApp()
    app.run()


def main() -> None:
    # Entry point for `python -m agent.bootstrap.tray`
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
    TRAY_PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    # Single instance check - lenient on unverifiable cmdline to avoid duplicates
    if is_tray_running():
        try:
            old_pid = int(TRAY_PID_FILE.read_text(encoding="utf-8").strip())
            print(f"Tray is already running (PID {old_pid}).", file=sys.stderr)
        except Exception:
            print("Tray is already running.", file=sys.stderr)
        sys.exit(1)
    # Write pid
    TRAY_PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
    try:
        run_tray_blocking()
    except RuntimeError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        pass
    finally:
        try:
            if TRAY_PID_FILE.exists():
                try:
                    file_pid = int(TRAY_PID_FILE.read_text(encoding="utf-8").strip())
                    if file_pid == os.getpid():
                        TRAY_PID_FILE.unlink(missing_ok=True)
                except Exception:
                    TRAY_PID_FILE.unlink(missing_ok=True)
        except Exception:
            pass


if __name__ == "__main__":
    main()
