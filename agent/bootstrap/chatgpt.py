"""CLI commands for transferring a ChatGPT session to a self-hosted server."""

from pathlib import Path

import typer


app = typer.Typer(
    help="Move a local ChatGPT connection to a self-hosted server.",
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
)


@app.command("export-session")
def export_session(
    provider: str = typer.Option(..., "--provider", help="Name of the locally connected ChatGPT provider."),
    output: Path = typer.Option(..., "--output", help="New encrypted session file; existing files are never overwritten."),
    directory: Path | None = typer.Option(None, "--directory", help="Credential directory used by this k41 runtime."),
) -> None:
    """Export one session and remove its local tokens without revoking them."""
    from agent.modules.providers.chatgpt.auth import ChatGPTAuthError
    from agent.modules.providers.chatgpt.storage import ChatGPTCredentialStore
    from agent.modules.providers.chatgpt.transfer import ChatGPTSessionTransfer

    passphrase = typer.prompt("Session-file passphrase (at least 12 characters)", hide_input=True, confirmation_prompt=True)
    try:
        ChatGPTSessionTransfer(ChatGPTCredentialStore(directory)).export_session(provider, output.expanduser(), passphrase)
    except (ChatGPTAuthError, OSError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from None
    typer.echo("Encrypted session exported. Local tokens were removed without revocation.")
    typer.echo("Transfer this file over SSH, then import it on the server with the same passphrase.")


@app.command("import-session")
def import_session(
    provider: str = typer.Option(..., "--provider", help="Name of the destination ChatGPT provider created in the dashboard."),
    input_file: Path = typer.Option(..., "--input", help="Encrypted session file copied from the local computer."),
    directory: Path | None = typer.Option(None, "--directory", help="Credential directory used by this k41 runtime."),
) -> None:
    """Import credentials while keeping this server's host ID and encryption key."""
    from agent.modules.providers.chatgpt.auth import ChatGPTAuthError
    from agent.modules.providers.chatgpt.storage import ChatGPTCredentialStore
    from agent.modules.providers.chatgpt.transfer import ChatGPTSessionTransfer

    passphrase = typer.prompt("Session-file passphrase", hide_input=True)
    try:
        ChatGPTSessionTransfer(ChatGPTCredentialStore(directory)).import_session(provider, input_file.expanduser(), passphrase)
    except (ChatGPTAuthError, OSError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from None
    typer.echo("ChatGPT session imported. This server now owns token refreshes.")
    typer.echo("Reload provider settings and refresh ChatGPT models. Delete the transfer file after verification.")
