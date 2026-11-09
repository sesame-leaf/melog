"""The Typer applications that the commands register on."""

from __future__ import annotations

import typer

app = typer.Typer(
    help="Record every ML experiment in a structured form.",
    no_args_is_help=True,
)
project_app = typer.Typer(
    help="Manage the settings of a melog project.",
    no_args_is_help=True,
)
app.add_typer(project_app, name="project")
