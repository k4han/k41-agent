from __future__ import annotations

import threading

from agent.bootstrap import tray


class _StopAfterOnePoll:
    def __init__(self) -> None:
        self._stopped = False

    def is_set(self) -> bool:
        return self._stopped

    def wait(self, timeout: float) -> None:
        self._stopped = True


class _TrayIcon:
    def __init__(self) -> None:
        self.icon = None
        self.title = ""
        self.update_menu_calls = 0

    def update_menu(self) -> None:
        self.update_menu_calls += 1


def test_status_poller_does_not_update_menu_from_worker_thread(monkeypatch) -> None:
    app = tray.TrayApp()
    icon = _TrayIcon()
    app.icon = icon
    app._running_cache = False
    app._stop_event = _StopAfterOnePoll()

    monkeypatch.setattr(tray, "is_server_running_or_reachable", lambda: True)
    monkeypatch.setattr(tray, "load_tray_icon", lambda running: "running-icon")

    app._status_poller()

    assert icon.icon == "running-icon"
    assert icon.title == "K41 Agent - Running"
    assert icon.update_menu_calls == 0


def test_xml_escape() -> None:
    assert tray._xml_escape('A & B < C > "D" \'E\'') == "A &amp; B &lt; C &gt; &quot;D&quot; &apos;E&apos;"
    assert tray._xml_escape("plain text") == "plain text"


def test_escape_desktop_arg() -> None:
    assert tray._escape_desktop_arg("") == '""'
    assert tray._escape_desktop_arg("simple") == "simple"
    assert tray._escape_desktop_arg("/path/with space/bin") == '"/path/with space/bin"'
    assert tray._escape_desktop_arg('quote"test') == '"quote\\"test"'
    assert tray._escape_desktop_arg(r"path\with\backslash") == '"path\\\\with\\\\backslash"'


def test_format_desktop_exec() -> None:
    argv = ["/home/user/my app/python", "-m", "agent.bootstrap.tray"]
    formatted = tray._format_desktop_exec(argv)
    assert formatted == '"/home/user/my app/python" -m agent.bootstrap.tray'


def test_get_autostart_path(monkeypatch, tmp_path) -> None:
    import os

    monkeypatch.setattr(tray.Path, "home", lambda: tmp_path)

    # Windows
    monkeypatch.setattr(os, "name", "nt")
    assert tray.get_autostart_path() is None

    # macOS
    monkeypatch.setattr(os, "name", "posix")
    monkeypatch.setattr(tray.sys, "platform", "darwin")
    mac_path = tray.get_autostart_path()
    assert mac_path == tmp_path / "Library" / "LaunchAgents" / "com.k41.agent.tray.plist"

    # Linux
    monkeypatch.setattr(tray.sys, "platform", "linux")
    linux_path = tray.get_autostart_path()
    assert linux_path == tmp_path / ".config" / "autostart" / "k41-agent-tray.desktop"


