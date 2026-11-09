"""Recording procedures: start, finish, abandon and register experiments."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime

from melog.git import Repo
from melog.ids import id_sort_key, next_id, parse_id
from melog.project import Project, load_project
from melog.record import (
    MOTIVATION,
    Record,
    RecordError,
    dump_record,
    now,
    parse_record,
    record_path,
    records_dir,
)
from melog.snapshot import SNAPSHOT_REF_PREFIX, keep, snapshot_ref, take_snapshot
from melog.store import Store, StoreView
from melog.workspace import Active, Workspace


class ExperimentError(Exception):
    """Base class for errors in recording procedures."""


class ExperimentNotFoundError(ExperimentError):
    def __init__(self, project: str, exp_id: str) -> None:
        super().__init__(f"experiment {exp_id!r} does not exist in project {project!r}")


class ParentNotFoundError(ExperimentError):
    def __init__(self, project: str, parents: list[str]) -> None:
        super().__init__(
            f"parent experiments not found in project {project!r}: {', '.join(parents)}"
        )


class InvalidTitleError(ExperimentError):
    def __init__(self) -> None:
        super().__init__("the title must not be empty")


class WorkspaceBusyError(ExperimentError):
    def __init__(self, active: list[Active]) -> None:
        self.active = active
        listed = ", ".join(f"{a.id} ({a.project})" for a in active)
        super().__init__(
            f"this workspace already has an experiment in progress: {listed}. "
            "Finish it with 'melog done' or 'melog abandon' first; an abandoned "
            "experiment can still be the parent of a new one. To run experiments "
            "in parallel, use another workspace such as a git worktree"
        )


class NothingActiveError(ExperimentError):
    def __init__(self) -> None:
        super().__init__(
            "no experiment is in progress in this workspace; pass an id, or record "
            "an experiment you never started with 'melog done --new TITLE'"
        )


class AmbiguousActiveError(ExperimentError):
    def __init__(self, ids: list[str]) -> None:
        self.ids = ids
        super().__init__(
            f"several experiments are in progress in this workspace "
            f"({', '.join(ids)}); pass the id of the one to finish"
        )


class NotInProgressError(ExperimentError):
    def __init__(self, exp_id: str, status: str) -> None:
        super().__init__(f"experiment {exp_id!r} is not in progress (status: {status})")


class NotActiveHereError(ExperimentError):
    def __init__(self, exp_id: str) -> None:
        super().__init__(
            f"experiment {exp_id!r} was started in another workspace; finish it "
            "there, because the end snapshot captures the code of the workspace you "
            "run the command in. Pass --force to finish it here anyway"
        )


class UnknownRevisionError(ExperimentError):
    def __init__(self, rev: str) -> None:
        super().__init__(f"{rev!r} is not a commit in this repository")


@dataclass
class Outcome:
    record: Record
    large_files: list[tuple[str, int]] = field(default_factory=list)


# Reading


def load_record(view: StoreView, project: str, exp_id: str) -> Record:
    text = view.read(record_path(project, exp_id))
    if text is None:
        raise ExperimentNotFoundError(project, exp_id)
    return parse_record(text)


def list_records(view: StoreView, project: str) -> list[Record]:
    """All records of ``project``, oldest first."""
    records = []
    for name in view.list_dir(records_dir(project)):
        if not name.endswith(".md"):
            continue
        try:
            records.append(parse_record(view.read(f"{records_dir(project)}/{name}")))
        except RecordError:
            continue
    return sorted(records, key=_chronological_key)


def _chronological_key(record: Record) -> tuple[float, tuple[str, int, str]]:
    # Compare instants, not strings: times may carry different UTC offsets.
    time = record.started_at or record.ended_at
    instant = datetime.fromisoformat(time).timestamp() if time else 0.0
    return (instant, id_sort_key(record.id))


def active_here(view: StoreView, workspace: Workspace) -> list[Active]:
    """Experiments started in this workspace that are still in progress."""
    result = []
    for entry in workspace.active():
        text = view.read(record_path(entry.project, entry.id))
        if text is None:
            continue
        try:
            if parse_record(text).status == "in_progress":
                result.append(entry)
        except RecordError:
            continue
    return result


# Checks


def _check_title(title: str) -> str:
    title = title.strip()
    if not title:
        raise InvalidTitleError()
    return title


def _check_parents(view: StoreView, project: str, parents: Iterable[str]) -> list[str]:
    parents = list(dict.fromkeys(parents))
    missing = [p for p in parents if view.read(record_path(project, p)) is None]
    if missing:
        raise ParentNotFoundError(project, missing)
    return parents


def _check_workspace(view: StoreView, workspace: Workspace, project: Project) -> None:
    """Allow several experiments at once only if all of them are horizontal."""
    active = active_here(view, workspace)
    if not active:
        return
    all_horizontal = project.horizontal and all(
        load_project(view, a.project).horizontal for a in active
    )
    if not all_horizontal:
        raise WorkspaceBusyError(active)


def _used_ids(repo: Repo, view: StoreView, project: str) -> list[str]:
    ids = [
        name.removesuffix(".md")
        for name in view.list_dir(records_dir(project))
        if name.endswith(".md")
    ]
    # Snapshot refs count too, in case a record was lost after its snapshot was kept.
    prefix = f"{SNAPSHOT_REF_PREFIX}/{project}/"
    ids += [ref.removeprefix(prefix).split("/")[0] for ref in repo.refs(prefix)]
    return [i for i in ids if parse_id(i) is not None]


def _new_record(
    project: Project,
    exp_id: str,
    title: str,
    parents: list[str],
    motivation: str | None,
    entry_points: Iterable[str],
) -> Record:
    record = Record(
        id=exp_id,
        title=title,
        parents=parents,
        entry_points=[p for p in entry_points if p.strip()],
        scores=dict.fromkeys(project.scores),
    )
    if motivation and motivation.strip():
        record.body[MOTIVATION] = motivation.strip()
    return record


def _apply_result(
    record: Record, scores: Mapping[str, str], verdict: str | None
) -> None:
    record.set_scores(scores)
    if verdict is not None and verdict.strip():
        record.verdict = verdict.strip()


# Procedures


def start(
    repo: Repo,
    store: Store,
    workspace: Workspace,
    project_name: str,
    initials: str,
    title: str,
    parents: Iterable[str] = (),
    motivation: str | None = None,
    entry_points: Iterable[str] = (),
) -> Outcome:
    title = _check_title(title)
    view = store.view()
    project = load_project(view, project_name)
    parent_ids = check_new(view, workspace, project_name, parents)

    snapshot = take_snapshot(repo, f"melog snapshot: start of {title}")
    created: list[Record] = []

    def build(view: StoreView):
        exp_id = next_id(initials, _used_ids(repo, view, project_name))
        record = _new_record(
            project, exp_id, title, parent_ids, motivation, entry_points
        )
        record.started_at = now()
        record.start_snapshot = snapshot.commit
        created[:] = [record]
        changes = {record_path(project_name, exp_id): dump_record(record)}
        return changes, f"start {project_name}/{exp_id}"

    store.update(build)
    record = created[0]
    keep(repo, snapshot_ref(project_name, record.id, "start"), snapshot.commit)
    workspace.add(project_name, record.id)
    return Outcome(record, snapshot.large_files)


def resolve_target(
    view: StoreView, workspace: Workspace, project: str, exp_id: str | None
) -> str:
    """The experiment that 'done' or 'abandon' acts on when the id may be omitted."""
    if exp_id is not None:
        return exp_id
    ids = [a.id for a in active_here(view, workspace) if a.project == project]
    if not ids:
        raise NothingActiveError()
    if len(ids) > 1:
        raise AmbiguousActiveError(ids)
    return ids[0]


def check_new(
    view: StoreView, workspace: Workspace, project_name: str, parents: Iterable[str]
) -> list[str]:
    """Check that a new experiment may be recorded here; return its parents."""
    project = load_project(view, project_name)
    parent_ids = _check_parents(view, project_name, parents)
    _check_workspace(view, workspace, project)
    return parent_ids


def prepare_finish(
    view: StoreView,
    workspace: Workspace,
    project_name: str,
    exp_id: str | None,
    force: bool = False,
) -> Record:
    """Find the experiment to finish and check that it may be finished here."""
    exp_id = resolve_target(view, workspace, project_name, exp_id)
    record = load_record(view, project_name, exp_id)
    if record.status != "in_progress":
        raise NotInProgressError(exp_id, record.status)
    if not force and Active(project_name, exp_id) not in workspace.active():
        raise NotActiveHereError(exp_id)
    return record


def finish(
    repo: Repo,
    store: Store,
    workspace: Workspace,
    project_name: str,
    exp_id: str | None,
    status: str,
    scores: Mapping[str, str] | None = None,
    verdict: str | None = None,
    force: bool = False,
) -> Outcome:
    """Mark an experiment in progress as done or abandoned."""
    record = prepare_finish(store.view(), workspace, project_name, exp_id, force)
    exp_id = record.id
    # Validate the scores before taking a snapshot, so a typo costs nothing.
    Record(id=exp_id, title=record.title).set_scores(scores or {})

    snapshot = take_snapshot(repo, f"melog snapshot: end of {record.title}")
    finished: list[Record] = []

    def build(view: StoreView):
        current = load_record(view, project_name, exp_id)
        if current.status != "in_progress":
            raise NotInProgressError(exp_id, current.status)
        current.status = status
        current.ended_at = now()
        current.end_snapshot = snapshot.commit
        _apply_result(current, scores or {}, verdict)
        finished[:] = [current]
        changes = {record_path(project_name, exp_id): dump_record(current)}
        return changes, f"{status} {project_name}/{exp_id}"

    store.update(build)
    keep(repo, snapshot_ref(project_name, exp_id, "end"), snapshot.commit)
    workspace.remove(project_name, exp_id)
    return Outcome(finished[0], snapshot.large_files)


def register(
    repo: Repo,
    store: Store,
    workspace: Workspace,
    project_name: str,
    initials: str,
    title: str,
    parents: Iterable[str] = (),
    motivation: str | None = None,
    entry_points: Iterable[str] = (),
    since: str | None = None,
    scores: Mapping[str, str] | None = None,
    verdict: str | None = None,
) -> Outcome:
    """Record a finished experiment whose start was never declared."""
    title = _check_title(title)
    view = store.view()
    project = load_project(view, project_name)
    parent_ids = check_new(view, workspace, project_name, parents)
    Record(id="check", title=title).set_scores(scores or {})

    start_commit = start_time = None
    if since is not None:
        start_commit = repo.resolve(f"{since}^{{commit}}")
        if start_commit is None:
            raise UnknownRevisionError(since)
        start_time = repo.commit_time(start_commit)

    snapshot = take_snapshot(repo, f"melog snapshot: end of {title}")
    created: list[Record] = []

    def build(view: StoreView):
        exp_id = next_id(initials, _used_ids(repo, view, project_name))
        record = _new_record(
            project, exp_id, title, parent_ids, motivation, entry_points
        )
        record.status = "done"
        record.started_at = start_time
        record.start_snapshot = start_commit
        record.ended_at = now()
        record.end_snapshot = snapshot.commit
        _apply_result(record, scores or {}, verdict)
        created[:] = [record]
        changes = {record_path(project_name, exp_id): dump_record(record)}
        return changes, f"register {project_name}/{exp_id}"

    store.update(build)
    record = created[0]
    if start_commit is not None:
        keep(repo, snapshot_ref(project_name, record.id, "start"), start_commit)
    keep(repo, snapshot_ref(project_name, record.id, "end"), snapshot.commit)
    return Outcome(record, snapshot.large_files)
