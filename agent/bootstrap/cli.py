import logging
import shutil
import sys
import asyncio
from functools import wraps
from pathlib import Path

import click

from agent.bootstrap.app import run as run_server
from agent.modules.admin_auth import get_admin_auth_service
from agent.shared.infrastructure.db import Base, create_tables, get_database_url, load_orm_models, initialize_async_engine
from agent.modules.users import get_pairing_service

logger = logging.getLogger(__name__)


def _setup_database() -> None:
    """Setup database models for CLI commands."""
    load_orm_models()


def with_async_db(func):
    """Decorator for CLI commands that need async DB access."""
    @wraps(func)
    def wrapper(*args, **kwargs):
        async def _run():
            _setup_database()
            await initialize_async_engine(metadata=Base.metadata)
            return await func(*args, **kwargs)
        asyncio.run(_run())
    return wrapper


@click.group()
def cli():
    """Kaka Agent CLI"""
    pass


@cli.command()
def init():
    """Initialize kaka-agent directory structure and database."""
    click.echo("Initializing kaka-agent...")

    # Create directory structure
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
        click.echo(f"[OK] Created {directory}")

    # Initialize database
    try:
        # Ensure ORM tables are registered on shared metadata before create_all().
        load_orm_models()

        database_url = get_database_url()
        click.echo(f"[OK] Database URL: {database_url}")

        # Create tables synchronously
        create_tables(database_url, metadata=Base.metadata)
        click.echo("[OK] Database tables created")

    except Exception as e:
        click.echo(f"[ERROR] Database initialization failed: {e}")
        sys.exit(1)

    # Create config from sample if not exists
    config_file = kaka_dir / "config.yaml"
    if not config_file.exists():
        # Read sample config from package
        try:
            # Try to find config.sample.yaml in project root
            project_root = Path(__file__).parent.parent.parent
            sample_file = project_root / "config.sample.yaml"

            if sample_file.exists():
                shutil.copy(sample_file, config_file)
                click.echo(f"[OK] Created config from sample at {config_file}")
                click.echo("[IMPORTANT] Please edit config.yaml and set your API key!")
            else:
                # Fallback: create minimal config
                minimal_config = (
                    "# Kaka Agent Configuration\n"
                    "# Please set your LLM API key below\n\n"
                    "llm:\n"
                    "  default_provider: \"primary\"\n"
                    "  providers:\n"
                    "    primary:\n"
                    "      type: \"openai_compatible\"\n"
                    "      api_key: \"your-api-key-here\"\n"
                    "      base_url: \"https://api.example.com/v1\"\n"
                    "      default_model: \"\"\n"
                    "      models: []\n"
                )
                config_file.write_text(minimal_config)
                click.echo(f"[OK] Created minimal config at {config_file}")
                click.echo("[IMPORTANT] Please edit config.yaml and set your API key!")
        except (OSError, IOError) as e:
            click.echo(f"[WARNING] Could not copy sample config: {e}")
            click.echo(f"[OK] Please create {config_file} manually")
        except Exception as e:
            logger.exception("Unexpected error during config creation")
            click.echo(f"[WARNING] Unexpected error: {e}")
            click.echo(f"[OK] Please create {config_file} manually")
    else:
        click.echo(f"[OK] Config already exists at {config_file}")

    click.echo("\n[OK] Initialization complete!")
    click.echo(f"  Home directory: {kaka_dir}")
    click.echo("  Run 'kaka' to start the server")


@cli.command()
def serve():
    """Start the kaka-agent server."""
    run_server()


@cli.command("cli")
def chat_cli():
    """Start an interactive chat CLI with the agent."""
    from agent.delivery.cli import run_repl

    run_repl()


@cli.command("pair-code")
@with_async_db
async def pair_code() -> None:
    """Generate a new pairing code for a root user."""
    pairing_service = get_pairing_service()
    code, user_id = await pairing_service.create_pairing_root_user_and_code()
    click.echo(f"[OK] Root user ready (ID: {user_id})")
    click.echo(f"[OK] Pairing code: {code} (expires in 24 hours)")


@cli.command("reset-password")
@click.argument("new_pass")
@with_async_db
async def reset_password(new_pass: str) -> None:
    """Reset the admin user password."""
    auth_service = get_admin_auth_service()
    await auth_service.set_admin_password(new_pass)
    click.echo(f"[OK] Admin password reset to: {new_pass}")


@cli.command("reset-quota")
@with_async_db
async def reset_quota() -> None:
    """Reset all recorded LLM usage/token logs."""
    await _perform_reset_quota()


@cli.command("resetquota")
@with_async_db
async def resetquota() -> None:
    """Reset all recorded LLM usage/token logs."""
    await _perform_reset_quota()


async def _perform_reset_quota() -> None:
    from agent.shared.infrastructure.db.session import get_async_session
    from agent.modules.usage.models import LLMUsageEvent
    from sqlalchemy import delete

    session = await get_async_session()
    async with session:
        result = await session.execute(delete(LLMUsageEvent))
        await session.commit()
        row_count = int(result.rowcount or 0)
    click.echo(f"[OK] Successfully reset usage logs. Deleted {row_count} record(s).")

def main():
    """Entry point for CLI."""
    # Default to serve if no command given
    if len(sys.argv) == 1:
        serve()
    else:
        cli()


if __name__ == "__main__":
    main()


__all__ = ["cli", "main", "reset_password"]