def test_enable_and_disable_autostart_darwin(monkeypatch, tmp_path) -> None:
    import os

    monkeypatch.setattr(tray.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(os, "name", "posix")
    monkeypatch.setattr(tray.sys, "platform", "darwin")
    monkeypatch.setattr(tray, "_get_autostart_executable", lambda: "/usr/local/bin/python")

    tray.enable_autostart()
    plist = tmp_path / "Library" / "LaunchAgents" / "com.k41.agent.tray.plist"
    assert plist.exists()
    content = plist.read_text(encoding="utf-8")
    assert "<string>/usr/local/bin/python</string>" in content
    assert "<string>-m</string>" in content
    assert "<string>agent.bootstrap.tray</string>" in content
    assert tray.is_autostart_enabled() is True

    tray.disable_autostart()
    assert not plist.exists()
    assert tray.is_autostart_enabled() is False


def test_enable_and_disable_autostart_linux(monkeypatch, tmp_path) -> None:
    import os

    monkeypatch.setattr(tray.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(os, "name", "posix")
    monkeypatch.setattr(tray.sys, "platform", "linux")
    monkeypatch.setattr(tray, "_get_autostart_executable", lambda: "/home/user/.venv/bin/python")

    tray.enable_autostart()
    desktop = tmp_path / ".config" / "autostart" / "k41-agent-tray.desktop"
    assert desktop.exists()
    content = desktop.read_text(encoding="utf-8")
    assert "Exec=/home/user/.venv/bin/python -m agent.bootstrap.tray" in content
    assert tray.is_autostart_enabled() is True

    tray.disable_autostart()
    assert not desktop.exists()
    assert tray.is_autostart_enabled() is False


def test_sync_autostart_from_config(monkeypatch) -> None:
    state = {"enabled": False, "enabled_calls": 0, "disabled_calls": 0}

    def _is_enabled():
        return state["enabled"]

    def _enable():
        state["enabled"] = True
        state["enabled_calls"] += 1

    def _disable():
        state["enabled"] = False
        state["disabled_calls"] += 1

    monkeypatch.setattr(tray, "is_autostart_enabled", _is_enabled)
    monkeypatch.setattr(tray, "enable_autostart", _enable)
    monkeypatch.setattr(tray, "disable_autostart", _disable)

    # 1. tray_enabled=True, tray_autostart=True, current=False -> enable
    msg = tray.sync_autostart_from_config(tray_enabled=True, tray_autostart=True)
    assert msg is not None
    assert "enabled per config" in msg
    assert state["enabled"] is True
    assert state["enabled_calls"] == 1

    # 2. Already enabled, desired enabled -> noop
    msg = tray.sync_autostart_from_config(tray_enabled=True, tray_autostart=True)
    assert msg is None
    assert state["enabled_calls"] == 1

    # 3. tray_enabled=True, tray_autostart=False, current=True -> disable
    msg = tray.sync_autostart_from_config(tray_enabled=True, tray_autostart=False)
    assert msg is not None
    assert "disabled per config" in msg
    assert state["enabled"] is False
    assert state["disabled_calls"] == 1

    # 4. tray_enabled=False, tray_autostart=True (desired=False), current was True
    state["enabled"] = True
    msg = tray.sync_autostart_from_config(tray_enabled=False, tray_autostart=True)
    assert msg is not None
    assert "disabled because tray.enabled=false" in msg
    assert state["enabled"] is False


def test_generate_icon() -> None:
    from PIL import Image

    icon_running = tray._generate_icon(running=True)
    assert isinstance(icon_running, Image.Image)
    assert icon_running.size == (64, 64)
    assert icon_running.mode == "RGBA"

    icon_stopped = tray._generate_icon(running=False)
    assert isinstance(icon_stopped, Image.Image)
    assert icon_stopped.size == (64, 64)
    assert icon_stopped.mode == "RGBA"


def test_check_tray_available_headless(monkeypatch) -> None:
    monkeypatch.setattr(tray, "is_display_available", lambda: False)
    avail, reason = tray.check_tray_available()
    assert avail is False
    assert "headless" in reason.lower()


def test_auto_start_server_starts_server_when_not_running(monkeypatch) -> None:
    calls = {"start": 0}

    monkeypatch.setattr(tray, "is_server_running_or_reachable", lambda: False)
    monkeypatch.setattr(tray, "_start_server", lambda: calls.__setitem__("start", calls["start"] + 1))

    app = tray.TrayApp()
    app._stop_event = threading.Event()

    app._auto_start_server()

    assert calls["start"] == 1


def test_auto_start_server_skips_when_already_running(monkeypatch) -> None:
    calls = {"start": 0}

    monkeypatch.setattr(tray, "is_server_running_or_reachable", lambda: True)
    monkeypatch.setattr(tray, "_start_server", lambda: calls.__setitem__("start", calls["start"] + 1))

    app = tray.TrayApp()
    app._stop_event = threading.Event()

    app._auto_start_server()

    assert calls["start"] == 0


def test_auto_start_server_skips_when_tray_stopped(monkeypatch) -> None:
    calls = {"start": 0}

    monkeypatch.setattr(tray, "_start_server", lambda: calls.__setitem__("start", calls["start"] + 1))

    app = tray.TrayApp()
    app._stop_event = threading.Event()
    app._stop_event.set()

    app._auto_start_server()

    assert calls["start"] == 0


def test_get_resources_icon_bundled() -> None:
    green_icon = tray._get_resources_icon("green")
    assert green_icon is not None
    assert green_icon.exists()
    assert green_icon.suffix.lower() == ".ico"
    assert "tray.ico" in green_icon.name

    yellow_icon = tray._get_resources_icon("yellow")
    assert yellow_icon is not None
    assert yellow_icon.exists()
    assert yellow_icon.suffix.lower() == ".ico"
    assert "tray-yellow.ico" in yellow_icon.name


def test_load_tray_icon_returns_valid_images() -> None:
    running_icon = tray.load_tray_icon(True)
    assert running_icon is not None
    assert hasattr(running_icon, "size")

    yellow_icon = tray.load_tray_icon("yellow")
    assert yellow_icon is not None
    assert hasattr(yellow_icon, "size")

    stopped_icon = tray.load_tray_icon(False)
    assert stopped_icon is not None
    assert hasattr(stopped_icon, "size")


def test_generate_icon_supports_yellow_status() -> None:
    img_green = tray._generate_icon(True)
    img_yellow = tray._generate_icon("yellow")
    img_stopped = tray._generate_icon(False)

    assert img_green.size == (64, 64)
    assert img_yellow.size == (64, 64)
    assert img_stopped.size == (64, 64)


