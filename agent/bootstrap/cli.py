import logging
import sys
from pathlib import Path

import click

from agent.bootstrap.app import run as run_server
from agent.shared.infrastructure.db import Base, create_tables, get_database_url, load_orm_models

logger = logging.getLogger(__name__)


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
        kaka_dir / "subagents",
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

    # Create sample config if not exists
    config_file = kaka_dir / "config.yaml"
    if not config_file.exists():
        sample_config = """# Kaka Agent Configuration
# See documentation for available options
"""
        config_file.write_text(sample_config)
        click.echo(f"[OK] Created sample config at {config_file}")
    else:
        click.echo(f"[OK] Config already exists at {config_file}")

    click.echo("\n[OK] Initialization complete!")
    click.echo(f"  Home directory: {kaka_dir}")
    click.echo("  Run 'kaka' to start the server")


@cli.command()
def serve():
    """Start the kaka-agent server."""
    run_server()


def main():
    """Entry point for CLI."""
    # Default to serve if no command given
    if len(sys.argv) == 1:
        serve()
    else:
        cli()


if __name__ == "__main__":
    main()


__all__ = ["cli", "main"]
