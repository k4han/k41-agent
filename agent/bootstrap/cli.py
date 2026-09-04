import asyncio
import logging
import os
import shutil
import subprocess
import sys
import time
from functools import wraps
from pathlib import Path
from typing import Any

import typer

from agent.bootstrap import ui
from agent.bootstrap.version import APP_VERSION

logger = logging.getLogger(__name__)

# Heavy imports (db/sqlalchemy, admin auth, users) are deferred: they are
# resolved lazily via module __getattr__ / inside the commands that need
# them, so that `k41 --help` and every command start instantly.

app = typer.Typer(
    name="k41",
    help="Kai Agent CLI - manage and interact with your AI agent.",
    epilog=(
        "Examples:\n\n"
        "    k41                    Start the server in the background\n\n"
        "    k41 status             Check server and tray status\n\n"
        "    k41 stop               Stop the running server\n\n"
        "    k41 cli                Start an interactive chat session\n\n"
        "    k41 tray --status      Show system tray status\n\n"
        "    k41 update             Update from the latest GitHub release"
    ),
    no_args_is_help=False,
    add_completion=False,
    rich_markup_mode="rich",
    pretty_exceptions_show_locals=False,
)

PID_FILE = Path.home() / ".k41-agent" / "server.pid"
SHUTDOWN_SIGNAL = Path.home() / ".k41-agent" / "shutdown.signal"
SERVER_LOG_FILE = Path.home() / ".k41-agent" / "server.log"
TRAY_PID_FILE = Path.home() / ".k41-agent" / "tray.pid"
TRAY_LOG_FILE = Path.home() / ".k41-agent" / "tray.log"


def _echo_info(message: str) -> None:
    ui.info(message)


def _echo_success(message: str) -> None:
    ui.success(message)


def _echo_warning(message: str) -> None:
    ui.warning(message)


def _echo_error(message: str) -> None:
    ui.error(message)


def _print_section(title: str) -> None:
    ui.section(title)


def _print_key_value(label: str, value: Any) -> None:
    ui.kv(label, value)


def _base_url(host: str, port: int) -> str:
    connect_host = "127.0.0.1" if host in {"0.0.0.0", "::", ""} else host
    if ":" in connect_host and not connect_host.startswith("["):
        connect_host = f"[{connect_host}]"
    return f"http://{connect_host}:{port}"


def _health_url(host: str, port: int) -> str:
    return f"{_base_url(host, port)}/health"


def _health_url_from_base(base_url: str) -> str:
    return f"{base_url}/health"


def _print_server_endpoints(config: Any) -> None:
    base_url = _base_url(config.host, config.port)
    rows: list[tuple[bool | None, str, str, str]] = [
        (True, "Server", "ready", base_url),
    ]
    if getattr(config, "enable_dashboard", False):
        rows.append((True, "Dashboard", "ready", f"{base_url}/dashboard"))
    if getattr(config, "enable_api", False):
        rows.append((True, "API", "active", f"{base_url}/api"))
    ui.services_table(rows)


def _print_server_endpoints_pending(config: Any) -> None:
    base_url = _base_url(config.host, config.port)
    rows: list[tuple[bool | None, str, str, str]] = [
        (None, "Server", "starting", base_url),
    ]
    if getattr(config, "enable_dashboard", False):
        rows.append((None, "Dashboard", "pending", f"{base_url}/dashboard"))
    if getattr(config, "enable_api", False):
        rows.append((None, "API", "pending", f"{base_url}/api"))
    ui.services_table(rows)


def _wait_for_server_startup(config: Any, timeout_seconds: float = 10.0) -> bool:
    """Poll until server PID is alive and health endpoint responds.

    Returns True if server is confirmed running, False otherwise.
    """
    health_url: str | None = None
    if getattr(config, "enable_web", True):
        try:
            health_url = _health_url(config.host, config.port)
        except Exception:
            health_url = None
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if PID_FILE.exists():
            try:
                pid = int(PID_FILE.read_text().strip())
            except (ValueError, OSError):
                time.sleep(0.3)
                continue
            if _is_process_alive(pid) and _is_k41_process(pid):
                if health_url is None:
                    return True
                try:
                    import httpx

                    resp = httpx.get(health_url, timeout=1.0)
                    if resp.status_code == 200:
                        return True
                except Exception:
                    pass
        time.sleep(0.3)
    # Final fallback: PID alive is considered started even if health not yet ready
    if PID_FILE.exists():
        try:
            pid = int(PID_FILE.read_text().strip())
            if _is_process_alive(pid) and _is_k41_process(pid):
                return True
        except (ValueError, OSError):
            pass
    return False




def _print_common_commands() -> None:
    ui.next_steps(
        [
            ("chat", "k41 cli"),
            ("status", "k41 status"),
            ("stop", "k41 stop"),
        ]
    )



def _daemonize() -> None:
    """Detach process and run in background."""
    from agent.bootstrap.process_utils import spawn_detached_process

    env = os.environ.copy()
    env["K41_DAEMONIZED"] = "1"
    cmd = _daemon_command()
    spawn_detached_process(cmd, SERVER_LOG_FILE, env=env)
    sys.exit(0)


