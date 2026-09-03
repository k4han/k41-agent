"""Minimal terminal UI helpers for the k41 bootstrap CLI.

Style guide: Modern Tech Minimal output inspired by modern developer tools
(e.g. `claude-code`, `vercel`, `supabase`, `uv`). Crisp typography, subtle dot
status indicators, no heavy boxes, clean borderless tables.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from typing import Any

from rich.console import Console
from rich.markup import escape
from rich.table import Table
from rich.text import Text

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
if hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

console = Console(highlight=False, soft_wrap=True)

_LABEL_WIDTH = 14


def info(message: str) -> None:
    """Print a plain informational line with a subtle muted bullet."""
    console.print(f"[dim]·[/dim] {escape(message)}")


def success(message: str) -> None:
    """Print a success line with a green dot indicator."""
    console.print(f"[bold green]●[/bold green] {escape(message)}")


def warning(message: str) -> None:
    """Print a warning line with a yellow triangle indicator."""
    console.print(f"[bold yellow]▲[/bold yellow] {escape(message)}")


def error(message: str) -> None:
    """Print an error line with a red cross indicator."""
    console.print(f"[bold red]✖[/bold red] {escape(message)}")


def section(title: str) -> None:
    """Print a light section heading (blank line + bold text, no rules)."""
    console.print()
    console.print(f"[bold]{escape(title)}[/bold]")


def _render_value(value: Any) -> Any:
    """Return a printable object; escape plain strings, keep rich objects."""
    if isinstance(value, (Text, Table)):
        return value
    return escape(str(value))


def kv(label: str, value: Any) -> None:
    """Print an aligned `label  value` line."""
    padded = f"{escape(label)}:"
    console.print(f"  [dim]{padded:<{_LABEL_WIDTH}}[/dim] {_render_value(value)}")


def banner(title: str, subtitle: str | None = None) -> None:
    """Print a single quiet app banner line (no panel)."""
    content = f"[bold]{escape(title)}[/bold]"
    if subtitle:
        content += f" [dim]{escape(subtitle)}[/dim]"
    console.print(content)


def status_line(ok: bool, text: str, hint: str | None = None) -> None:
    """Print an indented status line: `● text`, with optional hint."""
    mark = "[green]●[/green]" if ok else "[red]●[/red]"
    line = f"  {mark} {escape(text)}"
    if hint:
        line += f" [dim]- {escape(hint)}[/dim]"
    console.print(line)


def status_value(ok: bool, ok_text: str, bad_text: str) -> Text:
    """Build a colored `● text` status value for kv/table cells."""
    color = "green" if ok else "red"
    text = ok_text if ok else bad_text
    return Text.from_markup(f"[{color}]●[/{color}] {text}")


def table(
    title: str | None,
    columns: Sequence[str],
    rows: Sequence[Sequence[Any]],
) -> None:
    """Print a borderless table with dim headers (minimal style)."""
    rich_table = Table(
        title=title,
        box=None,
        show_edge=False,
        pad_edge=False,
        show_header=True,
        header_style="bold dim",
        padding=(0, 2),
    )
    for column in columns:
        rich_table.add_column(column)
    for row in rows:
        rich_table.add_row(*(_render_value(cell) for cell in row))
    console.print(rich_table)


def services_table(rows: Sequence[tuple[bool | None, str, str, str]]) -> None:
    """Print a clean status table of services (Status, Service, Detail, Endpoint)."""
    rich_table = Table(
        box=None,
        show_header=False,
        show_edge=False,
        pad_edge=False,
        padding=(0, 2),
    )
    rich_table.add_column("Icon", width=2)
    rich_table.add_column("Service", style="bold", min_width=12)
    rich_table.add_column("Detail", style="dim", min_width=20)
    rich_table.add_column("Endpoint", style="cyan", no_wrap=True)
    for ok, name, detail, endpoint in rows:
        if ok is True:
            icon = "[green]●[/green]"
        elif ok is False:
            icon = "[red]●[/red]"
        else:
            icon = "[dim]○[/dim]"
        rich_table.add_row(icon, escape(name), escape(detail), escape(endpoint) if endpoint else "")
    console.print(rich_table)


def next_steps(steps: Sequence[tuple[str, str]]) -> None:
    """Print command hints as a quiet aligned quick actions line."""
    console.print()
    parts = []
    for label, command in steps:
        clean_cmd = escape(command)
        clean_lbl = escape(label)
        if clean_lbl.lower() in clean_cmd.lower().split():
            parts.append(f"[cyan]{clean_cmd}[/cyan]")
        else:
            parts.append(f"[cyan]{clean_cmd}[/cyan] [dim]({clean_lbl})[/dim]")
    console.print(f"  [dim]Quick actions:[/dim] {'  ·  '.join(parts)}")


__all__ = [
    "banner",
    "console",
    "error",
    "info",
    "kv",
    "next_steps",
    "section",
    "services_table",
    "status_line",
    "status_value",
    "success",
    "table",
    "warning",
]
