"""Commands that record experiments: start, done, abandon and import."""

from __future__ import annotations

from typing import Annotated

import typer

from melog import experiment
from melog.cli.app import app
from melog.cli.common import (
    EntryOption,
    ForceOption,
    JsonOption,
    MotivationOption,
    ParentOption,
    ProjectOption,
    ScoreOption,
    VerdictOption,
    ask_initials,
    experiment_json,
    fail,
    handle_errors,
    is_interactive,
    open_context,
    parse_score_options,
    print_json,
    print_outcome,
    prompt_optional,
    prompt_scores,
)
from melog.project import (
    load_project,
)

# Recording commands


@app.command()
def start(
    title: Annotated[
        str | None, typer.Argument(help="A short name for the experiment.")
    ] = None,
    parent: ParentOption = None,
    motivation: MotivationOption = None,
    entry: EntryOption = None,
    project_name: ProjectOption = None,
    json_output: JsonOption = False,
) -> None:
    """Start an experiment and take a snapshot of the code as it is now."""
    interactive = is_interactive(json_output)
    ctx = open_context()
    with handle_errors():
        project = ctx.project(project_name)
        experiment.check_new(ctx.store.view(), ctx.workspace, project, parent or [])
        initials = ask_initials(ctx, interactive)
        if title is None:
            if not interactive:
                fail("missing the title of the experiment")
            title = typer.prompt("Title")
        if motivation is None and interactive:
            motivation = prompt_optional("Motivation: what, why, and what you expect")
        outcome = experiment.start(
            ctx.repo,
            ctx.store,
            ctx.workspace,
            project,
            initials,
            title,
            parents=parent or [],
            motivation=motivation,
            entry_points=entry or [],
        )
    print_outcome(
        project,
        outcome,
        "Started",
        "Run 'melog done' when the experiment is finished.",
        json_output,
    )


@app.command()
def done(
    exp_id: Annotated[
        str | None,
        typer.Argument(
            metavar="[ID]",
            help="The experiment to finish. Defaults to the one in progress here.",
        ),
    ] = None,
    score: ScoreOption = None,
    verdict: VerdictOption = None,
    new: Annotated[
        str | None,
        typer.Option(
            "--new",
            metavar="TITLE",
            help="Record a finished experiment whose start you never declared.",
        ),
    ] = None,
    since: Annotated[
        str | None,
        typer.Option(
            "--since",
            metavar="REV",
            help="With --new: the commit where the experiment started.",
        ),
    ] = None,
    parent: ParentOption = None,
    motivation: MotivationOption = None,
    entry: EntryOption = None,
    force: ForceOption = False,
    project_name: ProjectOption = None,
    json_output: JsonOption = False,
) -> None:
    """Finish an experiment and take a snapshot of its code."""
    if new is None:
        given = [
            opt
            for opt, value in (
                ("--since", since),
                ("--parent", parent),
                ("--motivation", motivation),
                ("--entry", entry),
            )
            if value
        ]
        if given:
            fail(f"{', '.join(given)} can only be used with --new")
    else:
        if exp_id is not None:
            fail("--new records a new experiment and cannot be given an id")
        if force:
            fail("--force cannot be used with --new")

    interactive = is_interactive(json_output)
    scores = parse_score_options(score)
    ctx = open_context()
    with handle_errors():
        project_id = ctx.project(project_name)
        view = ctx.store.view()
        project = load_project(view, project_id)
        # Check before asking anything, so that the user never types in vain.
        if new is None:
            exp_id = experiment.prepare_finish(
                view, ctx.workspace, project_id, exp_id, force
            ).id
        else:
            experiment.check_new(view, ctx.workspace, project_id, parent or [])
            initials = ask_initials(ctx, interactive)
            if motivation is None and interactive:
                motivation = prompt_optional(
                    "Motivation: what, why, and what you expected"
                )
        if interactive:
            prompt_scores(project, scores)
            if verdict is None:
                verdict = prompt_optional("Verdict")
        if new is not None:
            outcome = experiment.register(
                ctx.repo,
                ctx.store,
                ctx.workspace,
                project_id,
                initials,
                new,
                parents=parent or [],
                motivation=motivation,
                entry_points=entry or [],
                since=since,
                scores=scores,
                verdict=verdict,
            )
            verb = "Recorded"
        else:
            outcome = experiment.finish(
                ctx.repo,
                ctx.store,
                ctx.workspace,
                project_id,
                exp_id,
                "done",
                scores=scores,
                verdict=verdict,
                force=force,
            )
            verb = "Finished"
    print_outcome(
        project_id,
        outcome,
        verb,
        f"Fill in the record later with 'melog edit {outcome.record.id}'.",
        json_output,
    )


@app.command()
def abandon(
    exp_id: Annotated[
        str | None,
        typer.Argument(
            metavar="[ID]",
            help="The experiment to abandon. Defaults to the one in progress here.",
        ),
    ] = None,
    verdict: VerdictOption = None,
    force: ForceOption = False,
    project_name: ProjectOption = None,
    json_output: JsonOption = False,
) -> None:
    """Stop an experiment without finishing it. The record is kept."""
    ctx = open_context()
    with handle_errors():
        project_id = ctx.project(project_name)
        outcome = experiment.finish(
            ctx.repo,
            ctx.store,
            ctx.workspace,
            project_id,
            exp_id,
            "abandoned",
            verdict=verdict,
            force=force,
        )
    print_outcome(
        project_id,
        outcome,
        "Abandoned",
        f"You can still start a new experiment with --parent {outcome.record.id}.",
        json_output,
    )


@app.command("import")
def import_command(
    source: Annotated[
        str,
        typer.Argument(
            metavar="PROJECT/ID", help="The experiment to import, e.g. lgbm/bjs-3."
        ),
    ],
    title: Annotated[
        str | None,
        typer.Option("--title", help="A new title. Defaults to the original title."),
    ] = None,
    motivation: Annotated[
        str | None,
        typer.Option("--motivation", help="Why you import it into this project."),
    ] = None,
    project_name: ProjectOption = None,
    json_output: JsonOption = False,
) -> None:
    """Import an experiment of another melog project as a new experiment."""
    source_project, sep, source_id = source.partition("/")
    if not sep or not source_project or not source_id:
        fail(f"expected PROJECT/ID, such as lgbm/bjs-3, got {source!r}")
    interactive = is_interactive(json_output)
    ctx = open_context()
    with handle_errors():
        project_id = ctx.project(project_name)
        # Check before asking anything, so that the user never types in vain.
        experiment.prepare_import(
            ctx.store.view(), project_id, source_project, source_id
        )
        initials = ask_initials(ctx, interactive)
        if motivation is None and interactive:
            motivation = prompt_optional("Motivation: why you import it")
        outcome = experiment.import_experiment(
            ctx.repo,
            ctx.store,
            project_id,
            initials,
            source_project,
            source_id,
            title=title,
            motivation=motivation,
        )
    record = outcome.record
    if json_output:
        print_json({"experiment": experiment_json(project_id, record)})
        return
    typer.echo(f"Imported {source} into {project_id} as {record.id}: {record.title}")
    typer.echo(f"Use it as a parent with 'melog start --parent {record.id}'.")
