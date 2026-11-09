"""Commands that compare and check out the code of experiments."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Annotated

import typer

from melog import diff as code_diff
from melog import experiment
from melog.cli.app import app
from melog.cli.common import (
    Context,
    JsonOption,
    ProjectOption,
    fail,
    handle_errors,
    open_context,
    print_json,
)
from melog.views import display_width, pad

# Code commands


def _stdout_is_tty() -> bool:
    return sys.stdout.isatty()


def _colorize(text: str) -> str:
    lines = []
    for line in text.splitlines(keepends=True):
        if line.startswith(("diff --", "--- ", "+++ ")):
            lines.append(typer.style(line, bold=True))
        elif line.startswith("@@"):
            lines.append(typer.style(line, fg="cyan"))
        elif line.startswith("+"):
            lines.append(typer.style(line, fg="green"))
        elif line.startswith("-"):
            lines.append(typer.style(line, fg="red"))
        else:
            lines.append(line)
    return "".join(lines)


def _page(ctx: Context, text: str) -> None:
    """Show ``text`` through git's pager when writing to a terminal."""
    if not _stdout_is_tty():
        typer.echo(text, nl=False)
        return
    text = _colorize(text)
    pager = ctx.repo.run("var", "GIT_PAGER").strip()
    if not pager or pager == "cat":
        typer.echo(text, nl=False)
        return
    # The same defaults git gives less and lv, so colors show and short output
    # does not wait for a key press.
    env = {"LESS": "FRX", "LV": "-c", **os.environ}
    args: list[str] | str = pager if os.name == "nt" else ["sh", "-c", pager]
    try:
        subprocess.run(
            args,
            input=text.encode("utf-8"),
            env=env,
            shell=os.name == "nt",
            check=False,
        )
    except OSError:
        typer.echo(text, nl=False)


@app.command("diff")
def diff_command(
    exp_id: Annotated[str, typer.Argument(metavar="ID", help="The experiment.")],
    paths: Annotated[
        list[str] | None,
        typer.Argument(metavar="[PATH]...", help="Compare only these paths."),
    ] = None,
    against: Annotated[
        str | None,
        typer.Option(
            "--against",
            metavar="OTHER",
            help="Compare with another experiment's code or a git revision instead "
            "of the start snapshot.",
        ),
    ] = None,
    entry_points: Annotated[
        bool,
        typer.Option(
            "--entry-points",
            help="Compare the entry points of the two experiments with each other, "
            "such as the notebook of a parent with the notebook of this experiment.",
        ),
    ] = False,
    stat: Annotated[
        bool, typer.Option("--stat", help="Show only the changed files.")
    ] = False,
    raw: Annotated[
        bool,
        typer.Option("--raw", help="Compare notebooks as raw JSON, outputs included."),
    ] = False,
    project_name: ProjectOption = None,
    json_output: JsonOption = False,
) -> None:
    """Compare the code of an experiment, to help write what changed."""
    if entry_points and paths:
        fail("paths cannot be combined with --entry-points")
    ctx = open_context()
    with handle_errors():
        project_id = ctx.project(project_name)
        view = ctx.store.view()
        record = experiment.load_record(view, project_id, exp_id)
        base, target, base_record = code_diff.resolve_sides(
            ctx.repo, view, project_id, record, against
        )
        if entry_points:
            pairs = code_diff.entry_point_pairs(base_record, record)
            files = code_diff.diff_pairs(
                ctx.repo, base.commit, target.commit, pairs, raw
            )
        else:
            files = code_diff.diff_commits(
                ctx.repo, base.commit, target.commit, paths or [], raw
            )

    if json_output:
        print_json(
            {
                "base": base.to_dict(),
                "target": target.to_dict(),
                "files": [f.to_dict() for f in files],
            }
        )
        return
    title = f"# {base.label} -> {target.label}\n"
    if not files:
        typer.echo(f"{title}No differences.")
        return
    if stat:
        width = max(display_width(f.path) for f in files)
        lines = [
            f"{pad(f.path, width)}  +{f.additions} -{f.deletions}  ({f.status})\n"
            for f in files
        ]
        added = sum(f.additions for f in files)
        removed = sum(f.deletions for f in files)
        noun = "file" if len(files) == 1 else "files"
        summary = f"{len(files)} {noun} changed, +{added} -{removed}\n"
        _page(ctx, title + "".join(lines) + summary)
        return
    _page(ctx, title + "".join(f.text for f in files))


@app.command()
def checkout(
    exp_id: Annotated[str, typer.Argument(metavar="ID", help="The experiment.")],
    path: Annotated[
        Path | None,
        typer.Argument(
            help="Where to put the code. Defaults to <repository>-<id> next to the "
            "repository."
        ),
    ] = None,
    start: Annotated[
        bool,
        typer.Option("--start", help="Check out the code the experiment started from."),
    ] = False,
    project_name: ProjectOption = None,
    json_output: JsonOption = False,
) -> None:
    """Check out the code of an experiment into a new directory."""
    ctx = open_context()
    with handle_errors():
        project_id = ctx.project(project_name)
        target, commit = experiment.checkout(
            ctx.repo, ctx.store.view(), project_id, exp_id, path, start
        )
    kind = "start" if start else "end"
    if json_output:
        print_json(
            {
                "project": project_id,
                "id": exp_id,
                "snapshot": kind,
                "commit": commit,
                "path": str(target),
            }
        )
        return
    shown = os.path.relpath(target)
    typer.echo(f"Checked out the {kind} of {exp_id} into {shown}")
    typer.echo(
        "  Files ignored by git, such as data and model weights, are not included."
    )
    typer.echo("  It is a separate workspace: you can start a new experiment there.")
    typer.echo(f"  Remove it when you are done with 'git worktree remove {shown}'.")
