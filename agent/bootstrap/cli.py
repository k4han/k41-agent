import asyncio
import logging
import os
import shutil
import subprocess
import sys
from functools import wraps
from pathlib import Path
from typing import Any

import typer

from agent.bootstrap.app import run as run_server
from agent.modules.admin_auth import get_admin_auth_service
from agent.shared.infrastructure.db import (
    Base,
    create_tables,
    get_database_url,
    initialize_async_engine,
    load_orm_models,
)
from agent.modules.users import get_pairing_service

logger = logging.getLogger(__name__)

app = typer.Typer(
    name="kaka",
    help="Kaka Agent CLI — manage and interact with your AI agent.",
    no_args_is_help=False,
    add_completion=False,
)

PID_FILE = Path.home() / ".kaka-agent" / "server.pid"
SHUTDOWN_SIGNAL = Path.home() / ".kaka-agent" / "shutdown.signal"
SERVER_LOG_FILE = Path.home() / ".kaka-agent" / "server.log"


def _echo_info(message: str) -> None:
    typer.secho(f"[INFO] {message}", fg=typer.colors.BLUE)


def _echo_success(message: str) -> None:
    typer.secho(f"[OK] {message}", fg=typer.colors.GREEN)


def _echo_warning(message: str) -> None:
    typer.secho(f"[WARNING] {message}", fg=typer.colors.YELLOW)


def _echo_error(message: str) -> None:
    typer.secho(f"[ERROR] {message}", fg=typer.colors.RED)


def _print_section(title: str) -> None:
    typer.echo(f"\n{title}")


def _print_key_value(label: str, value: Any) -> None:
    typer.echo(f"  {label}: {value}")


def _base_url(host: str, port: int) -> str:
    connect_host = "127.0.0.1" if host in {"0.0.0.0", "::", ""} else host
    if ":" in connect_host and not connect_host.startswith("["):
        connect_host = f"[{connect_host}]"
    return f"http://{connect_host}:{port}"


def _health_url(host: str, port: int) -> str:
    return f"{_base_url(host, port)}/health"


def _print_server_endpoints(config: Any) -> None:
    base_url = _base_url(config.host, config.port)
    _print_section("Server")
    _print_key_value("URL", base_url)
    if config.enable_dashboard:
        _print_key_value("Dashboard", base_url)
    if config.enable_api:
        _print_key_value("API", f"{base_url}/api")
    _print_key_value("Health", _health_url(config.host, config.port))


def _print_runtime_files() -> None:
    _print_section("Runtime files")
    _print_key_value("PID", PID_FILE)
    _print_key_value("Logs", SERVER_LOG_FILE)


def _print_common_commands() -> None:
    _print_section("Commands")
    _print_key_value("Check", "kaka status")
    _print_key_value("Stop", "kaka stop")


def _daemonize() -> None:
    """Detach process and run in background."""
    env = os.environ.copy()
    env["KAKA_DAEMONIZED"] = "1"
    cmd = _daemon_command()
    SERVER_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with SERVER_LOG_FILE.open("ab") as log_file:
        if os.name == "nt":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = subprocess.SW_HIDE
            subprocess.Popen(
                cmd,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                creationflags=(
                    subprocess.CREATE_NO_WINDOW
                    | subprocess.DETACHED_PROCESS
                    | subprocess.CREATE_NEW_PROCESS_GROUP
                ),
                startupinfo=startupinfo,
                close_fds=True,
            )
        else:
            subprocess.Popen(
                cmd,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                close_fds=True,
            )
    sys.exit(0)