def _daemon_command() -> list[str]:
    args = list(sys.argv[1:])
    # Prevent double tray spawn: daemon child should not re-spawn tray.
    if "--no-tray" not in args and "--tray" not in args:
        args.append("--no-tray")
    return [
        _background_python_executable(sys.executable),
        "-m",
        "agent.bootstrap.cli",
        *args,
    ]


def _background_python_executable(
    executable: str,
    *,
    is_windows: bool | None = None,
) -> str:
    is_windows = os.name == "nt" if is_windows is None else is_windows
    if not is_windows:
        return executable
    path = Path(executable)
    if path.name.lower() != "python.exe":
        return executable
    pythonw = path.with_name("pythonw.exe")
    return str(pythonw) if pythonw.exists() else executable


def _is_process_alive(pid: int) -> bool:
    from agent.bootstrap.process_utils import is_process_alive as _shared_alive

    return _shared_alive(pid)


def _get_process_cmdline(pid: int) -> str:
    from agent.bootstrap.process_utils import get_process_cmdline as _shared_cmd

    return _shared_cmd(pid)


def _is_k41_process(pid: int) -> bool:
    """Check if PID belongs to a k41 server process."""
    from agent.bootstrap.process_utils import is_k41_process as _shared_k41

    return _shared_k41(pid)


def _is_tray_process(pid: int, *, strict: bool = False) -> bool:
    """Check if PID belongs to a k41 tray process."""
    from agent.bootstrap.process_utils import is_tray_process as _shared_tray

    return _shared_tray(pid, strict=strict)


def _is_tray_running() -> bool:
    from agent.bootstrap.process_utils import is_tray_running as _shared_is_tray_running

    return _shared_is_tray_running(TRAY_PID_FILE, lenient_on_unverifiable=True)


def _tray_command() -> list[str]:
    from agent.bootstrap.process_utils import get_dedicated_tray_executable

    # Prefer the dedicated, friendly-named tray GUI executable (Windows).
    dedicated = get_dedicated_tray_executable()
    if dedicated is not None:
        return [dedicated]
    return [
        _background_python_executable(sys.executable),
        "-m",
        "agent.bootstrap.tray",
    ]


def _spawn_tray_process() -> None:
    from agent.bootstrap.process_utils import spawn_detached_process

    env = os.environ.copy()
    env["K41_TRAY_DAEMONIZED"] = "1"
    cmd = _tray_command()
    spawn_detached_process(cmd, TRAY_LOG_FILE, env=env)


def _maybe_stop_tray(with_tray: bool) -> bool:
    """Helper to stop tray when requested. Returns True if tray was stopped."""
    if not with_tray or not _is_tray_running():
        return False
    _echo_info("Stopping tray as requested...")
    stopped = _stop_tray_process()
    if stopped:
        _echo_success("Tray stopped.")
        return True
    _echo_warning("Could not stop tray (unverifiable or termination failed).")
    return False


def _stop_tray_process() -> bool:
    if not TRAY_PID_FILE.exists():
        return False
    try:
        pid_text = TRAY_PID_FILE.read_text(encoding="utf-8").strip()
        pid = int(pid_text)
    except (OSError, ValueError):
        TRAY_PID_FILE.unlink(missing_ok=True)
        return False
    if not _is_process_alive(pid):
        TRAY_PID_FILE.unlink(missing_ok=True)
        return False
    cmd = _get_process_cmdline(pid)
    if not cmd:
        # Cannot verify cmdline (AccessDenied / psutil missing) -> refuse to
        # terminate to avoid killing an unrelated process that reused the PID.
        return False
    if not _is_tray_process(pid, strict=True):
        # Verified non-tray process (including k41 server pid leaked into tray.pid) -> stale
        TRAY_PID_FILE.unlink(missing_ok=True)
        return False
    # Try graceful termination
    try:
        import psutil

        try:
            proc = psutil.Process(pid)
            proc.terminate()
            for _ in range(10):
                time.sleep(0.5)
                if not _is_process_alive(pid):
                    break
            else:
                try:
                    proc.kill()
                except Exception:
                    pass
        except psutil.NoSuchProcess:
            pass
    except ImportError:
        try:
            if os.name == "nt":
                from agent.shared.infrastructure.subprocess_utils import (
                    hidden_subprocess_kwargs,
                )

                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/F"],
                    capture_output=True,
                    **hidden_subprocess_kwargs(),
                )
            else:
                os.kill(pid, 15)
                time.sleep(1)
                if _is_process_alive(pid):
                    os.kill(pid, 9)
        except Exception:
            pass
    for _ in range(6):
        time.sleep(0.5)
        if not _is_process_alive(pid):
            TRAY_PID_FILE.unlink(missing_ok=True)
            return True
    # Termination failed - keep pid file to avoid duplicate spawn.
    # Lenient is_tray_running will still block a second instance.
    if not _is_process_alive(pid):
        TRAY_PID_FILE.unlink(missing_ok=True)
        return True
    return False


def _setup_database() -> None:
    from agent.shared.infrastructure.db import load_orm_models

    load_orm_models()


