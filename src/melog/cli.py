"""Command line interface of melog."""

from __future__ import annotations

import copy
import json
import sys
import unicodedata
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, NoReturn

import typer

from melog import experiment
from melog.editor import (
    EditorError,
    parse_edited,
    render_record,
    run_editor,
    write_temp,
)
from melog.experiment import (
    ExperimentError,
    InvalidParentError,
    InvalidTitleError,
    NothingActiveError,
    Outcome,
    ParentNotFoundError,
)
from melog.git import GitError, NotARepositoryError, Repo
from melog.ids import InvalidInitialsError, get_initials, set_initials
from melog.project import (
    Project,
    ProjectError,
    create_project,
    load_overview,
    load_project,
    resolve_project,
    save_overview,
    update_project,
)
from melog.record import SECTIONS, STATUSES, Record, RecordError, normalize_score
from melog.store import ConcurrentUpdateError, Store
from melog.workspace import Workspace

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
)
# Errors in an edited document that the user can fix by editing again.
FIXABLE_ERRORS = (
    RecordError,
    InvalidTitleError,
    ParentNotFoundError,
    InvalidParentError,
)


# Plumbing shared by the commands


def _fail(message: str) -> NoReturn:
    typer.echo(f"error: {message}", err=True)
    raise typer.Exit(1)


def _warn(message: str) -> None:
    typer.echo(f"warning: {message}", err=True)


@contextmanager
def _errors() -> Iterator[None]:
    try:
        yield
    except KNOWN_ERRORS as e:
        _fail(str(e))


def _stdin_is_tty() -> bool:
    return sys.stdin.isatty()


def _interactive(json_output: bool) -> bool:
    return not json_output and _stdin_is_tty()


@dataclass
class Context:
    repo: Repo
    store: Store
    workspace: Workspace

    def project(self, name: str | None) -> str:
        view = self.store.view()
        active = [a.project for a in experiment.active_here(view, self.workspace)]
        return resolve_project(view, name, active)


def _context() -> Context:
    try:
        repo = Repo.discover()
    except NotARepositoryError:
        _fail("not inside a git repository; melog requires one")
    return Context(repo, Store(repo), Workspace(repo))


def _initials(ctx: Context, interactive: bool) -> str:
    initials = get_initials(ctx.repo)
    if initials is not None:
        return initials
    if not interactive:
        _fail(
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


def _parse_scores(values: list[str] | None) -> dict[str, str]:
    scores: dict[str, str] = {}
    for value in values or []:
        name, sep, number = value.rpartition("=")
        if not sep or not name.strip():
            _fail(f"--score expects NAME=VALUE, got {value!r}")
        scores[name.strip()] = number
    return scores


def _prompt_optional(text: str) -> str:
    return typer.prompt(f"{text} (Enter to skip)", default="", show_default=False)


def _prompt_scores(project: Project, scores: dict[str, str]) -> None:
    for name in project.scores:
        if name in scores:
            continue
        while True:
            value = _prompt_optional(name).strip()
            if not value:
                break
            try:
                scores[name] = normalize_score(name, value)
                break
            except RecordError as e:
                typer.echo(str(e), err=True)


class _Cancelled(Exception):
    """The user emptied the document to cancel the edit."""


_UNCHANGED = object()


def _edit_until_valid(
    ctx: Context, initial: str, prefix: str, apply: Callable[[str], object]
) -> object:
    """Open ``initial`` in the editor and apply it, letting the user fix mistakes.

    Returns what ``apply`` returned, or _UNCHANGED if the user changed nothing or
    cancelled. When the edit cannot be applied, the edited text is kept in a
    temporary file so that nothing the user typed is lost.
    """
    path = write_temp(initial, prefix)

    def give_up(message: str) -> NoReturn:
        typer.echo(f"error: {message}", err=True)
        typer.echo(f"Your edit is kept in {path}", err=True)
        raise typer.Exit(1)

    while True:
        try:
            run_editor(ctx.repo, path)
        except EditorError as e:
            give_up(str(e))
        text = path.read_text(encoding="utf-8")
        if text == initial:
            path.unlink()
            typer.echo("No changes.")
            return _UNCHANGED
        try:
            result = apply(text)
        except _Cancelled:
            path.unlink()
            typer.echo("Edit cancelled.")
            return _UNCHANGED
        except FIXABLE_ERRORS as e:
            typer.echo(f"error: {e}", err=True)
            if typer.confirm("Edit again?", default=True):
                continue
            give_up("the edit was not applied")
        except KNOWN_ERRORS as e:
            give_up(str(e))
        path.unlink()
        return result


def _field_label(key: str) -> str:
    if key.startswith("scores."):
        return f"score '{key.removeprefix('scores.')}'"
    if key.startswith("body."):
        return key.removeprefix("body.")
    return key.replace("_", " ")


# Output


def _width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in text)


