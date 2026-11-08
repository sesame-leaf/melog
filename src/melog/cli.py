"""Command line interface of melog."""

from __future__ import annotations

import json
from typing import Annotated, NoReturn

import typer

from melog.git import GitError, NotARepositoryError, Repo
from melog.project import (
    Project,
    ProjectError,
    create_project,
    resolve_project,
    update_project,
)
from melog.store import ConcurrentUpdateError, Store

app = typer.Typer(
    help="Record every ML experiment in a structured form.",
    no_args_is_help=True,
)
project_app = typer.Typer(
    help="Manage the settings of a melog project.",
    no_args_is_help=True,
)
app.add_typer(project_app, name="project")

JsonOption = Annotated[
    bool, typer.Option("--json", help="Print machine-readable JSON output.")
]
ProjectOption = Annotated[
    str | None,
    typer.Option("--project", "-p", help="The melog project to work on."),
]
ScoresOption = Annotated[
    str | None,
    typer.Option(
        "--scores",
        help="Comma-separated score names to record by default, "
        "e.g. 'CV,CV std,public LB'.",
    ),
]


def _fail(message: str) -> NoReturn:
    typer.echo(f"error: {message}", err=True)
    raise typer.Exit(1)


def _open_store() -> Store:
    try:
        return Store(Repo.discover())
    except NotARepositoryError:
        _fail("not inside a git repository; melog requires one")


def _split_scores(value: str | None) -> list[str] | None:
    return None if value is None else value.split(",")


def _print_project(project: Project, headline: str, json_output: bool) -> None:
    if json_output:
        typer.echo(json.dumps({"project": project.to_dict()}, ensure_ascii=False))
        return
    typer.echo(headline)
    typer.echo(f"  scores:     {', '.join(project.scores) or '(none)'}")
    typer.echo(f"  horizontal: {'yes' if project.horizontal else 'no'}")


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
    store = _open_store()
    try:
        project = create_project(
            store, name, scores=_split_scores(scores), horizontal=horizontal
        )
    except (ProjectError, ConcurrentUpdateError, GitError) as e:
        _fail(str(e))
    _print_project(project, f"Created melog project '{project.name}'.", json_output)


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
    """Change the settings of a melog project."""
    if scores is None and horizontal is None:
        _fail(
            "editing the overview in an editor is not available yet; "
            "pass --scores or --horizontal/--no-horizontal to change settings"
        )
    store = _open_store()
    try:
        name = resolve_project(store.view(), project_name)
        project = update_project(
            store, name, scores=_split_scores(scores), horizontal=horizontal
        )
    except (ProjectError, ConcurrentUpdateError, GitError) as e:
        _fail(str(e))
    _print_project(project, f"Updated melog project '{project.name}'.", json_output)