def with_async_db(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        async def _run():
            from agent.shared.infrastructure.db import (
                Base,
                initialize_async_engine,
            )

            _setup_database()
            await initialize_async_engine(metadata=Base.metadata)
            return await func(*args, **kwargs)

        asyncio.run(_run())

    return wrapper


def _set_log_level(verbose: bool, quiet: bool) -> None:
    if verbose:
        level = logging.DEBUG
    elif quiet:
        level = logging.WARNING
    else:
        level = logging.INFO
    logging.getLogger().setLevel(level)


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    version: bool = typer.Option(
        False, "--version", "-v", help="Show version and exit."
    ),
    verbose: bool = typer.Option(
        False, "--verbose", help="Enable debug logging."
    ),
    quiet: bool = typer.Option(
        False, "--quiet", "-q", help="Suppress info logs."
    ),
    foreground: bool = typer.Option(
        False, "--foreground", "-f", help="Run in foreground (don't daemonize)."
    ),
    tray: bool = typer.Option(
        False, "--tray", help="Force enable system tray (overrides config)."
    ),
    no_tray: bool = typer.Option(
        False, "--no-tray", help="Disable system tray for this run."
    ),
) -> None:
    """Kai Agent CLI."""
    if tray and no_tray:
        _echo_error("Cannot use --tray and --no-tray together.")
        raise typer.Exit(1)
    if version:
        typer.echo(f"k41-agent {APP_VERSION}")
        raise typer.Exit()
    _set_log_level(verbose, quiet)
    if ctx.invoked_subcommand is None:
        serve(foreground=foreground, tray=tray, no_tray=no_tray)


@app.command()
def init() -> None:
    """Initialize k41-agent directory structure and database."""
    _echo_info("Initializing Kai Agent...")

    home = Path.home()
    k41_dir = home / ".k41-agent"
    dirs = [
        k41_dir,
        k41_dir / "data",
        k41_dir / "agents",
        k41_dir / "skills",
    ]

    for directory in dirs:
        directory.mkdir(parents=True, exist_ok=True)
        _echo_success(f"Created {directory}")

    try:
        from agent.shared.infrastructure.db import (
            Base,
            create_tables,
            get_database_url,
            load_orm_models,
        )

        load_orm_models()
        database_url = get_database_url()
        _echo_success(f"Database URL: {database_url}")
        create_tables(database_url, metadata=Base.metadata)
        _echo_success("Database tables created")
    except Exception as e:
        _echo_error(f"Database initialization failed: {e}")
        raise typer.Exit(1)

    config_file = k41_dir / "config.yaml"
    if not config_file.exists():
        try:
            project_root = Path(__file__).parent.parent.parent
            sample_file = project_root / "config.sample.yaml"

            if sample_file.exists():
                shutil.copy(sample_file, config_file)
                _echo_success(f"Created config from sample at {config_file}")
                _echo_warning("Add an LLM provider from the dashboard Providers page.")
            else:
                minimal_config = (
                    "# Kai Agent Configuration\n"
                    "# Runtime provider, MCP, and channel policy settings live in the database.\n\n"
                    'host: "0.0.0.0"\n'
                    "port: 4141\n"
                    "enable_web: true\n"
                    "enable_api: true\n"
                    "enable_dashboard: true\n"
                )
                config_file.write_text(minimal_config)
                _echo_success(f"Created minimal config at {config_file}")
                _echo_warning("Add an LLM provider from the dashboard Providers page.")
        except (OSError, IOError) as e:
            _echo_warning(f"Could not copy sample config: {e}")
            _echo_info(f"Please create {config_file} manually")
        except Exception as e:
            logger.exception("Unexpected error during config creation")
            _echo_warning(f"Unexpected error: {e}")
            _echo_info(f"Please create {config_file} manually")
    else:
        _echo_success(f"Config already exists at {config_file}")

    _echo_success("Initialization complete.")
    ui.next_steps(
        [
            ("home", str(k41_dir)),
            ("start", "k41"),
        ]
    )


def _should_auto_start_tray(
    config: Any,
    tray: bool,
    no_tray: bool,
) -> bool:
    if no_tray:
        return False
    if tray:
        return True
    # Default: follow config
    return bool(getattr(config, "tray_enabled", True))


def _sync_autostart_from_config(config: Any) -> None:
    try:
        from agent.bootstrap.tray import sync_autostart_from_config

        tray_enabled = bool(getattr(config, "tray_enabled", True))
        tray_autostart = bool(getattr(config, "tray_autostart", False))
        sync_autostart_from_config(tray_enabled, tray_autostart)
    except Exception as exc:
        _echo_warning(f"Could not sync autostart: {exc}")


