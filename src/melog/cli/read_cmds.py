"""Commands that show experiment records."""

from __future__ import annotations

from typing import Annotated

import typer

from melog import experiment
from melog.cli.app import app
from melog.cli.common import (
    JsonOption,
    ProjectOption,
    display_width,
    experiment_json,
    fail,
    format_time,
    handle_errors,
    open_context,
    pad,
    print_json,
    score_line,
)
from melog.project import (
    load_project,
)
from melog.record import SECTIONS, STATUSES

# Reading commands


@app.command()
def show(
    exp_id: Annotated[str, typer.Argument(metavar="ID", help="The experiment.")],
    project_name: ProjectOption = None,
    json_output: JsonOption = False,
) -> None:
    """Show one experiment record."""
    ctx = open_context()
    with handle_errors():
        project_id = ctx.project(project_name)
        record = experiment.load_record(ctx.store.view(), project_id, exp_id)
    if json_output:
        print_json({"experiment": experiment_json(project_id, record)})
        return
    rows = [
        ("project", project_id),
        ("status", record.status),
        ("started", format_time(record.started_at)),
        ("ended", format_time(record.ended_at)),
        ("parents", ", ".join(record.parents) or "-"),
        ("entry points", ", ".join(record.entry_points) or "-"),
        ("scores", score_line(record)),
        ("verdict", record.verdict or "-"),
    ]
    if record.origin is not None:
        rows.insert(5, ("origin", f"{record.origin.project}/{record.origin.id}"))
    typer.echo(f"{record.id}: {record.title}")
    for label, value in rows:
        typer.echo(f"  {label + ':':<14}{value}")
    for name in SECTIONS:
        typer.echo(f"\n## {name}\n")
        typer.echo(record.section(name) or "-")


@app.command("list")
def list_command(
    status: Annotated[
        str | None,
        typer.Option(
            "--status",
            help=f"Show only experiments with this status: {', '.join(STATUSES)}.",
        ),
    ] = None,
    project_name: ProjectOption = None,
    json_output: JsonOption = False,
) -> None:
    """List the experiments of a project, oldest first."""
    if status is not None and status not in STATUSES:
        fail(f"unknown status {status!r}; use one of {', '.join(STATUSES)}")
    ctx = open_context()
    with handle_errors():
        project_id = ctx.project(project_name)
        view = ctx.store.view()
        project = load_project(view, project_id)
        records = experiment.list_records(view, project_id)
        here = {
            a.id
            for a in experiment.active_here(view, ctx.workspace)
            if a.project == project_id
        }
    if status is not None:
        records = [r for r in records if r.status == status]

    if json_output:
        print_json(
            {
                "project": project_id,
                "experiments": [
                    experiment_json(project_id, r, in_progress_here=r.id in here)
                    for r in records
                ],
            }
        )
        return
    if not records:
        if status is None:
            typer.echo(f"No experiments in project '{project_id}' yet.")
        else:
            typer.echo(f"No {status} experiments in project '{project_id}'.")
        return

    header = ["", "ID", "STATUS", "TITLE", *project.scores]
    rows = [
        [
            "*" if r.id in here else "",
            r.id,
            r.status,
            r.title,
            *(r.scores.get(name) or "-" for name in project.scores),
        ]
        for r in records
    ]
    widths = [
        max(display_width(row[i]) for row in [header, *rows])
        for i in range(len(header))
    ]
    for row in [header, *rows]:
        typer.echo(
            "  ".join(
                pad(cell, w) for cell, w in zip(row, widths, strict=True)
            ).rstrip()
        )