def _daemon_command() -> list[str]:
    return [
        _background_python_executable(sys.executable),
        "-m",
        "agent.bootstrap.cli",
        *sys.argv[1:],
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
    try:
        import psutil

        return psutil.pid_exists(pid)
    except Exception:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False


def _is_kaka_process(pid: int) -> bool:
    """Check if PID belongs to a kaka server process."""
    try:
        if os.name == "nt":
            result = subprocess.run(
                ["wmic", "process", "where", f"ProcessId={pid}", "get", "CommandLine", "/value"],
                capture_output=True,
                text=True,
            )
            output = result.stdout.lower()
        else:
            result = subprocess.run(
                ["ps", "-p", str(pid), "-o", "args="],
                capture_output=True,
                text=True,
            )
            output = result.stdout.lower()

        return "kaka" in output or "agent.bootstrap.cli" in output
    except Exception:
        return False


def _setup_database() -> None:
    load_orm_models()


def with_async_db(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        async def _run():
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
) -> None:
    """Kaka Agent CLI."""
    if version:
        typer.echo("kaka-agent 0.1.1")
        raise typer.Exit()
    _set_log_level(verbose, quiet)
    if ctx.invoked_subcommand is None:
        serve(foreground=foreground)


@app.command()
def init() -> None:
    """Initialize kaka-agent directory structure and database."""
    _echo_info("Initializing Kaka Agent...")

    home = Path.home()
    kaka_dir = home / ".kaka-agent"
    dirs = [
        kaka_dir,
        kaka_dir / "data",
        kaka_dir / "agents",
        kaka_dir / "skills",
    ]

    for directory in dirs:
        directory.mkdir(parents=True, exist_ok=True)
        _echo_success(f"Created {directory}")

    try:
        load_orm_models()
        database_url = get_database_url()
        _echo_success(f"Database URL: {database_url}")
        create_tables(database_url, metadata=Base.metadata)
        _echo_success("Database tables created")
    except Exception as e:
        _echo_error(f"Database initialization failed: {e}")
        raise typer.Exit(1)

    config_file = kaka_dir / "config.yaml"
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
                    "# Kaka Agent Configuration\n"
                    "# Runtime provider, MCP, and channel policy settings live in the database.\n\n"
                    'host: "0.0.0.0"\n'
                    "port: 8000\n"
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
    _print_section("Next steps")
    _print_key_value("Home", kaka_dir)
    _print_key_value("Start", "kaka")


def serve(foreground: bool = False) -> None:
    """Start the kaka-agent server.

    Args:
        foreground: If True, run in foreground. If False, daemonize.
    """
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    from agent.bootstrap.settings import load_bootstrap_config

    config = load_bootstrap_config()

    if PID_FILE.exists():
        try:
            old_pid = int(PID_FILE.read_text().strip())
            if _is_process_alive(old_pid) and _is_kaka_process(old_pid):
                _echo_error(f"Server is already running (PID {old_pid}).")
                _print_server_endpoints(config)
                _print_common_commands()
                raise typer.Exit(1)
        except (ValueError, OSError):
            pass

    if not foreground and os.environ.get("KAKA_DAEMONIZED") != "1":
        _echo_info("Starting Kaka Agent in background...")
        _print_server_endpoints(config)
        _print_runtime_files()
        _print_common_commands()
        _daemonize()
        return

    if foreground:
        _echo_info("Starting Kaka Agent in foreground. Press Ctrl+C to stop.")
        _print_server_endpoints(config)

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
    pairing_service = get_pairing_service()
    code, user_id = await pairing_service.create_pairing_root_user_and_code()
    typer.echo(f"[OK] Root user ready (ID: {user_id})")
    typer.echo(f"[OK] Pairing code: {code} (expires in 24 hours)")


@app.command("reset-password")
@with_async_db
async def reset_password() -> None:
    """Reset the admin user password (reads from stdin for security)."""
    import getpass

    new_pass = getpass.getpass("New admin password: ")
    if not new_pass:
        typer.echo("[ERROR] Password cannot be empty.")
        raise typer.Exit(1)
    confirm = getpass.getpass("Confirm password: ")
    if new_pass != confirm:
        typer.echo("[ERROR] Passwords do not match.")
        raise typer.Exit(1)

    auth_service = get_admin_auth_service()
    await auth_service.set_admin_password(new_pass)
    typer.echo("[OK] Admin password has been reset.")


@app.command("reset-quota")
@with_async_db
async def reset_quota() -> None:
    """Reset all recorded LLM usage/token logs."""
    from agent.shared.infrastructure.db.session import get_async_session
    from agent.modules.usage import LLMUsageEvent
    from sqlalchemy import delete

    session = await get_async_session()
    async with session:
        result = await session.execute(delete(LLMUsageEvent))
        await session.commit()
        row_count = int(result.rowcount or 0)
    typer.echo(f"[OK] Successfully reset usage logs. Deleted {row_count} record(s).")


@app.command()
def status() -> None:
    """Show the status of the kaka-agent server."""
    import httpx

    logging.getLogger("httpx").setLevel(logging.WARNING)
    typer.echo("Kaka Agent Status")
    if not PID_FILE.exists():
        _echo_warning("Server is not running.")
        _print_section("Next steps")
        _print_key_value("Start", "kaka")
        _print_key_value("PID file", PID_FILE)
        raise typer.Exit(1)

    pid_text = PID_FILE.read_text().strip()
    _print_section("Process")
    _print_key_value("PID", pid_text)

    try:
        pid = int(pid_text)
    except ValueError:
        _echo_error("Invalid PID file content.")
        _print_key_value("PID file", PID_FILE)
        raise typer.Exit(1)

    if not _is_process_alive(pid):
        _echo_error("Server process is not running. PID file may be stale.")
        _print_key_value("PID file", PID_FILE)
        raise typer.Exit(1)

    if not _is_kaka_process(pid):
        _echo_error("Process is not a Kaka Agent server. PID file may be stale.")
        _print_key_value("PID file", PID_FILE)
        raise typer.Exit(1)

    _echo_success("Server process is running.")

    try:
        from agent.bootstrap.settings import load_bootstrap_config

        config = load_bootstrap_config()
        _print_server_endpoints(config)
        _print_runtime_files()
        url = _health_url(config.host, config.port)
        resp = httpx.get(url, timeout=3.0)
        if resp.status_code == 200:
            data = resp.json()
            _print_section("Health")
            _echo_success("Health endpoint returned OK.")
            channels = data.get("services", [])
            if channels:
                _print_section("Channels")
                for ch in channels:
                    name = ch.get("name", "?")
                    ch_status = ch.get("status", "?")
                    _print_key_value(name, ch_status)
            else:
                _echo_info("No managed channels active.")
        else:
            _echo_warning(f"Health endpoint returned HTTP {resp.status_code}.")
    except httpx.ConnectError:
        _echo_warning("Could not connect to the health endpoint yet.")
    except Exception as e:
        _echo_warning(f"Could not query health: {e}")


@app.command()
def stop() -> None:
    """Stop the running kaka-agent server."""
    typer.echo("Kaka Agent Stop")
    if not PID_FILE.exists():
        _echo_warning("Server is not running.")
        _print_key_value("PID file", PID_FILE)
        raise typer.Exit(1)

    pid_text = PID_FILE.read_text().strip()
    try:
        pid = int(pid_text)
    except ValueError:
        _echo_error("Invalid PID file content.")
        PID_FILE.unlink(missing_ok=True)
        raise typer.Exit(1)

    if not _is_process_alive(pid):
        _echo_warning(f"Process {pid} was not found. Cleaning up PID file.")
        PID_FILE.unlink(missing_ok=True)
        raise typer.Exit(1)

    if not _is_kaka_process(pid):
        _echo_warning(f"Process {pid} is not a Kaka Agent server. Cleaning up PID file.")
        PID_FILE.unlink(missing_ok=True)
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
            return

    _echo_warning(f"Process {pid} is still alive after 5s.")
    _echo_info("It may take a moment to shut down.")
    PID_FILE.unlink(missing_ok=True)
    SHUTDOWN_SIGNAL.unlink(missing_ok=True)


def run_main() -> None:
    app()


if __name__ == "__main__":
    run_main()


__all__ = ["app", "run_main", "reset_password"]