def serve(foreground: bool = False, tray: bool = False, no_tray: bool = False) -> None:
    """Start the k41-agent server.

    Args:
        foreground: If True, run in foreground. If False, daemonize.
        tray: If True, force enable system tray.
        no_tray: If True, disable system tray for this run.
    """
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    from agent.bootstrap.settings import load_bootstrap_config

    config = load_bootstrap_config()

    # Decide tray usage: explicit flags override config
    use_tray = _should_auto_start_tray(config, tray, no_tray)

    # Sync autostart state with config regardless of whether tray is starting now
    # (so config change takes effect on next `k41` restart)
    _sync_autostart_from_config(config)

    # Handle tray early so `k41` (auto) or `k41 --tray` can start tray even if server already running.
    # Skip in daemon child - _daemon_command injects --no-tray and tray was already spawned by parent.
    is_daemon_child = os.environ.get("K41_DAEMONIZED") == "1"
    if not is_daemon_child:
        ui.banner("Kai Agent", f"v{APP_VERSION}")
        if use_tray:
            try:
                from agent.bootstrap.tray import check_tray_available

                available, reason = check_tray_available()
                if not available:
                    _echo_warning(f"System tray not available: {reason}")
                elif not _is_tray_running():
                    _spawn_tray_process()
                    tray_started = False
                    for _ in range(10):
                        time.sleep(0.2)
                        if _is_tray_running():
                            tray_started = True
                            break
                    if tray_started:
                        _echo_success("System tray started.")
                    else:
                        _echo_warning("Tray may have failed to start. Check logs at:")
                        _print_key_value("Log", TRAY_LOG_FILE)
            except ImportError as exc:
                _echo_warning(f"Tray dependencies missing: {exc}. Run: uv sync")
            except Exception as exc:
                _echo_warning(f"Could not start tray: {exc}")
        else:
            # If tray should not run but is still running, stop it.
            # This covers both config tray.enabled=false and explicit --no-tray.
            if _is_tray_running():
                _echo_info("Stopping running tray because tray is disabled for this run...")
                if _stop_tray_process():
                    _echo_success("Tray stopped.")
                else:
                    _echo_warning("Could not stop tray (unverifiable or termination failed).")

    if PID_FILE.exists():
        try:
            old_pid = int(PID_FILE.read_text().strip())
            if _is_process_alive(old_pid) and _is_k41_process(old_pid):
                # Server already running - tray request already handled above.
                if tray and _is_tray_running():
                    # Explicit --tray request satisfied (tray running) even though
                    # server was already running; return success so scripts/CI
                    # can detect tray started correctly.
                    _echo_success(f"Server already running (PID {old_pid}), tray is running.")
                    _print_server_endpoints(config)
                    _print_common_commands()
                    raise typer.Exit(0)
                if use_tray:
                    _echo_warning(f"Server is already running (PID {old_pid}).")
                else:
                    _echo_error(f"Server is already running (PID {old_pid}).")
                _print_server_endpoints(config)
                _print_common_commands()
                raise typer.Exit(1)
        except (ValueError, OSError):
            pass

    # Health fallback: PID file stale/missing but server is still serving.
    # Prevent duplicate spawn and give accurate "already running" message.
    try:
        import httpx

        health_url = _health_url(config.host, config.port)
        resp = httpx.get(health_url, timeout=2.0)
        if resp.status_code == 200:
            _echo_warning(
                "Server health endpoint is reachable but PID check failed - server is actually running (stale PID file)."
            )
            _echo_warning("If you want to restart, run `k41 stop` (may need --with-tray) then `k41`.")
            _print_server_endpoints(config)
            _print_common_commands()
            raise typer.Exit(1)
    except typer.Exit:
        raise
    except Exception:
        pass

    if not foreground and os.environ.get("K41_DAEMONIZED") != "1":
        _echo_info("Starting Kai Agent in background...")
        from agent.bootstrap.process_utils import spawn_detached_process

        env = os.environ.copy()
        env["K41_DAEMONIZED"] = "1"
        cmd = _daemon_command()
        spawn_detached_process(cmd, SERVER_LOG_FILE, env=env)

        started = _wait_for_server_startup(config, timeout_seconds=10.0)
        if started:
            _echo_success("Server started.")
            _print_server_endpoints(config)
        else:
            _echo_warning("Server is starting in background. It may take a moment to become ready.")
            _echo_warning("If it does not start, check logs at:")
            _print_key_value("Log", SERVER_LOG_FILE)
            _print_server_endpoints_pending(config)
        _print_common_commands()
        sys.exit(0)

    if foreground:
        _echo_info("Starting Kai Agent in foreground. Press Ctrl+C to stop.")
        _print_server_endpoints(config)

    from agent.bootstrap.app import run as run_server

    PID_FILE.write_text(str(os.getpid()))
    try:
        run_server()
    finally:
        if PID_FILE.exists():
            PID_FILE.unlink(missing_ok=True)


@app.command("cli")
def chat_cli() -> None:
    """Start an interactive chat CLI with the agent."""
    from agent.delivery.cli import run_repl

    run_repl()


@app.command("pair-code")
@with_async_db
async def pair_code() -> None:
    """Generate a new pairing code for a root user."""
    from agent.modules.users import get_pairing_service
    from rich.markup import escape

    pairing_service = get_pairing_service()
    code, user_id = await pairing_service.create_pairing_root_user_and_code()
    _echo_success(f"Root user ready (ID: {user_id})")
    ui.console.print(
        f"\n  pairing code: [bold cyan]{escape(code)}[/bold cyan]"
        f" [dim](expires in 24h)[/dim]\n",
    )


