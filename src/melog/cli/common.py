"""Options, error handling and output shared by the commands."""

from __future__ import annotations

import json
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Annotated, NoReturn

import typer

from melog import experiment
from melog import sync as syncing
from melog.editor import (
    EditorError,
)
from melog.experiment import (
    ExperimentError,
    InvalidParentError,
    InvalidTitleError,
    Outcome,
    ParentNotFoundError,
)
from melog.git import GitError, NotARepositoryError, Repo
from melog.ids import InvalidInitialsError, get_initials, set_initials
from melog.project import (
    Project,
    ProjectError,
    resolve_project,
)
from melog.record import Record, RecordError, normalize_score
from melog.store import ConcurrentUpdateError, Store
from melog.workspace import Workspace

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
ParentOption = Annotated[
    list[str] | None,
    typer.Option(
        "--parent", metavar="ID", help="An experiment this one builds on. Repeatable."
    ),
]
MotivationOption = Annotated[
    str | None,
    typer.Option("--motivation", help="What you try, why, and what you expect."),
]
EntryOption = Annotated[
    list[str] | None,
    typer.Option(
        "--entry",
        metavar="PATH",
        help="A file that represents the experiment's code. Repeatable.",
    ),
]
ScoreOption = Annotated[
    list[str] | None,
    typer.Option(
        "--score",
        metavar="NAME=VALUE",
        help="A score, e.g. 'CV=0.812'. Repeatable.",
    ),
]
VerdictOption = Annotated[
    str | None, typer.Option("--verdict", help="Your short judgment of the result.")
]
ForceOption = Annotated[
    bool,
    typer.Option(
        "--force",
        help="Finish an experiment started in another workspace. The end snapshot "
        "captures the code of this workspace.",
    ),
]

KNOWN_ERRORS = (
    ProjectError,
    ExperimentError,
    RecordError,
    InvalidInitialsError,
    ConcurrentUpdateError,
    GitError,
    EditorError,
    syncing.SyncError,
)
# Errors in an edited document that the user can fix by editing again.
FIXABLE_ERRORS = (
    RecordError,
    InvalidTitleError,
    ParentNotFoundError,
    InvalidParentError,
)


# Plumbing shared by the commands


def fail(message: str) -> NoReturn:
    typer.echo(f"error: {message}", err=True)
    raise typer.Exit(1)


def warn(message: str) -> None:
    typer.echo(f"warning: {message}", err=True)


@contextmanager
def handle_errors() -> Iterator[None]:
    try:
        yield
    except KNOWN_ERRORS as e:
        fail(str(e))


def stdin_is_tty() -> bool:
    return sys.stdin.isatty()


def is_interactive(json_output: bool) -> bool:
    return not json_output and stdin_is_tty()


@dataclass
class Context:
    repo: Repo
    store: Store
    workspace: Workspace

    def project(self, name: str | None) -> str:
        view = self.store.view()
        active = [a.project for a in experiment.active_here(view, self.workspace)]
        return resolve_project(view, name, active)


def open_context() -> Context:
    try:
        repo = Repo.discover()
    except NotARepositoryError:
        fail("not inside a git repository; melog requires one")
    return Context(repo, Store(repo), Workspace(repo))


def ask_initials(ctx: Context, interactive: bool) -> str:
    initials = get_initials(ctx.repo)
    if initials is not None:
        return initials
    if not interactive:
        fail(
            "your initials for experiment ids are not set; set them with "
            "'git config --global melog.initials <initials>'"
        )
    while True:
        answer = typer.prompt("Your initials for experiment ids (e.g. bjs)").strip()
        try:
            set_initials(ctx.repo, answer)
        except InvalidInitialsError as e:
            typer.echo(str(e), err=True)
            continue
        typer.echo(f"Saved your initials '{answer}' in your global git config.")
        return answer


def parse_score_options(values: list[str] | None) -> dict[str, str]:
    scores: dict[str, str] = {}
    for value in values or []:
        name, sep, number = value.rpartition("=")
        if not sep or not name.strip():
            fail(f"--score expects NAME=VALUE, got {value!r}")
        scores[name.strip()] = number
    return scores


def prompt_optional(text: str) -> str:
    return typer.prompt(f"{text} (Enter to skip)", default="", show_default=False)


def prompt_scores(project: Project, scores: dict[str, str]) -> None:
    for name in project.scores:
        if name in scores:
            continue
        while True:
            value = prompt_optional(name).strip()
            if not value:
                break
            try:
                scores[name] = normalize_score(name, value)
                break
            except RecordError as e:
                typer.echo(str(e), err=True)


# Output


def score_line(record: Record) -> str:
    if not record.scores:
        return "-"
    return "  ".join(f"{name}: {value or '-'}" for name, value in record.scores.items())


def experiment_json(project: str, record: Record, **extra: object) -> dict:
    return {"project": project, **record.to_dict(), **extra}


def print_json(data: object) -> None:
    typer.echo(json.dumps(data, ensure_ascii=False, indent=2))


def print_outcome(
    project: str, outcome: Outcome, verb: str, hint: str, json_output: bool
) -> None:
    record = outcome.record
    if json_output:
        print_json(
            {
                "experiment": experiment_json(project, record),
                "large_untracked_files": [
                    {"path": path, "bytes": size} for path, size in outcome.large_files
                ],
            }
        )
        return
    if outcome.large_files:
        listed = "\n".join(
            f"  {path} ({size / 1024 / 1024:.1f} MB)"
            for path, size in outcome.large_files
        )
        warn(
            "the snapshot includes large untracked files; add them to .gitignore "
            f"if they should not be recorded:\n{listed}"
        )
    typer.echo(f"{verb} {record.id}: {record.title} ({project})")
    if record.parents:
        typer.echo(f"  parents: {', '.join(record.parents)}")
    if record.status != "in_progress" and record.scores:
        typer.echo(f"  {score_line(record)}")
    if record.verdict:
        typer.echo(f"  verdict: {record.verdict}")
    typer.echo(hint)


def print_project(project: Project, headline: str, json_output: bool) -> None:
    if json_output:
        print_json({"project": project.to_dict()})
        return
    typer.echo(headline)
    typer.echo(f"  scores:     {', '.join(project.scores) or '(none)'}")
    typer.echo(f"  horizontal: {'yes' if project.horizontal else 'no'}")
