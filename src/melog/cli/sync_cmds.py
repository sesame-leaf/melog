"""The command that shares records through a git remote."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated

import typer

from melog import sync as syncing
from melog.cli.app import app
from melog.cli.common import (
    JsonOption,
    fail,
    handle_errors,
    is_interactive,
    open_context,
    print_json,
)
from melog.cli.editing import (
    field_label,
)

# Collaboration commands


def _show_value(value: object) -> str:
    if value is None or value is syncing.MISSING or value == "":
        return "(empty)"
    if isinstance(value, tuple):
        return ", ".join(value) or "(none)"
    lines = str(value).splitlines() or [""]
    shown = "\n          ".join(lines[:3])
    return shown + (
        f"\n          ... ({len(lines) - 3} more lines)" if len(lines) > 3 else ""
    )


def _ask_conflict(conflict: syncing.Conflict) -> str:
    typer.echo(f"\nConflict in {conflict.where}, {field_label(conflict.field)}:")
    typer.echo(f"  local:   {_show_value(conflict.local)}")
    typer.echo(f"  remote:  {_show_value(conflict.remote)}")
    while True:
        answer = typer.prompt("Keep [l]ocal or [r]emote?").strip().lower()
        if answer in ("l", "local"):
            return "local"
        if answer in ("r", "remote"):
            return "remote"


def _confirm_upload(remote: str) -> Callable[[int, list[str]], bool]:
    def confirm(count: int, files: list[str]) -> bool:
        typer.echo(
            f"This is the first sync with '{remote}'. It uploads {count} code "
            f"snapshot{'' if count == 1 else 's'}, which include files that are not "
            "committed unless git ignores them."
        )
        if files:
            typer.echo("These files are in the snapshots but were never committed:")
            for name in files[:20]:
                typer.echo(f"  {name}")
            if len(files) > 20:
                typer.echo(f"  ... and {len(files) - 20} more")
        return typer.confirm("Upload them?", default=False)

    return confirm


def _approve_upload(count: int, files: list[str]) -> bool:
    return True


@app.command("sync")
def sync_command(
    remote: Annotated[
        str, typer.Option("--remote", help="The git remote to sync with.")
    ] = "origin",
    prefer: Annotated[
        str | None,
        typer.Option(
            "--prefer",
            metavar="local|remote",
            help="Settle every conflict in favor of this side without asking.",
        ),
    ] = None,
    yes: Annotated[
        bool,
        typer.Option(
            "--yes", help="Upload code snapshots without asking on the first sync."
        ),
    ] = False,
    json_output: JsonOption = False,
) -> None:
    """Share experiment records and code snapshots through a git remote."""
    if prefer not in (None, "local", "remote"):
        fail(f"--prefer expects 'local' or 'remote', got {prefer!r}")
    interactive = is_interactive(json_output)
    confirm: Callable[[int, list[str]], bool] | None = None
    if yes:
        confirm = _approve_upload
    elif interactive:
        confirm = _confirm_upload(remote)
    ctx = open_context()
    with handle_errors():
        try:
            report = syncing.sync(
                ctx.repo,
                remote,
                prefer=prefer,
                resolve=_ask_conflict if interactive else None,
                confirm=confirm,
            )
        except syncing.SyncCancelledError:
            typer.echo("Sync cancelled. Nothing was uploaded.")
            raise typer.Exit(1) from None

    if json_output:
        print_json(report.to_dict())
        return
    lines = []
    if report.received:
        lines.append(f"  received: {', '.join(report.received)}")
    if report.updated:
        lines.append(f"  updated:  {', '.join(report.updated)}")
    if report.sent:
        lines.append(f"  sent:     {', '.join(report.sent)}")
    if report.sent_changes:
        lines.append(f"  sent changes to: {', '.join(report.sent_changes)}")
    for where, key, side in report.resolved:
        lines.append(f"  resolved: {where} {field_label(key)} (kept {side})")
    for project, old, new in report.renamed:
        lines.append(
            f"  renamed:  your {project}/{old} is now {project}/{new}, because "
            f"another clone also used {old}"
        )
    if not lines:
        typer.echo(f"Already in sync with {remote}.")
        return
    typer.echo(f"Synced with {remote}.")
    for line in lines:
        typer.echo(line)