@app.command("reset-password")
@with_async_db
async def reset_password() -> None:
    """Reset the admin user password (reads from stdin for security)."""
    import getpass

    from agent.modules.admin_auth import get_admin_auth_service

    new_pass = getpass.getpass("New admin password: ")
    if not new_pass:
        _echo_error("Password cannot be empty.")
        raise typer.Exit(1)
    confirm = getpass.getpass("Confirm password: ")
    if new_pass != confirm:
        _echo_error("Passwords do not match.")
        raise typer.Exit(1)

    auth_service = get_admin_auth_service()
    await auth_service.set_admin_password(new_pass)
    _echo_success("Admin password has been reset.")


@app.command("reset-quota")
@with_async_db
async def reset_quota() -> None:
    """Reset all recorded LLM usage/token logs."""
    from sqlalchemy import delete

    from agent.modules.usage import LLMUsageEvent
    from agent.shared.infrastructure.db.session import get_async_session

    session = await get_async_session()
    async with session:
        result = await session.execute(delete(LLMUsageEvent))
        await session.commit()
        row_count = int(result.rowcount or 0)
    _echo_success(f"Successfully reset usage logs. Deleted {row_count} record(s).")


@app.command()
def status() -> None:
    """Show the status of the k41-agent server."""
    import httpx
    from rich.markup import escape

    logging.getLogger("httpx").setLevel(logging.WARNING)

    ui.banner("Kai Agent", f"v{APP_VERSION}")
    ui.console.print()

    # --- Server process state ---
    # Load config early so health fallback can run even when PID is stale/missing
    base_url: str | None = None
    config: Any = None
    try:
        from agent.bootstrap.settings import load_bootstrap_config

        config = load_bootstrap_config()
        base_url = _base_url(config.host, config.port)
    except Exception:
        base_url = None
        config = None

    server_ok = False
    pid_text = ""
    pid_stale_reason = ""
    if PID_FILE.exists():
        pid_text = PID_FILE.read_text().strip()
        try:
            pid = int(pid_text)
        except ValueError:
            _echo_error("Invalid PID file content.")
            pid_stale_reason = "invalid pid file"
        else:
            if not _is_process_alive(pid):
                _echo_warning("Server process is not running. PID file may be stale.")
                pid_stale_reason = "process not alive"
            elif not _is_k41_process(pid):
                _echo_warning("Process is not a Kai Agent server. PID file may be stale.")
                pid_stale_reason = "not a k41 process"
            else:
                server_ok = True
    else:
        pid_stale_reason = "pid file missing"

    # Health fallback: if PID check says stopped but health endpoint is reachable,
    # the server is actually running (pid file stale/deleted, pid reused, etc.).
    health_data: Any = None
    health_fetch_error: str | None = None
    health_status_code: int | None = None
    if base_url is not None:
        try:
            url = _health_url_from_base(base_url)
            resp = httpx.get(url, timeout=3.0)
            health_status_code = resp.status_code
            if resp.status_code == 200:
                health_data = resp.json()
                if not server_ok:
                    _echo_warning(
                        f"Server PID check failed ({pid_stale_reason}) but health endpoint is reachable - server is actually running."
                    )
                    server_ok = True
                    if pid_text:
                        pid_text = f"{pid_text} (stale, health ok)"
                    else:
                        pid_text = "unknown (health check)"
            elif server_ok:
                health_fetch_error = f"Health endpoint returned HTTP {resp.status_code}."
        except httpx.ConnectError:
            if server_ok:
                health_fetch_error = "Could not connect to the health endpoint yet."
        except Exception as e:
            if server_ok:
                health_fetch_error = f"Could not query health: {e}"

    rows: list[tuple[bool | None, str, str, str]] = []
    if server_ok:
        label = f"running (pid {pid_text})" if pid_text else "running (health check)"
        rows.append((True, "Server", label, base_url or ""))
        if config and getattr(config, "enable_dashboard", False) and base_url:
            rows.append((True, "Dashboard", "ready", f"{base_url}/dashboard"))
        if config and getattr(config, "enable_api", False) and base_url:
            rows.append((True, "API", "active", f"{base_url}/api"))
    else:
        rows.append((False, "Server", "stopped", ""))

    # --- System tray ---
    tray_running = _is_tray_running()
    if tray_running:
        try:
            from agent.bootstrap.settings import load_bootstrap_config

            tray_cfg = load_bootstrap_config()
            autostart = "on" if tray_cfg.tray_autostart else "off"
        except Exception:
            autostart = "unknown"
        tray_pid = ""
        try:
            tray_pid = TRAY_PID_FILE.read_text(encoding="utf-8").strip()
        except Exception:
            pass
        tray_detail = f"active (autostart {autostart})"
        if tray_pid:
            tray_detail = f"running (pid {tray_pid}, autostart {autostart})"
        rows.append((True, "System Tray", tray_detail, ""))
    else:
        rows.append((False, "System Tray", "stopped", ""))

    # --- Health & channels ---
    if server_ok and base_url:
        # Reuse health_data fetched for fallback to avoid extra request and to
        # keep the fallback's error handling consistent.
        if health_fetch_error:
            _echo_warning(health_fetch_error)
        if health_data is not None:
            data = health_data
            channels = data.get("services", [])
            if channels:
                healthy = {"running", "ok", "connected", "active", "started"}
                for ch in channels:
                    name = str(ch.get("name", "?"))
                    state = str(ch.get("status", "?"))
                    is_ok = state.lower() in healthy
                    rows.append((is_ok, f"Channel {name}", state, ""))
            else:
                rows.append((None, "Channels", "0 active", ""))
        elif health_status_code is None and health_fetch_error is None:
            # No health attempt yet (e.g. base_url was None at first fetch but now set)
            # or fallback fetch was skipped due to missing base_url - retry once.
            try:
                url = _health_url_from_base(base_url)
                resp = httpx.get(url, timeout=3.0)
                if resp.status_code == 200:
                    data = resp.json()
                    channels = data.get("services", [])
                    if channels:
                        healthy = {"running", "ok", "connected", "active", "started"}
                        for ch in channels:
                            name = str(ch.get("name", "?"))
                            state = str(ch.get("status", "?"))
                            is_ok = state.lower() in healthy
                            rows.append((is_ok, f"Channel {name}", state, ""))
                    else:
                        rows.append((None, "Channels", "0 active", ""))
                else:
                    _echo_warning(f"Health endpoint returned HTTP {resp.status_code}.")
            except httpx.ConnectError:
                _echo_warning("Could not connect to the health endpoint yet.")
            except Exception as e:
                _echo_warning(f"Could not query health: {e}")
        elif health_data is None and health_fetch_error is None and health_status_code is not None:
            # Health returned non-200 and no explicit error was stored for non-server_ok case
            _echo_warning(f"Health endpoint returned HTTP {health_status_code}.")

    ui.services_table(rows)

    if not server_ok:
        ui.next_steps(
            [
                ("start", "k41"),
                ("status", "k41 status"),
            ]
        )
        raise typer.Exit(1)

    ui.next_steps(
        [
            ("chat", "k41 cli"),
            ("stop", "k41 stop"),
        ]
    )


