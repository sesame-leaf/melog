"""Commands that show experiment records."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from melog import diff as code_diff
from melog import experiment, views
from melog.cli.app import app
from melog.cli.common import (
    JsonOption,
    ProjectOption,
    experiment_json,
    fail,
    handle_errors,
    open_context,
    print_json,
    score_line,
)
from melog.project import load_overview, load_project
from melog.record import SECTIONS, STATUSES
from melog.views import display_width, pad
from melog.views import local_time as format_time

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


ScoreNameOption = Annotated[
    str | None,
    typer.Option(
        "--score",
        metavar="NAME",
        help="The score to show. Defaults to the first one the project records.",
    ),
]


@app.command()
def lineage(
    mermaid: Annotated[
        bool, typer.Option("--mermaid", help="Print a mermaid graph instead.")
    ] = False,
    score: ScoreNameOption = None,
    project_name: ProjectOption = None,
    json_output: JsonOption = False,
) -> None:
    """Show which experiments each experiment builds on."""
    ctx = open_context()
    with handle_errors():
        project_id = ctx.project(project_name)
        view = ctx.store.view()
        project = load_project(view, project_id)
        records = experiment.list_records(view, project_id)
    shown_score = views.score_name(project.scores, score)
    if json_output:
        print_json(
            {
                "project": project_id,
                "experiments": [
                    {
                        "id": r.id,
                        "title": r.title,
                        "status": r.status,
                        "parents": r.parents,
                        "origin": r.origin.to_dict() if r.origin else None,
                        "score": r.scores.get(shown_score) if shown_score else None,
                    }
                    for r in records
                ],
                "score": shown_score,
            }
        )
        return
    if not records:
        typer.echo(f"No experiments in project '{project_id}' yet.")
        return
    if mermaid:
        lines = views.render_mermaid(records, shown_score)
    else:
        lines = views.render_tree(views.build_lineage(records), shown_score)
    typer.echo("\n".join(lines))


def _diff_stat(ctx, view, project_id: str, record) -> str:
    try:
        base, target, _ = code_diff.resolve_sides(
            ctx.repo, view, project_id, record, None
        )
    except code_diff.NoBaseError:
        return "(시작 스냅샷이 없어서 비교할 수 없습니다)"
    files = code_diff.diff_commits(ctx.repo, base.commit, target.commit)
    if not files:
        return "(바뀐 파일이 없습니다)"
    width = max(display_width(f.path) for f in files)
    return "\n".join(
        f"{pad(f.path, width)}  +{f.additions} -{f.deletions}  ({f.status})"
        for f in files
    )


@app.command()
def export(
    output: Annotated[
        Path | None,
        typer.Option("--output", "-o", help="Write to this file instead of stdout."),
    ] = None,
    status: Annotated[
        str | None,
        typer.Option(
            "--status",
            help=f"Collect only experiments with this status: {', '.join(STATUSES)}.",
        ),
    ] = None,
    with_diffs: Annotated[
        bool,
        typer.Option("--with-diffs", help="Add the files each experiment changed."),
    ] = False,
    score: ScoreNameOption = None,
    project_name: ProjectOption = None,
    json_output: JsonOption = False,
) -> None:
    """Collect the overview, lineage and records of a project in one document."""
    if status is not None and status not in STATUSES:
        fail(f"unknown status {status!r}; use one of {', '.join(STATUSES)}")
    ctx = open_context()
    with handle_errors():
        project_id = ctx.project(project_name)
        view = ctx.store.view()
        project = load_project(view, project_id)
        overview = load_overview(view, project_id)
        records = experiment.list_records(view, project_id)
        if status is not None:
            records = [r for r in records if r.status == status]
        stats = (
            {r.id: _diff_stat(ctx, view, project_id, r) for r in records}
            if with_diffs
            else None
        )

    if json_output:
        text = (
            json.dumps(
                {
                    "project": project.to_dict(),
                    "overview": overview,
                    "experiments": [
                        experiment_json(
                            project_id,
                            r,
                            **({"diff_stat": stats[r.id]} if stats is not None else {}),
                        )
                        for r in records
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n"
        )
    else:
        text = views.export_markdown(
            project_id,
            overview,
            records,
            views.score_name(project.scores, score),
            stats,
        )
    if output is None:
        typer.echo(text, nl=False)
        return
    output.write_text(text, encoding="utf-8")
    noun = "experiment" if len(records) == 1 else "experiments"
    typer.echo(f"Wrote {len(records)} {noun} of '{project_id}' to {output}.")