def _pad(text: str, width: int) -> str:
    return text + " " * (width - _width(text))


def _time(value: str | None) -> str:
    if value is None:
        return "-"
    return datetime.fromisoformat(value).astimezone().strftime("%Y-%m-%d %H:%M")


def _score_line(record: Record) -> str:
    if not record.scores:
        return "-"
    return "  ".join(f"{name}: {value or '-'}" for name, value in record.scores.items())


def _experiment_json(project: str, record: Record, **extra: object) -> dict:
    return {"project": project, **record.to_dict(), **extra}


def _print_json(data: object) -> None:
    typer.echo(json.dumps(data, ensure_ascii=False, indent=2))


def _print_outcome(
    project: str, outcome: Outcome, verb: str, hint: str, json_output: bool
) -> None:
    record = outcome.record
    if json_output:
        _print_json(
            {
                "experiment": _experiment_json(project, record),
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
        _warn(
            "the snapshot includes large untracked files; add them to .gitignore "
            f"if they should not be recorded:\n{listed}"
        )
    typer.echo(f"{verb} {record.id}: {record.title} ({project})")
    if record.parents:
        typer.echo(f"  parents: {', '.join(record.parents)}")
    if record.status != "in_progress" and record.scores:
        typer.echo(f"  {_score_line(record)}")
    if record.verdict:
        typer.echo(f"  verdict: {record.verdict}")
    typer.echo(hint)


def _print_project(project: Project, headline: str, json_output: bool) -> None:
    if json_output:
        _print_json({"project": project.to_dict()})
        return
    typer.echo(headline)
    typer.echo(f"  scores:     {', '.join(project.scores) or '(none)'}")
    typer.echo(f"  horizontal: {'yes' if project.horizontal else 'no'}")


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
    ctx = _context()
    with _errors():
        project = create_project(
            ctx.store,
            name,
            scores=None if scores is None else scores.split(","),
            horizontal=horizontal,
        )
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
    """Change the settings of a melog project, or edit its overview."""
    ctx = _context()
    if scores is None and horizontal is None:
        if not _interactive(json_output):
            _fail(
                "pass --scores or --horizontal/--no-horizontal to change settings; "
                "run in a terminal without options to edit the overview"
            )
        with _errors():
            name = ctx.project(project_name)
            base = load_overview(ctx.store.view(), name)
        saved = _edit_until_valid(
            ctx, base, name, lambda text: save_overview(ctx.store, name, base, text)
        )
        if saved is not _UNCHANGED:
            typer.echo(f"Updated the overview of '{name}'.")
        return
    with _errors():
        project = update_project(
            ctx.store,
            ctx.project(project_name),
            scores=None if scores is None else scores.split(","),
            horizontal=horizontal,
        )
    _print_project(project, f"Updated melog project '{project.name}'.", json_output)


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
    interactive = _interactive(json_output)
    ctx = _context()
    with _errors():
        project = ctx.project(project_name)
        experiment.check_new(ctx.store.view(), ctx.workspace, project, parent or [])
        initials = _initials(ctx, interactive)
        if title is None:
            if not interactive:
                _fail("missing the title of the experiment")
            title = typer.prompt("Title")
        if motivation is None and interactive:
            motivation = _prompt_optional("Motivation: what, why, and what you expect")
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
    _print_outcome(
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
            _fail(f"{', '.join(given)} can only be used with --new")
    else:
        if exp_id is not None:
            _fail("--new records a new experiment and cannot be given an id")
        if force:
            _fail("--force cannot be used with --new")

    interactive = _interactive(json_output)
    scores = _parse_scores(score)
    ctx = _context()
    with _errors():
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
            initials = _initials(ctx, interactive)
            if motivation is None and interactive:
                motivation = _prompt_optional(
                    "Motivation: what, why, and what you expected"
                )
        if interactive:
            _prompt_scores(project, scores)
            if verdict is None:
                verdict = _prompt_optional("Verdict")
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
    _print_outcome(
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
    ctx = _context()
    with _errors():
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
    _print_outcome(
        project_id,
        outcome,
        "Abandoned",
        f"You can still start a new experiment with --parent {outcome.record.id}.",
        json_output,
    )


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
        _fail("--parent and --clear-parents cannot be used together")
    if entry and clear_entries:
        _fail("--entry and --clear-entries cannot be used together")
    if sum(value == "-" for value in sections.values()) > 1:
        _fail("only one section can be read from stdin with '-'")
    if not use_options and not _interactive(json_output):
        _fail(
            "pass options such as --score or --changes to edit without an editor, "
            "or run in a terminal to open one"
        )

    ctx = _context()
    with _errors():
        project_id = ctx.project(project_name)
        view = ctx.store.view()
        try:
            target = experiment.resolve_target(view, ctx.workspace, project_id, exp_id)
        except NothingActiveError:
            _fail(
                "no experiment is in progress in this workspace; "
                "pass the id of the experiment to edit"
            )
        base = experiment.load_record(view, project_id, target)

    if use_options:
        edited = copy.deepcopy(base)
        with _errors():
            if title is not None:
                edited.title = title.strip()
            if parent is not None or clear_parents:
                edited.parents = list(dict.fromkeys(parent or []))
            if entry is not None or clear_entries:
                edited.entry_points = [e for e in entry or [] if e.strip()]
            for name, value in _parse_scores(score).items():
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
                raise _Cancelled()
            return experiment.edit(ctx.store, project_id, base, edited)

        outcome = _edit_until_valid(
            ctx, render_record(project_id, base), f"{project_id}-{base.id}", apply
        )
        if outcome is _UNCHANGED:
            return
        record, changed = outcome  # type: ignore[misc]

    if json_output:
        _print_json(
            {"experiment": _experiment_json(project_id, record), "changed": changed}
        )
    elif changed:
        labels = ", ".join(_field_label(key) for key in changed)
        typer.echo(f"Updated {record.id}: {labels}")
    else:
        typer.echo("No changes.")


# Reading commands


@app.command()
def show(
    exp_id: Annotated[str, typer.Argument(metavar="ID", help="The experiment.")],
    project_name: ProjectOption = None,
    json_output: JsonOption = False,
) -> None:
    """Show one experiment record."""
    ctx = _context()
    with _errors():
        project_id = ctx.project(project_name)
        record = experiment.load_record(ctx.store.view(), project_id, exp_id)
    if json_output:
        _print_json({"experiment": _experiment_json(project_id, record)})
        return
    rows = [
        ("project", project_id),
        ("status", record.status),
        ("started", _time(record.started_at)),
        ("ended", _time(record.ended_at)),
        ("parents", ", ".join(record.parents) or "-"),
        ("entry points", ", ".join(record.entry_points) or "-"),
        ("scores", _score_line(record)),
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
        _fail(f"unknown status {status!r}; use one of {', '.join(STATUSES)}")
    ctx = _context()
    with _errors():
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
        _print_json(
            {
                "project": project_id,
                "experiments": [
                    _experiment_json(project_id, r, in_progress_here=r.id in here)
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
        max(_width(row[i]) for row in [header, *rows]) for i in range(len(header))
    ]
    for row in [header, *rows]:
        typer.echo(
            "  ".join(
                _pad(cell, w) for cell, w in zip(row, widths, strict=True)
            ).rstrip()
        )