@app.command()
def stop(
    with_tray: bool = typer.Option(
        False,
        "--with-tray",
        help="Also stop system tray if running.",
    ),
) -> None:
    """Stop the running k41-agent server."""
    _echo_info("Stopping Kai Agent server...")
    if not PID_FILE.exists():
        _echo_warning("Server is not running.")
        _print_key_value("PID file", PID_FILE)
        _maybe_stop_tray(with_tray)
        raise typer.Exit(1)

    pid_text = PID_FILE.read_text().strip()
    try:
        pid = int(pid_text)
    except ValueError:
        _echo_error("Invalid PID file content.")
        PID_FILE.unlink(missing_ok=True)
        _maybe_stop_tray(with_tray)
        raise typer.Exit(1)

    if not _is_process_alive(pid):
        _echo_warning(f"Process {pid} was not found. Cleaning up PID file.")
        PID_FILE.unlink(missing_ok=True)
        _maybe_stop_tray(with_tray)
        raise typer.Exit(1)

    if not _is_k41_process(pid):
        _echo_warning(f"Process {pid} is not a Kai Agent server. Cleaning up PID file.")
        PID_FILE.unlink(missing_ok=True)
        _maybe_stop_tray(with_tray)
        raise typer.Exit(1)

    SHUTDOWN_SIGNAL.write_text(str(pid))
    _echo_info(f"Sent shutdown signal to server (PID {pid}).")

    import time

    for _ in range(10):
        time.sleep(0.5)
        if not _is_process_alive(pid):
            PID_FILE.unlink(missing_ok=True)
            SHUTDOWN_SIGNAL.unlink(missing_ok=True)
            _echo_success(f"Server stopped (PID {pid}).")
            _maybe_stop_tray(with_tray)
            return

    _echo_warning(f"Process {pid} is still alive after 5s.")
    _echo_info("It may take a moment to shut down.")
    PID_FILE.unlink(missing_ok=True)
    SHUTDOWN_SIGNAL.unlink(missing_ok=True)

    _maybe_stop_tray(with_tray)


def _tray_handle_enabled_toggle(
    enable_tray: bool,
    disable_tray: bool,
    stop: bool,
    status: bool,
    foreground: bool,
    enable_autostart: bool,
    disable_autostart: bool,
) -> bool:
    """Handle --enable-tray / --disable-tray. Return True if caller should return early."""
    if not (enable_tray or disable_tray):
        return False
    try:
        from agent.shared.config import get_config_service

        svc = get_config_service()
        if enable_tray:
            svc.update_setting("tray.enabled", True)
            _echo_success("System tray enabled (tray.enabled=true). Restart `k41` to apply.")
        if disable_tray:
            svc.update_setting("tray.enabled", False)
            _echo_success("System tray disabled (tray.enabled=false). Stopping tray if running...")
            if _is_tray_running():
                _stop_tray_process()
                _echo_success("Tray stopped.")
        svc2 = get_config_service()
        _print_section("Tray Config")
        _print_key_value("tray.enabled", svc2.get_bool("tray.enabled", True))
        _print_key_value("tray.autostart", svc2.get_bool("tray.autostart", False))
        if not (stop or status or foreground or enable_autostart or disable_autostart):
            return True
    except Exception as exc:
        _echo_error(f"Tray config update failed: {exc}")
        raise typer.Exit(1)
    return False


