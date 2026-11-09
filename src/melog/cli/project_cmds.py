"""Commands that create and configure melog projects."""

from __future__ import annotations

from typing import Annotated

import typer

from melog.cli.app import app, project_app
from melog.cli.common import (
    JsonOption,
    ProjectOption,
    ScoresOption,
    fail,
    handle_errors,
    is_interactive,
    open_context,
    print_project,
)
from melog.cli.editing import (
    UNCHANGED,
    edit_until_valid,
)
from melog.project import (
    create_project,
    load_overview,
    save_overview,
    update_project,
)

# Project commands


@app.command()
def init(
    name: Annotated[str, typer.Argument(help="Name of the new melog project.")],
    scores: ScoresOption = None,
    horizontal: Annotated[
        bool,
        typer.Option(
            "--horizontal",
            help="Allow several experiments in progress in one workspace. "
            "Use only when every experiment lives in its own files.",
        ),
    ] = False,
    json_output: JsonOption = False,
) -> None:
    """Create a melog project."""
    ctx = open_context()
    with handle_errors():
        project = create_project(
            ctx.store,
            name,
            scores=None if scores is None else scores.split(","),
            horizontal=horizontal,
        )
    print_project(project, f"Created melog project '{project.name}'.", json_output)


@project_app.command("edit")
def project_edit(
    scores: ScoresOption = None,
    horizontal: Annotated[
        bool | None,
        typer.Option(
            "--horizontal/--no-horizontal",
            help="Allow or forbid several experiments in progress in one workspace.",
        ),
    ] = None,
    project_name: ProjectOption = None,
    json_output: JsonOption = False,
) -> None:
    """Change the settings of a melog project, or edit its overview."""
    ctx = open_context()
    if scores is None and horizontal is None:
        if not is_interactive(json_output):
            fail(
                "pass --scores or --horizontal/--no-horizontal to change settings; "
                "run in a terminal without options to edit the overview"
            )
        with handle_errors():
            name = ctx.project(project_name)
            base = load_overview(ctx.store.view(), name)
        saved = edit_until_valid(
            ctx, base, name, lambda text: save_overview(ctx.store, name, base, text)
        )
        if saved is not UNCHANGED:
            typer.echo(f"Updated the overview of '{name}'.")
        return
    with handle_errors():
        project = update_project(
            ctx.store,
            ctx.project(project_name),
            scores=None if scores is None else scores.split(","),
            horizontal=horizontal,
        )
    print_project(project, f"Updated melog project '{project.name}'.", json_output)
