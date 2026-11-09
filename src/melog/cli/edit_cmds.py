"""The command that edits experiment records."""

from __future__ import annotations

import copy
import sys
from typing import Annotated

import typer

from melog import experiment
from melog.cli.app import app
from melog.cli.common import (
    JsonOption,
    ProjectOption,
    experiment_json,
    fail,
    handle_errors,
    is_interactive,
    open_context,
    parse_score_options,
    print_json,
)
from melog.cli.editing import (
    UNCHANGED,
    EditCancelled,
    edit_until_valid,
    field_label,
)
from melog.editor import (
    parse_edited,
    render_record,
)
from melog.experiment import (
    NothingActiveError,
)
from melog.record import SECTIONS, Record, normalize_score


def _section_option(flag: str, section: str) -> object:
    return typer.Option(
        flag, help=f"Replace the '{section}' section. Use '-' to read it from stdin."
    )


@app.command()
def edit(
    exp_id: Annotated[
        str | None,
        typer.Argument(
            metavar="[ID]",
            help="The experiment to edit. Defaults to the one in progress here.",
        ),
    ] = None,
    title: Annotated[str | None, typer.Option("--title", help="A new title.")] = None,
    parent: Annotated[
        list[str] | None,
        typer.Option("--parent", metavar="ID", help="Replace the parents. Repeatable."),
    ] = None,
    clear_parents: Annotated[
        bool, typer.Option("--clear-parents", help="Remove all parents.")
    ] = False,
    entry: Annotated[
        list[str] | None,
        typer.Option(
            "--entry", metavar="PATH", help="Replace the entry points. Repeatable."
        ),
    ] = None,
    clear_entries: Annotated[
        bool, typer.Option("--clear-entries", help="Remove all entry points.")
    ] = False,
    score: Annotated[
        list[str] | None,
        typer.Option(
            "--score",
            metavar="NAME=VALUE",
            help="Set a score; 'NAME=' empties it. Repeatable.",
        ),
    ] = None,
    verdict: Annotated[
        str | None,
        typer.Option("--verdict", help="A new verdict; an empty string removes it."),
    ] = None,
    motivation: Annotated[
        str | None, _section_option("--motivation", "동기와 예상")
    ] = None,
    changes: Annotated[str | None, _section_option("--changes", "변경점")] = None,
    result: Annotated[str | None, _section_option("--result", "결과")] = None,
    memo: Annotated[str | None, _section_option("--memo", "메모")] = None,
    project_name: ProjectOption = None,
    json_output: JsonOption = False,
) -> None:
    """Edit an experiment record, in your editor or with options."""
    sections = dict(zip(SECTIONS, (motivation, changes, result, memo), strict=True))
    options = [title, parent, entry, score, verdict, *sections.values()]
    use_options = any(o is not None for o in options) or clear_parents or clear_entries
    if parent and clear_parents:
        fail("--parent and --clear-parents cannot be used together")
    if entry and clear_entries:
        fail("--entry and --clear-entries cannot be used together")
    if sum(value == "-" for value in sections.values()) > 1:
        fail("only one section can be read from stdin with '-'")
    if not use_options and not is_interactive(json_output):
        fail(
            "pass options such as --score or --changes to edit without an editor, "
            "or run in a terminal to open one"
        )

    ctx = open_context()
    with handle_errors():
        project_id = ctx.project(project_name)
        view = ctx.store.view()
        try:
            target = experiment.resolve_target(view, ctx.workspace, project_id, exp_id)
        except NothingActiveError:
            fail(
                "no experiment is in progress in this workspace; "
                "pass the id of the experiment to edit"
            )
        base = experiment.load_record(view, project_id, target)

    if use_options:
        edited = copy.deepcopy(base)
        with handle_errors():
            if title is not None:
                edited.title = title.strip()
            if parent is not None or clear_parents:
                edited.parents = list(dict.fromkeys(parent or []))
            if entry is not None or clear_entries:
                edited.entry_points = [e for e in entry or [] if e.strip()]
            for name, value in parse_score_options(score).items():
                edited.scores[name] = (
                    normalize_score(name, value) if value.strip() else None
                )
            if verdict is not None:
                edited.verdict = verdict.strip() or None
            for name, value in sections.items():
                if value is not None:
                    text = sys.stdin.read() if value == "-" else value
                    edited.body[name] = text.strip("\n")
            record, changed = experiment.edit(ctx.store, project_id, base, edited)
    else:

        def apply(text: str) -> tuple[Record, list[str]]:
            edited = parse_edited(text, base)
            if edited is None:
                raise EditCancelled()
            return experiment.edit(ctx.store, project_id, base, edited)

        outcome = edit_until_valid(
            ctx, render_record(project_id, base), f"{project_id}-{base.id}", apply
        )
        if outcome is UNCHANGED:
            return
        record, changed = outcome  # type: ignore[misc]

    if json_output:
        print_json(
            {"experiment": experiment_json(project_id, record), "changed": changed}
        )
    elif changed:
        labels = ", ".join(field_label(key) for key in changed)
        typer.echo(f"Updated {record.id}: {labels}")
    else:
        typer.echo("No changes.")