def _tray_handle_autostart_toggle(
    enable_autostart: bool,
    disable_autostart: bool,
    stop: bool,
    status: bool,
    foreground: bool,
) -> bool:
    """Handle --enable-autostart / --disable-autostart. Return True if caller should return."""
    if not (enable_autostart or disable_autostart):
        return False
    try:
        from agent.bootstrap.tray import disable_autostart as tray_disable
        from agent.bootstrap.tray import enable_autostart as tray_enable
        from agent.bootstrap.tray import is_autostart_enabled
        from agent.shared.config import get_config_service

        svc = get_config_service()
        if enable_autostart:
            # Update DB first for atomicity; rollback OS change if DB fails,
            # and rollback DB if OS fails.
            svc.update_setting("tray.autostart", True)
            try:
                tray_enable()
            except Exception as os_exc:
                try:
                    svc.update_setting("tray.autostart", False)
                except Exception:
                    pass
                raise RuntimeError(f"OS autostart enable failed: {os_exc}") from os_exc
            _echo_success("Autostart enabled (tray.autostart=true).")
        if disable_autostart:
            svc.update_setting("tray.autostart", False)
            try:
                tray_disable()
            except Exception as os_exc:
                try:
                    svc.update_setting("tray.autostart", True)
                except Exception:
                    pass
                raise RuntimeError(f"OS autostart disable failed: {os_exc}") from os_exc
            _echo_success("Autostart disabled (tray.autostart=false).")
        _print_section("Autostart")
        _print_key_value("OS autostart", is_autostart_enabled())
        _print_key_value("Config tray.autostart", svc.get_bool("tray.autostart", False))
        if not (stop or status or foreground):
            return True
    except Exception as exc:
        _echo_error(f"Autostart operation failed: {exc}")
        raise typer.Exit(1)
    return False


def _tray_show_status() -> None:
    if _is_tray_running():
        try:
            tray_pid = int(TRAY_PID_FILE.read_text(encoding="utf-8").strip())
            _echo_success(f"Tray is running (PID {tray_pid}).")
        except Exception:
            _echo_success("Tray is running.")
        _print_key_value("PID file", TRAY_PID_FILE)
        _print_key_value("Log", TRAY_LOG_FILE)
    else:
        _echo_warning("Tray is not running.")
        _print_key_value("start", "k41 tray")
    _print_section("Config")
    try:
        from agent.bootstrap.settings import load_bootstrap_config

        cfg = load_bootstrap_config()
        _print_key_value("tray.enabled", cfg.tray_enabled)
        _print_key_value("tray.autostart", cfg.tray_autostart)
        try:
            from agent.bootstrap.tray import is_autostart_enabled

            _print_key_value("OS autostart", is_autostart_enabled())
        except Exception:
            pass
    except Exception:
        pass


def _tray_stop() -> None:
    _echo_info("Stopping system tray...")
    if not TRAY_PID_FILE.exists():
        _echo_warning("Tray is not running.")
        _print_key_value("PID file", TRAY_PID_FILE)
        raise typer.Exit(1)
    try:
        tray_pid_text = TRAY_PID_FILE.read_text(encoding="utf-8").strip()
        tray_pid = int(tray_pid_text)
    except ValueError:
        _echo_error("Invalid tray PID file content.")
        TRAY_PID_FILE.unlink(missing_ok=True)
        raise typer.Exit(1)
    if not _is_process_alive(tray_pid):
        _echo_warning(f"Tray process {tray_pid} not found. Cleaning up PID file.")
        TRAY_PID_FILE.unlink(missing_ok=True)
        raise typer.Exit(1)
    _cmd = _get_process_cmdline(tray_pid)
    if not _cmd:
        _echo_warning(
            f"Cannot verify tray process {tray_pid} (unverifiable cmdline). "
            "Refusing to stop to avoid killing unrelated process. "
            "Remove tray.pid manually if stale."
        )
        raise typer.Exit(1)
    if not _is_tray_process(tray_pid, strict=True):
        _echo_warning(f"Process {tray_pid} is not a tray process. Cleaning up PID file.")
        TRAY_PID_FILE.unlink(missing_ok=True)
        raise typer.Exit(1)
    _echo_info(f"Stopping tray (PID {tray_pid})...")
    if _stop_tray_process():
        _echo_success(f"Tray stopped (PID {tray_pid}).")
    else:
        _echo_warning("Could not stop tray cleanly. PID file kept to avoid duplicate spawn.")


def _tray_clean_stale_pid_file() -> None:
    if not TRAY_PID_FILE.exists():
        return
    try:
        stale_pid = int(TRAY_PID_FILE.read_text(encoding="utf-8").strip())
        if not _is_process_alive(stale_pid):
            TRAY_PID_FILE.unlink(missing_ok=True)
        else:
            _cmd = _get_process_cmdline(stale_pid)
            if not _cmd:
                # Unverifiable cmdline -> keep file to avoid duplicate spawn
                # and to avoid deleting a valid tray pid when AccessDenied.
                return
            if not _is_tray_process(stale_pid, strict=True):
                TRAY_PID_FILE.unlink(missing_ok=True)
    except (ValueError, OSError):
        TRAY_PID_FILE.unlink(missing_ok=True)


def _tray_wait_for_start(timeout_seconds: float = 2.0) -> bool:
    interval = 0.2
    steps = int(timeout_seconds / interval)
    for _ in range(steps):
        time.sleep(interval)
        if _is_tray_running():
            return True
    return False


def _tray_start(foreground: bool) -> None:
    if _is_tray_running():
        try:
            old_pid = int(TRAY_PID_FILE.read_text(encoding="utf-8").strip())
            _echo_error(f"Tray is already running (PID {old_pid}).")
        except Exception:
            _echo_error("Tray is already running.")
        raise typer.Exit(1)
    _tray_clean_stale_pid_file()

    try:
        from agent.bootstrap.tray import check_tray_available

        available, reason = check_tray_available()
        if not available:
            _echo_error(f"System tray not available: {reason}")
            if "Missing tray dependencies" in reason:
                _echo_info("Install tray dependencies with: uv sync")
            raise typer.Exit(1)
    except ImportError as exc:
        _echo_error(f"Tray dependencies missing: {exc}")
        _echo_info("Install with: uv sync")
        raise typer.Exit(1)

    if not foreground and os.environ.get("K41_TRAY_DAEMONIZED") != "1":
        _echo_info("Starting system tray in background...")
        _print_key_value("PID file", TRAY_PID_FILE)
        _print_key_value("Log", TRAY_LOG_FILE)
        _print_key_value("Stop", "k41 tray --stop")
        _spawn_tray_process()
        if _tray_wait_for_start():
            _echo_success("System tray started.")
        else:
            _echo_warning("Tray may have failed to start. Check logs at:")
            _print_key_value("Log", TRAY_LOG_FILE)
        return

    _echo_info("Starting system tray in foreground. Close the tray icon to exit.")
    TRAY_PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    TRAY_PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
    try:
        from agent.bootstrap.tray import run_tray_blocking

        run_tray_blocking()
    except RuntimeError as exc:
        _echo_error(str(exc))
        raise typer.Exit(1) from exc
    except KeyboardInterrupt:
        _echo_info("Tray stopped by user.")
    finally:
        if TRAY_PID_FILE.exists():
            try:
                file_pid = int(TRAY_PID_FILE.read_text(encoding="utf-8").strip())
                if file_pid == os.getpid():
                    TRAY_PID_FILE.unlink(missing_ok=True)
            except Exception:
                TRAY_PID_FILE.unlink(missing_ok=True)


@app.command("tray")
def tray(
    foreground: bool = typer.Option(
        False, "--foreground", "-f", help="Run tray in foreground."
    ),
    stop: bool = typer.Option(
        False, "--stop", help="Stop running tray."
    ),
    status: bool = typer.Option(
        False, "--status", help="Show tray status."
    ),
    enable_autostart: bool = typer.Option(
        False, "--enable-autostart", help="Enable autostart on login."
    ),
    disable_autostart: bool = typer.Option(
        False, "--disable-autostart", help="Disable autostart on login."
    ),
    enable_tray: bool = typer.Option(
        False, "--enable-tray", help="Enable system tray (config tray.enabled=true)."
    ),
    disable_tray: bool = typer.Option(
        False, "--disable-tray", help="Disable system tray (config tray.enabled=false)."
    ),
) -> None:
    """Manage system tray."""
    if enable_tray and disable_tray:
        _echo_error("Cannot use --enable-tray and --disable-tray together.")
        raise typer.Exit(1)
    if enable_autostart and disable_autostart:
        _echo_error("Cannot use --enable-autostart and --disable-autostart together.")
        raise typer.Exit(1)

    if _tray_handle_enabled_toggle(
        enable_tray, disable_tray, stop, status, foreground, enable_autostart, disable_autostart
    ):
        return

    if _tray_handle_autostart_toggle(enable_autostart, disable_autostart, stop, status, foreground):
        return

    if status:
        _tray_show_status()
        return

    if stop:
        _tray_stop()
        return

    _tray_start(foreground)


@app.command("update")
def update_app(
    check: bool = typer.Option(
        False,
        "--check",
        help="Check for updates without changing the installation.",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Install the latest release even when the local version matches.",
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Confirm the update without prompting.",
    ),
) -> None:
    """Update K41 Agent from the latest GitHub release."""
    from agent.bootstrap.update import UpdateError, UpdateOptions, run_update

    try:
        run_update(
            UpdateOptions(check_only=check, force=force, yes=yes),
            echo=_echo_info,
            confirm=lambda message: typer.confirm(message, abort=False),
        )
    except UpdateError as exc:
        _echo_error(str(exc))
        raise typer.Exit(1) from exc


def run_main() -> None:
    app()


if __name__ == "__main__":
    run_main()


__all__ = ["app", "run_main", "reset_password", "update_app"]
